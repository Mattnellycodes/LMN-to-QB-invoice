"""Tests for the QBO items module (cache, search, fallback)."""

from unittest.mock import MagicMock, patch

import pytest


def _mock_response(payload):
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    return response


def _patch_api_base():
    return patch("src.qbo.items.get_api_base_url", return_value="https://api.fake")


class TestFetchAllItems:
    def test_single_page_returns_cache_keyed_lowercase(self):
        payload = {
            "QueryResponse": {
                "Item": [
                    {"Id": "1", "Name": "Deer Spray"},
                    {"Id": "2", "Name": "Mulch, Soil Pep, bulk [Yd]"},
                    {"Id": "3", "Name": "  Dump Fee  "},
                ]
            }
        }

        with _patch_api_base(), patch(
            "src.qbo.items.requests.get", return_value=_mock_response(payload)
        ) as mock_get:
            from src.qbo.items import fetch_all_items

            cache = fetch_all_items("tok", "realm")

        assert mock_get.call_count == 1
        assert cache["deer spray"] == {"value": "1", "name": "Deer Spray"}
        assert cache["mulch, soil pep, bulk [yd]"]["value"] == "2"
        assert cache["dump fee"]["name"] == "Dump Fee"

    def test_paginates_until_short_page(self):
        full_page = {
            "QueryResponse": {
                "Item": [{"Id": str(i), "Name": f"Item {i}"} for i in range(1000)]
            }
        }
        short_page = {
            "QueryResponse": {
                "Item": [{"Id": "x", "Name": "Extra"}]
            }
        }

        responses = [_mock_response(full_page), _mock_response(short_page)]

        with _patch_api_base(), patch(
            "src.qbo.items.requests.get", side_effect=responses
        ) as mock_get:
            from src.qbo.items import fetch_all_items

            cache = fetch_all_items("tok", "realm")

        assert mock_get.call_count == 2
        assert len(cache) == 1001
        assert "extra" in cache

    def test_skips_items_without_name_or_id(self):
        payload = {
            "QueryResponse": {
                "Item": [
                    {"Id": "1", "Name": ""},
                    {"Id": "", "Name": "NoId"},
                    {"Id": "2", "Name": "Valid"},
                ]
            }
        }

        with _patch_api_base(), patch(
            "src.qbo.items.requests.get", return_value=_mock_response(payload)
        ):
            from src.qbo.items import fetch_all_items

            cache = fetch_all_items("tok", "realm")

        assert cache == {"valid": {"value": "2", "name": "Valid"}}


class TestGetFallbackItemRef:
    def test_returns_ref_when_present(self):
        from src.qbo.items import FALLBACK_ITEM_NAME, get_fallback_item_ref

        cache = {FALLBACK_ITEM_NAME.lower(): {"value": "99", "name": FALLBACK_ITEM_NAME}}

        ref = get_fallback_item_ref(cache)

        assert ref == {"value": "99", "name": FALLBACK_ITEM_NAME}

    def test_raises_when_missing(self):
        from src.qbo.items import ItemMappingError, get_fallback_item_ref

        with pytest.raises(ItemMappingError):
            get_fallback_item_ref({"other thing": {"value": "1", "name": "Other Thing"}})


class TestSearchItemsByName:
    def test_escapes_single_quotes(self):
        payload = {"QueryResponse": {"Item": []}}

        with _patch_api_base(), patch(
            "src.qbo.items.requests.get", return_value=_mock_response(payload)
        ) as mock_get:
            from src.qbo.items import search_items_by_name

            search_items_by_name("tok", "realm", "O'Brien")

        query = mock_get.call_args.kwargs["params"]["query"]
        assert "O\\'Brien" in query

    def test_returns_id_name_pairs(self):
        payload = {
            "QueryResponse": {
                "Item": [
                    {"Id": "5", "Name": "Deer Spray"},
                    {"Id": "6", "Name": ""},
                    {"Id": "7", "Name": "Deer Repellent"},
                ]
            }
        }

        with _patch_api_base(), patch(
            "src.qbo.items.requests.get", return_value=_mock_response(payload)
        ):
            from src.qbo.items import search_items_by_name

            results = search_items_by_name("tok", "realm", "deer", limit=5)

        assert results == [
            {"id": "5", "name": "Deer Spray"},
            {"id": "7", "name": "Deer Repellent"},
        ]

    def test_empty_query_short_circuits(self):
        from src.qbo.items import search_items_by_name

        assert search_items_by_name("tok", "realm", "") == []
        assert search_items_by_name("tok", "realm", "   ") == []
