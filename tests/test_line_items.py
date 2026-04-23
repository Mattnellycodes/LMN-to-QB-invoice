"""Tests for invoice line-item building: dedupe, included filter, zero-price notes."""

from __future__ import annotations

import pytest

from src.calculations.allocation import JobsiteRollup
from src.invoice.line_items import (
    build_invoice,
    calculate_direct_payment_fee,
    extract_service_line_items,
    extract_zero_price_items,
    format_labor_description,
    load_included_items,
    strip_unit_marker,
)


INCLUDED = frozenset(["GRUB-SPRING(VT)", "General Garden Maintenance(VT)", "Small Project"])


def _svc(description: str, qty: float = 1.0, total: float = 0.0, rate: float = 0.0,
         date: str = "Mon-Apr-13-2026", foreman: str = "Jenna Andrews", notes: str = "") -> dict:
    return {
        "description": description,
        "act_qty": str(qty),
        "inv_qty": str(qty),
        "rate": f"${rate:.2f}",
        "total_price": f"${total:.2f}",
        "source_context": {"date": date, "foreman": foreman, "notes": notes},
    }


def test_included_zero_price_items_dropped():
    services = [
        _svc("GRUB-SPRING(VT)", qty=1, total=0, rate=0),
        _svc("Deer Spray", qty=1, total=10, rate=10),
    ]
    billable = extract_service_line_items(services, INCLUDED)
    assert len(billable) == 1
    assert billable[0].description == "Deer Spray"

    zero = extract_zero_price_items(services, INCLUDED)
    assert zero == [], "Included zero-price items must not surface in modal"


def test_unknown_zero_price_item_goes_to_modal_with_notes():
    services = [
        _svc("Mystery Task", qty=2, total=0, rate=0, notes="Crew mentioned new service"),
    ]
    zero = extract_zero_price_items(services, INCLUDED)
    assert len(zero) == 1
    assert zero[0]["description"] == "Mystery Task"
    assert zero[0]["source_context"]["notes"] == "Crew mentioned new service"
    assert zero[0]["rate"] == 0.0
    assert zero[0]["quantity"] == 2.0


def test_services_dedupe_by_description():
    services = [
        _svc("Delivery, Bozeman", qty=1, total=85, rate=85),
        _svc("Delivery, Bozeman", qty=1, total=85, rate=85),
        _svc("Dump fee", qty=0.25, total=13.75, rate=55),
    ]
    items = extract_service_line_items(services, INCLUDED)
    descs = [i.description for i in items]
    assert descs.count("Delivery, Bozeman") == 1
    delivery = next(i for i in items if i.description == "Delivery, Bozeman")
    assert delivery.quantity == pytest.approx(2.0)
    assert delivery.amount == pytest.approx(170.0)


def test_similarly_named_billable_item_is_not_skipped():
    """'Small Project' with $0 is bundled; with a real price it must bill."""
    services = [
        _svc("Small Project", qty=1, total=0, rate=0),         # bundled — drop
        _svc("Small Project Extra", qty=1, total=50, rate=50),  # different name, bills
    ]
    items = extract_service_line_items(services, INCLUDED)
    assert [i.description for i in items] == ["Small Project Extra"]
    assert extract_zero_price_items(services, INCLUDED) == []


def test_bundled_name_with_real_price_still_bills():
    """Exact-match + zero-price is the gate. A 'Small Project' with $ bills normally."""
    services = [_svc("Small Project", qty=1, total=120, rate=120)]
    items = extract_service_line_items(services, INCLUDED)
    assert len(items) == 1
    assert items[0].amount == 120.0


def test_zero_price_with_zero_quantity_is_ignored():
    services = [_svc("Ghost Item", qty=0, total=0, rate=0)]
    assert extract_zero_price_items(services, INCLUDED) == []


def test_direct_payment_fee_tiers():
    assert calculate_direct_payment_fee(500) == pytest.approx(50.0)
    assert calculate_direct_payment_fee(1000) == 15.0
    assert calculate_direct_payment_fee(1500) == 15.0
    assert calculate_direct_payment_fee(2001) == 20.0


def test_format_labor_description_single_and_range():
    assert format_labor_description(["Mon-Apr-13-2026"]) == "Skilled Garden Hourly Labor 4/13"
    assert (
        format_labor_description(["Mon-Apr-13-2026", "Wed-Apr-15-2026"])
        == "Skilled Garden Hourly Labor 4/13-4/15"
    )


def test_build_invoice_aggregates_labor_and_items():
    rollup = JobsiteRollup(
        jobsite_id="ABC",
        customer_name="Customer A",
        hourly_rate=75.0,
    )
    rollup.work_by_date_foreman[("Mon-Apr-13-2026", "Jenna")] = 10.0
    rollup.work_by_date_foreman[("Tue-Apr-14-2026", "Jenna")] = 4.0
    rollup.allocated_drive_hours = 2.0
    rollup.services = [
        _svc("Dump fee", qty=0.25, total=13.75, rate=55),
        _svc("GRUB-SPRING(VT)", qty=1, total=0, rate=0),  # dropped
    ]

    inv = build_invoice(rollup, INCLUDED, invoice_date="2026-04-19")

    # Labor line: 14 work + 2 drive = 16h * $75 = $1200
    labor = inv.line_items[0]
    assert labor.quantity == pytest.approx(16.0)
    assert labor.rate == 75.0
    assert labor.amount == 1200.0

    descs = [i.description for i in inv.line_items]
    assert "Dump fee" in descs
    assert "GRUB-SPRING(VT)" not in descs

    # date_foreman_pairs built from (date, foreman) keys
    assert inv.date_foreman_pairs == sorted([
        "Mon-Apr-13-2026|Jenna",
        "Tue-Apr-14-2026|Jenna",
    ])


