"""Build invoice line items from LMN Job History PDF rollups.

Input shape: `JobsiteRollup` from src.calculations.allocation — one per jobsite,
already aggregated across multiple days and augmented with allocated drive time.
Services on the rollup carry `source_context` (date, foreman, notes) for the
zero-price modal.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from src.calculations.allocation import JobsiteRollup
from src.parsing.pdf_parser import parse_money, parse_qty


INCLUDED_ITEMS_PATH = Path(__file__).resolve().parents[2] / "config" / "included_items.txt"

# LMN service names include a trailing unit-of-measure tag (e.g.
# "Deer Spray, Bozeman, ea [ea]", "Mulch, Soil Pep, bulk [Yd]"). QBO items
# are named without the tag, so strip it before using the name as a lookup
# key. Customer-facing `description` still carries the full LMN string.
_UNIT_MARKER_RE = re.compile(r"\s*\[[^\]]*\]\s*$")


def strip_unit_marker(name: str) -> str:
    """Remove a trailing `[unit]` tag from an LMN service name."""
    if not name:
        return ""
    return _UNIT_MARKER_RE.sub("", name).strip()


@dataclass
class LineItem:
    """A single invoice line item."""

    description: str
    quantity: float
    rate: float
    amount: float
    # Match key used to resolve the QBO Product/Service ItemRef.
    # For services/materials this equals `description`; for labor it's the
    # LMN rate name (distinct from the synthesized customer-facing description).
    item_lookup_name: str = ""


@dataclass
class InvoiceData:
    """Complete invoice data for a single jobsite."""

    jobsite_id: str
    jobsite_name: str
    customer_name: str
    invoice_date: str
    line_items: list[LineItem] = field(default_factory=list)
    subtotal: float = 0.0
    direct_payment_fee: float = 0.0
    total: float = 0.0
    work_dates: list[str] = field(default_factory=list)
    foremen: list[str] = field(default_factory=list)
    # "<date>|<foreman>" strings — the canonical duplicate-detection key.
    date_foreman_pairs: list[str] = field(default_factory=list)


def load_included_items(path: Path = INCLUDED_ITEMS_PATH) -> frozenset[str]:
    """Load the allow-list of bundled service names. Exact-match, case-sensitive."""
    if not path.exists():
        return frozenset()
    items: set[str] = set()
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        items.add(line)
    return frozenset(items)


def calculate_direct_payment_fee(subtotal: float) -> float:
    """Direct payment fee tiered by subtotal.

    Under $1,000 -> 10% of subtotal
    $1,000 - $2,000 -> $15 flat
    Over $2,000 -> $20 flat
    """
    if subtotal < 1000:
        return round(subtotal * 0.10, 2)
    if subtotal <= 2000:
        return 15.00
    return 20.00


def format_labor_description(dates: list[str]) -> str:
    """Build a human-readable labor line description from LMN date strings.

    LMN date format: "Mon-Apr-13-2026". Output: "Skilled Garden Hourly Labor 4/13"
    or "... 4/13-4/15" for ranges.
    """
    if not dates:
        return "Skilled Garden Hourly Labor"
    shorts = [_short_date(d) for d in dates]
    shorts = [s for s in shorts if s]
    if not shorts:
        return "Skilled Garden Hourly Labor"
    if len(shorts) == 1:
        return f"Skilled Garden Hourly Labor {shorts[0]}"
    return f"Skilled Garden Hourly Labor {shorts[0]}-{shorts[-1]}"


def _short_date(lmn_date: str) -> str:
    """Convert 'Mon-Apr-13-2026' to '4/13'. Returns '' on failure."""
    try:
        # Strip day-of-week prefix.
        parts = lmn_date.split("-", 1)
        if len(parts) != 2:
            return ""
        dt = datetime.strptime(parts[1], "%b-%d-%Y")
        return f"{dt.month}/{dt.day}"
    except (ValueError, TypeError):
        return ""


def _classify_service(description: str, total_price: float, included: frozenset[str]) -> str:
    """Return one of: 'billable', 'included', 'zero_price'."""
    if total_price > 0:
        return "billable"
    if description in included:
        return "included"
    return "zero_price"


def extract_service_line_items(
    services: Iterable[dict], included: frozenset[str]
) -> list[LineItem]:
    """Dedupe billable services by description; sum quantities and totals."""
    aggregated: "OrderedDict[str, LineItem]" = OrderedDict()
    for svc in services:
        desc = svc.get("description", "").strip()
        if not desc:
            continue
        total = parse_money(svc.get("total_price", ""))
        if _classify_service(desc, total, included) != "billable":
            continue

        qty = parse_qty(svc.get("inv_qty", "")) or parse_qty(svc.get("act_qty", ""))
        rate = parse_money(svc.get("rate", ""))

        existing = aggregated.get(desc)
        if existing is None:
            aggregated[desc] = LineItem(
                description=desc,
                quantity=qty,
                rate=rate,
                amount=round(total, 2),
                item_lookup_name=strip_unit_marker(desc),
            )
        else:
            existing.quantity = round(existing.quantity + qty, 4)
            existing.amount = round(existing.amount + total, 2)
            # Keep the first non-zero rate seen; don't overwrite.
            if existing.rate == 0 and rate > 0:
                existing.rate = rate

    return list(aggregated.values())


def extract_zero_price_items(
    services: Iterable[dict], included: frozenset[str]
) -> list[dict]:
    """Return service rows with Total Price = $0 whose name is NOT on the allow-list.

    Items are NOT deduped — each crew entry is a distinct row the user may want
    to evaluate individually, and each carries its own source_context.
    """
    out: list[dict] = []
    for svc in services:
        desc = svc.get("description", "").strip()
        if not desc:
            continue
        total = parse_money(svc.get("total_price", ""))
        if _classify_service(desc, total, included) != "zero_price":
            continue

        qty = parse_qty(svc.get("inv_qty", "")) or parse_qty(svc.get("act_qty", ""))
        if qty <= 0:
            # No quantity means nothing to bill — skip silently.
            continue

        out.append(
            {
                "description": desc,
                "quantity": qty,
                "rate": 0.0,
                "source_context": dict(svc.get("source_context") or {}),
            }
        )
    return out


def build_invoice(
    rollup: JobsiteRollup,
    included: frozenset[str],
    invoice_date: Optional[str] = None,
) -> InvoiceData:
    """Build a full InvoiceData for one jobsite."""
    if invoice_date is None:
        invoice_date = datetime.now().strftime("%Y-%m-%d")

    pairs = sorted({f"{d}|{f}" for (d, f) in rollup.work_by_date_foreman})
    invoice = InvoiceData(
        jobsite_id=rollup.jobsite_id,
        jobsite_name=rollup.customer_name,
        customer_name=rollup.customer_name,
        invoice_date=invoice_date,
        work_dates=list(rollup.work_dates),
        foremen=list(rollup.foremen),
        date_foreman_pairs=pairs,
    )

    total_hours = rollup.total_billable_hours
    rate = rollup.hourly_rate
    if total_hours > 0 and rate > 0:
        # Derive Amount from the rounded Qty so QBO's Amount == UnitPrice*Qty
        # validation passes. Computing Amount from the raw hours while sending
        # a rounded Qty causes rejection when fractional hours are involved.
        qty = round(total_hours, 2)
        invoice.line_items.append(
            LineItem(
                description=format_labor_description(rollup.work_dates),
                quantity=qty,
                rate=rate,
                amount=round(qty * rate, 2),
                item_lookup_name=rollup.hourly_rate_name,
            )
        )

    invoice.line_items.extend(extract_service_line_items(rollup.services, included))

    invoice.subtotal = round(sum(i.amount for i in invoice.line_items), 2)
    invoice.direct_payment_fee = calculate_direct_payment_fee(invoice.subtotal)
    invoice.total = round(invoice.subtotal + invoice.direct_payment_fee, 2)

    if invoice.direct_payment_fee > 0:
        invoice.line_items.append(
            LineItem(
                description="Please subtract if paying by USPS check",
                quantity=1,
                rate=invoice.direct_payment_fee,
                amount=invoice.direct_payment_fee,
                item_lookup_name="Direct Payment Fee",
            )
        )

    return invoice


def build_all_invoices(
    rollups: Iterable[JobsiteRollup],
    included: Optional[frozenset[str]] = None,
    invoice_date: Optional[str] = None,
) -> list[InvoiceData]:
    """Build invoices for every jobsite rollup with a non-empty subtotal."""
    if included is None:
        included = load_included_items()
    invoices: list[InvoiceData] = []
    for rollup in rollups:
        invoice = build_invoice(rollup, included, invoice_date)
        if invoice.subtotal > 0:
            invoices.append(invoice)
    return invoices
