"""Tests for LMN CSV parsing."""

import pytest
import pandas as pd
from io import StringIO

from src.parsing.lmn_parser import (
    TIME_DATA_REQUIRED_COLUMNS,
    SERVICE_DATA_REQUIRED_COLUMNS,
    parse_time_data,
    parse_service_data,
    validate_columns,
    filter_billable_services,
    filter_uninvoiced,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def valid_time_data_csv(tmp_path):
    """Create a valid time data CSV file."""
    csv_content = """TimesheetID,JobsiteID,Jobsite,CustomerName,TaskName,CostCode,Man Hours,Billable Rate,EndDate
TS001,JS001,123 Main St,John Smith,General Maintenance,200,2.5,$75.00,2026-01-15
TS001,JS001,123 Main St,John Smith,Drive Time,900,0.5,$0.00,2026-01-15
TS001,JS002,456 Oak Ave,Jane Doe,General Maintenance,200,3.0,$75.00,2026-01-15
"""
    csv_path = tmp_path / "time_data.csv"
    csv_path.write_text(csv_content)
    return csv_path


@pytest.fixture
def valid_service_data_csv(tmp_path):
    """Create a valid service data CSV file."""
    csv_content = """TimesheetID,JobsiteID,Jobsite,CustomerName,Service_Activity,Timesheet Qty,Invoice Type,Unit Price,Total Price,Invoiced,EndDate
TS001,JS001,123 Main St,John Smith,Mulch - Premium,5,Per Unit,$45.00,$225.00,N,2026-01-15
TS001,JS001,123 Main St,John Smith,Labor Included,1,Included,$0.00,$0.00,N,2026-01-15
TS001,JS002,456 Oak Ave,Jane Doe,Plants - Perennials,10,Per Unit,$25.00,$250.00,Y,2026-01-15
"""
    csv_path = tmp_path / "service_data.csv"
    csv_path.write_text(csv_content)
    return csv_path


# =============================================================================
# Test validate_columns
# =============================================================================


class TestValidateColumns:
    """Test validate_columns function."""

    def test_returns_empty_when_all_present(self):
        """Returns empty list when all required columns present."""
        df = pd.DataFrame(columns=["A", "B", "C", "D"])
        missing = validate_columns(df, ["A", "B"])
        assert missing == []

    def test_returns_missing_columns(self):
        """Returns list of missing columns."""
        df = pd.DataFrame(columns=["A", "B"])
        missing = validate_columns(df, ["A", "B", "C", "D"])
        assert missing == ["C", "D"]

    def test_handles_empty_required_list(self):
        """Handles empty required columns list."""
        df = pd.DataFrame(columns=["A", "B"])
        missing = validate_columns(df, [])
        assert missing == []


# =============================================================================
# Test parse_time_data
# =============================================================================


class TestParseTimeData:
    """Test parse_time_data function."""

    def test_parses_valid_csv(self, valid_time_data_csv):
        """Parses valid time data CSV successfully."""
        df = parse_time_data(valid_time_data_csv)

        assert len(df) == 3
        assert "TimesheetID" in df.columns
        assert "Man Hours" in df.columns

    def test_converts_man_hours_to_numeric(self, valid_time_data_csv):
        """Converts Man Hours column to numeric."""
        df = parse_time_data(valid_time_data_csv)

        assert df["Man Hours"].dtype in ["float64", "int64"]
        assert df["Man Hours"].iloc[0] == 2.5

    def test_cleans_billable_rate(self, valid_time_data_csv):
        """Removes $ and converts Billable Rate to numeric."""
        df = parse_time_data(valid_time_data_csv)

        assert df["Billable Rate"].dtype in ["float64", "int64"]
        assert df["Billable Rate"].iloc[0] == 75.0

    def test_converts_ids_to_string(self, valid_time_data_csv):
        """Converts JobsiteID and TimesheetID to string."""
        df = parse_time_data(valid_time_data_csv)

        assert df["JobsiteID"].dtype == object
        assert df["TimesheetID"].dtype == object

    def test_raises_on_missing_columns(self, tmp_path):
        """Raises ValueError when required columns missing."""
        csv_content = "Col1,Col2\n1,2"
        csv_path = tmp_path / "bad_time.csv"
        csv_path.write_text(csv_content)

        with pytest.raises(ValueError) as exc_info:
            parse_time_data(csv_path)

        assert "missing required columns" in str(exc_info.value).lower()


# =============================================================================
# Test parse_service_data
# =============================================================================


class TestParseServiceData:
    """Test parse_service_data function."""

    def test_parses_valid_csv(self, valid_service_data_csv):
        """Parses valid service data CSV successfully."""
        df = parse_service_data(valid_service_data_csv)

        assert len(df) == 3
        assert "Service_Activity" in df.columns

    def test_cleans_price_columns(self, valid_service_data_csv):
        """Removes $ and converts price columns to numeric."""
        df = parse_service_data(valid_service_data_csv)

        assert df["Unit Price"].dtype in ["float64", "int64"]
        assert df["Total Price"].dtype in ["float64", "int64"]
        assert df["Unit Price"].iloc[0] == 45.0
        assert df["Total Price"].iloc[0] == 225.0

    def test_converts_quantity_to_numeric(self, valid_service_data_csv):
        """Converts Timesheet Qty to numeric."""
        df = parse_service_data(valid_service_data_csv)

        assert df["Timesheet Qty"].dtype in ["float64", "int64"]
        assert df["Timesheet Qty"].iloc[0] == 5

    def test_raises_on_missing_columns(self, tmp_path):
        """Raises ValueError when required columns missing."""
        csv_content = "Col1,Col2\n1,2"
        csv_path = tmp_path / "bad_service.csv"
        csv_path.write_text(csv_content)

        with pytest.raises(ValueError) as exc_info:
            parse_service_data(csv_path)

        assert "missing required columns" in str(exc_info.value).lower()


# =============================================================================
# Test filter_billable_services
# =============================================================================


class TestFilterBillableServices:
    """Test filter_billable_services function."""

    def test_filters_by_total_price_and_invoice_type(self, valid_service_data_csv):
        """Filters to items with Total Price > 0 and Invoice Type != 'Included'."""
        df = parse_service_data(valid_service_data_csv)
        billable = filter_billable_services(df)

        # Should exclude the "Included" item
        assert len(billable) == 2
        assert all(billable["Total Price"] > 0)
        assert all(billable["Invoice Type"].str.lower() != "included")

    def test_returns_empty_when_no_billable(self, tmp_path):
        """Returns empty DataFrame when no billable items."""
        csv_content = """TimesheetID,JobsiteID,Jobsite,CustomerName,Service_Activity,Timesheet Qty,Invoice Type,Unit Price,Total Price,Invoiced,EndDate
TS001,JS001,123 Main St,John Smith,Included Item,1,Included,$0.00,$0.00,N,2026-01-15
"""
        csv_path = tmp_path / "no_billable.csv"
        csv_path.write_text(csv_content)

        df = parse_service_data(csv_path)
        billable = filter_billable_services(df)

        assert len(billable) == 0


# =============================================================================
# Test filter_uninvoiced
# =============================================================================


class TestFilterUninvoiced:
    """Test filter_uninvoiced function."""

    def test_filters_to_uninvoiced_only(self, valid_service_data_csv):
        """Filters to rows where Invoiced == 'N'."""
        df = parse_service_data(valid_service_data_csv)
        uninvoiced = filter_uninvoiced(df)

        # Third row has Invoiced='Y', should be excluded
        assert len(uninvoiced) == 2
        assert all(uninvoiced["Invoiced"].str.upper() == "N")

    def test_handles_lowercase(self, tmp_path):
        """Handles lowercase 'n' for uninvoiced."""
        csv_content = """TimesheetID,JobsiteID,Jobsite,CustomerName,Service_Activity,Timesheet Qty,Invoice Type,Unit Price,Total Price,Invoiced,EndDate
TS001,JS001,123 Main St,John Smith,Item,1,Per Unit,$10.00,$10.00,n,2026-01-15
"""
        csv_path = tmp_path / "lowercase.csv"
        csv_path.write_text(csv_content)

        df = parse_service_data(csv_path)
        uninvoiced = filter_uninvoiced(df)

        assert len(uninvoiced) == 1
