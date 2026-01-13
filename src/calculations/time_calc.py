"""Calculate billable hours including drive time allocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import pandas as pd


@dataclass
class JobsiteHours:
    """Billable hours breakdown for a single jobsite."""

    jobsite_id: str
    jobsite_name: str
    customer_name: str
    work_hours: float
    allocated_drive_time: float
    total_billable_hours: float
    billable_rate: float
    dates: List[str]


def is_drive_time(cost_code: str) -> bool:
    """Check if a cost code represents drive time (unbillable/overhead)."""
    return "900" in str(cost_code)


def is_billable_work(cost_code: str) -> bool:
    """Check if a cost code represents billable work (grounds maintenance)."""
    return "200" in str(cost_code)


def calculate_drive_time_allocation(time_df: pd.DataFrame) -> Dict[str, Dict[str, float]]:
    """
    Calculate drive time allocation per jobsite for each timesheet.

    Formula: Allocated Drive Time = Total Drive Hours / Number of Unique Jobsites

    Returns:
        {timesheet_id: {jobsite_id: allocated_hours}}
    """
    allocation = {}

    for timesheet_id, group in time_df.groupby("TimesheetID"):
        # Get total drive time hours for this timesheet
        drive_time_mask = group["CostCode"].apply(is_drive_time)
        total_drive_hours = group.loc[drive_time_mask, "Man Hours"].sum()

        # Get unique jobsites in this timesheet
        unique_jobsites = group["JobsiteID"].unique().tolist()
        num_jobsites = len(unique_jobsites)

        if num_jobsites == 0:
            continue

        # Allocate drive time equally
        hours_per_jobsite = total_drive_hours / num_jobsites

        allocation[timesheet_id] = {
            jobsite_id: hours_per_jobsite for jobsite_id in unique_jobsites
        }

    return allocation


def calculate_work_hours_by_jobsite(time_df: pd.DataFrame) -> Dict[str, float]:
    """
    Calculate total work hours (non-drive time) per jobsite.

    Returns:
        {jobsite_id: total_work_hours}
    """
    # Filter to billable work only (CostCode 200)
    billable_mask = time_df["CostCode"].apply(is_billable_work)
    billable_df = time_df[billable_mask]

    return billable_df.groupby("JobsiteID")["Man Hours"].sum().to_dict()


def calculate_billable_hours(time_df: pd.DataFrame) -> List[JobsiteHours]:
    """
    Calculate total billable hours per jobsite including allocated drive time.

    Returns list of JobsiteHours with complete breakdown.
    """
    drive_allocation = calculate_drive_time_allocation(time_df)
    work_hours = calculate_work_hours_by_jobsite(time_df)

    # Aggregate drive time allocation across all timesheets per jobsite
    jobsite_drive_time: Dict[str, float] = {}
    for timesheet_allocations in drive_allocation.values():
        for jobsite_id, hours in timesheet_allocations.items():
            jobsite_drive_time[jobsite_id] = (
                jobsite_drive_time.get(jobsite_id, 0) + hours
            )

    # Get jobsite metadata (name, customer, rate, dates)
    jobsite_meta = get_jobsite_metadata(time_df)

    results = []
    all_jobsites = set(work_hours.keys()) | set(jobsite_drive_time.keys())

    for jobsite_id in all_jobsites:
        work = work_hours.get(jobsite_id, 0)
        drive = jobsite_drive_time.get(jobsite_id, 0)
        meta = jobsite_meta.get(jobsite_id, {})

        results.append(
            JobsiteHours(
                jobsite_id=jobsite_id,
                jobsite_name=meta.get("jobsite_name", ""),
                customer_name=meta.get("customer_name", ""),
                work_hours=round(work, 2),
                allocated_drive_time=round(drive, 2),
                total_billable_hours=round(work + drive, 2),
                billable_rate=meta.get("billable_rate", 0),
                dates=meta.get("dates", []),
            )
        )

    return results


def get_jobsite_metadata(time_df: pd.DataFrame) -> Dict[str, dict]:
    """
    Extract metadata for each jobsite (name, customer, rate, dates).

    Returns:
        {jobsite_id: {jobsite_name, customer_name, billable_rate, dates}}
    """
    metadata = {}

    for jobsite_id, group in time_df.groupby("JobsiteID"):
        # Get the first non-drive-time row for rate (drive time rows may have different rates)
        billable_rows = group[group["CostCode"].apply(is_billable_work)]
        if not billable_rows.empty:
            rate = billable_rows["Billable Rate"].iloc[0]
        else:
            rate = group["Billable Rate"].iloc[0]

        # Collect unique dates
        dates = sorted(group["EndDate"].dropna().unique().tolist())

        metadata[jobsite_id] = {
            "jobsite_name": group["Jobsite"].iloc[0],
            "customer_name": group["CustomerName"].iloc[0],
            "billable_rate": rate,
            "dates": dates,
        }

    return metadata
