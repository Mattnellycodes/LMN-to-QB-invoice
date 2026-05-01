"""Golden tests for the LMN Job History PDF parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.parsing.pdf_parser import (
    SHOP_JOBSITE_ID,
    PdfParseError,
    _walk,
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


def test_notes_captured_for_billable_tasks(report):
    """Sample PDF yields the expected count of non-empty crew notes."""
    with_notes = [t for t in report.tasks if t.notes]
    assert len(with_notes) == 22


def test_multi_line_notes_accumulated(report):
    """Notes that span multiple PDF rows must be captured in full.

    Regression for the bug where the parser only kept the row containing
    the literal 'Notes:' label and silently dropped every continuation row.
    """
    multi_line = [t for t in report.tasks if "\n" in t.notes]
    assert multi_line, (
        "Expected at least one task with a multi-line note; if this fails, "
        "the parser is dropping note continuation rows again."
    )


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


BILLION_PDF = (
    Path(__file__).resolve().parents[1]
    / "Sample Time Sheets"
    / "7473-639125667698527109.pdf"
)


@pytest.fixture(scope="module")
def billion_report():
    if not BILLION_PDF.exists():
        pytest.skip(f"Sample PDF missing: {BILLION_PDF}")
    return parse_pdf(BILLION_PDF)


def test_single_digit_day_headers_are_parsed(billion_report):
    """LMN renders single-digit days without a leading zero
    (e.g. 'Tue-Apr-7-2026'). All three tasks must be captured across
    two days, not collapsed to just the Apr-22 task."""
    assert len(billion_report.customers) == 1
    assert "5796015W" in billion_report.customers
    assert len(billion_report.tasks) == 3

    dates = sorted({t.date for t in billion_report.tasks})
    assert dates == ["Tue-Apr-7-2026", "Wed-Apr-22-2026"]

    foremen = sorted(t.foreman for t in billion_report.tasks)
    assert foremen == ["Katy Brennan", "Kree S", "Ruby Loeffelholz"]


def test_single_digit_day_task_totals(billion_report):
    by_foreman = {t.foreman: t for t in billion_report.tasks}

    ruby = by_foreman["Ruby Loeffelholz"]
    assert ruby.date == "Tue-Apr-7-2026"
    assert ruby.task_man_hrs == pytest.approx(3.13)
    assert sum(
        float(r.total_price.replace("$", "").replace(",", ""))
        for r in ruby.rates
    ) == pytest.approx(235.00)

    kree = by_foreman["Kree S"]
    assert kree.date == "Tue-Apr-7-2026"
    assert kree.task_man_hrs == pytest.approx(2.93)
    assert any(s.total_price == "$13.75" for s in kree.services)


# ---------------------------------------------------------------------------
# Unit tests for the in_notes multi-line continuation logic added in PR
# ---------------------------------------------------------------------------
#
# These tests exercise _walk() directly with synthetic page data so they run
# without any sample PDF on disk.  The synthetic structure mirrors what
# _extract_lines() would produce: pages[page][line][(x, y, text)].
#
# Each helper line is built as a list of (x, y, text) triples.  _line_text()
# joins the text parts with spaces, so the exact coordinate values don't
# matter as long as they are consistent floats.


def _ln(*texts: str, y: float = 100.0) -> list[tuple[float, float, str]]:
    """Return a synthetic PDF line: one (x, y, text) triple per token group."""
    return [(float(i * 50), y, t) for i, t in enumerate(texts)]


def _make_task_pages(note_lines: list[str], after_notes: list[str] | None = None) -> list:
    """Build a minimal single-page synthetic PDF that produces one task.

    The page contains:
      - a customer header (jobsite ID token + name)
      - a day header
      - a Task Name: row
      - a Notes: row (first element of note_lines)
      - zero or more note continuation rows (remaining note_lines)
      - any extra rows supplied via after_notes (e.g. a Services header)
    """
    # Customer header — jobsite token must match JOBSITE_ID_RE exactly.
    customer_line = _ln("Acme Corp", "9999001A", y=900.0)

    # Day header — tokens[0] must match DAY_HEADER_RE; line must contain
    # "Total Man Hrs for Day".
    day_line = _ln("Mon-Apr-13-2026", "Total Man Hrs for Day", y=800.0)

    # Task Name line
    task_line = _ln("Task Name: Mowing Foreman: Bob", y=700.0)

    lines: list[list[tuple[float, float, str]]] = [customer_line, day_line, task_line]

    # First element of note_lines is the "Notes:" row itself (may include
    # text after the label, or just "Notes:" with nothing).
    if note_lines:
        lines.append(_ln(note_lines[0], y=600.0))
        for i, cont in enumerate(note_lines[1:], start=1):
            lines.append(_ln(cont, y=float(600 - i * 20)))

    for i, extra in enumerate(after_notes or []):
        lines.append(_ln(extra, y=float(400 - i * 20)))

    # Wrap in the required pages > lines > tokens structure.
    return [lines]


def _first_task(note_lines: list[str], after_notes: list[str] | None = None):
    """Parse synthetic page data and return the first (and only) task."""
    pages = _make_task_pages(note_lines, after_notes)
    report = _walk(pages)
    assert report.tasks, "Expected at least one task from synthetic page data"
    return report.tasks[0]


class TestInNotesFlag:
    """Unit tests for the in_notes state introduced in this PR."""

    def test_single_line_note_captured(self):
        """A 'Notes:' line with inline text sets the task's notes field."""
        task = _first_task(["Notes: Trimmed hedges along fence"])
        assert task.notes == "Trimmed hedges along fence"

    def test_continuation_line_appended_with_newline(self):
        """A row following 'Notes:' is appended to notes with a newline separator."""
        task = _first_task([
            "Notes: First line of notes",
            "Second line continuation",
        ])
        assert task.notes == "First line of notes\nSecond line continuation"

    def test_multiple_continuation_lines_all_captured(self):
        """All continuation rows are accumulated in order."""
        task = _first_task([
            "Notes: Line one",
            "Line two",
            "Line three",
        ])
        assert task.notes == "Line one\nLine two\nLine three"

    def test_empty_continuation_line_is_skipped(self):
        """A blank continuation row is not appended (no spurious newlines)."""
        # Simulate an empty text element by using a string that is whitespace only;
        # _line_text strips the result, so the continuation handler sees "".
        task = _first_task([
            "Notes: Initial note",
            "   ",   # whitespace-only row — should be ignored
            "Real continuation",
        ])
        # The blank row must NOT produce an extra newline.
        assert task.notes == "Initial note\nReal continuation"
        assert "\n\n" not in task.notes

    def test_notes_label_only_no_inline_text_then_continuation(self):
        """'Notes:' with no trailing text followed by continuation row.

        When the 'Notes:' label has no inline text the initial notes value is
        empty ("").  The continuation block must use the continuation text as
        the first content rather than prepending a spurious newline.
        """
        task = _first_task([
            "Notes:",          # nothing after the label
            "Continuation text",
        ])
        assert task.notes == "Continuation text"
        assert not task.notes.startswith("\n")

    def test_services_header_stops_notes_accumulation(self):
        """Encountering a Services/Activities table header resets in_notes.

        Any row that arrives after the services header must NOT be appended
        to notes — instead it should be processed as a service row.
        """
        task = _first_task(
            ["Notes: Some crew note"],
            after_notes=["Services/Activities Total Price", "Not a note"],
        )
        # The "Not a note" row comes after the services header, so it must NOT
        # appear in notes.
        assert "Not a note" not in task.notes
        assert task.notes == "Some crew note"

    def test_rates_header_stops_notes_accumulation(self):
        """Encountering a Rates table header resets in_notes."""
        task = _first_task(
            ["Notes: Initial note"],
            after_notes=["Rates Total Price", "Should not be in notes"],
        )
        assert "Should not be in notes" not in task.notes
        assert task.notes == "Initial note"

    def test_notes_not_carried_into_next_task(self):
        """close_task() resets in_notes so continuation rows never bleed into
        a subsequent task."""
        # Build two tasks on the same page.  The second task starts with a
        # new "Task Name:" line.  Any rows between that line and the next
        # "Notes:" must not be attributed to the first task's notes.
        customer_line = _ln("Acme Corp", "9999001A", y=900.0)
        day_line = _ln("Mon-Apr-13-2026", "Total Man Hrs for Day", y=800.0)

        task1_line = _ln("Task Name: Task One Foreman: Alice", y=700.0)
        notes1_line = _ln("Notes: Task one note", y=660.0)
        cont1_line = _ln("Continuation of task one", y=640.0)

        task2_line = _ln("Task Name: Task Two Foreman: Bob", y=600.0)
        # No Notes: line for task two.
        unrelated_line = _ln("Should not appear in task one notes", y=560.0)

        page = [
            customer_line,
            day_line,
            task1_line,
            notes1_line,
            cont1_line,
            task2_line,
            unrelated_line,
        ]
        report = _walk([page])
        assert len(report.tasks) == 2

        task1 = next(t for t in report.tasks if t.task_name == "Task One")
        task2 = next(t for t in report.tasks if t.task_name == "Task Two")

        assert "Continuation of task one" in task1.notes
        assert "Should not appear in task one notes" not in task1.notes
        assert task2.notes == ""

    def test_new_day_header_resets_notes_state(self):
        """A day-header line for a new date triggers close_task, resetting in_notes."""
        customer_line = _ln("Acme Corp", "9999001A", y=900.0)
        day1_line = _ln("Mon-Apr-13-2026", "Total Man Hrs for Day", y=800.0)
        task1_line = _ln("Task Name: Monday Task Foreman: Alice", y=700.0)
        notes1_line = _ln("Notes: Monday note", y=660.0)

        # A new day header — must close the current task (resetting in_notes).
        day2_line = _ln("Tue-Apr-14-2026", "Total Man Hrs for Day", y=600.0)
        # This line appears AFTER the day change.  Since in_notes was reset by
        # close_task(), it should NOT be treated as a note continuation.
        stray_line = _ln("Task Name: Tuesday Task Foreman: Bob", y=560.0)

        page = [
            customer_line,
            day1_line,
            task1_line,
            notes1_line,
            day2_line,
            stray_line,
        ]
        report = _walk([page])
        assert len(report.tasks) == 2

        mon_task = next(t for t in report.tasks if t.date == "Mon-Apr-13-2026")
        assert mon_task.notes == "Monday note"

        tue_task = next(t for t in report.tasks if t.date == "Tue-Apr-14-2026")
        # "Tuesday Task" comes after the new day header and is a Task Name line,
        # not a note continuation.
        assert tue_task.notes == ""

    def test_inline_text_and_continuation_combined(self):
        """Inline 'Notes:' text plus continuation rows are all joined correctly."""
        task = _first_task([
            "Notes: Inline part one",
            "Continuation part two",
            "Continuation part three",
        ])
        parts = task.notes.split("\n")
        assert parts == ["Inline part one", "Continuation part two", "Continuation part three"]


