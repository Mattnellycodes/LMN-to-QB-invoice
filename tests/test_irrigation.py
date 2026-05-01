"""Tests for irrigation jobsite pairing, merged invoice building, and class tagging."""

from __future__ import annotations

import pytest

from src.calculations.allocation import JobsiteRollup
from src.invoice.irrigation import (
    RollupGroup,
    has_irr_suffix,
    pair_rollups,
    strip_irr_suffix,
)
from src.invoice.line_items import (
    FEE_DESCRIPTION,
    IRRIGATION_CLASS_NAME,
    MAINTENANCE_CLASS_NAME,
    build_all_invoices,
    build_invoice_for_group,
)


INCLUDED = frozenset()


def _svc(description, qty=1.0, total=0.0, rate=0.0,
         date="Mon-Apr-13-2026", foreman="Jenna", notes=""):
    return {
        "description": description,
        "act_qty": str(qty), "inv_qty": str(qty),
        "rate": f"${rate:.2f}", "total_price": f"${total:.2f}",
        "source_context": {"date": date, "foreman": foreman, "notes": notes},
    }


def _rollup(jobsite_id, name, work_hours=8.0, rate=75.0, services=None,
            date="Mon-Apr-13-2026", foreman="Jenna", is_irrigation=None):
    # Default: classifier mirrors the production rule — anything with the
    # " - Irr." suffix is irrigation. Tests can override explicitly.
    if is_irrigation is None:
        from src.invoice.irrigation import has_irr_suffix
        is_irrigation = has_irr_suffix(name)
    r = JobsiteRollup(
        jobsite_id=jobsite_id,
        customer_name=name,
        hourly_rate=rate,
        hourly_rate_name="TOWN Hourly",
        is_irrigation=is_irrigation,
    )
    r.work_by_date_foreman[(date, foreman)] = work_hours
    r.services = list(services or [])
    return r


# ---------- Suffix helpers ----------

class TestSuffixHelpers:
    def test_has_suffix_basic(self):
        assert has_irr_suffix("Smith Residence - Irr.")
        assert has_irr_suffix("Jones - irr.")                # case-insensitive
        assert has_irr_suffix("Davis   -   Irr.   ")         # whitespace-tolerant

    def test_has_suffix_negative(self):
        assert not has_irr_suffix("Smith Residence")
        assert not has_irr_suffix("Irrigation Supply Co")    # "Irr" mid-name
        assert not has_irr_suffix("- Irr. Landscaping")       # suffix prefix-only
        assert not has_irr_suffix("")
        assert not has_irr_suffix(None)

    def test_strip_suffix_removes_trailing_only(self):
        assert strip_irr_suffix("Smith Residence - Irr.") == "Smith Residence"
        assert strip_irr_suffix("Davis   -   irr.   ") == "Davis"
        assert strip_irr_suffix("No Suffix Here") == "No Suffix Here"
        assert strip_irr_suffix("") == ""
        assert strip_irr_suffix(None) == ""


# ---------- Pair detection ----------

class TestPairRollups:
    def test_pair_detected_case_insensitive(self):
        maint = _rollup("1000001A", "Smith Residence")
        irr = _rollup("1000002A", "smith residence - IRR.")
        groups = pair_rollups([maint, irr])
        assert len(groups) == 1
        assert groups[0].maintenance is maint
        assert groups[0].irrigation is irr

    def test_standalone_irr_when_no_match(self):
        irr = _rollup("1000002A", "Lone Irr Customer - Irr.")
        groups = pair_rollups([irr])
        assert len(groups) == 1
        assert groups[0].maintenance is None
        assert groups[0].irrigation is irr

    def test_maint_without_irr_stays_standalone(self):
        maint = _rollup("1000001A", "Plain Maint")
        groups = pair_rollups([maint])
        assert len(groups) == 1
        assert groups[0].maintenance is maint
        assert groups[0].irrigation is None

    def test_ambiguous_maint_name_blocks_merge(self, caplog):
        # Two maint rollups with the same stripped name — the Irr must fall
        # through to standalone rather than attach to the wrong customer.
        m1 = _rollup("1000001A", "Johnson")
        m2 = _rollup("1000003A", "johnson")
        irr = _rollup("1000002A", "Johnson - Irr.")
        with caplog.at_level("WARNING"):
            groups = pair_rollups([m1, m2, irr])
        # Expect: two standalone maint groups + one standalone irr group.
        assert len(groups) == 3
        merged = [g for g in groups if g.maintenance and g.irrigation]
        standalone_irr = [g for g in groups if g.maintenance is None and g.irrigation]
        assert merged == []
        assert len(standalone_irr) == 1
        assert standalone_irr[0].irrigation is irr
        assert any("Ambiguous maintenance name" in r.message for r in caplog.records)


