"""Tests for invoice line item functions."""

import pytest

from src.invoice.line_items import (
    calculate_direct_payment_fee,
    format_labor_description,
    format_date_short,
)


class TestDirectPaymentFee:
    """Tests for fee calculation tiers."""

    def test_fee_under_1000_is_10_percent(self):
        assert calculate_direct_payment_fee(100) == 10.00
        assert calculate_direct_payment_fee(500) == 50.00
        assert calculate_direct_payment_fee(999.99) == 100.00  # rounds to 100.00

    def test_fee_at_1000_is_15_flat(self):
        assert calculate_direct_payment_fee(1000) == 15.00

    def test_fee_between_1000_and_2000_is_15_flat(self):
        assert calculate_direct_payment_fee(1500) == 15.00
        assert calculate_direct_payment_fee(2000) == 15.00

    def test_fee_over_2000_is_20_flat(self):
        assert calculate_direct_payment_fee(2000.01) == 20.00
        assert calculate_direct_payment_fee(5000) == 20.00
        assert calculate_direct_payment_fee(10000) == 20.00

    def test_fee_zero_subtotal(self):
        assert calculate_direct_payment_fee(0) == 0.00


class TestFormatLaborDescription:
    """Tests for labor line item description formatting."""

    def test_single_date(self):
        result = format_labor_description(["2026-01-05"])
        assert result == "Skilled Garden Hourly Labor 1/05"

    def test_date_range(self):
        result = format_labor_description(["2026-01-05", "2026-01-06", "2026-01-07"])
        assert result == "Skilled Garden Hourly Labor 1/05-1/07"

    def test_with_task_summary(self):
        result = format_labor_description(["2026-01-05"], "refresh winter containers")
        assert result == "Skilled Garden Hourly Labor 1/05- refresh winter containers"

    def test_no_dates(self):
        result = format_labor_description([])
        assert result == "Skilled Garden Hourly Labor"


class TestFormatDateShort:
    """Tests for date formatting."""

    def test_standard_date(self):
        assert format_date_short("2026-01-05") == "1/05"
        assert format_date_short("2026-11-24") == "11/24"

    def test_invalid_date_returns_original(self):
        assert format_date_short("invalid") == "invalid"
        assert format_date_short("") == ""
