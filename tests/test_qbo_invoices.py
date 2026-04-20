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
