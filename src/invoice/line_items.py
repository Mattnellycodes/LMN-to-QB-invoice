"""Build invoice line items from LMN Job History PDF rollups.

Input shape: `JobsiteRollup` from src.calculations.allocation — one per jobsite,
already aggregated across multiple days and augmented with allocated drive time.
Services on the rollup carry `source_context` (date, foreman, notes) for the
zero-price modal.

Irrigation support: when a jobsite name ends in ` - Irr.`, its lines merge
onto the matching maintenance jobsite's invoice (see src/invoice/irrigation.py)
and are tagged with the QBO "Irrigation" class. The Direct Payment Fee always
lands last and is always tagged "Maintenance".
"""

from __future__ import annotations

import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from src.calculations.allocation import JobsiteRollup
from src.parsing.pdf_parser import parse_money, parse_qty

logger = logging.getLogger(__name__)


INCLUDED_ITEMS_PATH = Path(__file__).resolve().parents[2] / "config" / "included_items.txt"

# QBO Class names. Declared here (not in src/qbo/) so invoice-domain code can
# reference them without pulling in the QBO integration layer.
MAINTENANCE_CLASS_NAME = "Maintenance"
IRRIGATION_CLASS_NAME = "Irrigation"

FEE_DESCRIPTION = "Direct Payment Fee (Subtract if paying by USPS check)"
FEE_ITEM_LOOKUP_NAME = "Direct Payment Fee"

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
    # QBO Class applied to this line. Defaults to Maintenance; lines sourced
    # from an " - Irr." jobsite are tagged IRRIGATION_CLASS_NAME.
    class_name: str = MAINTENANCE_CLASS_NAME


@dataclass
class InvoiceSource:
    """One rollup's contribution to an invoice.

    Non-merged invoices have a single source; merged (maint + Irr) invoices
    have two. Each source carries its own history data so duplicate detection
    can flag either side in future uploads.
    """

    jobsite_id: str
    jobsite_name: str
    class_name: str
    work_dates: list[str] = field(default_factory=list)
    foremen: list[str] = field(default_factory=list)
    date_foreman_pairs: list[str] = field(default_factory=list)
    task_notes: list[dict] = field(default_factory=list)


@dataclass
class InvoiceData:
    """Complete invoice data for a single QBO invoice.

    `jobsite_id` is the **primary** ID — maintenance's if paired, else irr's
    (for standalone Irr invoices), else the only source's. It is the lookup
    key used for QBO customer mapping.
    """

    jobsite_id: str
    jobsite_name: str
    customer_name: str
    invoice_date: str
    line_items: list[LineItem] = field(default_factory=list)
    subtotal: float = 0.0
    direct_payment_fee: float = 0.0
    total: float = 0.0
    sources: list[InvoiceSource] = field(default_factory=list)

    @property
    def has_irrigation(self) -> bool:
        return any(s.class_name == IRRIGATION_CLASS_NAME for s in self.sources)

    @property
    def work_dates(self) -> list[str]:
        return sorted({d for s in self.sources for d in s.work_dates})

    @property
    def foremen(self) -> list[str]:
        return sorted({f for s in self.sources for f in s.foremen})

    @property
    def date_foreman_pairs(self) -> list[str]:
        return sorted({p for s in self.sources for p in s.date_foreman_pairs})

    @property
    def task_notes(self) -> list[dict]:
        """Flattened task_notes across all sources (maintenance first)."""
        notes: list[dict] = []
        for s in self.sources:
            notes.extend(s.task_notes)
        return notes


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


# LMN service descriptions that should never appear on an invoice, regardless
# of LMN's reported price. Reuses the silent-drop ("included") classification
# below so neither the invoice nor the zero-price modal surfaces them.
_ALWAYS_SKIP_DESCRIPTIONS: frozenset[str] = frozenset({
    "IRR-STARTUP(VT)",
})


def _classify_service(description: str, total_price: float, included: frozenset[str]) -> str:
    """Return one of: 'billable', 'included', 'zero_price'."""
    if description in _ALWAYS_SKIP_DESCRIPTIONS:
        return "included"
    if total_price > 0:
        return "billable"
    if description in included:
        return "included"
    return "zero_price"


