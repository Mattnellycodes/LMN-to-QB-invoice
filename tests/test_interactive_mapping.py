"""Tests for interactive customer mapping functions."""

import pytest
from unittest.mock import patch

from src.calculations.time_calc import JobsiteHours
from src.mapping.interactive_mapping import (
    build_unmapped_context,
    get_user_selection,
    prompt_single_jobsite_mapping,
    prompt_interactive_mapping,
    UnmappedJobsite,
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


def test_prompt_single_jobsite_mapping_preview_mode_skips_qbo():
    """Test that preview_mode=True skips QBO API calls and returns None."""
    unmapped = UnmappedJobsite(
        jobsite_id="J1",
        jobsite_name="Test Site",
        lmn_customer_name="Test Customer",
        estimated_amount=300.0,
    )

    # Should NOT call any QBO API or prompt for input
    with patch("src.mapping.interactive_mapping.search_and_select_customer") as mock_search:
        with patch("builtins.input") as mock_input:
            result = prompt_single_jobsite_mapping(unmapped, 1, 1, preview_mode=True)

    assert result is None
    mock_search.assert_not_called()
    mock_input.assert_not_called()


def test_prompt_interactive_mapping_preview_mode_skips_all():
    """Test that preview_mode=True skips QBO search for all unmapped jobsites."""
    unmapped_context = [
        UnmappedJobsite(
            jobsite_id="J1",
            jobsite_name="Site One",
            lmn_customer_name="Customer A",
            estimated_amount=300.0,
        ),
        UnmappedJobsite(
            jobsite_id="J2",
            jobsite_name="Site Two",
            lmn_customer_name="Customer B",
            estimated_amount=500.0,
        ),
    ]
    existing_mappings = {}

    with patch("src.mapping.interactive_mapping.search_and_select_customer") as mock_search:
        with patch("builtins.input") as mock_input:
            result = prompt_interactive_mapping(
                unmapped_context=unmapped_context,
                existing_mappings=existing_mappings,
                preview_mode=True,
            )

    # No QBO calls should be made
    mock_search.assert_not_called()
    mock_input.assert_not_called()
    # Mappings should be unchanged (all skipped)
    assert result == {}


def test_prompt_single_jobsite_mapping_normal_mode_uses_qbo(capsys):
    """Test that preview_mode=False (default) prompts for QBO search."""
    unmapped = UnmappedJobsite(
        jobsite_id="J1",
        jobsite_name="Test Site",
        lmn_customer_name="Test Customer",
        estimated_amount=300.0,
    )

    # User skips immediately
    with patch("builtins.input", return_value="s"):
        result = prompt_single_jobsite_mapping(unmapped, 1, 1, preview_mode=False)

    assert result is None
    # Check that it prompted for input (not skipped automatically)
    captured = capsys.readouterr()
    assert "Skipped: J1" in captured.out
