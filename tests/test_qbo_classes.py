"""Tests for the QBO classes module (default class lookup for invoice lines)."""

from unittest.mock import MagicMock, patch


def _mock_response(payload):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


def _patch_api_base():
    return patch("src.qbo.classes.get_api_base_url", return_value="https://api.fake")


class TestGetClassByName:
    def test_returns_ref_when_class_exists(self):
        payload = {"QueryResponse": {"Class": [{"Id": "42", "Name": "Maintenance"}]}}

        with _patch_api_base(), patch(
            "src.qbo.classes.requests.get", return_value=_mock_response(payload)
        ):
            from src.qbo.classes import get_class_by_name

            ref = get_class_by_name("tok", "realm", "Maintenance")

        assert ref == {"value": "42", "name": "Maintenance"}

    def test_returns_none_when_class_missing(self):
        payload = {"QueryResponse": {}}

        with _patch_api_base(), patch(
            "src.qbo.classes.requests.get", return_value=_mock_response(payload)
        ):
            from src.qbo.classes import get_class_by_name

            ref = get_class_by_name("tok", "realm", "Maintenance")

        assert ref is None

    def test_returns_none_when_class_entries_lack_id_or_name(self):
        payload = {
            "QueryResponse": {
                "Class": [
                    {"Id": "", "Name": "Maintenance"},
                    {"Id": "7", "Name": ""},
                ]
            }
        }

        with _patch_api_base(), patch(
            "src.qbo.classes.requests.get", return_value=_mock_response(payload)
        ):
            from src.qbo.classes import get_class_by_name

            ref = get_class_by_name("tok", "realm", "Maintenance")

        assert ref is None

    def test_escapes_single_quotes(self):
        payload = {"QueryResponse": {"Class": []}}

        with _patch_api_base(), patch(
            "src.qbo.classes.requests.get", return_value=_mock_response(payload)
        ) as mock_get:
            from src.qbo.classes import get_class_by_name

            get_class_by_name("tok", "realm", "O'Brien")

        query = mock_get.call_args.kwargs["params"]["query"]
        assert "O\\'Brien" in query
