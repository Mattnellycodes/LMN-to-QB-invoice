"""Tests for invoice line item functions."""

import pytest
import pandas as pd

from src.invoice.line_items import (
    calculate_direct_payment_fee,
    format_labor_description,
    format_date_short,
    extract_service_line_items,
    build_invoice,
    build_all_invoices,
    LineItem,
    InvoiceData,
)
from src.calculations.time_calc import JobsiteHours


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


# =============================================================================
# Test Fixtures for Invoice Building
# =============================================================================


@pytest.fixture
def sample_service_df():
    """Sample service data DataFrame."""
    return pd.DataFrame({
        "JobsiteID": ["JS001", "JS001", "JS002", "JS001"],
        "Service_Activity": ["Mulch", "Plants", "Fertilizer", "Included Item"],
        "Timesheet Qty": [5, 10, 2, 1],
        "Invoice Type": ["Per Unit", "Per Unit", "Per Unit", "Included"],
        "Unit Price": [45.0, 25.0, 30.0, 0.0],
        "Total Price": [225.0, 250.0, 60.0, 0.0],
    })


@pytest.fixture
def sample_jobsite_hours():
    """Sample JobsiteHours object."""
    return JobsiteHours(
        jobsite_id="JS001",
        jobsite_name="123 Main St",
        customer_name="John Smith",
        work_hours=2.5,
        allocated_drive_time=0.5,
        total_billable_hours=3.0,
        billable_rate=75.0,
        dates=["2026-01-15"],
    )


@pytest.fixture
def zero_hours_jobsite():
    """JobsiteHours with no billable hours."""
    return JobsiteHours(
        jobsite_id="JS003",
        jobsite_name="789 Pine St",
        customer_name="Bob Builder",
        work_hours=0,
        allocated_drive_time=0,
        total_billable_hours=0,
        billable_rate=75.0,
        dates=[],
    )


# =============================================================================
# Test extract_service_line_items
# =============================================================================


class TestExtractServiceLineItems:
    """Tests for extracting service line items from DataFrame."""

    def test_extracts_billable_items_for_jobsite(self, sample_service_df):
        """Extracts only billable items for specified jobsite."""
        items = extract_service_line_items(sample_service_df, "JS001")

        # Should get Mulch and Plants, not Included Item
        assert len(items) == 2
        assert items[0].description == "Mulch"
        assert items[0].quantity == 5
        assert items[0].rate == 45.0
        assert items[0].amount == 225.0

    def test_excludes_included_invoice_type(self, sample_service_df):
        """Excludes items with Invoice Type = 'Included'."""
        items = extract_service_line_items(sample_service_df, "JS001")

        descriptions = [item.description for item in items]
        assert "Included Item" not in descriptions

    def test_returns_empty_for_nonexistent_jobsite(self, sample_service_df):
        """Returns empty list for jobsite with no services."""
        items = extract_service_line_items(sample_service_df, "NONEXISTENT")
        assert items == []

    def test_filters_by_jobsite_id(self, sample_service_df):
        """Only returns items for the specified jobsite."""
        items = extract_service_line_items(sample_service_df, "JS002")

        assert len(items) == 1
        assert items[0].description == "Fertilizer"


# =============================================================================
# Test build_invoice
# =============================================================================


