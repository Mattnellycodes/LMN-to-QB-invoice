"""Tests for interactive customer mapping functions."""

import pytest
from unittest.mock import patch

from src.calculations.time_calc import JobsiteHours
from src.mapping.interactive_mapping import (
    build_unmapped_context,
    get_user_selection,
)


def test_build_unmapped_context():
    """Test building context from JobsiteHours."""
    jobsite_hours = [
        JobsiteHours(
            jobsite_id="J1",
            jobsite_name="Site One",
            customer_name="Customer A",
            work_hours=3.0,
            allocated_drive_time=1.0,
            total_billable_hours=4.0,
            billable_rate=75.0,
            dates=[],
        ),
    ]

    result = build_unmapped_context(jobsite_hours, ["J1"])

    assert len(result) == 1
    assert result[0].jobsite_id == "J1"
    assert result[0].estimated_amount == 300.0  # 4.0 * 75.0


def test_get_user_selection_valid():
    """Test valid number selection returns 0-based index."""
    with patch("builtins.input", return_value="2"):
        assert get_user_selection(3) == 1


def test_get_user_selection_skip():
    """Test 's' returns None."""
    with patch("builtins.input", return_value="s"):
        assert get_user_selection(3) is None


def test_get_user_selection_research():
    """Test 'r' returns -1."""
    with patch("builtins.input", return_value="r"):
        assert get_user_selection(3) == -1
