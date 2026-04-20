"""QuickBooks Online invoice operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional

import requests

from src.invoice.line_items import InvoiceData, LineItem
from src.qbo.context import get_qbo_credentials
from src.qbo.customers import get_api_base_url


@dataclass
class InvoiceResult:
    """Result of creating an invoice in QBO."""

    success: bool
    jobsite_id: str
    customer_name: str
    invoice_id: Optional[str] = None
    invoice_number: Optional[str] = None
    total: float = 0
    error: Optional[str] = None


def create_draft_invoice(
    invoice_data: InvoiceData,
    qbo_customer_id: str,
    item_refs: Dict[str, Dict[str, str]],
    class_ref: Optional[Dict[str, str]] = None,
    terms: str = "Net 15",
) -> InvoiceResult:
    """
    Create a draft invoice in QuickBooks Online.

    Args:
        invoice_data: Invoice data with line items
        qbo_customer_id: QBO customer ID
        item_refs: Map of `item_lookup_name` -> QBO ItemRef. Every line's
            lookup name must resolve (unmatched names use the pre-fetched
            fallback ItemRef) — QBO rejects invoice lines missing ItemRef.
        class_ref: Optional QBO ClassRef applied to every line. Requires
            ClassTrackingPerTxnLine preference enabled on the company.
        terms: Payment terms (default Net 15)

    Returns:
        InvoiceResult with success status and invoice details
    """
    access_token, realm_id = get_qbo_credentials()

    # Calculate due date based on terms
    invoice_date = datetime.strptime(invoice_data.invoice_date, "%Y-%m-%d")
    due_date = calculate_due_date(invoice_date, terms)

    # Build line items for QBO API
    qbo_lines = []
    for i, item in enumerate(invoice_data.line_items, start=1):
        ref = item_refs.get(item.item_lookup_name)
        qbo_line = build_qbo_line_item(item, i, ref, class_ref=class_ref)
        qbo_lines.append(qbo_line)

    # Build invoice payload
    payload = {
        "CustomerRef": {"value": qbo_customer_id},
        "TxnDate": invoice_data.invoice_date,
        "DueDate": due_date.strftime("%Y-%m-%d"),
        "Line": qbo_lines,
        "PrivateNote": f"Created from LMN export. JobsiteID: {invoice_data.jobsite_id}",
    }

    url = f"{get_api_base_url()}/{realm_id}/invoice"

    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        response.raise_for_status()

        data = response.json()
        invoice = data.get("Invoice", {})

        invoice_id = invoice.get("Id")
        invoice_number = invoice.get("DocNumber")
        total_amt = float(invoice.get("TotalAmt", 0))

        # Record invoice history for duplicate detection
        try:
            from src.db.invoice_history import record_invoice_creation
            if invoice_id and invoice_data.date_foreman_pairs:
                record_invoice_creation(
                    jobsite_id=invoice_data.jobsite_id,
                    work_dates=invoice_data.work_dates,
                    foremen=invoice_data.foremen,
                    date_foreman_pairs=invoice_data.date_foreman_pairs,
                    qbo_invoice_id=invoice_id,
                    qbo_invoice_number=invoice_number or "",
                    total_amount=total_amt,
                )
        except Exception:
            # Database not available - skip history recording
            pass

        return InvoiceResult(
            success=True,
            jobsite_id=invoice_data.jobsite_id,
            customer_name=invoice_data.customer_name,
            invoice_id=invoice_id,
            invoice_number=invoice_number,
            total=total_amt,
        )

    except requests.exceptions.HTTPError as e:
        error_msg = str(e)
        try:
            error_detail = e.response.json()
            if "Fault" in error_detail:
                errors = error_detail["Fault"].get("Error", [])
                if errors:
                    error_msg = errors[0].get("Detail", error_msg)
        except Exception:
            pass

        return InvoiceResult(
            success=False,
            jobsite_id=invoice_data.jobsite_id,
            customer_name=invoice_data.customer_name,
            error=error_msg,
        )

    except Exception as e:
        return InvoiceResult(
            success=False,
            jobsite_id=invoice_data.jobsite_id,
            customer_name=invoice_data.customer_name,
            error=str(e),
        )


def build_qbo_line_item(
    item: LineItem,
    line_num: int,
    item_ref: Optional[Dict],
    class_ref: Optional[Dict[str, str]] = None,
) -> Dict:
    """
    Build a QBO API line item from our LineItem.

    Uses SalesItemLineDetail for proper invoice formatting.
    """
    line = {
        "LineNum": line_num,
        "DetailType": "SalesItemLineDetail",
        "Amount": round(item.amount, 2),
        "Description": item.description,
        "SalesItemLineDetail": {
            "Qty": item.quantity,
            "UnitPrice": item.rate,
        },
    }

    if item_ref:
        line["SalesItemLineDetail"]["ItemRef"] = item_ref

    if class_ref:
        line["SalesItemLineDetail"]["ClassRef"] = class_ref

    return line


def calculate_due_date(invoice_date: datetime, terms: str) -> datetime:
    """Calculate due date based on payment terms."""
    terms_days = {
        "Net 10": 10,
        "Net 15": 15,
        "Net 30": 30,
        "Net 60": 60,
        "Due on receipt": 0,
    }

    days = terms_days.get(terms, 15)
    return invoice_date + timedelta(days=days)


