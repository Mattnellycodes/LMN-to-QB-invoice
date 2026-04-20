"""Unit tests for drive-time allocation from the *SHOP pool."""

from __future__ import annotations

import pytest

from src.calculations.allocation import (
    BILLABLE_COST_CODE,
    build_shop_pool,
    compute,
)
from src.parsing.pdf_parser import (
    SHOP_JOBSITE_ID,
    Customer,
    ParsedReport,
    RateRow,
    Task,
)


def _shop_task(date: str, foreman: str, hours: float, task_name: str = "Drive Time") -> Task:
    return Task(
        date=date,
        customer_name="*SHOP",
        jobsite_id=SHOP_JOBSITE_ID,
        task_name=task_name,
        cost_code_num="900",
        foreman=foreman,
        task_man_hrs=hours,
    )


def _work_task(
    date: str, foreman: str, hours: float, jobsite_id: str, customer: str, rate: float = 75.0
) -> Task:
    return Task(
        date=date,
        customer_name=customer,
        jobsite_id=jobsite_id,
        task_name="General Maintenance",
        cost_code_num=BILLABLE_COST_CODE,
        foreman=foreman,
        task_man_hrs=hours,
        rates=[RateRow(description="Labor", qty=str(hours), rate=f"${rate:.2f}", total_price="")],
    )


def _report(tasks: list[Task], customers: dict[str, Customer] | None = None) -> ParsedReport:
    return ParsedReport(customers=customers or {}, tasks=tasks)


def test_shop_pool_sums_by_date_and_foreman():
    tasks = [
        _shop_task("Mon-Apr-13-2026", "Jenna", 2.0),
        _shop_task("Mon-Apr-13-2026", "Jenna", 1.5),
        _shop_task("Mon-Apr-13-2026", "Kyle", 3.0),
        _shop_task("Tue-Apr-14-2026", "Jenna", 4.0),
    ]
    pool = build_shop_pool(tasks)
    assert pool[("Mon-Apr-13-2026", "Jenna")] == pytest.approx(3.5)
    assert pool[("Mon-Apr-13-2026", "Kyle")] == pytest.approx(3.0)
    assert pool[("Tue-Apr-14-2026", "Jenna")] == pytest.approx(4.0)


def test_one_foreman_three_jobs_splits_equally():
    tasks = [
        _shop_task("Mon", "Jenna", 6.0),
        _work_task("Mon", "Jenna", 10.0, "A", "Cust A"),
        _work_task("Mon", "Jenna", 5.0, "B", "Cust B"),
        _work_task("Mon", "Jenna", 3.0, "C", "Cust C"),
    ]
    result = compute(_report(tasks))
    # 6h shop / 3 jobs = 2h each
    assert result.rollups["A"].allocated_drive_hours == pytest.approx(2.0)
    assert result.rollups["B"].allocated_drive_hours == pytest.approx(2.0)
    assert result.rollups["C"].allocated_drive_hours == pytest.approx(2.0)


def test_multi_day_aggregates_into_same_jobsite_invoice():
    """Per-day allocation; jobsite hours accumulate across days."""
    tasks = [
        # Mon: Jenna does Job A and Job B
        _shop_task("Mon", "Jenna", 4.0),
        _work_task("Mon", "Jenna", 8.0, "A", "Cust A"),
        _work_task("Mon", "Jenna", 4.0, "B", "Cust B"),
        # Tue: Jenna does only Job A (shop time all goes to A)
        _shop_task("Tue", "Jenna", 2.0),
        _work_task("Tue", "Jenna", 6.0, "A", "Cust A"),
    ]
    result = compute(_report(tasks))

    # A: Mon share 4/2=2, Tue share 2/1=2, total 4. Work hours 8+6=14.
    a = result.rollups["A"]
    assert a.work_hours == pytest.approx(14.0)
    assert a.allocated_drive_hours == pytest.approx(4.0)
    # B: Mon share 2, no Tue presence.
    b = result.rollups["B"]
    assert b.work_hours == pytest.approx(4.0)
    assert b.allocated_drive_hours == pytest.approx(2.0)


def test_foreman_with_no_billable_jobs_loses_shop_allocation():
    """Shop hours for a foreman who didn't touch any billable jobsite aren't allocated."""
    tasks = [
        _shop_task("Mon", "Ghost", 5.0),  # Ghost only drove, no customer work
        _shop_task("Mon", "Jenna", 2.0),
        _work_task("Mon", "Jenna", 8.0, "A", "Cust A"),
    ]
    result = compute(_report(tasks))
    assert result.rollups["A"].allocated_drive_hours == pytest.approx(2.0)
    # Ghost's 5 hours don't go anywhere (no billable work to attach to).


def test_shop_tasks_do_not_appear_in_rollups():
    tasks = [
        _shop_task("Mon", "Jenna", 1.0),
        _work_task("Mon", "Jenna", 4.0, "A", "Cust A"),
    ]
    result = compute(_report(tasks))
    assert SHOP_JOBSITE_ID not in result.rollups


def test_allocation_breakdown_captures_each_day():
    tasks = [
        _shop_task("Mon", "Jenna", 3.0),
        _work_task("Mon", "Jenna", 4.0, "A", "A"),
        _work_task("Mon", "Jenna", 2.0, "B", "B"),
        _shop_task("Tue", "Jenna", 1.0),
        _work_task("Tue", "Jenna", 3.0, "A", "A"),
    ]
    result = compute(_report(tasks))
    breakdown = result.rollups["A"].allocation_breakdown
    # Two allocation rows for Job A — one per day.
    dates = sorted(row.date for row in breakdown)
    assert dates == ["Mon", "Tue"]
    # Mon shop 3 / 2 jobsites = 1.5; Tue shop 1 / 1 jobsite = 1.0
    mon = next(r for r in breakdown if r.date == "Mon")
    tue = next(r for r in breakdown if r.date == "Tue")
    assert mon.share == pytest.approx(1.5)
    assert tue.share == pytest.approx(1.0)


def test_hourly_rate_picked_up_from_first_non_zero_rate():
    tasks = [
        _work_task("Mon", "Jenna", 4.0, "A", "Cust A", rate=75.0),
        _work_task("Tue", "Jenna", 3.0, "A", "Cust A", rate=90.0),  # ignored
    ]
    result = compute(_report(tasks))
    # First non-zero rate wins ($75 from Mon).
    assert result.rollups["A"].hourly_rate == 75.0
