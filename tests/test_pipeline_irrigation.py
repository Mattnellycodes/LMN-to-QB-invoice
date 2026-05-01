"""End-to-end pipeline coverage for irrigation classification + bundle handling.

Skips parse_pdf to avoid committing client PDFs, but exercises:
    Task list → compute() → build_all_invoices()

so a regression that re-introduces the cost-code filter, mis-classifies
irrigation by suffix, or breaks the Cannery HOA bundle is caught.
"""

from __future__ import annotations

from src.calculations.allocation import compute
from src.invoice.line_items import (
    FEE_DESCRIPTION,
    IRRIGATION_CLASS_NAME,
    MAINTENANCE_CLASS_NAME,
    build_all_invoices,
)
from src.parsing.pdf_parser import (
    ParsedReport,
    RateRow,
    Task,
)


CANNERY_IRR_IDS = [
    "5923663W",
    "5923708W",
    "5923738W",
    "5923744W",
    "5923753W",
    "5923755W",
    "5923785W",
]
CANNERY_MAINT_ID = "5810615W"


def _maint_task(jobsite_id: str, customer_name: str, hours: float = 4.0) -> Task:
    return Task(
        date="Mon-Apr-27-2026",
        customer_name=customer_name,
        jobsite_id=jobsite_id,
        task_name="Maintenance",
        cost_code_num="200",
        foreman="Cassie",
        task_man_hrs=hours,
        rates=[RateRow(
            description="Maintenance Skilled Hourly Labor - TOWN",
            qty=str(hours),
            rate="$75.00",
            total_price=f"${hours * 75:.2f}",
        )],
    )


def _irr_task(jobsite_id: str, customer_name: str, hours: float = 5.17) -> Task:
    return Task(
        date="Tue-Apr-28-2026",
        customer_name=customer_name,
        jobsite_id=jobsite_id,
        task_name="Irrigation Install",
        cost_code_num="100",
        foreman="Josh",
        task_man_hrs=hours,
        rates=[RateRow(
            description="Irrigation Technician Hourly Labor",
            qty=str(hours),
            rate="$95.00",
            total_price=f"${hours * 95:.2f}",
        )],
    )


def _report(tasks: list[Task]) -> ParsedReport:
    return ParsedReport(customers={}, tasks=tasks)


def _by_customer_name(invoices, name: str):
    matches = [inv for inv in invoices if inv.customer_name == name]
    assert len(matches) == 1, f"expected one invoice for {name!r}, got {len(matches)}"
    return matches[0]


# ---------- cost-code filter is gone ----------

def test_cc_100_tasks_produce_an_irrigation_invoice():
    """41B-style standalone irrigation jobsite emits its own invoice."""
    tasks = [_irr_task("5951126W", "41B East Hitching Post Road-Irrigation")]
    result = compute(_report(tasks))
    invoices = build_all_invoices(result.rollups.values(), included=frozenset())

    assert len(invoices) == 1
    inv = invoices[0]
    # Suffix-stripped display name (broader regex catches "-Irrigation").
    assert inv.customer_name == "41B East Hitching Post Road"
    classes = [li.class_name for li in inv.line_items]
    # All non-fee lines tagged Irrigation; fee always Maintenance.
    assert IRRIGATION_CLASS_NAME in classes
    assert classes[-1] == MAINTENANCE_CLASS_NAME
    assert inv.line_items[-1].description == FEE_DESCRIPTION


# ---------- Cannery HOA bundle ----------

def _cannery_task_set() -> list[Task]:
    """One maint Cannery HOA task + 7 irrigation Cannery HOA zone tasks."""
    tasks = [_maint_task(CANNERY_MAINT_ID, "Cannery HOA", hours=6.0)]
    zones = [
        ("5923663W", "Cannery HOA - A118 - Irr."),
        ("5923708W", "Cannery HOA - Bldg K - Irr."),
        ("5923738W", "Cannery HOA - 7 Sushi - Irr."),
        ("5923744W", "Cannery HOA - Water Tower - Irr."),
        ("5923753W", "Cannery HOA - Blackbird - Irr."),
        ("5923755W", "Cannery HOA - Common Area - Irr."),
        ("5923785W", "Cannery HOA - Parking Lot - Irr."),
    ]
    tasks.extend(_irr_task(jid, name) for jid, name in zones)
    return tasks


