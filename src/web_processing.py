"""Web-adapted processing logic for LMN data files (CSV/Excel)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from io import BytesIO, StringIO
from typing import Any, Dict, List, Tuple

import pandas as pd

from src.calculations.time_calc import calculate_billable_hours
from src.invoice.line_items import build_all_invoices, InvoiceData, LineItem
from src.mapping.customer_mapping import (
    CustomerMapping,
    find_unmapped_jobsites,
    get_qbo_customer_id,
    load_customer_mapping,
)
from src.parsing.lmn_parser import (
    detect_file_type,
    read_data_file,
    TIME_DATA_REQUIRED_COLUMNS,
    SERVICE_DATA_REQUIRED_COLUMNS,
)


class ProcessingError(Exception):
    """Error during file processing."""

    pass


def detect_uploaded_files(
    files: List[Tuple[str, BytesIO]],
) -> Dict[str, Dict[str, Any]]:
    """
    Detect file types from uploaded files.

    Args:
        files: List of (filename, BytesIO) tuples

    Returns:
        Dict with detection results:
        - files: List of {filename, detected_type, error} dicts
        - valid: Boolean indicating if exactly one of each type
        - error: Overall error message if invalid
    """
    results = {"files": [], "valid": False, "error": None}
    detected_types = {"time_data": None, "service_data": None}

    for filename, content in files:
        file_info = {"filename": filename, "detected_type": None, "error": None}

        try:
            detected_type = detect_file_type(filename, content)
            file_info["detected_type"] = detected_type

            # Check for duplicates
            if detected_types[detected_type] is not None:
                file_info["error"] = f"Duplicate {detected_type.replace('_', ' ')} file"
            else:
                detected_types[detected_type] = filename
        except ValueError as e:
            file_info["error"] = str(e)

        results["files"].append(file_info)

    # Check if we have exactly one of each
    has_time = detected_types["time_data"] is not None
    has_service = detected_types["service_data"] is not None
    has_errors = any(f["error"] for f in results["files"])

    if has_time and has_service and not has_errors:
        results["valid"] = True
    elif not has_time and not has_service:
        results["error"] = "Please upload both Time Data and Service Data files"
    elif not has_time:
        results["error"] = "Missing Time Data file"
    elif not has_service:
        results["error"] = "Missing Service Data file"
    elif has_errors:
        results["error"] = "Please fix file errors before proceeding"

    return results


def process_uploaded_files(
    files: List[Tuple[str, BytesIO]],
) -> Dict[str, Any]:
    """
    Process uploaded files with auto-detection.

    Args:
        files: List of (filename, BytesIO) tuples

    Returns:
        Dict with:
        - invoices: List of invoice data dicts ready for display/creation
        - unmapped_jobsites: List of jobsites needing customer mapping
        - total_amount: Sum of all invoice totals
        - summary: Processing summary info

    Raises:
        ProcessingError: If files cannot be processed
    """
    # Detect file types
    detection = detect_uploaded_files(files)
    if not detection["valid"]:
        raise ProcessingError(detection["error"])

    # Find time and service files
    time_file = None
    service_file = None
    for filename, content in files:
        detected = detect_file_type(filename, content)
        if detected == "time_data":
            time_file = (filename, content)
        elif detected == "service_data":
            service_file = (filename, content)

    if not time_file or not service_file:
        raise ProcessingError("Could not identify both required files")

    # Parse files
    try:
        time_df = read_data_file(time_file[1], time_file[0])
        validate_time_data(time_df)
        time_df = clean_time_data(time_df)
    except ProcessingError:
        raise
    except Exception as e:
        raise ProcessingError(f"Error parsing Time Data file: {e}")

    try:
        service_df = read_data_file(service_file[1], service_file[0])
        validate_service_data(service_df)
        service_df = clean_service_data(service_df)
    except ProcessingError:
        raise
    except Exception as e:
        raise ProcessingError(f"Error parsing Service Data file: {e}")

    # Calculate billable hours
    jobsite_hours_list = calculate_billable_hours(time_df)

    # Build invoices
    invoice_date = datetime.now().strftime("%Y-%m-%d")
    invoices = build_all_invoices(jobsite_hours_list, service_df, invoice_date)

    # Load customer mappings
    mappings = load_customer_mapping()

    # Find unmapped jobsites
    jobsite_ids = [inv.jobsite_id for inv in invoices]
    unmapped_ids = find_unmapped_jobsites(jobsite_ids, mappings)

    # Build unmapped jobsite info for UI
    unmapped_jobsites = []
    for inv in invoices:
        if inv.jobsite_id in unmapped_ids:
            unmapped_jobsites.append({
                "jobsite_id": inv.jobsite_id,
                "jobsite_name": inv.jobsite_name,
                "customer_name": inv.customer_name,
            })

    # Convert ALL invoices to dicts (store all, filter at results time)
    all_invoices = [invoice_to_dict(inv) for inv in invoices]

    # Calculate totals for currently mapped invoices
    mapped_count = 0
    total_amount = 0.0
    for inv_dict in all_invoices:
        qbo_customer_id = get_qbo_customer_id(inv_dict["jobsite_id"], mappings)
        if qbo_customer_id:
            inv_dict["qbo_customer_id"] = qbo_customer_id
            mapped_count += 1
            total_amount += inv_dict["total"]

    return {
        "invoices": all_invoices,
        "unmapped_jobsites": unmapped_jobsites,
        "total_amount": total_amount,
        "summary": {
            "total_jobsites": len(invoices),
            "mapped_jobsites": mapped_count,
            "unmapped_jobsites": len(unmapped_jobsites),
            "total_line_items": sum(len(inv["line_items"]) for inv in all_invoices),
        },
    }


def process_csv_files(
    time_data: StringIO, service_data: StringIO
) -> Dict[str, Any]:
    """
    Process uploaded CSV files and prepare invoice data.

    DEPRECATED: Use process_uploaded_files() instead for new code.

    Args:
        time_data: StringIO containing time data CSV
        service_data: StringIO containing service data CSV

    Returns:
        Dict with:
        - invoices: List of invoice data dicts ready for display/creation
        - unmapped_jobsites: List of jobsites needing customer mapping
        - total_amount: Sum of all invoice totals
        - summary: Processing summary info
    """
    # Parse CSVs
    try:
        time_df = pd.read_csv(time_data)
        validate_time_data(time_df)
        time_df = clean_time_data(time_df)
    except Exception as e:
        raise ProcessingError(f"Error parsing Time Data CSV: {e}")

    try:
        service_df = pd.read_csv(service_data)
        validate_service_data(service_df)
        service_df = clean_service_data(service_df)
    except Exception as e:
        raise ProcessingError(f"Error parsing Service Data CSV: {e}")

    # Calculate billable hours
    jobsite_hours_list = calculate_billable_hours(time_df)

    # Build invoices
    invoice_date = datetime.now().strftime("%Y-%m-%d")
    invoices = build_all_invoices(jobsite_hours_list, service_df, invoice_date)

    # Load customer mappings
    mappings = load_customer_mapping()

    # Find unmapped jobsites
    jobsite_ids = [inv.jobsite_id for inv in invoices]
    unmapped_ids = find_unmapped_jobsites(jobsite_ids, mappings)

    # Build unmapped jobsite info for UI
    unmapped_jobsites = []
    for inv in invoices:
        if inv.jobsite_id in unmapped_ids:
            unmapped_jobsites.append({
                "jobsite_id": inv.jobsite_id,
                "jobsite_name": inv.jobsite_name,
                "customer_name": inv.customer_name,
            })

    # Convert ALL invoices to dicts (store all, filter at results time)
    all_invoices = [invoice_to_dict(inv) for inv in invoices]

    # Calculate totals for currently mapped invoices
    mapped_count = 0
    total_amount = 0.0
    for inv_dict in all_invoices:
        qbo_customer_id = get_qbo_customer_id(inv_dict["jobsite_id"], mappings)
        if qbo_customer_id:
            inv_dict["qbo_customer_id"] = qbo_customer_id
            mapped_count += 1
            total_amount += inv_dict["total"]

    return {
        "invoices": all_invoices,  # Store ALL invoices
        "unmapped_jobsites": unmapped_jobsites,
        "total_amount": total_amount,
        "summary": {
            "total_jobsites": len(invoices),
            "mapped_jobsites": mapped_count,
            "unmapped_jobsites": len(unmapped_jobsites),
            "total_line_items": sum(len(inv["line_items"]) for inv in all_invoices),
        },
    }


def validate_time_data(df: pd.DataFrame) -> None:
    """Validate time data CSV has required columns."""
    missing = [col for col in TIME_DATA_REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ProcessingError(f"Time data missing required columns: {missing}")


def validate_service_data(df: pd.DataFrame) -> None:
    """Validate service data CSV has required columns."""
    missing = [col for col in SERVICE_DATA_REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ProcessingError(f"Service data missing required columns: {missing}")


def clean_time_data(df: pd.DataFrame) -> pd.DataFrame:
    """Clean and normalize time data."""
    df["Man Hours"] = pd.to_numeric(df["Man Hours"], errors="coerce").fillna(0)
    df["Billable Rate"] = (
        df["Billable Rate"]
        .replace(r"[\$,]", "", regex=True)
        .pipe(pd.to_numeric, errors="coerce")
        .fillna(0)
    )
    df["JobsiteID"] = df["JobsiteID"].astype(str)
    df["TimesheetID"] = df["TimesheetID"].astype(str)
    return df


def clean_service_data(df: pd.DataFrame) -> pd.DataFrame:
    """Clean and normalize service data."""
    for col in ["Unit Price", "Total Price", "Unit Cost"]:
        if col in df.columns:
            df[col] = (
                df[col]
                .replace(r"[\$,]", "", regex=True)
                .pipe(pd.to_numeric, errors="coerce")
                .fillna(0)
            )
    df["Timesheet Qty"] = pd.to_numeric(df["Timesheet Qty"], errors="coerce").fillna(0)
    df["JobsiteID"] = df["JobsiteID"].astype(str)
    df["TimesheetID"] = df["TimesheetID"].astype(str)
    return df


def invoice_to_dict(invoice: InvoiceData) -> Dict[str, Any]:
    """Convert InvoiceData to dict for JSON serialization."""
    return {
        "jobsite_id": invoice.jobsite_id,
        "jobsite_name": invoice.jobsite_name,
        "customer_name": invoice.customer_name,
        "invoice_date": invoice.invoice_date,
        "line_items": [
            {
                "description": item.description,
                "quantity": item.quantity,
                "rate": item.rate,
                "amount": item.amount,
            }
            for item in invoice.line_items
        ],
        "subtotal": invoice.subtotal,
        "direct_payment_fee": invoice.direct_payment_fee,
        "total": invoice.total,
        "timesheet_ids": invoice.timesheet_ids,
        "work_dates": invoice.work_dates,
    }


def create_qbo_invoices(invoices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Create draft invoices in QuickBooks Online.

    Args:
        invoices: List of invoice dicts with qbo_customer_id

    Returns:
        List of result dicts with success status and invoice details
    """
    from src.qbo.invoices import create_draft_invoice, get_labor_item_ref, InvoiceResult
    from src.invoice.line_items import InvoiceData, LineItem

    # Get the labor item reference for line items
    labor_item_ref = get_labor_item_ref()

    results = []
    for inv_dict in invoices:
        # Reconstruct InvoiceData from dict
        invoice = InvoiceData(
            jobsite_id=inv_dict["jobsite_id"],
            jobsite_name=inv_dict["jobsite_name"],
            customer_name=inv_dict["customer_name"],
            invoice_date=inv_dict["invoice_date"],
            line_items=[
                LineItem(
                    description=item["description"],
                    quantity=item["quantity"],
                    rate=item["rate"],
                    amount=item["amount"],
                )
                for item in inv_dict["line_items"]
            ],
            subtotal=inv_dict["subtotal"],
            direct_payment_fee=inv_dict["direct_payment_fee"],
            total=inv_dict["total"],
            timesheet_ids=inv_dict.get("timesheet_ids", []),
            work_dates=inv_dict.get("work_dates", []),
        )

        # Create invoice in QBO
        result = create_draft_invoice(
            invoice,
            qbo_customer_id=inv_dict["qbo_customer_id"],
            item_ref=labor_item_ref,
        )

        results.append({
            "success": result.success,
            "jobsite_id": result.jobsite_id,
            "customer_name": inv_dict.get("qbo_display_name") or result.customer_name,
            "invoice_id": result.invoice_id,
            "invoice_number": result.invoice_number,
            "total": result.total,
            "error": result.error,
        })

    return results