def extract_service_line_items(
    services: Iterable[dict],
    included: frozenset[str],
    class_name: str = MAINTENANCE_CLASS_NAME,
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
                class_name=class_name,
            )
        else:
            existing.quantity = round(existing.quantity + qty, 4)
            existing.amount = round(existing.amount + total, 2)
            # Re-derive rate from the aggregated amount/qty so the LineItem
            # stays internally consistent. Mixed per-entry rates would
            # otherwise leave rate * quantity != amount and trip QBO's check.
            if existing.quantity > 0:
                existing.rate = round(existing.amount / existing.quantity, 6)
            elif existing.rate == 0 and rate > 0:
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


def _make_invoice_source(rollup: JobsiteRollup, class_name: str) -> InvoiceSource:
    pairs = sorted({f"{d}|{f}" for (d, f) in rollup.work_by_date_foreman})
    return InvoiceSource(
        jobsite_id=rollup.jobsite_id,
        jobsite_name=rollup.customer_name,
        class_name=class_name,
        work_dates=list(rollup.work_dates),
        foremen=list(rollup.foremen),
        date_foreman_pairs=pairs,
        task_notes=[dict(n) for n in rollup.task_notes],
    )


def _build_rollup_lines(
    rollup: JobsiteRollup,
    included: frozenset[str],
    class_name: str,
) -> list[LineItem]:
    """Labor + materials for one rollup, tagged with the given QBO class.

    No subtotal, no DP fee — pure line-item construction. The caller stitches
    together one or two of these results and then calls `_finalize_invoice`.
    """
    lines: list[LineItem] = []

    total_hours = rollup.total_billable_hours
    rate = rollup.hourly_rate
    if total_hours > 0 and rate > 0:
        # Derive Amount from the rounded Qty so QBO's Amount == UnitPrice*Qty
        # validation passes. Computing Amount from the raw hours while sending
        # a rounded Qty causes rejection when fractional hours are involved.
        qty = round(total_hours, 2)
        lines.append(
            LineItem(
                description=format_labor_description(rollup.work_dates),
                quantity=qty,
                rate=rate,
                amount=round(qty * rate, 2),
                item_lookup_name=rollup.hourly_rate_name,
                class_name=class_name,
            )
        )

    lines.extend(extract_service_line_items(rollup.services, included, class_name))
    return lines


def _finalize_invoice(invoice: InvoiceData) -> None:
    """Compute subtotal, DP fee, total; append DP-fee line (Maintenance class).

    Runs exactly once per invoice at the very end of the build flow. The fee
    line is always tagged Maintenance regardless of the invoice's mix.
    """
    invoice.subtotal = round(sum(i.amount for i in invoice.line_items), 2)
    fee = calculate_direct_payment_fee(invoice.subtotal)
    invoice.direct_payment_fee = fee
    invoice.total = round(invoice.subtotal + fee, 2)

    if fee > 0:
        invoice.line_items.append(
            LineItem(
                description=FEE_DESCRIPTION,
                quantity=1,
                rate=fee,
                amount=fee,
                item_lookup_name=FEE_ITEM_LOOKUP_NAME,
                class_name=MAINTENANCE_CLASS_NAME,
            )
        )


