"""Parse LMN Excel/CSV exports for time data and service data."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import List, Literal, Union

import pandas as pd


# Required columns for time data
TIME_DATA_REQUIRED_COLUMNS = [
    "TimesheetID",
    "JobsiteID",
    "Jobsite",
    "CustomerName",
    "TaskName",
    "CostCode",
    "Man Hours",
    "Billable Rate",
    "EndDate",
]

# Required columns for service data
SERVICE_DATA_REQUIRED_COLUMNS = [
    "TimesheetID",
    "JobsiteID",
    "Jobsite",
    "CustomerName",
    "Service_Activity",
    "Timesheet Qty",
    "Invoice Type",
    "Unit Price",
    "Total Price",
    "Invoiced",
    "EndDate",
]


FileType = Literal["time_data", "service_data"]


def get_file_extension(filename: str) -> str:
    """Get lowercase file extension from filename."""
    return Path(filename).suffix.lower()


def is_excel_file(filename: str) -> bool:
    """Check if filename indicates an Excel file."""
    return get_file_extension(filename) in (".xlsx", ".xls")


def is_csv_file(filename: str) -> bool:
    """Check if filename indicates a CSV file."""
    return get_file_extension(filename) == ".csv"


def detect_file_type(filename: str, file_content: Union[BytesIO, None] = None) -> FileType:
    """
    Detect whether a file contains time data or service data.

    Detection order:
    1. Filename contains 'time' -> time_data
    2. Filename contains 'service' -> service_data
    3. Fallback: Check which required columns are present in the file

    Args:
        filename: Name of the file
        file_content: Optional BytesIO with file content for column-based detection

    Returns:
        'time_data' or 'service_data'

    Raises:
        ValueError: If file type cannot be determined
    """
    filename_lower = filename.lower()

    # Check filename first
    if "time" in filename_lower:
        return "time_data"
    if "service" in filename_lower:
        return "service_data"

    # Fallback: check columns if file content provided
    if file_content is not None:
        file_content.seek(0)
        try:
            # Read just headers based on file type
            if is_excel_file(filename):
                df = pd.read_excel(file_content, nrows=0)
            else:
                df = pd.read_csv(file_content, nrows=0)
            file_content.seek(0)  # Reset for later reading

            columns = set(df.columns)

            # Check for unique columns that distinguish the file types
            has_taskname = "TaskName" in columns
            has_costcode = "CostCode" in columns
            has_service_activity = "Service_Activity" in columns
            has_invoice_type = "Invoice Type" in columns

            if (has_taskname or has_costcode) and not has_service_activity:
                return "time_data"
            if (has_service_activity or has_invoice_type) and not has_taskname:
                return "service_data"

            # Check by column overlap
            time_cols = set(TIME_DATA_REQUIRED_COLUMNS)
            service_cols = set(SERVICE_DATA_REQUIRED_COLUMNS)
            time_overlap = len(columns & time_cols)
            service_overlap = len(columns & service_cols)

            if time_overlap > service_overlap:
                return "time_data"
            if service_overlap > time_overlap:
                return "service_data"
        except Exception:
            pass

    raise ValueError(
        f"Could not detect file type for '{filename}'. "
        "Please ensure the filename contains 'time' or 'service'. "
        "See the instructions for how to download the correct files from LMN."
    )


def read_data_file(
    source: Union[str, Path, BytesIO],
    filename: str = "",
) -> pd.DataFrame:
    """
    Read a data file (CSV or Excel) into a DataFrame.

    Args:
        source: File path or BytesIO containing file data
        filename: Original filename (used to detect format when source is BytesIO)

    Returns:
        DataFrame with file contents
    """
    # Determine if source is a file path or BytesIO
    if isinstance(source, (str, Path)):
        path = Path(source)
        filename = path.name if not filename else filename
        if is_excel_file(filename):
            return pd.read_excel(path)
        else:
            return pd.read_csv(path)
    else:
        # BytesIO - use filename to determine format
        source.seek(0)
        if is_excel_file(filename):
            return pd.read_excel(source)
        else:
            # Decode bytes to string for CSV
            content = source.read().decode("utf-8")
            source.seek(0)
            from io import StringIO
            return pd.read_csv(StringIO(content))


def parse_time_data(
    source: Union[str, Path, BytesIO],
    filename: str = "",
) -> pd.DataFrame:
    """
    Parse the LMN Job History Time Data export.

    Args:
        source: File path or BytesIO containing file data
        filename: Original filename (used to detect format when source is BytesIO)

    Returns:
        DataFrame with columns needed for labor hour calculations.
    """
    df = read_data_file(source, filename)

    missing = validate_columns(df, TIME_DATA_REQUIRED_COLUMNS)
    if missing:
        raise ValueError(f"Time data file missing required columns: {missing}")

    # Clean up numeric columns
    df["Man Hours"] = pd.to_numeric(df["Man Hours"], errors="coerce").fillna(0)
    df["Billable Rate"] = (
        df["Billable Rate"]
        .replace(r"[\$,]", "", regex=True)
        .pipe(pd.to_numeric, errors="coerce")
        .fillna(0)
    )

    # Ensure JobsiteID is string for consistent matching
    df["JobsiteID"] = df["JobsiteID"].astype(str)
    df["TimesheetID"] = df["TimesheetID"].astype(str)

    return df


def parse_service_data(
    source: Union[str, Path, BytesIO],
    filename: str = "",
) -> pd.DataFrame:
    """
    Parse the LMN Job History Service Data export.

    Args:
        source: File path or BytesIO containing file data
        filename: Original filename (used to detect format when source is BytesIO)

    Returns:
        DataFrame with columns needed for materials/services line items.
    """
    df = read_data_file(source, filename)

    missing = validate_columns(df, SERVICE_DATA_REQUIRED_COLUMNS)
    if missing:
        raise ValueError(f"Service data file missing required columns: {missing}")

    # Clean up numeric columns (handle dollar signs and commas)
    for col in ["Unit Price", "Total Price", "Unit Cost"]:
        if col in df.columns:
            df[col] = (
                df[col]
                .replace(r"[\$,]", "", regex=True)
                .pipe(pd.to_numeric, errors="coerce")
                .fillna(0)
            )

    df["Timesheet Qty"] = pd.to_numeric(df["Timesheet Qty"], errors="coerce").fillna(0)

    # Ensure JobsiteID is string for consistent matching
    df["JobsiteID"] = df["JobsiteID"].astype(str)
    df["TimesheetID"] = df["TimesheetID"].astype(str)

    return df


def validate_columns(df: pd.DataFrame, required: List[str]) -> List[str]:
    """Return list of missing required columns."""
    return [col for col in required if col not in df.columns]


def filter_billable_services(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filter service data to only billable items.

    Billable means: Total Price > 0 AND Invoice Type != 'Included'
    """
    return df[
        (df["Total Price"] > 0) &
        (df["Invoice Type"].str.lower() != "included")
    ].copy()


def filter_uninvoiced(df: pd.DataFrame) -> pd.DataFrame:
    """Filter to only rows that haven't been invoiced yet."""
    return df[df["Invoiced"].str.upper() == "N"].copy()
