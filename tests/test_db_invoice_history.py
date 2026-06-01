"""Tests for invoice-history duplicate-detection queries."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

from src.db.invoice_history import _query_overlapping_pairs


def _cursor_returning(rows):
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    return cursor


def test_query_overlapping_pairs_keeps_only_intersecting_rows():
    created = datetime(2026, 5, 1, 12, 0, 0)
    cursor = _cursor_returning(
        [
            (["2026-04-13|Jenna", "2026-04-14|Jenna"], "26-00001", "1001", created),
            (["2026-04-20|Cassie"], "26-00002", "1002", created),
        ]
    )

    matches = _query_overlapping_pairs(
        cursor, "5843557W", ["2026-04-13|Jenna", "2026-04-99|Nobody"]
    )

    assert len(matches) == 1
    assert matches[0]["overlapping_pairs"] == ["2026-04-13|Jenna"]
    assert matches[0]["qbo_invoice_number"] == "26-00001"
    assert matches[0]["qbo_invoice_id"] == "1001"
    assert matches[0]["created_at"] == created.isoformat()


def test_query_overlapping_pairs_handles_null_created_at():
    cursor = _cursor_returning([(["2026-04-13|Jenna"], "26-00001", "1001", None)])

    matches = _query_overlapping_pairs(cursor, "5843557W", ["2026-04-13|Jenna"])

    assert matches[0]["created_at"] is None