# ---------- Merged invoice build ----------

class TestBuildInvoiceForGroup:
    def _paired_group(self):
        maint = _rollup(
            "1000001A", "Smith Residence",
            services=[_svc("Mulch, bulk [Yd]", qty=2, total=50, rate=25)],
        )
        irr = _rollup(
            "1000002A", "Smith Residence - Irr.",
            work_hours=4.0, date="Tue-Apr-14-2026", foreman="Cassie",
            services=[_svc("Sprinkler head", qty=3, total=45, rate=15)],
        )
        return RollupGroup(maintenance=maint, irrigation=irr), maint, irr

    def test_line_order_and_class_tags(self):
        group, maint, irr = self._paired_group()
        inv = build_invoice_for_group(group, INCLUDED, invoice_date="2026-04-19")

        classes = [li.class_name for li in inv.line_items]
        # Maint labor + maint material + irr labor + irr material + DP fee
        assert len(inv.line_items) == 5
        assert classes[:2] == [MAINTENANCE_CLASS_NAME, MAINTENANCE_CLASS_NAME]
        assert classes[2:4] == [IRRIGATION_CLASS_NAME, IRRIGATION_CLASS_NAME]
        # Fee is always Maintenance regardless of upstream mix.
        assert classes[4] == MAINTENANCE_CLASS_NAME
        assert inv.line_items[4].description == FEE_DESCRIPTION

    def test_primary_jobsite_id_is_maint(self):
        group, maint, irr = self._paired_group()
        inv = build_invoice_for_group(group, INCLUDED)
        assert inv.jobsite_id == maint.jobsite_id
        assert inv.jobsite_name == "Smith Residence"  # stripped / maint's name

    def test_sources_carry_per_side_history(self):
        group, maint, irr = self._paired_group()
        inv = build_invoice_for_group(group, INCLUDED)
        assert len(inv.sources) == 2
        maint_src = next(s for s in inv.sources if s.class_name == MAINTENANCE_CLASS_NAME)
        irr_src = next(s for s in inv.sources if s.class_name == IRRIGATION_CLASS_NAME)
        assert maint_src.jobsite_id == maint.jobsite_id
        assert maint_src.date_foreman_pairs == ["Mon-Apr-13-2026|Jenna"]
        assert irr_src.jobsite_id == irr.jobsite_id
        assert irr_src.date_foreman_pairs == ["Tue-Apr-14-2026|Cassie"]

    def test_has_irrigation_true_when_irr_source_present(self):
        group, _, _ = self._paired_group()
        inv = build_invoice_for_group(group, INCLUDED)
        assert inv.has_irrigation is True

    def test_dp_fee_computed_on_combined_subtotal_only_once(self):
        group, _, _ = self._paired_group()
        inv = build_invoice_for_group(group, INCLUDED)
        fee_lines = [li for li in inv.line_items if li.description == FEE_DESCRIPTION]
        assert len(fee_lines) == 1
        non_fee_total = sum(
            li.amount for li in inv.line_items if li.description != FEE_DESCRIPTION
        )
        # Under $1000 so 10% fee
        assert inv.subtotal == pytest.approx(non_fee_total)
        assert inv.direct_payment_fee == pytest.approx(round(non_fee_total * 0.10, 2))
        assert inv.total == pytest.approx(non_fee_total + inv.direct_payment_fee)


