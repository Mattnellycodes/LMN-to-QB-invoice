"""Tests for database item override operations."""

from unittest.mock import MagicMock, patch


def _mock_db_cursor(cursor):
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=cursor)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


class TestGetItemOverrides:
    def test_returns_overrides_keyed_by_lmn_name(self):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("Deer Spray, Bozeman, ea", "42", "Deer Spray"),
            ("Mulch, Soil Pep, bulk [Yd]", "77", "Mulch (bulk yard)"),
        ]

        with patch("src.db.item_overrides.db_cursor") as mock_db_cursor:
            mock_db_cursor.return_value = _mock_db_cursor(mock_cursor)

            from src.db.item_overrides import get_item_overrides

            overrides = get_item_overrides()

        assert overrides == {
            "Deer Spray, Bozeman, ea": {"value": "42", "name": "Deer Spray"},
            "Mulch, Soil Pep, bulk [Yd]": {"value": "77", "name": "Mulch (bulk yard)"},
        }

    def test_empty_when_no_rows(self):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []

        with patch("src.db.item_overrides.db_cursor") as mock_db_cursor:
            mock_db_cursor.return_value = _mock_db_cursor(mock_cursor)

            from src.db.item_overrides import get_item_overrides

            assert get_item_overrides() == {}

    def test_null_qbo_item_name_becomes_empty_string(self):
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [("Foo", "1", None)]

        with patch("src.db.item_overrides.db_cursor") as mock_db_cursor:
            mock_db_cursor.return_value = _mock_db_cursor(mock_cursor)

            from src.db.item_overrides import get_item_overrides

            overrides = get_item_overrides()

        assert overrides["Foo"]["name"] == ""


class TestSaveItemOverride:
    def test_upserts_with_expected_args(self):
        mock_cursor = MagicMock()

        with patch("src.db.item_overrides.db_cursor") as mock_db_cursor:
            mock_db_cursor.return_value = _mock_db_cursor(mock_cursor)

            from src.db.item_overrides import save_item_override

            save_item_override("Deer Spray, Bozeman, ea", "42", "Deer Spray", "note")

        mock_cursor.execute.assert_called_once()
        sql, params = mock_cursor.execute.call_args[0]
        assert "INSERT INTO item_mapping_overrides" in sql
        assert "ON CONFLICT (lmn_item_name) DO UPDATE" in sql
        assert params == ("Deer Spray, Bozeman, ea", "42", "Deer Spray", "note")


class TestDeleteItemOverride:
    def test_returns_true_when_row_deleted(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1

        with patch("src.db.item_overrides.db_cursor") as mock_db_cursor:
            mock_db_cursor.return_value = _mock_db_cursor(mock_cursor)

            from src.db.item_overrides import delete_item_override

            assert delete_item_override("Foo") is True

    def test_returns_false_when_no_row(self):
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0

        with patch("src.db.item_overrides.db_cursor") as mock_db_cursor:
            mock_db_cursor.return_value = _mock_db_cursor(mock_cursor)

            from src.db.item_overrides import delete_item_override

            assert delete_item_override("Missing") is False