def build_invoice_for_group(
    group,
    included: frozenset[str],
    invoice_date: Optional[str] = None,
) -> InvoiceData:
    """Build an InvoiceData for one RollupGroup (maintenance, irrigation, or both).

    Line order: maintenance labor+materials, then irrigation labor+materials,
    then the Direct Payment Fee (always Maintenance). Primary jobsite_id is
    the maintenance jobsite's if present, else the irrigation jobsite's — this
    is the ID used for QBO customer-mapping lookup downstream.
    """
    if invoice_date is None:
        invoice_date = datetime.now().strftime("%Y-%m-%d")

    maint = group.maintenance
    irr = group.irrigation
    if maint is None and irr is None:
        raise ValueError("RollupGroup must contain at least one rollup")

    # Strip the " - Irr." suffix for standalone Irr so the customer name is clean.
    # For paired invoices the maintenance name is already suffix-free.
    from src.invoice.irrigation import strip_irr_suffix

    if maint is not None:
        primary_id = maint.jobsite_id
        display_name = maint.customer_name
    else:
        primary_id = irr.jobsite_id
        display_name = strip_irr_suffix(irr.customer_name)

    invoice = InvoiceData(
        jobsite_id=primary_id,
        jobsite_name=display_name,
        customer_name=display_name,
        invoice_date=invoice_date,
    )

    def _emit_sources(rollup: JobsiteRollup, class_name: str) -> list:
        # Bundle-merged rollups expose one source per contributing jobsite so
        # downstream zero-price-item lookup, duplicate detection, and
        # invoice_history rows stay keyed per LMN jobsite.
        if rollup.member_rollups:
            return [_make_invoice_source(m, class_name) for m in rollup.member_rollups]
        return [_make_invoice_source(rollup, class_name)]

    if maint is not None:
        invoice.line_items.extend(
            _build_rollup_lines(maint, included, MAINTENANCE_CLASS_NAME)
        )
        invoice.sources.extend(_emit_sources(maint, MAINTENANCE_CLASS_NAME))
    if irr is not None:
        invoice.line_items.extend(
            _build_rollup_lines(irr, included, IRRIGATION_CLASS_NAME)
        )
        invoice.sources.extend(_emit_sources(irr, IRRIGATION_CLASS_NAME))

    _finalize_invoice(invoice)
    return invoice


def build_invoice(
    rollup: JobsiteRollup,
    included: frozenset[str],
    invoice_date: Optional[str] = None,
) -> InvoiceData:
    """Build an InvoiceData from a single rollup (backward-compat convenience).

    Treats the rollup as a standalone maintenance job. For irrigation-aware
    builds, use `build_invoice_for_group` with a `RollupGroup`.
    """
    from src.invoice.irrigation import RollupGroup, has_irr_suffix

    if rollup.is_irrigation or has_irr_suffix(rollup.customer_name):
        group = RollupGroup(maintenance=None, irrigation=rollup)
    else:
        group = RollupGroup(maintenance=rollup, irrigation=None)
    return build_invoice_for_group(group, included, invoice_date)


def build_all_invoices(
    rollups: Iterable[JobsiteRollup],
    included: Optional[frozenset[str]] = None,
    invoice_date: Optional[str] = None,
) -> list[InvoiceData]:
    """Build invoices for every rollup, merging Irr jobs onto their maintenance twin.

    Pairs `- Irr.` jobsites with their maintenance counterparts (same stripped
    name in the same upload), emits one InvoiceData per resulting group, and
    drops groups with zero subtotal.
    """
    from src.invoice.bundles import apply_bundles
    from src.invoice.irrigation import pair_rollups

    if included is None:
        included = load_included_items()
    logger.debug("Included-items allow-list size: %d", len(included))

    rollups_list = list(rollups)
    # Hardcoded bundles (e.g., Cannery HOA's 8 jobsites) collapse before
    # name-suffix pairing — their grouping is fixed by the bundle config.
    non_bundled, bundle_groups = apply_bundles(rollups_list)
    groups = bundle_groups + pair_rollups(non_bundled)

    invoices: list[InvoiceData] = []
    skipped = 0
    fees_applied = 0
    merged_count = 0
    standalone_irr_count = 0
    for group in groups:
        invoice = build_invoice_for_group(group, included, invoice_date)
        if invoice.subtotal <= 0:
            skipped += 1
            continue
        invoices.append(invoice)
        if invoice.direct_payment_fee > 0:
            fees_applied += 1
        if group.maintenance is not None and group.irrigation is not None:
            merged_count += 1
        elif group.irrigation is not None:
            standalone_irr_count += 1

    logger.info(
        "Built %d invoices (skipped=%d zero-subtotal, direct-pay fee applied=%d, "
        "merged maint+irr=%d, standalone irr=%d)",
        len(invoices),
        skipped,
        fees_applied,
        merged_count,
        standalone_irr_count,
    )
    return invoices
