"""Tests for the QBO invoices module payload builder."""

from src.invoice.line_items import LineItem
from src.qbo.invoices import build_qbo_line_item


def _sample_line() -> LineItem:
    return LineItem(
        description="Skilled Garden Hourly Labor",
        quantity=4.0,
        rate=85.0,
        amount=340.0,
        item_lookup_name="Skilled Garden Hourly Labor",
    )


class TestBuildQboLineItem:
    def test_injects_class_ref_when_provided(self):
        class_ref = {"value": "42", "name": "Maintenance"}

        payload = build_qbo_line_item(
            _sample_line(),
            line_num=1,
            item_ref={"value": "1", "name": "Labor"},
            class_ref=class_ref,
        )

        assert payload["SalesItemLineDetail"]["ClassRef"] == class_ref

    def test_omits_class_ref_when_absent(self):
        payload = build_qbo_line_item(
            _sample_line(),
            line_num=1,
            item_ref={"value": "1", "name": "Labor"},
        )

        assert "ClassRef" not in payload["SalesItemLineDetail"]

    def test_injects_class_ref_without_item_ref(self):
        class_ref = {"value": "42", "name": "Maintenance"}

        payload = build_qbo_line_item(
            _sample_line(),
            line_num=1,
            item_ref=None,
            class_ref=class_ref,
        )

        assert payload["SalesItemLineDetail"]["ClassRef"] == class_ref
        assert "ItemRef" not in payload["SalesItemLineDetail"]

    def test_unit_price_derived_so_qbo_amount_check_passes(self):
        # The Ferrin, Andy failure: aggregated entries left rate=2.49 but the
        # summed amount was 23.62 with qty=9.5 (real per-entry rate ~2.486).
        # QBO rejects: 23.62 != round(9.5 * 2.49, 2) = 23.66.
        item = LineItem(
            description="Aggregated mixed-rate line",
            quantity=9.5,
            rate=2.49,  # stale aggregated rate
            amount=23.62,  # ground-truth from LMN
            item_lookup_name="Foo",
        )

        payload = build_qbo_line_item(item, line_num=1, item_ref=None)

        amount = payload["Amount"]
        qty = payload["SalesItemLineDetail"]["Qty"]
        unit_price = payload["SalesItemLineDetail"]["UnitPrice"]

        assert amount == 23.62
        # QBO's invariant: round(Qty * UnitPrice, 2) must equal Amount.
        assert round(qty * unit_price, 2) == amount

    def test_zero_quantity_falls_back_to_rate(self):
        item = LineItem(
            description="Flat-fee line",
            quantity=0.0,
            rate=50.0,
            amount=50.0,
            item_lookup_name="Foo",
        )

        payload = build_qbo_line_item(item, line_num=1, item_ref=None)

        assert payload["Amount"] == 50.0
        assert payload["SalesItemLineDetail"]["UnitPrice"] == 50.0
