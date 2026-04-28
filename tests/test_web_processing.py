"""Tests for web-facing multi-PDF upload processing."""

from __future__ import annotations

import pytest

from src.parsing.pdf_parser import Customer, ParsedReport, Task
from src.web_processing import (
    ProcessingError,
    UploadedPdf,
    _reject_overlapping_tasks,
    process_uploaded_pdfs,
)


def _task(
    jobsite_id: str = "5843557W",
    date: str = "Mon-Apr-13-2026",
    foreman: str = "Jenna Andrews",
    task_name: str = "General Maintenance",
    start_time: str = "8:00 AM",
    end_time: str = "10:00 AM",
    hours: float = 2.0,
) -> Task:
    return Task(
        date=date,
        customer_name="Customer A",
        jobsite_id=jobsite_id,
        task_name=task_name,
        cost_code_num="200",
        start_time=start_time,
        end_time=end_time,
        foreman=foreman,
        task_man_hrs=hours,
    )


def _report(task: Task, customer_name: str = "Customer A") -> ParsedReport:
    return ParsedReport(
        customers={
            task.jobsite_id: Customer(jobsite_id=task.jobsite_id, name=customer_name)
        },
        tasks=[task],
    )


def test_exact_duplicate_pdf_content_is_rejected_before_parsing():
    files = [
        UploadedPdf(filename="week-one.pdf", content=b"same bytes"),
        UploadedPdf(filename="week-one-copy.pdf", content=b"same bytes"),
    ]

    with pytest.raises(ProcessingError, match="Duplicate PDF uploaded"):
        process_uploaded_pdfs(files)


def test_overlapping_task_across_different_pdfs_is_rejected():
    task = _task()
    reports = [
        ("a.pdf", _report(task)),
        ("b.pdf", _report(_task())),
    ]

    with pytest.raises(ProcessingError, match="Overlapping task"):
        _reject_overlapping_tasks(reports)


def test_same_task_repeated_inside_one_pdf_does_not_reject():
    task = _task()
    report = ParsedReport(customers={}, tasks=[task, _task()])

    _reject_overlapping_tasks([("a.pdf", report)])


def test_distinct_pdfs_are_merged_before_processing(monkeypatch):
    reports = {
        b"a": _report(_task(jobsite_id="5843557W")),
        b"b": _report(_task(jobsite_id="5843558W", foreman="Cassie")),
    }

    def fake_parse_pdf(stream):
        return reports[stream.read()]

    captured = {}

    def fake_process_parsed_report(report, upload_label, t0=None):
        captured["report"] = report
        captured["upload_label"] = upload_label
        return {"summary": {"total_jobsites": 2}}

    monkeypatch.setattr("src.web_processing.parse_pdf", fake_parse_pdf)
    monkeypatch.setattr(
        "src.web_processing._process_parsed_report",
        fake_process_parsed_report,
    )

    result = process_uploaded_pdfs(
        [
            UploadedPdf(filename="a.pdf", content=b"a"),
            UploadedPdf(filename="b.pdf", content=b"b"),
        ]
    )

    assert result["summary"]["total_jobsites"] == 2
    assert captured["upload_label"] == "2 PDFs"
    assert sorted(captured["report"].customers) == ["5843557W", "5843558W"]
    assert len(captured["report"].tasks) == 2
