"""Temporary hardcoded price-list support backed by the 2026 Excel workbook.

This is intentionally small and config-file driven. The workbook is loaded once
per process, then invoice building uses an in-memory lookup keyed by normalized
LMN/QBO item names.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from src.mapping.item_mapping import canonicalize_item_name

logger = logging.getLogger(__name__)


PRICE_LIST_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "Master Price List - 2026.xlsx"
)


@dataclass(frozen=True)
class PriceEntry:
    """One resolved workbook price."""

    lookup_name: str
    price: float
    source_sheet: str = ""
    min_quantity: float | None = None


class HardcodedPriceLookup:
    """Fast name -> price lookup with exact and canonicalized aliases."""

    def __init__(self, entries: Mapping[str, PriceEntry] | None = None) -> None:
        self._exact: dict[str, PriceEntry] = {}
        self._canonical: dict[str, PriceEntry] = {}
        for name, entry in (entries or {}).items():
            self.add_alias(name, entry)

    def add_alias(self, name: str, entry: PriceEntry) -> None:
        key = _exact_key(name)
        if key:
            self._exact[key] = entry
        canon = _canonical_key(name)
        if canon:
            self._canonical[canon] = entry

    def resolve(self, name: str) -> PriceEntry | None:
        exact = self._exact.get(_exact_key(name))
        if exact is not None:
            return exact
        return self._canonical.get(_canonical_key(name))

    def __len__(self) -> int:
        return len({entry.lookup_name.lower() for entry in self._exact.values()})


# User-confirmed LMN PDF names that differ from the workbook's price names.
_ALIASES: dict[str, str] = {
    "Deer Spray": "Deer Spray, Bozeman, ea",
    "Dump fee, ea [ea]": "Dump Fee Bozeman, ea",
    "Dump Fee": "Dump Fee Bozeman, ea",
    "Edging-Aluminium [ft]": "Edging, Aluminium, ft",
    "Emitter, drip [Ea]": "Drip Emitter, ea",
    "Fertilizer [Bags]": "Fertilizer, Bagged, ea",
    "Hedge Shearing [Day]": "Hedge Shearing, hr",
    "Hedge Shearing, daily [daily]": "Hedge Shearing, hr",
    "Rotor, 5004, ea [ea]": "5004 with Street L, ea",
    "Weed Mat Pins": "Weedmat Pins, ea",
    "Maintenance Skilled Hourly Labor - TOWN": "Maintenance Hourly Labor - TOWN",
    "Maintenance Skilled Hourly Labor - BIG SKY": "Maintenance Hourly Labor - BIG SKY",
}

_MIN_QUANTITIES: dict[str, float] = {
    "Hedge Shearing [Day]": 1.0,
    "Hedge Shearing, daily [daily]": 1.0,
}


@lru_cache(maxsize=1)
def load_price_lookup(path: Path = PRICE_LIST_PATH) -> HardcodedPriceLookup:
    """Load the Excel workbook into a cached HardcodedPriceLookup."""
    if not path.exists():
        raise FileNotFoundError(f"Hardcoded price list not found: {path}")

    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError(
            "Hardcoded price list requires pandas and openpyxl. "
            "Install project requirements before using this option."
        ) from exc

    try:
        sheets = pd.read_excel(path, sheet_name=None, header=None, engine="openpyxl")
    except ImportError as exc:
        raise RuntimeError(
            "Hardcoded price list requires openpyxl. "
            "Install project requirements before using this option."
        ) from exc

    entries_by_name: dict[str, PriceEntry] = {}
    lookup = HardcodedPriceLookup()
    for sheet_name, raw_df in sheets.items():
        for row in _iter_price_rows(raw_df):
            names = _row_names(row)
            price = _to_float(row.get("Price")) or _to_float(
                row.get("Unit Price (Cell D)")
            )
            if not names or price is None:
                continue
            lookup_name = names[0]
            entry = PriceEntry(
                lookup_name=lookup_name,
                price=round(price, 6),
                source_sheet=str(sheet_name),
            )
            for name in names:
                entries_by_name.setdefault(_exact_key(name), entry)
                lookup.add_alias(name, entry)

    for pdf_name, workbook_name in _ALIASES.items():
        entry = entries_by_name.get(_exact_key(workbook_name))
        if entry is None:
            entry = lookup.resolve(workbook_name)
        if entry is None:
            logger.warning(
                "Hardcoded price alias target not found: %s -> %s",
                pdf_name,
                workbook_name,
            )
            continue
        min_quantity = _MIN_QUANTITIES.get(pdf_name)
        if min_quantity is not None:
            entry = PriceEntry(
                lookup_name=entry.lookup_name,
                price=entry.price,
                source_sheet=entry.source_sheet,
                min_quantity=min_quantity,
            )
        lookup.add_alias(pdf_name, entry)

    logger.info("Loaded hardcoded price list: %d price names", len(lookup))
    return lookup


def _iter_price_rows(raw_df: Any) -> list[dict[str, Any]]:
    header_idx = None
    values = raw_df.fillna("").values.tolist()
    for idx, row in enumerate(values[:20]):
        normalized = {str(cell).strip().lower() for cell in row if str(cell).strip()}
        has_name = bool({"name", "product/service name", "item"} & normalized)
        has_price = bool({"price", "unit price (cell d)"} & normalized)
        if has_name and has_price:
            header_idx = idx
            break
    if header_idx is None:
        return []

    headers = [str(cell).strip() for cell in values[header_idx]]
    rows: list[dict[str, Any]] = []
    for values_row in values[header_idx + 1:]:
        row = {
            header: values_row[index] if index < len(values_row) else ""
            for index, header in enumerate(headers)
            if header
        }
        if any(str(value).strip() for value in row.values()):
            rows.append(row)
    return rows


def _row_names(row: Mapping[str, Any]) -> list[str]:
    seen: set[str] = set()
    names: list[str] = []
    for key in (
        "Name",
        "Product/Service Name",
        "Sales Description",
        "Purchase Description",
        "Description",
        "Item",
    ):
        raw = row.get(key)
        name = "" if raw is None else str(raw).strip()
        if not name or name.startswith("**"):
            continue
        key_name = _exact_key(name)
        if key_name in seen:
            continue
        seen.add(key_name)
        names.append(name)
    return names


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if value != value:
            return None
        return float(value)
    text = str(value).replace("$", "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _exact_key(name: str) -> str:
    return (name or "").strip().lower()


def _canonical_key(name: str) -> str:
    return canonicalize_item_name(name).strip().lower()