class TestJobsiteIdLengths:
    """Customer-header detection must accept 6- and 7-digit LMN jobsite IDs.

    Regression for the April 2026 incident where `*Maintenance Sample- Land`
    (jobsite `665522W`, 6 digits) was not recognized as a customer header,
    causing its tasks to be silently attributed to the previous customer
    (Dowling, Margaret).
    """

    def _two_customer_pages(self, second_jobsite_id: str) -> list:
        first_header = _ln("First Customer", "9999001A", y=900.0)
        first_day = _ln("Mon-Apr-6-2026", "Total Man Hrs for Day", y=860.0)
        first_task = _ln("Task Name: First Task Foreman: Alice", y=820.0)
        first_close = _ln("Total Man Hours for Job: 8.00", y=780.0)

        second_header = _ln("Second Customer", second_jobsite_id, y=720.0)
        second_day = _ln("Tue-Apr-7-2026", "Total Man Hrs for Day", y=680.0)
        second_task = _ln("Task Name: Second Task Foreman: Bob", y=640.0)

        return [[
            first_header,
            first_day,
            first_task,
            first_close,
            second_header,
            second_day,
            second_task,
        ]]

    def test_six_digit_jobsite_id_recognized(self):
        report = _walk(self._two_customer_pages("665522W"))
        assert "665522W" in report.customers
        assert report.customers["665522W"].name == "Second Customer"
        second = next(t for t in report.tasks if t.task_name == "Second Task")
        assert second.jobsite_id == "665522W"
        assert second.customer_name == "Second Customer"

    def test_seven_digit_jobsite_id_still_recognized(self):
        report = _walk(self._two_customer_pages("5813613W"))
        assert "5813613W" in report.customers
        second = next(t for t in report.tasks if t.task_name == "Second Task")
        assert second.jobsite_id == "5813613W"