class TestStandaloneIrr:
    def test_all_lines_irrigation_except_fee(self):
        irr = _rollup(
            "1000002A", "Lone House - Irr.",
            services=[_svc("Sprinkler head", qty=3, total=45, rate=15)],
        )
        inv = build_invoice_for_group(
            RollupGroup(maintenance=None, irrigation=irr), INCLUDED
        )
        non_fee = [li for li in inv.line_items if li.description != FEE_DESCRIPTION]
        fee = [li for li in inv.line_items if li.description == FEE_DESCRIPTION]
        assert all(li.class_name == IRRIGATION_CLASS_NAME for li in non_fee)
        assert fee and fee[0].class_name == MAINTENANCE_CLASS_NAME
        assert inv.has_irrigation is True
        assert inv.jobsite_id == irr.jobsite_id
        assert inv.jobsite_name == "Lone House"

    def test_has_irrigation_false_for_maint_only(self):
        maint = _rollup(
            "1000001A", "Maint Only",
            services=[_svc("Mulch", qty=1, total=25, rate=25)],
        )
        inv = build_invoice_for_group(
            RollupGroup(maintenance=maint, irrigation=None), INCLUDED
        )
        assert inv.has_irrigation is False
        assert all(li.class_name == MAINTENANCE_CLASS_NAME for li in inv.line_items)


# ---------- build_all_invoices orchestration ----------

class TestBuildAllInvoices:
    def test_merged_and_standalone_in_one_upload(self):
        rollups = [
            _rollup(
                "1000001A", "Smith Residence",
                services=[_svc("Mulch", qty=1, total=25, rate=25)],
            ),
            _rollup(
                "1000002A", "Smith Residence - Irr.",
                services=[_svc("Sprinkler", qty=1, total=30, rate=30)],
            ),
            _rollup(
                "1000003A", "Plain Maint",
                services=[_svc("Mow", qty=1, total=50, rate=50)],
            ),
        ]
        invoices = build_all_invoices(rollups, INCLUDED, invoice_date="2026-04-19")
        assert len(invoices) == 2
        merged = next(i for i in invoices if i.has_irrigation)
        plain = next(i for i in invoices if not i.has_irrigation)
        assert merged.jobsite_id == "1000001A"
        assert len(merged.sources) == 2
        assert plain.jobsite_id == "1000003A"
        assert len(plain.sources) == 1


# ---------- _active_zero_price_items (app.py) ----------

class TestActiveZeroPriceItemsMerged:
    def test_irr_side_items_survive_filter(self):
        # Simulate a merged invoice's session dict — the key test is that
        # zero-price items whose jobsite_id is the Irr SOURCE id (not the
        # invoice's primary maint id) still pass the mapped filter.
        from app import _active_zero_price_items

        result = {
            "invoices": [
                {
                    "jobsite_id": "1000001A",  # primary (maintenance)
                    "qbo_customer_id": "QBO-123",
                    "sources": [
                        {"jobsite_id": "1000001A", "class_name": "Maintenance"},
                        {"jobsite_id": "1000002A", "class_name": "Irrigation"},
                    ],
                }
            ],
            "zero_price_items": [
                {"jobsite_id": "1000002A", "index": 0, "description": "Irr $0 item",
                 "class_name": "Irrigation"},
                {"jobsite_id": "1000001A", "index": 1, "description": "Maint $0 item",
                 "class_name": "Maintenance"},
                {"jobsite_id": "9999999X", "index": 2, "description": "Unmapped",
                 "class_name": "Maintenance"},
            ],
        }
        active = _active_zero_price_items(result)
        descs = {i["description"] for i in active}
        assert descs == {"Irr $0 item", "Maint $0 item"}

    def test_unmapped_invoice_drops_all_its_items(self):
        from app import _active_zero_price_items

        result = {
            "invoices": [
                {
                    "jobsite_id": "1000001A",
                    # No qbo_customer_id -> unmapped
                    "sources": [{"jobsite_id": "1000001A", "class_name": "Maintenance"}],
                }
            ],
            "zero_price_items": [
                {"jobsite_id": "1000001A", "index": 0, "description": "X",
                 "class_name": "Maintenance"},
            ],
        }
        assert _active_zero_price_items(result) == []