class TestBuildInvoice:
    """Tests for building complete invoice data."""

    def test_builds_complete_invoice(self, sample_jobsite_hours, sample_service_df):
        """Builds invoice with labor and service items."""
        invoice = build_invoice(sample_jobsite_hours, sample_service_df, "2026-01-20")

        assert invoice.jobsite_id == "JS001"
        assert invoice.jobsite_name == "123 Main St"
        assert invoice.customer_name == "John Smith"
        assert invoice.invoice_date == "2026-01-20"

    def test_includes_labor_line_item(self, sample_jobsite_hours, sample_service_df):
        """Includes labor line item when billable hours > 0."""
        invoice = build_invoice(sample_jobsite_hours, sample_service_df)

        labor_items = [i for i in invoice.line_items if "Labor" in i.description]
        assert len(labor_items) == 1
        assert labor_items[0].quantity == 3.0
        assert labor_items[0].rate == 75.0
        assert labor_items[0].amount == 225.0

    def test_excludes_labor_when_zero_hours(self, zero_hours_jobsite, sample_service_df):
        """Excludes labor line item when billable hours = 0."""
        invoice = build_invoice(zero_hours_jobsite, sample_service_df)

        labor_items = [i for i in invoice.line_items if "Labor" in i.description]
        assert len(labor_items) == 0

    def test_calculates_subtotal(self, sample_jobsite_hours, sample_service_df):
        """Calculates correct subtotal."""
        invoice = build_invoice(sample_jobsite_hours, sample_service_df)

        # Labor: 3.0 * 75 = 225, Mulch: 225, Plants: 250 = 700
        assert invoice.subtotal == 700.0

    def test_adds_direct_payment_fee(self, sample_jobsite_hours, sample_service_df):
        """Adds direct payment fee based on subtotal."""
        invoice = build_invoice(sample_jobsite_hours, sample_service_df)

        # Subtotal 700 -> 10% fee = 70
        assert invoice.direct_payment_fee == 70.0

    def test_calculates_total(self, sample_jobsite_hours, sample_service_df):
        """Calculates correct total with fee."""
        invoice = build_invoice(sample_jobsite_hours, sample_service_df)

        # 700 + 70 = 770
        assert invoice.total == 770.0

    def test_includes_fee_line_item(self, sample_jobsite_hours, sample_service_df):
        """Includes fee as a line item."""
        invoice = build_invoice(sample_jobsite_hours, sample_service_df)

        fee_items = [i for i in invoice.line_items if "check" in i.description.lower()]
        assert len(fee_items) == 1

    def test_uses_current_date_when_not_provided(self, sample_jobsite_hours, sample_service_df):
        """Uses current date when invoice_date not provided."""
        invoice = build_invoice(sample_jobsite_hours, sample_service_df)

        # Should be a valid date string
        assert invoice.invoice_date is not None
        assert len(invoice.invoice_date) == 10  # YYYY-MM-DD format


# =============================================================================
# Test build_all_invoices
# =============================================================================


class TestBuildAllInvoices:
    """Tests for building invoices for multiple jobsites."""

    def test_builds_invoices_for_all_jobsites(self, sample_jobsite_hours, sample_service_df):
        """Builds invoices for all provided jobsites."""
        jobsite_list = [sample_jobsite_hours]
        invoices = build_all_invoices(jobsite_list, sample_service_df)

        assert len(invoices) == 1
        assert invoices[0].jobsite_id == "JS001"

    def test_skips_zero_subtotal_invoices(self, zero_hours_jobsite):
        """Skips invoices with zero subtotal."""
        # Empty service df with proper dtypes
        service_df = pd.DataFrame({
            "JobsiteID": pd.Series([], dtype=str),
            "Service_Activity": pd.Series([], dtype=str),
            "Timesheet Qty": pd.Series([], dtype=float),
            "Invoice Type": pd.Series([], dtype=str),
            "Unit Price": pd.Series([], dtype=float),
            "Total Price": pd.Series([], dtype=float),
        })

        invoices = build_all_invoices([zero_hours_jobsite], service_df)

        assert len(invoices) == 0

    def test_handles_multiple_jobsites(self, sample_service_df):
        """Handles multiple jobsites correctly."""
        jobsite1 = JobsiteHours(
            jobsite_id="JS001",
            jobsite_name="Site 1",
            customer_name="Customer 1",
            work_hours=1.0,
            allocated_drive_time=0.0,
            total_billable_hours=1.0,
            billable_rate=75.0,
            dates=["2026-01-15"],
        )
        jobsite2 = JobsiteHours(
            jobsite_id="JS002",
            jobsite_name="Site 2",
            customer_name="Customer 2",
            work_hours=2.0,
            allocated_drive_time=0.0,
            total_billable_hours=2.0,
            billable_rate=75.0,
            dates=["2026-01-16"],
        )

        invoices = build_all_invoices([jobsite1, jobsite2], sample_service_df)

        assert len(invoices) == 2
        jobsite_ids = [inv.jobsite_id for inv in invoices]
        assert "JS001" in jobsite_ids
        assert "JS002" in jobsite_ids
