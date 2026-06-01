"""Tests for the QBO invoices module payload builder."""

from unittest.mock import MagicMock, patch

import requests

from src.invoice.line_items import InvoiceData, LineItem
from src.qbo.invoices import build_qbo_line_item, create_draft_invoice


def _sample_line() -> LineItem:
    return LineItem(
        description="Skilled Garden Hourly Labor",
        quantity=4.0,
        rate=85.0,
        amount=340.0,
        item_lookup_name="Skilled Garden Hourly Labor",
    )


def _sample_invoice() -> InvoiceData:
    return InvoiceData(
        jobsite_id="5613100",
        jobsite_name="Test Site",
        customer_name="Test Customer",
        invoice_date="2026-05-31",
        line_items=[_sample_line()],
        sources=[],
    )


def _ok_response(doc_number: str) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "Invoice": {"Id": "99", "DocNumber": doc_number, "TotalAmt": 340.0}
    }
    return resp


def _duplicate_docnumber_response() -> MagicMock:
    resp = MagicMock()
    resp.headers = {"intuit_tid": "tid-1"}
    resp.status_code = 400
    resp.json.return_value = {
        "Fault": {"Error": [{"code": "6140", "Detail": "Duplicate Document Number"}]}
    }
    err = requests.exceptions.HTTPError()
    err.response = resp
    resp.raise_for_status.side_effect = err
    return resp


class TestCreateDraftInvoice:
    def test_sets_docnumber_from_counter(self):
        with (
            patch(
                "src.qbo.invoices.get_qbo_credentials", return_value=("tok", "realm")
            ),
            patch("src.qbo.invoices.get_api_base_url", return_value="https://api.fake"),
            patch("src.qbo.invoices.get_term_id_by_name", return_value=None),
            patch(
                "src.db.invoice_counter.claim_next_invoice_number",
                return_value="26-00007",
            ),
            patch(
                "src.qbo.invoices.requests.post", return_value=_ok_response("26-00007")
            ) as mock_post,
        ):
            result = create_draft_invoice(_sample_invoice(), "1", {})

        assert result.success is True
        assert result.invoice_number == "26-00007"
        assert mock_post.call_args.kwargs["json"]["DocNumber"] == "26-00007"

    def test_omits_docnumber_when_counter_unavailable(self):
        with (
            patch(
                "src.qbo.invoices.get_qbo_credentials", return_value=("tok", "realm")
            ),
            patch("src.qbo.invoices.get_api_base_url", return_value="https://api.fake"),
            patch("src.qbo.invoices.get_term_id_by_name", return_value=None),
            patch(
                "src.db.invoice_counter.claim_next_invoice_number",
                side_effect=ValueError("no DB"),
            ),
            patch(
                "src.qbo.invoices.requests.post", return_value=_ok_response("")
            ) as mock_post,
        ):
            result = create_draft_invoice(_sample_invoice(), "1", {})

        assert result.success is True
        assert "DocNumber" not in mock_post.call_args.kwargs["json"]

    def test_retries_once_on_duplicate_docnumber(self):
        with (
            patch(
                "src.qbo.invoices.get_qbo_credentials", return_value=("tok", "realm")
            ),
            patch("src.qbo.invoices.get_api_base_url", return_value="https://api.fake"),
            patch("src.qbo.invoices.get_term_id_by_name", return_value=None),
            patch(
                "src.db.invoice_counter.claim_next_invoice_number",
                side_effect=["26-00007", "26-00008"],
            ),
            patch(
                "src.qbo.invoices.requests.post",
                side_effect=[_duplicate_docnumber_response(), _ok_response("26-00008")],
            ) as mock_post,
        ):
            result = create_draft_invoice(_sample_invoice(), "1", {})

        assert mock_post.call_count == 2
        assert result.success is True
        assert result.invoice_number == "26-00008"
        assert mock_post.call_args.kwargs["json"]["DocNumber"] == "26-00008"


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
