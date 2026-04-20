"""Golden tests for the LMN Job History PDF parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.parsing.pdf_parser import (
    SHOP_JOBSITE_ID,
    PdfParseError,
    parse_pdf,
)


SAMPLE_PDF = Path(__file__).resolve().parents[1] / "Sample Time Sheets" / "NewSampleData.pdf"


@pytest.fixture(scope="module")
def report():
    if not SAMPLE_PDF.exists():
        pytest.skip(f"Sample PDF missing: {SAMPLE_PDF}")
    return parse_pdf(SAMPLE_PDF)


def test_finds_shop_and_billable_customers(report):
    assert SHOP_JOBSITE_ID in report.customers
    # One *SHOP + nine billable jobsites in this week's sample.
    assert len(report.customers) == 10


def test_total_task_count(report):
    assert len(report.tasks) == 74


def test_shop_tasks_are_cost_code_900(report):
    shop_tasks = [t for t in report.tasks if t.jobsite_id == SHOP_JOBSITE_ID]
    assert shop_tasks, "Expected at least one *SHOP task"
    assert all(t.cost_code_num == "900" for t in shop_tasks)


def test_billable_tasks_are_cost_code_200(report):
    billable = [t for t in report.tasks if t.jobsite_id != SHOP_JOBSITE_ID]
    # All customer tasks in this sample are General Maintenance / Land Time at 200.
    assert all(t.cost_code_num == "200" for t in billable)


def test_harris_monday_general_maintenance_task(report):
    """Anchor test for a specific known task — guards against parser regressions."""
    matches = [
        t for t in report.tasks
        if t.jobsite_id == "5843557W"
        and t.date == "Mon-Apr-13-2026"
        and t.foreman == "Jenna Andrews"
    ]
    assert len(matches) == 1
    task = matches[0]
    assert task.task_name == "General Maintenance"
    assert task.task_man_hrs == pytest.approx(14.77)
    assert task.notes.startswith("Cut back cottoneaster")
    assert len(task.services) >= 9


def test_page_break_continuation_not_dropped(report):
    """Shop tasks whose fields span a page break must still be captured."""
    shop_tasks = [t for t in report.tasks if t.jobsite_id == SHOP_JOBSITE_ID]
    missing = [t for t in shop_tasks if t.task_man_hrs == 0.0]
    assert missing == [], f"Found shop tasks with 0 hours (likely page-break drops): {missing}"


def test_parse_from_bytes():
    data = SAMPLE_PDF.read_bytes()
    report = parse_pdf(data)
    assert SHOP_JOBSITE_ID in report.customers


def test_unreadable_pdf_raises():
    """Non-PDF bytes still raise — we only relaxed the *SHOP-presence check."""
    with pytest.raises(PdfParseError):
        parse_pdf(b"%PDF-1.4\n%% not a real pdf\n")
