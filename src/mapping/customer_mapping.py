"""Customer mapping between LMN JobsiteID and QuickBooks CustomerID."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Union

# Default mapping file location
DEFAULT_MAPPING_FILE = Path(__file__).parent.parent.parent / "config" / "customer_mapping.csv"


@dataclass
class CustomerMapping:
    """Mapping between LMN jobsite and QBO customer."""

    jobsite_id: str
    qbo_customer_id: str
    qbo_display_name: str
    notes: str = ""


def load_customer_mapping(
    mapping_path: Optional[Union[str, Path]] = None,
) -> Dict[str, CustomerMapping]:
    """
    Load JobsiteID -> QBO CustomerID mapping from CSV.

    CSV format: JobsiteID,QBO_CustomerID,QBO_DisplayName,Notes

    Returns:
        {jobsite_id: CustomerMapping}
    """
    path = Path(mapping_path) if mapping_path else DEFAULT_MAPPING_FILE

    if not path.exists():
        return {}

    mappings = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            jobsite_id = str(row["JobsiteID"]).strip()
            mappings[jobsite_id] = CustomerMapping(
                jobsite_id=jobsite_id,
                qbo_customer_id=str(row["QBO_CustomerID"]).strip(),
                qbo_display_name=row.get("QBO_DisplayName", "").strip(),
                notes=row.get("Notes", "").strip(),
            )

    return mappings


def load_mapping_from_lmn_api(
    use_db_overrides: bool = True,
    csv_override_path: Optional[Union[str, Path]] = None,
) -> Dict[str, CustomerMapping]:
    """
    Load JobsiteID -> QBO CustomerID mapping from LMN API.

    Overrides from database (production) or CSV (local dev) take precedence
    over LMN API mappings.

    Args:
        use_db_overrides: If True, load overrides from PostgreSQL database.
                          If False, use csv_override_path instead.
        csv_override_path: Path to CSV with manual overrides (when use_db_overrides=False).

    Returns:
        {jobsite_id: CustomerMapping}
    """
    # Start with LMN API mappings (if available)
    mappings = {}
    try:
        from src.lmn.api import load_mapping_from_lmn_api as fetch_lmn_mappings
        mappings = fetch_lmn_mappings()
    except (ValueError, Exception):
        # LMN API not configured or failed - continue with empty base mappings
        pass

    # Apply overrides (database for production, CSV for local dev)
    if use_db_overrides:
        try:
            from src.db.customer_overrides import get_customer_overrides
            db_overrides = get_customer_overrides()
            if db_overrides:
                mappings.update(db_overrides)
        except Exception:
            # Database not available - fall back to CSV
            csv_overrides = load_customer_mapping(csv_override_path)
            if csv_overrides:
                mappings.update(csv_overrides)
    else:
        csv_overrides = load_customer_mapping(csv_override_path)
        if csv_overrides:
            mappings.update(csv_overrides)

    return mappings


def save_customer_mapping(
    mappings: Dict[str, CustomerMapping],
    mapping_path: Optional[Union[str, Path]] = None,
) -> None:
    """Save customer mappings to CSV."""
    path = Path(mapping_path) if mapping_path else DEFAULT_MAPPING_FILE
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["JobsiteID", "QBO_CustomerID", "QBO_DisplayName", "Notes"]
        )
        writer.writeheader()
        for mapping in mappings.values():
            writer.writerow(
                {
                    "JobsiteID": mapping.jobsite_id,
                    "QBO_CustomerID": mapping.qbo_customer_id,
                    "QBO_DisplayName": mapping.qbo_display_name,
                    "Notes": mapping.notes,
                }
            )


def get_qbo_customer_id(jobsite_id: str, mappings: Dict[str, CustomerMapping]) -> Optional[str]:
    """Look up QBO customer ID for a jobsite."""
    mapping = mappings.get(str(jobsite_id))
    return mapping.qbo_customer_id if mapping else None


def find_unmapped_jobsites(
    jobsite_ids: List[str], mappings: Dict[str, CustomerMapping]
) -> List[str]:
    """Find jobsite IDs that don't have a QBO customer mapping."""
    return [jid for jid in jobsite_ids if str(jid) not in mappings]


def create_mapping_template(jobsite_ids: List[str], output_path: Union[str, Path]) -> None:
    """
    Create a CSV template for manual customer mapping.

    Use this to bootstrap the mapping file with jobsite IDs from LMN data.
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["JobsiteID", "QBO_CustomerID", "QBO_DisplayName", "Notes"]
        )
        writer.writeheader()
        for jobsite_id in sorted(set(jobsite_ids)):
            writer.writerow(
                {
                    "JobsiteID": jobsite_id,
                    "QBO_CustomerID": "",
                    "QBO_DisplayName": "",
                    "Notes": "",
                }
            )

    print(f"Created mapping template with {len(jobsite_ids)} jobsites: {path}")
