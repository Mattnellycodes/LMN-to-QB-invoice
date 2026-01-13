"""Tests for time calculation functions."""

import pandas as pd
import pytest

from src.calculations.time_calc import (
    is_drive_time,
    is_billable_work,
    calculate_drive_time_allocation,
    calculate_work_hours_by_jobsite,
    calculate_billable_hours,
)


def test_is_drive_time():
    assert is_drive_time("900 Unbillable/Overhead") is True
    assert is_drive_time("900") is True
    assert is_drive_time("200 Grounds Maintenance") is False
    assert is_drive_time("200") is False


def test_is_billable_work():
    assert is_billable_work("200 Grounds Maintenance") is True
    assert is_billable_work("200") is True
    assert is_billable_work("900 Unbillable/Overhead") is False
    assert is_billable_work("900") is False


def test_calculate_drive_time_allocation_single_timesheet():
    """Test drive time split equally among jobsites in a timesheet."""
    df = pd.DataFrame({
        "TimesheetID": ["T1", "T1", "T1", "T1"],
        "JobsiteID": ["J1", "J1", "J2", "J3"],
        "CostCode": ["200", "900", "200", "200"],
        "Man Hours": [2.0, 3.0, 2.0, 2.0],  # 3 hours drive time
    })

    allocation = calculate_drive_time_allocation(df)

    # 3 hours drive time / 3 jobsites = 1 hour each
    assert allocation["T1"]["J1"] == 1.0
    assert allocation["T1"]["J2"] == 1.0
    assert allocation["T1"]["J3"] == 1.0


def test_calculate_drive_time_allocation_multiple_timesheets():
    """Test drive time allocation across multiple timesheets."""
    df = pd.DataFrame({
        "TimesheetID": ["T1", "T1", "T2", "T2"],
        "JobsiteID": ["J1", "J2", "J1", "J3"],
        "CostCode": ["200", "900", "200", "900"],
        "Man Hours": [2.0, 2.0, 2.0, 4.0],
    })

    allocation = calculate_drive_time_allocation(df)

    # T1: 2 hours / 2 jobsites = 1 hour each
    assert allocation["T1"]["J1"] == 1.0
    assert allocation["T1"]["J2"] == 1.0

    # T2: 4 hours / 2 jobsites = 2 hours each
    assert allocation["T2"]["J1"] == 2.0
    assert allocation["T2"]["J3"] == 2.0


def test_calculate_work_hours_by_jobsite():
    """Test work hours summed per jobsite."""
    df = pd.DataFrame({
        "TimesheetID": ["T1", "T1", "T1", "T1"],
        "JobsiteID": ["J1", "J1", "J2", "J1"],
        "CostCode": ["200", "200", "200", "900"],  # Last one is drive time
        "Man Hours": [2.0, 3.0, 4.0, 1.0],
    })

    work_hours = calculate_work_hours_by_jobsite(df)

    # J1: 2 + 3 = 5 (drive time excluded)
    assert work_hours["J1"] == 5.0
    # J2: 4
    assert work_hours["J2"] == 4.0


def test_calculate_billable_hours_integration():
    """Integration test combining work hours and drive time."""
    df = pd.DataFrame({
        "TimesheetID": ["T1", "T1", "T1", "T1"],
        "JobsiteID": ["J1", "J2", "J1", "J2"],
        "Jobsite": ["Site 1", "Site 2", "Site 1", "Site 2"],
        "CustomerName": ["Customer A", "Customer B", "Customer A", "Customer B"],
        "CostCode": ["200", "200", "900", "900"],
        "Man Hours": [3.0, 2.0, 1.0, 1.0],  # 2 hours total drive time
        "Billable Rate": [75.0, 80.0, 75.0, 80.0],
        "EndDate": ["2026-01-05", "2026-01-05", "2026-01-05", "2026-01-05"],
    })

    results = calculate_billable_hours(df)

    # Should have 2 jobsites
    assert len(results) == 2

    # Find J1 result
    j1 = next(r for r in results if r.jobsite_id == "J1")
    assert j1.work_hours == 3.0
    assert j1.allocated_drive_time == 1.0  # 2 hours / 2 jobsites
    assert j1.total_billable_hours == 4.0
    assert j1.billable_rate == 75.0

    # Find J2 result
    j2 = next(r for r in results if r.jobsite_id == "J2")
    assert j2.work_hours == 2.0
    assert j2.allocated_drive_time == 1.0
    assert j2.total_billable_hours == 3.0
    assert j2.billable_rate == 80.0