def test_build_invoice_copies_task_notes_from_rollup():
    rollup = JobsiteRollup(
        jobsite_id="ABC",
        customer_name="Customer A",
        hourly_rate=75.0,
    )
    rollup.work_by_date_foreman[("Mon-Apr-13-2026", "Jenna")] = 4.0
    rollup.task_notes = [
        {"date": "Mon-Apr-13-2026", "foreman": "Jenna", "notes": "Prune back shrub"},
        {"date": "Tue-Apr-14-2026", "foreman": "Cassie", "notes": "Finished pruning"},
    ]

    inv = build_invoice(rollup, INCLUDED, invoice_date="2026-04-19")

    assert inv.task_notes == rollup.task_notes
    # Guard against aliasing — must be a copy so later mutation doesn't bleed.
    assert inv.task_notes is not rollup.task_notes
    assert inv.task_notes[0] is not rollup.task_notes[0]


def test_load_included_items_reads_config_file():
    """Sanity check that the real config file loads with the expected names."""
    items = load_included_items()
    assert "GRUB-SPRING(VT)" in items
    assert "MOW" in items
    assert "Small Project" in items
    assert len(items) == 10


def test_service_lines_strip_unit_marker_from_item_lookup_name():
    services = [_svc("Mulch, Soil Pep, bulk [Yd]", qty=6, total=378.48, rate=63.08)]
    items = extract_service_line_items(services, INCLUDED)
    # Customer-facing description keeps the unit marker.
    assert items[0].description == "Mulch, Soil Pep, bulk [Yd]"
    # QBO lookup key strips it so names like "Mulch, Soil Pep, bulk" match.
    assert items[0].item_lookup_name == "Mulch, Soil Pep, bulk"


def test_strip_unit_marker_handles_various_shapes():
    assert strip_unit_marker("Deer Spray, Bozeman, ea [ea]") == "Deer Spray, Bozeman, ea"
    assert strip_unit_marker("Hedge Shearing [Day]") == "Hedge Shearing"
    assert strip_unit_marker("Weed Mat Pins") == "Weed Mat Pins"
    assert strip_unit_marker("") == ""
    assert strip_unit_marker("Name [lb]  ") == "Name"
    # Brackets mid-string are NOT stripped — only a single trailing marker.
    assert strip_unit_marker("Foo [x] bar") == "Foo [x] bar"


def test_labor_line_uses_rate_name_as_lookup_description_preserved():
    rollup = JobsiteRollup(
        jobsite_id="ABC",
        customer_name="Customer A",
        hourly_rate=75.0,
        hourly_rate_name="Maintenance Skilled Hourly Labor - TOWN",
    )
    rollup.work_by_date_foreman[("Mon-Apr-13-2026", "Jenna")] = 10.0

    inv = build_invoice(rollup, INCLUDED, invoice_date="2026-04-19")
    labor = inv.line_items[0]

    # Customer-facing description stays synthesized.
    assert labor.description == "Skilled Garden Hourly Labor 4/13"
    # QBO lookup key is the raw LMN rate name.
    assert labor.item_lookup_name == "Maintenance Skilled Hourly Labor - TOWN"


def test_labor_line_amount_matches_rounded_qty_times_rate():
    """Regression: QBO rejects with 'Amount is not equal to UnitPrice * Qty'
    when Amount is computed from raw hours but Qty is rounded. Both must
    derive from the same rounded value.
    """
    rollup = JobsiteRollup(
        jobsite_id="5843897W",
        customer_name="Brannin McBee and Meghan Bennett",
        hourly_rate=75.0,
        hourly_rate_name="Maintenance Skilled Hourly Labor - TOWN",
    )
    # 12.9450666... hours is the value that exposed the rounding mismatch:
    # old code sent Qty=12.95 but Amount=round(12.9450666*75, 2)=970.88,
    # while QBO computed 12.95*75=971.25.
    rollup.work_by_date_foreman[("Mon-Apr-13-2026", "Jenna")] = 12.9450666

    inv = build_invoice(rollup, INCLUDED, invoice_date="2026-04-19")
    labor = inv.line_items[0]

    assert labor.quantity == 12.95
    assert labor.amount == round(labor.quantity * labor.rate, 2)
    assert labor.amount == 971.25


def test_fee_line_has_stable_item_lookup_name():
    rollup = JobsiteRollup(
        jobsite_id="ABC",
        customer_name="Customer A",
        hourly_rate=75.0,
        hourly_rate_name="Labor",
    )
    rollup.work_by_date_foreman[("Mon-Apr-13-2026", "Jenna")] = 1.0

    inv = build_invoice(rollup, INCLUDED, invoice_date="2026-04-19")
    fee = next(li for li in inv.line_items
               if li.description == "Direct Payment Fee (Subtract if paying by USPS check)")
    assert fee.item_lookup_name == "Direct Payment Fee"
