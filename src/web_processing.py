"""Web-adapted processing for LMN Job History PDF uploads."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List

from src.calculations.allocation import compute
from src.db.invoice_history import find_already_invoiced
from src.invoice.line_items import (
    InvoiceData,
    LineItem,
    build_all_invoices,
    extract_zero_price_items,
    load_included_items,
)
from src.mapping.customer_mapping import (
    find_unmapped_jobsites,
    load_mapping_from_lmn_api,
)
from src.parsing.pdf_parser import PdfParseError, parse_pdf


class ProcessingError(Exception):
    """Raised when a PDF upload can't be processed."""


def check_for_duplicates(invoices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Look up prior invoices that overlap any (jobsite, date, foreman) triple.

    Silently returns [] if the DB is unavailable so the app keeps working
    without duplicate detection when running outside production.
    """
    duplicates: list[dict] = []
    try:
        for inv in invoices:
            pairs = inv.get("date_foreman_pairs") or []
            if not pairs:
                continue
            matches = find_already_invoiced(inv["jobsite_id"], pairs)
            for m in matches:
                duplicates.append(
                    {
                        "jobsite_id": inv["jobsite_id"],
                        "customer_name": inv["customer_name"],
                        "overlapping_pairs": m["overlapping_pairs"],
                        "qbo_invoice_number": m["qbo_invoice_number"],
                        "qbo_invoice_id": m["qbo_invoice_id"],
                        "created_at": m["created_at"],
                    }
                )
    except Exception:
        return []
    return duplicates


def process_uploaded_pdf(filename: str, content: BytesIO) -> Dict[str, Any]:
    """Parse an LMN Job History PDF and build invoices ready for the UI.

    Returns a session-shaped dict with `invoices`, `unmapped_jobsites`,
    `duplicates`, `zero_price_items`, `lmn_mapping_count`, `total_amount`,
    and `summary`.
    """
    if not filename.lower().endswith(".pdf"):
        raise ProcessingError("Upload must be a .pdf file.")

    try:
        content.seek(0)
        report = parse_pdf(content)
    except PdfParseError as e:
        raise ProcessingError(str(e))
    except Exception as e:
        raise ProcessingError(f"Could not read PDF: {e}")

    allocation = compute(report)
    included = load_included_items()
    invoice_date = datetime.now().strftime("%Y-%m-%d")
    invoices = build_all_invoices(
        allocation.rollups.values(), included=included, invoice_date=invoice_date
    )

    zero_price_items: list[dict] = []
    for invoice in invoices:
        rollup = allocation.rollups.get(invoice.jobsite_id)
        if rollup is None:
            continue
        for item in extract_zero_price_items(rollup.services, included):
            item["jobsite_id"] = invoice.jobsite_id
            item["jobsite_name"] = invoice.jobsite_name
            item["customer_name"] = invoice.customer_name
            item["index"] = len(zero_price_items)
            zero_price_items.append(item)

    mappings = load_mapping_from_lmn_api()
    lmn_mapping_count = len(mappings)

    jobsite_ids = [inv.jobsite_id for inv in invoices]
    unmapped_ids = find_unmapped_jobsites(jobsite_ids, mappings)

    unmapped_jobsites: list[dict] = []
    for inv in invoices:
        if inv.jobsite_id in unmapped_ids:
            unmapped_jobsites.append(
                {
                    "jobsite_id": inv.jobsite_id,
                    "jobsite_name": inv.jobsite_name,
                    "customer_name": inv.customer_name,
                }
            )

    all_invoices = [invoice_to_dict(inv) for inv in invoices]

    mapped_count = 0
    total_amount = 0.0
    for inv_dict in all_invoices:
        mapping = mappings.get(str(inv_dict["jobsite_id"]))
        if mapping:
            inv_dict["qbo_customer_id"] = mapping.qbo_customer_id
            inv_dict["qbo_display_name"] = mapping.qbo_display_name
            mapped_count += 1
            total_amount += inv_dict["total"]

    duplicates = check_for_duplicates(all_invoices)

    return {
        "invoices": all_invoices,
        "unmapped_jobsites": unmapped_jobsites,
        "duplicates": duplicates,
        "zero_price_items": zero_price_items,
        "lmn_mapping_count": lmn_mapping_count,
        "total_amount": total_amount,
        "summary": {
            "total_jobsites": len(invoices),
            "mapped_jobsites": mapped_count,
            "unmapped_jobsites": len(unmapped_jobsites),
            "duplicates_found": len(duplicates),
            "total_line_items": sum(len(inv["line_items"]) for inv in all_invoices),
        },
    }


def invoice_to_dict(invoice: InvoiceData) -> Dict[str, Any]:
    """Convert InvoiceData to a JSON-serializable dict for session storage."""
    return {
        "jobsite_id": str(invoice.jobsite_id),
        "jobsite_name": str(invoice.jobsite_name),
        "customer_name": str(invoice.customer_name),
        "invoice_date": str(invoice.invoice_date),
        "line_items": [
            {
                "description": str(item.description),
                "quantity": float(item.quantity),
                "rate": float(item.rate),
                "amount": float(item.amount),
            }
            for item in invoice.line_items
        ],
        "subtotal": float(invoice.subtotal),
        "direct_payment_fee": float(invoice.direct_payment_fee),
        "total": float(invoice.total),
        "work_dates": [str(d) for d in invoice.work_dates],
        "foremen": [str(f) for f in invoice.foremen],
        "date_foreman_pairs": [str(p) for p in invoice.date_foreman_pairs],
    }


def create_qbo_invoices(invoices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Create draft invoices in QBO from session invoice dicts."""
    from src.qbo.invoices import create_draft_invoice, get_labor_item_ref

    labor_item_ref = get_labor_item_ref()
    results: list[dict] = []

    for inv_dict in invoices:
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
            work_dates=inv_dict.get("work_dates", []),
            foremen=inv_dict.get("foremen", []),
            date_foreman_pairs=inv_dict.get("date_foreman_pairs", []),
        )

        result = create_draft_invoice(
            invoice,
            qbo_customer_id=inv_dict["qbo_customer_id"],
            item_ref=labor_item_ref,
        )

        results.append(
            {
                "success": result.success,
                "jobsite_id": result.jobsite_id,
                "customer_name": inv_dict.get("qbo_display_name") or result.customer_name,
                "invoice_id": result.invoice_id,
                "invoice_number": result.invoice_number,
                "total": result.total,
                "error": result.error,
            }
        )

    return results
