"""Tests for the self-assigned invoice number counter."""

from unittest.mock import MagicMock, patch


def _mock_db_cursor(cursor):
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=cursor)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


class TestClaimNextInvoiceNumber:
    def test_formats_with_prefix_and_zero_padding(self):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (0,)

        with patch("src.db.invoice_counter.db_cursor") as mock_db_cursor:
            mock_db_cursor.return_value = _mock_db_cursor(mock_cursor)

            from src.db.invoice_counter import claim_next_invoice_number

            assert claim_next_invoice_number() == "26-00000"

    def test_increments_on_successive_claims(self):
        mock_cursor = MagicMock()

        with patch("src.db.invoice_counter.db_cursor") as mock_db_cursor:
            mock_db_cursor.return_value = _mock_db_cursor(mock_cursor)

            from src.db.invoice_counter import claim_next_invoice_number

            mock_cursor.fetchone.return_value = (7,)
            assert claim_next_invoice_number() == "26-00007"

            mock_cursor.fetchone.return_value = (8,)
            assert claim_next_invoice_number() == "26-00008"

    def test_advances_the_counter_row(self):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (3,)

        with patch("src.db.invoice_counter.db_cursor") as mock_db_cursor:
            mock_db_cursor.return_value = _mock_db_cursor(mock_cursor)

            from src.db.invoice_counter import claim_next_invoice_number

            claim_next_invoice_number()

        executed = " ".join(call[0][0] for call in mock_cursor.execute.call_args_list)
        assert "FOR UPDATE" in executed
        assert "counter = counter + 1" in executed
