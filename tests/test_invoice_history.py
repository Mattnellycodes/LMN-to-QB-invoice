"""Tests for invoice history tracking module."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


class TestRecordInvoiceCreation:
    """Tests for record_invoice_creation function."""

    @patch("src.db.invoice_history.db_cursor")
    def test_inserts_history_record(self, mock_db_cursor):
        """Should insert a history record with all fields."""
        from src.db.invoice_history import record_invoice_creation

        mock_cursor = MagicMock()
        mock_db_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_db_cursor.return_value.__exit__ = MagicMock(return_value=False)

        record_invoice_creation(
            jobsite_id="J123",
            timesheet_ids=["T1", "T2"],
            work_dates=["2024-01-15", "2024-01-16"],
            qbo_invoice_id="INV-001",
            qbo_invoice_number="1001",
            total_amount=500.00,
        )

        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args
        sql = call_args[0][0]
        params = call_args[0][1]

        assert "INSERT INTO invoice_history" in sql
        assert params[0] == "J123"
        assert params[1] == ["T1", "T2"]
        assert params[2] == ["2024-01-15", "2024-01-16"]
        assert params[3] == "INV-001"
        assert params[4] == "1001"
        assert params[5] == 500.00


class TestFindAlreadyInvoicedTimesheets:
    """Tests for find_already_invoiced_timesheets function."""

    @patch("src.db.invoice_history.db_cursor")
    def test_returns_matching_timesheets(self, mock_db_cursor):
        """Should return timesheets that have been invoiced."""
        from src.db.invoice_history import find_already_invoiced_timesheets

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            (["T1", "T2"], "1001", "INV-001", datetime(2024, 1, 15)),
        ]
        mock_db_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_db_cursor.return_value.__exit__ = MagicMock(return_value=False)

        results = find_already_invoiced_timesheets(["T1", "T3"])

        assert len(results) == 1
        assert results[0]["timesheet_id"] == "T1"
        assert results[0]["qbo_invoice_number"] == "1001"
        assert results[0]["qbo_invoice_id"] == "INV-001"

    @patch("src.db.invoice_history.db_cursor")
    def test_returns_empty_for_no_matches(self, mock_db_cursor):
        """Should return empty list when no timesheets match."""
        from src.db.invoice_history import find_already_invoiced_timesheets

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        mock_db_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_db_cursor.return_value.__exit__ = MagicMock(return_value=False)

        results = find_already_invoiced_timesheets(["T99"])

        assert results == []

    def test_returns_empty_for_empty_input(self):
        """Should return empty list for empty timesheet IDs."""
        from src.db.invoice_history import find_already_invoiced_timesheets

        results = find_already_invoiced_timesheets([])

        assert results == []


class TestFindOverlappingDates:
    """Tests for find_overlapping_dates function."""

    @patch("src.db.invoice_history.db_cursor")
    def test_returns_overlap_info(self, mock_db_cursor):
        """Should return overlap info when dates match."""
        from src.db.invoice_history import find_overlapping_dates

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (
            ["2024-01-15", "2024-01-16"],
            "1001",
            datetime(2024, 1, 15),
        )
        mock_db_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_db_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = find_overlapping_dates("J123", ["2024-01-15"])

        assert result is not None
        assert "2024-01-15" in result["overlapping_dates"]
        assert result["qbo_invoice_number"] == "1001"

    @patch("src.db.invoice_history.db_cursor")
    def test_returns_none_when_no_overlap(self, mock_db_cursor):
        """Should return None when no dates overlap."""
        from src.db.invoice_history import find_overlapping_dates

        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_db_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_db_cursor.return_value.__exit__ = MagicMock(return_value=False)

        result = find_overlapping_dates("J123", ["2024-02-01"])

        assert result is None

    def test_returns_none_for_empty_dates(self):
        """Should return None for empty work dates."""
        from src.db.invoice_history import find_overlapping_dates

        result = find_overlapping_dates("J123", [])

        assert result is None


class TestGetInvoiceHistory:
    """Tests for get_invoice_history function."""

    @patch("src.db.invoice_history.db_cursor")
    def test_returns_all_history(self, mock_db_cursor):
        """Should return all history records when no filter."""
        from src.db.invoice_history import get_invoice_history

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("J123", ["T1"], ["2024-01-15"], "INV-001", "1001", 500.00, datetime(2024, 1, 15)),
            ("J456", ["T2"], ["2024-01-16"], "INV-002", "1002", 300.00, datetime(2024, 1, 16)),
        ]
        mock_db_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_db_cursor.return_value.__exit__ = MagicMock(return_value=False)

        results = get_invoice_history()

        assert len(results) == 2
        assert results[0]["jobsite_id"] == "J123"
        assert results[1]["jobsite_id"] == "J456"

    @patch("src.db.invoice_history.db_cursor")
    def test_filters_by_jobsite(self, mock_db_cursor):
        """Should filter by jobsite ID when provided."""
        from src.db.invoice_history import get_invoice_history

        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = [
            ("J123", ["T1"], ["2024-01-15"], "INV-001", "1001", 500.00, datetime(2024, 1, 15)),
        ]
        mock_db_cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_db_cursor.return_value.__exit__ = MagicMock(return_value=False)

        results = get_invoice_history(jobsite_id="J123")

        mock_cursor.execute.assert_called_once()
        call_args = mock_cursor.execute.call_args
        params = call_args[0][1]
        assert params == ("J123",)
