"""Turn raw sheet rows (lists of cell values) into client records.

Works on rows from either backend: the Google Sheets API or a local .xlsx
snapshot. Header rows are located by label so the tools survive Josh adding
rows above the table.
"""

from __future__ import annotations

import re

from .matching import IrrigationClient, ScheduleClient

_PAST_INACTIVE = re.compile(r"past\s*/?\s*inactive", re.IGNORECASE)
_BLOWOUT_ONLY = re.compile(
    r"blow\s*-?\s*out\s+only\s+clients|blow\s+only\s+clients", re.IGNORECASE
)
_MAINTENANCE_ONLY = re.compile(r"mainten[ae]nce\s+only|maintence\s+only", re.IGNORECASE)
_PURE_NUMBER = re.compile(r"[\d.\s]+$")


def _cell(value: object) -> str:
    return str(value).strip() if value is not None else ""


def find_header_row(rows: list[list[object]], label: str) -> tuple[int, int]:
    """(row index, column index) of the first cell equal to `label` (case-insensitive)."""
    want = label.strip().lower()
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            if _cell(value).lower() == want:
                return r, c
    raise ValueError(f"no header cell {label!r} found")


def _column_index(rows: list[list[object]], header_row: int, label: str) -> int | None:
    for c, value in enumerate(rows[header_row]):
        if _cell(value).lower() == label.strip().lower():
            return c
    return None


def extract_schedule_clients(
    rows: list[list[object]], name_label: str
) -> list[ScheduleClient]:
    header_row, name_col = find_header_row(rows, name_label)
    address_col = _column_index(rows, header_row, "address")
    clients = []
    for row in rows[header_row + 1 :]:
        name = _cell(row[name_col] if name_col < len(row) else None)
        if not name:
            continue
        address = (
            _cell(row[address_col])
            if address_col is not None and address_col < len(row)
            else ""
        )
        clients.append(ScheduleClient(name=name, address=address))
    return clients


def extract_irrigation_clients(
    rows: list[list[object]], name_label: str, source_tab: str
) -> list[IrrigationClient]:
    """Client rows from one irrigation tab, honoring inline section markers.

    Rows after a "Blowout Only" marker are flagged. Rows after "Past/Inactive"
    or "Maintenance Only" markers are excluded: the former are not current
    clients, the latter are maintenance clients by definition.
    """
    header_row, name_col = find_header_row(rows, name_label)
    address_col = _column_index(rows, header_row, "address")
    clients = []
    section = "Active"
    for row in rows[header_row + 1 :]:
        row_text = " ".join(_cell(v) for v in row)
        if _PAST_INACTIVE.search(row_text):
            section = "Past/Inactive"
            continue
        if _MAINTENANCE_ONLY.search(row_text):
            section = "Maintenance Only"
            continue
        if _BLOWOUT_ONLY.search(row_text):
            section = "Blowout Only"
            continue
        if section in ("Past/Inactive", "Maintenance Only"):
            continue
        name = _cell(row[name_col] if name_col < len(row) else None)
        if not name or _PURE_NUMBER.fullmatch(name):
            continue
        address = (
            _cell(row[address_col])
            if address_col is not None and address_col < len(row)
            else ""
        )
        clients.append(
            IrrigationClient(
                name=name, address=address, source_tab=source_tab, section=section
            )
        )
    return clients
