"""Parser for LMN 'Job History (All Details)' PDF reports.

Extracts customers (jobsites) and task blocks (with services, rates, notes)
from the single-PDF LMN export. The `*SHOP` jobsite (5613100W) contains
CostCode 900 Land/Drive Time entries that are allocated across billable
jobsites downstream; billable work lives under other jobsite blocks.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Union

import pypdfium2 as pdfium

logger = logging.getLogger(__name__)


SHOP_JOBSITE_ID = "5613100W"

DAY_HEADER_RE = re.compile(
    r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)-"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)-\d{1,2}-\d{4}$"
)
JOBSITE_ID_RE = re.compile(r"^\d{7}[A-Z]$")
DATE_RANGE_LINE_RE = re.compile(
    r"^[A-Z][a-z]{2}-\d{1,2}-\d{4} to [A-Z][a-z]{2}-\d{1,2}-\d{4}"
)

_FIELD_LABELS = (
    "Foreman:",
    "# of Staff:",
    "Task Man Hrs:",
    "Cost Code:",
    "Start Time:",
    "End Time:",
    "Task Name:",
    "Total Man Hrs for Day:",
)


@dataclass
class LineItem:
    description: str
    act_qty: str = ""
    est_cost: str = ""
    inv_qty: str = ""
    rate: str = ""
    total_price: str = ""


@dataclass
class RateRow:
    description: str
    qty: str = ""
    rate: str = ""
    total_price: str = ""


@dataclass
class Customer:
    jobsite_id: str
    name: str
    address: str = ""


@dataclass
class Task:
    date: str
    customer_name: str
    jobsite_id: str
    task_name: str = ""
    cost_code_num: str = ""
    cost_code_desc: str = ""
    start_time: str = ""
    end_time: str = ""
    foreman: str = ""
    num_staff: str = ""
    task_man_hrs: float = 0.0
    notes: str = ""
    services: list[LineItem] = field(default_factory=list)
    rates: list[RateRow] = field(default_factory=list)


@dataclass
class ParsedReport:
    customers: dict[str, Customer]
    tasks: list[Task]


class PdfParseError(ValueError):
    """Raised when the PDF can't be parsed into the expected structure."""


def parse_pdf(source: Union[str, Path, bytes, BinaryIO]) -> ParsedReport:
    """Parse an LMN Job History PDF into a ParsedReport.

    Accepts a filesystem path, raw bytes, or a readable binary stream.
    """
    pdf_bytes = _read_source(source)
    try:
        pages_lines = _extract_lines(pdf_bytes)
    except pdfium.PdfiumError as e:
        raise PdfParseError(f"Could not read PDF: {e}") from e
    logger.debug("Extracted %d pages from PDF", len(pages_lines))
    report = _walk(pages_lines)
    _validate(report)
    logger.debug(
        "Walk complete: customers=%d tasks=%d",
        len(report.customers),
        len(report.tasks),
    )
    return report


def _read_source(source: Union[str, Path, bytes, BinaryIO]) -> bytes:
    if isinstance(source, bytes):
        return source
    if isinstance(source, (str, Path)):
        return Path(source).read_bytes()
    data = source.read()
    if isinstance(data, str):
        raise PdfParseError("PDF source must be binary, got text stream.")
    return data


def _extract_lines(pdf_bytes: bytes) -> list[list[list[tuple[float, float, str]]]]:
    """Return [page][line][(x, y, text)] with lines grouped by y-coordinate."""
    doc = pdfium.PdfDocument(BytesIO(pdf_bytes))
    pages: list[list[list[tuple[float, float, str]]]] = []
    try:
        for page in doc:
            tp = page.get_textpage()
            try:
                items: list[tuple[float, float, str]] = []
                for i in range(tp.count_rects()):
                    left, bottom, right, top = tp.get_rect(i)
                    text = tp.get_text_bounded(left, bottom, right, top).strip()
                    if text:
                        items.append((left, top, text))
                # Sort by y descending (PDF origin at bottom-left), then x ascending.
                items.sort(key=lambda it: (-it[1], it[0]))
                lines: list[list[tuple[float, float, str]]] = []
                current_y: float | None = None
                for x, y, text in items:
                    if current_y is None or abs(y - current_y) > 3:
                        lines.append([])
                        current_y = y
                    lines[-1].append((x, y, text))
                for line in lines:
                    line.sort(key=lambda it: it[0])
                pages.append(lines)
            finally:
                tp.close()
    finally:
        doc.close()
    return pages


