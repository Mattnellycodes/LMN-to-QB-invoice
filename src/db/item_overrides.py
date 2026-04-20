"""Database operations for QBO item mapping overrides.

Persists user-confirmed mappings from an LMN service/material/rate name
(the line's `item_lookup_name`) to a QBO Product/Service ItemRef.
"""

from __future__ import annotations

from typing import Dict, Optional

from src.db.connection import db_cursor


def get_item_overrides() -> Dict[str, Dict[str, str]]:
    """Load item mapping overrides keyed by LMN item name.

    Returns `{lmn_item_name: {"value": qbo_item_id, "name": qbo_item_name}}`
    — shape is already a valid QBO ItemRef so callers can pass it through.
    """
    overrides: Dict[str, Dict[str, str]] = {}

    with db_cursor() as cursor:
        cursor.execute("""
            SELECT lmn_item_name, qbo_item_id, qbo_item_name
            FROM item_mapping_overrides
        """)
        for lmn_name, qbo_id, qbo_name in cursor.fetchall():
            overrides[lmn_name] = {
                "value": qbo_id,
                "name": qbo_name or "",
            }

    return overrides


def save_item_override(
    lmn_item_name: str,
    qbo_item_id: str,
    qbo_item_name: str,
    notes: Optional[str] = None,
) -> None:
    """Upsert an item mapping override."""
    with db_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO item_mapping_overrides
                (lmn_item_name, qbo_item_id, qbo_item_name, notes, updated_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (lmn_item_name) DO UPDATE SET
                qbo_item_id = EXCLUDED.qbo_item_id,
                qbo_item_name = EXCLUDED.qbo_item_name,
                notes = EXCLUDED.notes,
                updated_at = CURRENT_TIMESTAMP
            """,
            (lmn_item_name, qbo_item_id, qbo_item_name, notes),
        )


def delete_item_override(lmn_item_name: str) -> bool:
    """Delete an item override. Returns True if a row was removed."""
    with db_cursor() as cursor:
        cursor.execute(
            "DELETE FROM item_mapping_overrides WHERE lmn_item_name = %s",
            (lmn_item_name,),
        )
        return cursor.rowcount > 0
