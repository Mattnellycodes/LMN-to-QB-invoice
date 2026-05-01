"""Unit tests for drive-time allocation from the *SHOP pool."""

from __future__ import annotations

import pytest

from src.calculations.allocation import (
    BILLABLE_COST_CODE,
    build_shop_pool,
    compute,
    load_excluded_jobsites,
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


def test_one_foreman_three_jobs_splits_weighted():
    tasks = [
        _shop_task("Mon", "Jenna", 6.0),
        _work_task("Mon", "Jenna", 10.0, "A", "Cust A"),
        _work_task("Mon", "Jenna", 5.0, "B", "Cust B"),
        _work_task("Mon", "Jenna", 3.0, "C", "Cust C"),
    ]
    result = compute(_report(tasks))
    # Total work hours = 18; 6h shop allocated by ratio.
    assert result.rollups["A"].allocated_drive_hours == pytest.approx(6 * 10 / 18)
    assert result.rollups["B"].allocated_drive_hours == pytest.approx(6 * 5 / 18)
    assert result.rollups["C"].allocated_drive_hours == pytest.approx(6 * 3 / 18)


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

    # A: Mon weight 8/(8+4)=2/3 → share 4*2/3≈2.667, Tue share 2*6/6=2.0,
    # total ≈4.667. Work hours 8+6=14.
    a = result.rollups["A"]
    assert a.work_hours == pytest.approx(14.0)
    assert a.allocated_drive_hours == pytest.approx(4 * 8 / 12 + 2.0)
    # B: Mon weight 4/12 → share 4*4/12≈1.333, no Tue presence.
    b = result.rollups["B"]
    assert b.work_hours == pytest.approx(4.0)
    assert b.allocated_drive_hours == pytest.approx(4 * 4 / 12)


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
    # Mon: A=4h, B=2h (total 6); A share = 3 * 4/6 = 2.0. Tue: A=3h alone = 1.0.
    mon = next(r for r in breakdown if r.date == "Mon")
    tue = next(r for r in breakdown if r.date == "Tue")
    assert mon.share == pytest.approx(2.0)
    assert tue.share == pytest.approx(1.0)


def test_excluded_jobsite_does_not_receive_or_dilute_shop_pool():
    """Excluded jobsite gets its work hours but no drive share; remaining jobsites absorb the pool."""
    tasks = [
        _shop_task("Mon", "Jenna", 6.0),
        _work_task("Mon", "Jenna", 10.0, "A", "Cust A"),
        _work_task("Mon", "Jenna", 5.0, "B", "Cust B"),
        _work_task("Mon", "Jenna", 3.0, "EXCLUDED", "Maintenance Land Time"),
    ]
    result = compute(_report(tasks), excluded_from_shop=frozenset({"EXCLUDED"}))

    # EXCLUDED keeps its work hours but receives no allocation.
    excluded = result.rollups["EXCLUDED"]
    assert excluded.work_hours == pytest.approx(3.0)
    assert excluded.allocated_drive_hours == 0.0

    # A and B split the full 6-hour pool weighted by their own work hours only
    # (denominator = 10 + 5 = 15, NOT 18).
    assert result.rollups["A"].allocated_drive_hours == pytest.approx(6 * 10 / 15)
    assert result.rollups["B"].allocated_drive_hours == pytest.approx(6 * 5 / 15)


def test_load_excluded_jobsites_handles_comments_and_blanks(tmp_path):
    path = tmp_path / "no_shop_allocation.txt"
    path.write_text("# header comment\n\n5923036W\n  \n# trailing\nABC123\n")
    assert load_excluded_jobsites(path) == frozenset({"5923036W", "ABC123"})


def test_load_excluded_jobsites_missing_file_returns_empty(tmp_path):
    assert load_excluded_jobsites(tmp_path / "does_not_exist.txt") == frozenset()


def test_zero_work_hours_falls_back_to_equal_split():
    """Degenerate case: foreman has billable tasks with 0 hours at every jobsite."""
    tasks = [
        _shop_task("Mon", "Jenna", 4.0),
        _work_task("Mon", "Jenna", 0.0, "A", "Cust A"),
        _work_task("Mon", "Jenna", 0.0, "B", "Cust B"),
    ]
    result = compute(_report(tasks))
    assert result.rollups["A"].allocated_drive_hours == pytest.approx(2.0)
    assert result.rollups["B"].allocated_drive_hours == pytest.approx(2.0)


def test_hourly_rate_picked_up_from_first_non_zero_rate():
    tasks = [
        _work_task("Mon", "Jenna", 4.0, "A", "Cust A", rate=75.0),
        _work_task("Tue", "Jenna", 3.0, "A", "Cust A", rate=90.0),  # ignored
    ]
    result = compute(_report(tasks))
    # First non-zero rate wins ($75 from Mon).
    assert result.rollups["A"].hourly_rate == 75.0


def _work_task_with_notes(
    date: str, foreman: str, jobsite_id: str, notes: str, hours: float = 4.0
) -> Task:
    task = _work_task(date, foreman, hours, jobsite_id, f"Cust {jobsite_id}")
    task.notes = notes
    return task


def test_task_notes_rollup_preserves_order_and_dedupes():
    tasks = [
        _work_task_with_notes("Mon", "Jenna", "A", "Prune back shrub"),
        _work_task_with_notes("Mon", "Jenna", "A", "Prune back shrub"),  # dup
        _work_task_with_notes("Tue", "Cassie", "A", "Finished pruning"),
        _work_task_with_notes("Mon", "Jenna", "B", "Load mulch"),
        _work_task("Mon", "Kyle", 3.0, "A", "Cust A"),  # no notes — skipped
    ]
    shop_with_notes = _shop_task("Mon", "Jenna", 1.0)
    shop_with_notes.notes = "Shop note ignored"
    tasks.append(shop_with_notes)

    result = compute(_report(tasks))

    a_notes = result.rollups["A"].task_notes
    assert a_notes == [
        {"date": "Mon", "foreman": "Jenna", "notes": "Prune back shrub"},
        {"date": "Tue", "foreman": "Cassie", "notes": "Finished pruning"},
    ]
    assert result.rollups["B"].task_notes == [
        {"date": "Mon", "foreman": "Jenna", "notes": "Load mulch"},
    ]
    assert SHOP_JOBSITE_ID not in result.rollups
