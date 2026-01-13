"""Parse LMN CSV exports for time data and service data."""

from __future__ import annotations

from pathlib import Path
from typing import List, Union

import pandas as pd


# Required columns for time data CSV
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

# Required columns for service data CSV
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


def parse_time_data(csv_path: Union[str, Path]) -> pd.DataFrame:
    """
    Parse the LMN Job History Time Data export.

    Returns DataFrame with columns needed for labor hour calculations.
    """
    df = pd.read_csv(csv_path)

    missing = validate_columns(df, TIME_DATA_REQUIRED_COLUMNS)
    if missing:
        raise ValueError(f"Time data CSV missing required columns: {missing}")

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


def parse_service_data(csv_path: Union[str, Path]) -> pd.DataFrame:
    """
    Parse the LMN Job History Service Data export.

    Returns DataFrame with columns needed for materials/services line items.
    """
    df = pd.read_csv(csv_path)

    missing = validate_columns(df, SERVICE_DATA_REQUIRED_COLUMNS)
    if missing:
        raise ValueError(f"Service data CSV missing required columns: {missing}")

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
