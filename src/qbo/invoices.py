"""QuickBooks Online invoice operations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, Optional

import requests

from src.invoice.line_items import MAINTENANCE_CLASS_NAME, InvoiceData, LineItem
from src.qbo.context import get_qbo_credentials
from src.qbo.customers import get_api_base_url

logger = logging.getLogger(__name__)


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
    class_refs_by_name: Optional[Dict[str, Dict[str, str]]] = None,
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
        class_refs_by_name: Map of class name ("Maintenance"/"Irrigation")
            to QBO ClassRef. Each line is tagged via `item.class_name`.
            Requires ClassTrackingPerTxnLine preference on the company.
        terms: Payment terms (default Net 15)

    Returns:
        InvoiceResult with success status and invoice details
    """
    access_token, realm_id = get_qbo_credentials()

    # Calculate due date based on terms
    invoice_date = datetime.strptime(invoice_data.invoice_date, "%Y-%m-%d")
    due_date = calculate_due_date(invoice_date, terms)

    class_refs_by_name = class_refs_by_name or {}
    default_class_ref = class_refs_by_name.get(MAINTENANCE_CLASS_NAME)

    # Build line items for QBO API
    qbo_lines = []
    for i, item in enumerate(invoice_data.line_items, start=1):
        ref = item_refs.get(item.item_lookup_name)
        cref = class_refs_by_name.get(
            item.class_name or MAINTENANCE_CLASS_NAME, default_class_ref
        )
        qbo_line = build_qbo_line_item(item, i, ref, class_ref=cref)
        qbo_lines.append(qbo_line)

    source_ids = [s.jobsite_id for s in invoice_data.sources] or [invoice_data.jobsite_id]
    private_note = (
        "Created from LMN export. JobsiteIDs: " + ", ".join(source_ids)
    )

    # Build invoice payload
    payload = {
        "CustomerRef": {"value": qbo_customer_id},
        "TxnDate": invoice_data.invoice_date,
        "DueDate": due_date.strftime("%Y-%m-%d"),
        "Line": qbo_lines,
        "PrivateNote": private_note,
    }

    url = f"{get_api_base_url()}/{realm_id}/invoice"

    logger.debug(
        "POST QBO invoice: jobsite=%s customer_ref=%s lines=%d",
        invoice_data.jobsite_id,
        qbo_customer_id,
        len(qbo_lines),
    )

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

        # Record invoice history for duplicate detection.
        # Merged (maint + Irr) invoices write one row per source so future
        # uploads catch overlap on either side.
        try:
            from src.db.invoice_history import record_invoice_creation

            if invoice_id:
                for src in invoice_data.sources:
                    if not src.date_foreman_pairs:
                        continue
                    record_invoice_creation(
                        jobsite_id=src.jobsite_id,
                        work_dates=src.work_dates,
                        foremen=src.foremen,
                        date_foreman_pairs=src.date_foreman_pairs,
                        qbo_invoice_id=invoice_id,
                        qbo_invoice_number=invoice_number or "",
                        total_amount=total_amt,
                    )
        except Exception:
            logger.exception(
                "Failed to record invoice history for jobsite=%s invoice_id=%s",
                invoice_data.jobsite_id,
                invoice_id,
            )

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
        intuit_tid = None
        try:
            intuit_tid = e.response.headers.get("intuit_tid") if e.response is not None else None
            error_detail = e.response.json()
            if "Fault" in error_detail:
                errors = error_detail["Fault"].get("Error", [])
                if errors:
                    error_msg = errors[0].get("Detail", error_msg)
        except Exception:
            pass
        logger.error(
            "QBO invoice POST failed: jobsite=%s status=%s intuit_tid=%s error=%s",
            invoice_data.jobsite_id,
            getattr(e.response, "status_code", "?"),
            intuit_tid,
            error_msg,
        )

        return InvoiceResult(
            success=False,
            jobsite_id=invoice_data.jobsite_id,
            customer_name=invoice_data.customer_name,
            error=error_msg,
        )

    except Exception as e:
        logger.exception(
            "Unexpected error creating QBO invoice for jobsite=%s",
            invoice_data.jobsite_id,
        )
        return InvoiceResult(
            success=False,
            jobsite_id=invoice_data.jobsite_id,
            customer_name=invoice_data.customer_name,
            error=str(e),
        )


@dataclass
class DeleteResult:
    """Result of deleting an invoice in QBO."""

    success: bool
    invoice_id: str
    error: Optional[str] = None