def _line_text(line: list[tuple[float, float, str]]) -> str:
    return " ".join(t for _, _, t in line).strip()


def _get_field(line_str: str, label: str) -> str | None:
    """Extract the value after a field label, stopping at the next known label."""
    idx = line_str.find(label)
    if idx < 0:
        return None
    rest = line_str[idx + len(label):].strip()
    for nxt in _FIELD_LABELS:
        if nxt == label:
            continue
        j = rest.find(nxt)
        if j > 0:
            rest = rest[:j].strip()
    return rest or None


def _walk(pages: list[list[list[tuple[float, float, str]]]]) -> ParsedReport:
    customers: dict[str, Customer] = {}
    tasks: list[Task] = []

    current_customer_name: str | None = None
    current_jobsite_id: str | None = None
    current_date: str | None = None
    current_task: Task | None = None
    in_services_table = False
    in_rates_table = False
    in_notes = False

    def close_task() -> None:
        nonlocal current_task, in_services_table, in_rates_table, in_notes
        if current_task is not None:
            tasks.append(current_task)
        current_task = None
        in_services_table = False
        in_rates_table = False
        in_notes = False

    total_pages = len(pages)
    for page_idx, lines in enumerate(pages):
        for line in lines:
            s = _line_text(line)
            if not s:
                continue
            if _is_boilerplate(s, page_idx, total_pages):
                continue

            tokens = s.split()

            # Customer header: a line with a jobsite ID at the right side.
            jobsite_match = next(
                (t for _, _, t in line if JOBSITE_ID_RE.match(t)), None
            )
            if jobsite_match and "Task Name" not in s:
                close_task()
                name = " ".join(
                    t for _, _, t in line if not JOBSITE_ID_RE.match(t)
                ).strip()
                current_customer_name = name
                current_jobsite_id = jobsite_match
                customers.setdefault(
                    jobsite_match, Customer(jobsite_id=jobsite_match, name=name)
                )
                continue

            # Customer address line (immediately after header, before any day)
            if (
                current_customer_name
                and current_jobsite_id
                and customers[current_jobsite_id].address == ""
                and current_date is None
                and any(tag in s for tag in ("Montana", "MT,", ", MT "))
            ):
                customers[current_jobsite_id].address = s
                continue

            # Day header: may repeat at the top of a continuation page; only
            # close the open task if the date actually changes.
            if tokens and DAY_HEADER_RE.match(tokens[0]) and "Total Man Hrs for Day" in s:
                if tokens[0] != current_date:
                    close_task()
                    current_date = tokens[0]
                continue

            if s.startswith("Total Man Hours for Job"):
                close_task()
                current_date = None
                continue

            # Task Name line starts a new task.
            if "Task Name:" in s and current_customer_name and current_date:
                close_task()
                current_task = Task(
                    date=current_date,
                    customer_name=current_customer_name,
                    jobsite_id=current_jobsite_id or "",
                )
                val = _get_field(s, "Task Name:")
                if val:
                    current_task.task_name = val
                foreman = _get_field(s, "Foreman:")
                if foreman:
                    current_task.foreman = foreman
                continue

            if current_task is None:
                continue

            if "Cost Code:" in s:
                val = _get_field(s, "Cost Code:")
                if val:
                    m = re.match(r"^(\d+)\s+(.+)$", val)
                    if m:
                        current_task.cost_code_num = m.group(1)
                        current_task.cost_code_desc = m.group(2)
                    else:
                        current_task.cost_code_desc = val
                staff = _get_field(s, "# of Staff:")
                if staff:
                    current_task.num_staff = staff
                continue

            if "Start Time:" in s:
                val = _get_field(s, "Start Time:")
                if val:
                    current_task.start_time = val
                hrs = _get_field(s, "Task Man Hrs:")
                if hrs:
                    try:
                        current_task.task_man_hrs = float(hrs)
                    except ValueError:
                        logger.warning(
                            "Skipping invalid Task Man Hrs=%r on task=%r "
                            "jobsite=%s",
                            hrs,
                            current_task.task_name,
                            current_task.jobsite_id,
                        )
                continue

            if "End Time:" in s:
                val = _get_field(s, "End Time:")
                if val:
                    current_task.end_time = val
                continue

            if s.startswith("Foreman:") and not current_task.foreman:
                val = _get_field(s, "Foreman:")
                if val:
                    current_task.foreman = val
                continue

            if s.startswith("Notes:"):
                current_task.notes = s[len("Notes:"):].strip()
                in_notes = True
                continue

            if s.startswith("Services/Activities") and "Total Price" in s:
                in_services_table = True
                in_rates_table = False
                in_notes = False
                continue
            if s.startswith("Rates") and "Total Price" in s:
                in_rates_table = True
                in_services_table = False
                in_notes = False
                continue

            if in_services_table:
                if s.startswith("Total") and not s.startswith("Total Billable"):
                    in_services_table = False
                    continue
                parts = [t for _, _, t in line]
                if len(parts) >= 6:
                    current_task.services.append(
                        LineItem(
                            description=" ".join(parts[:-5]).strip(),
                            act_qty=parts[-5],
                            est_cost=parts[-4],
                            inv_qty=parts[-3],
                            rate=parts[-2],
                            total_price=parts[-1],
                        )
                    )
                elif len(parts) >= 2:
                    logger.warning(
                        "Skipping malformed service row (%d tokens) on task=%r "
                        "jobsite=%s: %s",
                        len(parts),
                        current_task.task_name,
                        current_task.jobsite_id,
                        " | ".join(parts),
                    )
                continue

            if in_rates_table:
                if s.startswith("Total Billable Rates"):
                    in_rates_table = False
                    continue
                parts = [t for _, _, t in line]
                if len(parts) >= 4:
                    current_task.rates.append(
                        RateRow(
                            description=" ".join(parts[:-3]).strip(),
                            qty=parts[-3],
                            rate=parts[-2],
                            total_price=parts[-1],
                        )
                    )
                elif len(parts) >= 2:
                    logger.warning(
                        "Skipping malformed rate row (%d tokens) on task=%r "
                        "jobsite=%s: %s",
                        len(parts),
                        current_task.task_name,
                        current_task.jobsite_id,
                        " | ".join(parts),
                    )
                continue

            if in_notes:
                continuation = s.strip()
                if continuation:
                    current_task.notes = (
                        f"{current_task.notes}\n{continuation}"
                        if current_task.notes
                        else continuation
                    )
                continue

    close_task()
    return ParsedReport(customers=customers, tasks=tasks)


def _is_boilerplate(s: str, page_idx: int, total_pages: int) -> bool:
    if s.startswith(("Job History", "Valley of the Flowers")):
        return True
    if s.startswith(("Task Name Like:", "Date Range:", "Job: GROUP")):
        return True
    if s.startswith(("Activity Pricing:", "Generated On:")):
        return True
    if s.endswith(f"Page {page_idx + 1} of {total_pages}"):
        return True
    if DATE_RANGE_LINE_RE.match(s):
        return True
    return False


def _validate(report: ParsedReport) -> None:
    # *SHOP is optional — reports for groups/date-ranges without shop activity
    # are still valid; callers can detect its absence and warn that no drive
    # time will be allocated.
    if not report.tasks:
        raise PdfParseError("No tasks parsed from PDF.")


def parse_money(s: str) -> float:
    """Parse strings like '$1,234.56' or '13.75' to float; return 0 on failure."""
    if not s:
        return 0.0
    cleaned = s.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def parse_qty(s: str) -> float:
    """Parse a quantity string to float; return 0 on failure."""
    if not s:
        return 0.0
    try:
        return float(s.replace(",", "").strip())
    except ValueError:
        return 0.0
