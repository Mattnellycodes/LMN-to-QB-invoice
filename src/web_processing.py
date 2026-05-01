"""Web-adapted processing for LMN Job History PDF uploads."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional

from src.calculations.allocation import compute, load_excluded_jobsites
from src.db.invoice_history import find_already_invoiced
from src.invoice.line_items import (
    MAINTENANCE_CLASS_NAME,
    InvoiceData,
    InvoiceSource,
    LineItem,
    build_all_invoices,
    extract_zero_price_items,
    load_included_items,
)
from src.mapping.customer_mapping import (
    find_unmapped_jobsites,
    load_mapping_from_lmn_api,
)
from src.mapping.item_mapping import build_item_refs, build_normalized_cache
from src.parsing.pdf_parser import (
    ParsedReport,
    PdfParseError,
    SHOP_JOBSITE_ID,
    Task,
    parse_pdf,
)

logger = logging.getLogger(__name__)


class ProcessingError(Exception):
    """Raised when a PDF upload can't be processed."""


@dataclass(frozen=True)
class UploadedPdf:
    """PDF upload content after Flask has read the incoming file stream."""

    filename: str
    content: bytes


def check_for_duplicates(invoices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Look up prior invoices that overlap any (jobsite, date, foreman) triple.

    For merged invoices (maint + Irr) we query each source jobsite separately
    and collapse multiple hits for the same prior QBO invoice number into one
    warning row — the user sees a single "already invoiced" entry per prior
    invoice even when both sides overlapped.

    Silently returns [] if the DB is unavailable so the app keeps working
    without duplicate detection when running outside production.
    """
    duplicates: list[dict] = []
    try:
        for inv in invoices:
            sources = inv.get("sources") or []
            seen_by_invoice_num: dict[str, dict] = {}
            for src in sources:
                pairs = src.get("date_foreman_pairs") or []
                if not pairs:
                    continue
                for m in find_already_invoiced(src["jobsite_id"], pairs):
                    key = m["qbo_invoice_number"] or m.get("qbo_invoice_id") or ""
                    if key in seen_by_invoice_num:
                        entry = seen_by_invoice_num[key]
                        if src["jobsite_id"] not in entry["source_jobsite_ids"]:
                            entry["source_jobsite_ids"].append(src["jobsite_id"])
                        entry["overlapping_pairs"] = sorted(
                            set(entry["overlapping_pairs"])
                            | set(m["overlapping_pairs"])
                        )
                    else:
                        seen_by_invoice_num[key] = {
                            "jobsite_id": inv["jobsite_id"],
                            "source_jobsite_ids": [src["jobsite_id"]],
                            "customer_name": inv["customer_name"],
                            "overlapping_pairs": list(m["overlapping_pairs"]),
                            "qbo_invoice_number": m["qbo_invoice_number"],
                            "qbo_invoice_id": m["qbo_invoice_id"],
                            "created_at": m["created_at"],
                        }
            duplicates.extend(seen_by_invoice_num.values())
    except Exception:
        logger.exception("Duplicate detection failed; returning no duplicates")
        return []
    if duplicates:
        logger.info(
            "Duplicate detection: %d overlapping prior invoices", len(duplicates)
        )
    return duplicates


def process_uploaded_pdf(
    filename: str,
    content: BytesIO,
    use_hardcoded_prices: bool = False,
) -> Dict[str, Any]:
    """Parse an LMN Job History PDF and build invoices ready for the UI.

    Returns a session-shaped dict with `invoices`, `unmapped_jobsites`,
    `duplicates`, `zero_price_items`, `lmn_mapping_count`, `total_amount`,
    and `summary`.
    """
    content.seek(0)
    return process_uploaded_pdfs(
        [UploadedPdf(filename=filename, content=content.read())],
        use_hardcoded_prices=use_hardcoded_prices,
    )


def process_uploaded_pdfs(
    files: list[UploadedPdf],
    use_hardcoded_prices: bool = False,
) -> Dict[str, Any]:
    """Parse one or more LMN Job History PDFs as a single billing batch."""
    if not files:
        raise ProcessingError("Please upload at least one PDF.")

    t0 = time.monotonic()
    seen_hashes: dict[str, str] = {}
    parsed_reports: list[tuple[str, ParsedReport]] = []
    total_size = 0

    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            raise ProcessingError(f"{file.filename} must be a .pdf file.")
        digest = sha256(file.content).hexdigest()
        if digest in seen_hashes:
            raise ProcessingError(
                "Duplicate PDF uploaded: "
                f"{seen_hashes[digest]} and {file.filename} appear to be the same file."
            )
        seen_hashes[digest] = file.filename
        total_size += len(file.content)

    for file in files:
        logger.info(
            "Processing upload file: filename=%s size=%d bytes",
            file.filename,
            len(file.content),
        )
        try:
            report = parse_pdf(BytesIO(file.content))
        except PdfParseError as e:
            logger.warning("PDF parse failed for %s: %s", file.filename, e)
            raise ProcessingError(f"{file.filename}: {e}")
        except Exception as e:
            logger.exception("Unexpected error reading PDF %s", file.filename)
            raise ProcessingError(f"{file.filename}: Could not read PDF: {e}")

        logger.info(
            "Parsed PDF %s: customers=%d tasks=%d",
            file.filename,
            len(report.customers),
            len(report.tasks),
        )
        parsed_reports.append((file.filename, report))

    _reject_overlapping_tasks(parsed_reports)

    combined = ParsedReport(customers={}, tasks=[])
    for _, report in parsed_reports:
        combined.customers.update(report.customers)
        combined.tasks.extend(report.tasks)

    upload_label = files[0].filename if len(files) == 1 else f"{len(files)} PDFs"
    logger.info(
        "Processing upload batch: label=%s files=%d size=%d bytes tasks=%d",
        upload_label,
        len(files),
        total_size,
        len(combined.tasks),
    )
    if use_hardcoded_prices:
        return _process_parsed_report(
            combined,
            upload_label,
            t0,
            use_hardcoded_prices=True,
        )
    return _process_parsed_report(combined, upload_label, t0)


def _task_fingerprint(task: Task) -> tuple[str, str, str, str, str, str, str, float]:
    """Stable key for detecting the same LMN task across multiple PDFs."""
    return (
        task.jobsite_id,
        task.date,
        task.foreman,
        task.task_name,
        task.cost_code_num,
        task.start_time,
        task.end_time,
        round(task.task_man_hrs, 2),
    )


def _reject_overlapping_tasks(parsed_reports: list[tuple[str, ParsedReport]]) -> None:
    """Reject a batch when two different PDFs contain the same parsed task."""
    seen: dict[tuple[str, str, str, str, str, str, str, float], str] = {}
    for filename, report in parsed_reports:
        local_seen: set[tuple[str, str, str, str, str, str, str, float]] = set()
        for task in report.tasks:
            key = _task_fingerprint(task)
            if key in local_seen:
                continue
            local_seen.add(key)
            other_filename = seen.get(key)
            if other_filename and other_filename != filename:
                jobsite_id, date, foreman, task_name, *_ = key
                raise ProcessingError(
                    "Overlapping task found in uploaded PDFs: "
                    f"{other_filename} and {filename} both include "
                    f"{jobsite_id} / {date} / {foreman} / {task_name}."
                )
            seen[key] = filename


def _process_parsed_report(
    report: ParsedReport,
    upload_label: str,
    t0: float | None = None,
    use_hardcoded_prices: bool = False,
) -> Dict[str, Any]:
    """Build invoices and UI state from a parsed single- or multi-PDF report."""
    if t0 is None:
        t0 = time.monotonic()
    logger.info(
        "Parsed upload: label=%s customers=%d total_tasks=%d (%dms)",
        upload_label,
        len(report.customers),
        len(report.tasks),
        int((time.monotonic() - t0) * 1000),
    )

    excluded_from_shop = load_excluded_jobsites()
    allocation = compute(report, excluded_from_shop=excluded_from_shop)
    logger.info(
        "Allocation: shop_pool_entries=%d rollups=%d excluded_from_shop=%d",
        len(allocation.shop_pool),
        len(allocation.rollups),
        len(excluded_from_shop),
    )
    shop_missing = SHOP_JOBSITE_ID not in report.customers or not allocation.shop_pool
    included = load_included_items()
    hardcoded_prices = None
    hardcoded_price_count = 0
    if use_hardcoded_prices:
        try:
            from src.pricing.hardcoded_price_list import load_price_lookup

            hardcoded_prices = load_price_lookup()
            hardcoded_price_count = len(hardcoded_prices)
        except Exception as e:
            logger.exception("Hardcoded price list could not be loaded")
            raise ProcessingError(f"Hardcoded price list could not be loaded: {e}")

    invoice_date = datetime.now().strftime("%Y-%m-%d")
    invoices = build_all_invoices(
        allocation.rollups.values(),
        included=included,
        invoice_date=invoice_date,
        hardcoded_prices=hardcoded_prices,
    )

    zero_price_items: list[dict] = []
    for invoice in invoices:
        for src in invoice.sources:
            rollup = allocation.rollups.get(src.jobsite_id)
            if rollup is None:
                continue
            for item in extract_zero_price_items(
                rollup.services,
                included,
                hardcoded_prices=hardcoded_prices,
            ):
                item["jobsite_id"] = src.jobsite_id
                item["jobsite_name"] = rollup.customer_name
                item["customer_name"] = invoice.customer_name
                item["invoice_primary_jobsite_id"] = invoice.jobsite_id
                item["class_name"] = src.class_name
                item["index"] = len(zero_price_items)
                zero_price_items.append(item)

    mappings = load_mapping_from_lmn_api()
    lmn_mapping_count = len(mappings)
    logger.info("Loaded %d customer mappings from LMN", lmn_mapping_count)

    jobsite_ids = [inv.jobsite_id for inv in invoices]
    unmapped_ids = find_unmapped_jobsites(jobsite_ids, mappings)
    if unmapped_ids:
        logger.info(
            "Unmapped jobsites: %d of %d",
            len(unmapped_ids),
            len(jobsite_ids),
        )

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

    item_refs, fallback_lookup_names, fallback_error = _resolve_line_items(all_invoices)

    logger.info(
        "Upload ready: invoices=%d mapped=%d fallback_lookups=%d "
        "zero_price=%d duplicates=%d total=$%.2f (%dms end-to-end)",
        len(invoices),
        mapped_count,
        len(fallback_lookup_names),
        len(zero_price_items),
        len(duplicates),
        total_amount,
        int((time.monotonic() - t0) * 1000),
    )

    return {
        "invoices": all_invoices,
        "unmapped_jobsites": unmapped_jobsites,
        "duplicates": duplicates,
        "zero_price_items": zero_price_items,
        "lmn_mapping_count": lmn_mapping_count,
        "total_amount": total_amount,
        "item_refs": item_refs,
        "fallback_lookup_names": fallback_lookup_names,
        "fallback_error": fallback_error,
        "shop_missing": shop_missing,
        "hardcoded_prices_applied": bool(use_hardcoded_prices),
        "hardcoded_price_count": hardcoded_price_count,
        "summary": {
            "total_jobsites": len(invoices),
            "mapped_jobsites": mapped_count,
            "unmapped_jobsites": len(unmapped_jobsites),
            "duplicates_found": len(duplicates),
            "total_line_items": sum(len(inv["line_items"]) for inv in all_invoices),
            "fallback_items": len(fallback_lookup_names),
            "hardcoded_prices_applied": bool(use_hardcoded_prices),
        },
    }


def _resolve_line_items(
    all_invoices: List[Dict[str, Any]],
) -> tuple[Dict[str, Dict[str, str]], List[str], str | None]:
    """Resolve every line's QBO ItemRef and decorate line dicts in place.

    Returns `(item_refs, fallback_lookup_names, fallback_error)`. The
    lookup hits the QBO API once for the full item catalog; failures
    (no QBO creds, no Other item, DB down) degrade gracefully — the
    submit path re-checks `fallback_error` before posting.
    """
    item_cache: Dict[str, Dict[str, str]] = {}
    db_overrides: Dict[str, Dict[str, str]] = {}
    fallback_ref: Dict[str, str] | None = None
    fallback_error: str | None = None

    try:
        from src.qbo.context import get_qbo_credentials
        from src.qbo.items import (
            ItemMappingError,
            fetch_all_items,
            get_fallback_item_ref,
        )

        access_token, realm_id = get_qbo_credentials()
        item_cache = fetch_all_items(access_token, realm_id)
        logger.info("Fetched QBO item catalog: %d items", len(item_cache))
        try:
            fallback_ref = get_fallback_item_ref(item_cache)
        except ItemMappingError as e:
            fallback_error = str(e)
            logger.warning("QBO fallback item missing: %s", e)
    except Exception as e:
        fallback_error = fallback_error or (
            "QBO item catalog could not be loaded "
            f"(per-line items will use the fallback): {e}"
        )
        logger.exception("Failed to load QBO item catalog")

    try:
        from src.db.item_overrides import get_item_overrides

        db_overrides = get_item_overrides()
    except Exception:
        logger.exception("Failed to load item overrides from DB")
        db_overrides = {}

    if fallback_ref is None:
        for inv in all_invoices:
            for line in inv.get("line_items", []):
                line["qbo_item_name"] = None
                line["uses_fallback"] = False
        return {}, [], fallback_error

    normalized_cache = build_normalized_cache(item_cache)
    item_refs, fallback_names = build_item_refs(
        all_invoices, item_cache, normalized_cache, db_overrides, fallback_ref
    )

    for inv in all_invoices:
        for line in inv.get("line_items", []):
            lookup = (line.get("item_lookup_name") or "").strip()
            ref = item_refs.get(lookup)
            line["qbo_item_name"] = ref["name"] if ref else None
            line["uses_fallback"] = lookup in fallback_names

    return item_refs, sorted(fallback_names), fallback_error


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
                "item_lookup_name": str(item.item_lookup_name or ""),
                "class_name": str(item.class_name or MAINTENANCE_CLASS_NAME),
                "qbo_item_name": None,
                "uses_fallback": False,
            }
            for item in invoice.line_items
        ],
        "subtotal": float(invoice.subtotal),
        "direct_payment_fee": float(invoice.direct_payment_fee),
        "total": float(invoice.total),
        "has_irrigation": bool(invoice.has_irrigation),
        "sources": [
            {
                "jobsite_id": str(s.jobsite_id),
                "jobsite_name": str(s.jobsite_name),
                "class_name": str(s.class_name),
                "work_dates": [str(d) for d in s.work_dates],
                "foremen": [str(f) for f in s.foremen],
                "date_foreman_pairs": [str(p) for p in s.date_foreman_pairs],
                "task_notes": [
                    {
                        "date": str(n.get("date", "")),
                        "foreman": str(n.get("foreman", "")),
                        "notes": str(n.get("notes", "")),
                    }
                    for n in s.task_notes
                ],
            }
            for s in invoice.sources
        ],
        # Flattened task_notes across all sources — the results template
        # renders a single crew-notes list so keep the top-level contract.
        "task_notes": [
            {
                "date": str(n.get("date", "")),
                "foreman": str(n.get("foreman", "")),
                "notes": str(n.get("notes", "")),
            }
            for n in invoice.task_notes
        ],
    }


def create_qbo_invoices(
    invoices: List[Dict[str, Any]],
    item_refs: Dict[str, Dict[str, str]],
    progress_callback: Optional[Callable[[int, int, Dict[str, Any]], None]] = None,
) -> List[Dict[str, Any]]:
    """Create draft invoices in QBO from session invoice dicts.

    `item_refs` maps each line's `item_lookup_name` to a QBO ItemRef and is
    built up-front in `process_uploaded_pdf` (with any user overrides folded
    in via `/item-mapping/save`). Every line description must have an entry
    or QBO will reject the invoice; the fallback `"Other"` ItemRef covers
    unmatched names.

    If `progress_callback` is provided, it's invoked after each invoice with
    `(completed, total, last_result)`. Callback errors are swallowed so they
    can never abort invoice creation.
    """
    from src.qbo.classes import get_required_class_refs
    from src.qbo.context import get_qbo_credentials
    from src.qbo.invoices import create_draft_invoice

    access_token, realm_id = get_qbo_credentials()
    class_refs_by_name = get_required_class_refs(access_token, realm_id)

    logger.info("Creating %d draft invoice(s) in QBO", len(invoices))
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
                    item_lookup_name=item.get("item_lookup_name", ""),
                    class_name=item.get("class_name") or MAINTENANCE_CLASS_NAME,
                )
                for item in inv_dict["line_items"]
            ],
            subtotal=inv_dict["subtotal"],
            direct_payment_fee=inv_dict["direct_payment_fee"],
            total=inv_dict["total"],
            sources=[
                InvoiceSource(
                    jobsite_id=str(s["jobsite_id"]),
                    jobsite_name=str(s.get("jobsite_name", "")),
                    class_name=str(s.get("class_name") or MAINTENANCE_CLASS_NAME),
                    work_dates=list(s.get("work_dates", [])),
                    foremen=list(s.get("foremen", [])),
                    date_foreman_pairs=list(s.get("date_foreman_pairs", [])),
                    task_notes=[dict(n) for n in s.get("task_notes", [])],
                )
                for s in inv_dict.get("sources", [])
            ],
        )

        # Idempotency: if any source's (date|foreman) pairs already exist in
        # invoice_history, skip. Protects against worker-death + retry creating
        # duplicates of work that's already on a real QBO invoice from a prior
        # attempt. Misses orphan QBO invoices that never made it to the table —
        # those still need the cleanup admin route.
        existing_match = None
        for src in invoice.sources:
            if not src.date_foreman_pairs:
                continue
            for m in find_already_invoiced(src.jobsite_id, src.date_foreman_pairs):
                existing_match = m
                break
            if existing_match:
                break

        if existing_match:
            logger.info(
                "Skipped jobsite=%s — already invoiced in QBO (matching id=%s number=%s)",
                invoice.jobsite_id,
                existing_match.get("qbo_invoice_id"),
                existing_match.get("qbo_invoice_number"),
            )
            results.append(
                {
                    "success": True,
                    "skipped": True,
                    "jobsite_id": invoice.jobsite_id,
                    "customer_name": inv_dict.get("qbo_display_name")
                    or invoice.customer_name,
                    "invoice_id": existing_match.get("qbo_invoice_id"),
                    "invoice_number": existing_match.get("qbo_invoice_number"),
                    "total": 0.0,
                    "error": None,
                    "reason": "already invoiced",
                }
            )
            if progress_callback is not None:
                try:
                    progress_callback(len(results), len(invoices), results[-1])
                except Exception:
                    logger.exception("progress_callback raised; continuing")
            continue

        result = create_draft_invoice(
            invoice,
            qbo_customer_id=inv_dict["qbo_customer_id"],
            item_refs=item_refs,
            class_refs_by_name=class_refs_by_name,
        )

        if result.success:
            logger.info(
                "Created QBO invoice: jobsite=%s number=%s id=%s total=$%.2f",
                result.jobsite_id,
                result.invoice_number,
                result.invoice_id,
                result.total or 0.0,
            )
        else:
            logger.error(
                "QBO invoice creation failed: jobsite=%s error=%s",
                result.jobsite_id,
                result.error,
            )

        results.append(
            {
                "success": result.success,
                "jobsite_id": result.jobsite_id,
                "customer_name": inv_dict.get("qbo_display_name")
                or result.customer_name,
                "invoice_id": result.invoice_id,
                "invoice_number": result.invoice_number,
                "total": result.total,
                "error": result.error,
            }
        )

        if progress_callback is not None:
            try:
                progress_callback(len(results), len(invoices), results[-1])
            except Exception:
                logger.exception("progress_callback raised; continuing")

    return results