def delete_invoice(invoice_id: str) -> DeleteResult:
    """Delete a QBO invoice by ID.

    Fast path: drafts created by this app are never edited, so their SyncToken
    is "0". POST `?operation=delete` with `SyncToken="0"` directly. Only on a
    SyncToken mismatch (rare — only if something else touched the invoice) do
    we fall back to GET-then-POST. Halves wall time on the common path.
    """
    access_token, realm_id = get_qbo_credentials()
    base = f"{get_api_base_url()}/{realm_id}/invoice"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    def _post_delete(sync_token: str) -> requests.Response:
        return requests.post(
            f"{base}?operation=delete",
            headers=headers,
            json={"Id": invoice_id, "SyncToken": sync_token},
        )

    def _format_http_error(e: requests.exceptions.HTTPError) -> str:
        msg = str(e)
        try:
            detail = e.response.json()
            if "Fault" in detail:
                errs = detail["Fault"].get("Error", [])
                if errs:
                    msg = errs[0].get("Detail", msg)
        except Exception:
            pass
        return msg

    try:
        resp = _post_delete("0")
        if resp.status_code == 200:
            logger.info("Deleted QBO invoice id=%s (sync=0 fast path)", invoice_id)
            return DeleteResult(success=True, invoice_id=invoice_id)

        # Fallback: 4xx may mean SyncToken mismatch. Fetch the invoice and retry.
        if 400 <= resp.status_code < 500:
            get_resp = requests.get(f"{base}/{invoice_id}", headers=headers)
            get_resp.raise_for_status()
            sync_token = get_resp.json().get("Invoice", {}).get("SyncToken")
            if sync_token is None:
                return DeleteResult(
                    success=False,
                    invoice_id=invoice_id,
                    error="Missing SyncToken on fetched invoice",
                )
            retry = _post_delete(str(sync_token))
            retry.raise_for_status()
            logger.info(
                "Deleted QBO invoice id=%s (sync=%s after fast-path 4xx)",
                invoice_id,
                sync_token,
            )
            return DeleteResult(success=True, invoice_id=invoice_id)

        resp.raise_for_status()
        return DeleteResult(success=True, invoice_id=invoice_id)

    except requests.exceptions.HTTPError as e:
        error_msg = _format_http_error(e)
        logger.error(
            "QBO delete failed: id=%s status=%s error=%s",
            invoice_id,
            getattr(e.response, "status_code", "?"),
            error_msg,
        )
        return DeleteResult(success=False, invoice_id=invoice_id, error=error_msg)
    except Exception as e:
        logger.exception("Unexpected error deleting invoice id=%s", invoice_id)
        return DeleteResult(success=False, invoice_id=invoice_id, error=str(e))


def query_invoices_created_since(iso_datetime: str) -> list:
    """Return QBO invoices with MetaData.CreateTime >= the given ISO-8601 timestamp.

    Each entry is the raw QBO Invoice dict (includes Id, DocNumber, CustomerRef,
    TotalAmt, MetaData.CreateTime). Used by the cleanup preview to detect
    QBO-side invoices that have no matching invoice_history row.
    """
    access_token, realm_id = get_qbo_credentials()
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    query = (
        "SELECT Id, DocNumber, CustomerRef, TotalAmt, MetaData "
        f"FROM Invoice WHERE MetaData.CreateTime >= '{iso_datetime}' "
        "MAXRESULTS 1000"
    )
    url = f"{get_api_base_url()}/{realm_id}/query"
    resp = requests.get(url, headers=headers, params={"query": query})
    resp.raise_for_status()
    return resp.json().get("QueryResponse", {}).get("Invoice", [])


def build_qbo_line_item(
    item: LineItem,
    line_num: int,
    item_ref: Optional[Dict],
    class_ref: Optional[Dict[str, str]] = None,
) -> Dict:
    """
    Build a QBO API line item from our LineItem.

    Uses SalesItemLineDetail for proper invoice formatting.

    QBO validates `Amount == round(Qty * UnitPrice, 2)`. Aggregating multiple
    LMN entries with different per-entry rates can leave `item.rate`
    inconsistent with `item.amount`, so we derive UnitPrice from amount/qty
    at 6-decimal precision — that's enough to round-trip cleanly through
    QBO's check while preserving the LMN-reported amount as ground truth.
    """
    amount = round(item.amount, 2)
    qty = item.quantity or 0
    if qty > 0 and amount != 0:
        unit_price = round(amount / qty, 6)
    else:
        unit_price = round(item.rate or 0, 6)

    line = {
        "LineNum": line_num,
        "DetailType": "SalesItemLineDetail",
        "Amount": amount,
        "Description": item.description,
        "SalesItemLineDetail": {
            "Qty": qty,
            "UnitPrice": unit_price,
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