def test_cannery_hoa_bundles_into_one_invoice():
    invoices = build_all_invoices(
        compute(_report(_cannery_task_set())).rollups.values(),
        included=frozenset(),
    )

    # Exactly one invoice for the bundle, named per the bundle config.
    inv = _by_customer_name(invoices, "Cannery District HOA")

    # Primary jobsite drives the QBO mapping lookup.
    assert inv.jobsite_id == "5923663W"

    # 8 sources — one per contributing LMN jobsite — so duplicate detection
    # and zero-price-item lookup keep working per-jobsite.
    source_ids = sorted(s.jobsite_id for s in inv.sources)
    expected = sorted([CANNERY_MAINT_ID] + CANNERY_IRR_IDS)
    assert source_ids == expected


def test_cannery_bundle_line_order_maint_then_irr_then_fee():
    invoices = build_all_invoices(
        compute(_report(_cannery_task_set())).rollups.values(),
        included=frozenset(),
    )
    inv = _by_customer_name(invoices, "Cannery District HOA")

    classes = [li.class_name for li in inv.line_items]
    fee_idx = next(i for i, li in enumerate(inv.line_items) if li.description == FEE_DESCRIPTION)
    maint_classes = classes[:fee_idx]

    # All Maintenance lines come before any Irrigation line.
    last_maint_idx = max(
        (i for i, c in enumerate(maint_classes) if c == MAINTENANCE_CLASS_NAME),
        default=-1,
    )
    first_irr_idx = next(
        (i for i, c in enumerate(maint_classes) if c == IRRIGATION_CLASS_NAME),
        len(maint_classes),
    )
    assert last_maint_idx < first_irr_idx, (
        f"Maint lines must precede Irr lines, got classes={maint_classes}"
    )
    # Fee is the last line and tagged Maintenance.
    assert classes[fee_idx] == MAINTENANCE_CLASS_NAME


def test_irrigation_only_bundle_emits_invoice_without_maint_section():
    """Stress: a week with no maint Cannery HOA work still emits the bundle."""
    irr_only = [_irr_task(jid, name) for jid, name in [
        ("5923663W", "Cannery HOA - A118 - Irr."),
        ("5923708W", "Cannery HOA - Bldg K - Irr."),
    ]]
    invoices = build_all_invoices(
        compute(_report(irr_only)).rollups.values(),
        included=frozenset(),
    )
    inv = _by_customer_name(invoices, "Cannery District HOA")

    classes = [li.class_name for li in inv.line_items]
    fee_idx = next(i for i, li in enumerate(inv.line_items) if li.description == FEE_DESCRIPTION)
    # No non-fee Maintenance lines.
    assert MAINTENANCE_CLASS_NAME not in classes[:fee_idx]
    assert IRRIGATION_CLASS_NAME in classes[:fee_idx]


# ---------- pair_rollups double-pair fix ----------

def test_two_irr_rollups_with_colliding_stripped_name_dont_double_pair():
    """Latent bug: two Irr rollups whose names strip to the same key were both
    pairing with the same Maint rollup, double-billing maintenance."""
    tasks = [
        _maint_task("MAINT1", "Smith Residence", hours=4.0),
        _irr_task("IRR1", "Smith Residence - Irr.", hours=3.0),
        _irr_task("IRR2", "smith residence - IRR.", hours=2.0),
    ]
    invoices = build_all_invoices(
        compute(_report(tasks)).rollups.values(),
        included=frozenset(),
    )

    # Three invoices total: Smith Residence (paired with IRR1) +
    # standalone IRR2 + (no third because IRR1 was paired). MAINT1 must NOT
    # appear in two invoices — that was the double-billing bug.
    primary_jobsites = sorted(inv.jobsite_id for inv in invoices)
    assert primary_jobsites.count("MAINT1") == 1


# ---------- rollup-level is_irrigation classifier ----------

def test_first_task_cost_code_classifies_rollup():
    tasks = [
        _maint_task("M", "Maint Customer", hours=3.0),
        _irr_task("I", "Irr Customer - Irr.", hours=4.0),
    ]
    result = compute(_report(tasks))
    assert result.rollups["I"].is_irrigation is True
    assert result.rollups["M"].is_irrigation is False
