"""Tests for database customer override operations."""

from unittest.mock import MagicMock, patch, call

import pytest

from src.mapping.customer_mapping import CustomerMapping


class TestGetCustomerOverrides:
    """Test get_customer_overrides function."""

    def test_returns_mappings_from_database(self):
        """Returns customer mappings from database."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("67135", "313", "Roe, Sandra", "manual override"),
            ("67200", "400", "Smith, John", ""),
        ]

        with patch("src.db.customer_overrides.db_cursor") as mock_db_cursor:
            mock_db_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_db_cursor.return_value.__exit__ = MagicMock(return_value=False)

            from src.db.customer_overrides import get_customer_overrides

            mappings = get_customer_overrides()

        assert len(mappings) == 2
        assert "67135" in mappings
        assert mappings["67135"].qbo_customer_id == "313"
        assert mappings["67135"].qbo_display_name == "Roe, Sandra"
        assert mappings["67135"].notes == "manual override"

    def test_returns_empty_dict_when_no_overrides(self):
        """Returns empty dict when no overrides in database."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []

        with patch("src.db.customer_overrides.db_cursor") as mock_db_cursor:
            mock_db_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_db_cursor.return_value.__exit__ = MagicMock(return_value=False)

            from src.db.customer_overrides import get_customer_overrides

            mappings = get_customer_overrides()

        assert mappings == {}


class TestSaveCustomerOverride:
    """Test save_customer_override function."""

    def test_inserts_new_override(self):
        """Inserts new customer override."""
        mock_cursor = MagicMock()

        with patch("src.db.customer_overrides.db_cursor") as mock_db_cursor:
            mock_db_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_db_cursor.return_value.__exit__ = MagicMock(return_value=False)

            from src.db.customer_overrides import save_customer_override

            mapping = CustomerMapping(
                jobsite_id="67135",
                qbo_customer_id="313",
                qbo_display_name="Roe, Sandra",
                notes="test override",
            )
            save_customer_override(mapping)

        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args
        assert "INSERT INTO customer_mapping_overrides" in call_args[0][0]
        assert call_args[0][1] == ("67135", "313", "Roe, Sandra", "test override")


class TestDeleteCustomerOverride:
    """Test delete_customer_override function."""

    def test_deletes_existing_override(self):
        """Deletes existing override and returns True."""
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1

        with patch("src.db.customer_overrides.db_cursor") as mock_db_cursor:
            mock_db_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_db_cursor.return_value.__exit__ = MagicMock(return_value=False)

            from src.db.customer_overrides import delete_customer_override

            result = delete_customer_override("67135")

        assert result is True
        mock_cursor.execute.assert_called_once()

    def test_returns_false_when_not_found(self):
        """Returns False when override not found."""
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0

        with patch("src.db.customer_overrides.db_cursor") as mock_db_cursor:
            mock_db_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
            mock_db_cursor.return_value.__exit__ = MagicMock(return_value=False)

            from src.db.customer_overrides import delete_customer_override

            result = delete_customer_override("nonexistent")

        assert result is False
