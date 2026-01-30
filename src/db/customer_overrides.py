"""Database operations for customer mapping overrides."""

from __future__ import annotations

from typing import Dict

from src.db.connection import db_cursor
from src.mapping.customer_mapping import CustomerMapping


def get_customer_overrides() -> Dict[str, CustomerMapping]:
    """
    Load customer mapping overrides from database.

    Returns:
        {jobsite_id: CustomerMapping}
    """
    mappings = {}

    with db_cursor() as cursor:
        cursor.execute("""
            SELECT jobsite_id, qbo_customer_id, qbo_display_name, notes
            FROM customer_mapping_overrides
        """)

        for row in cursor.fetchall():
            jobsite_id, qbo_customer_id, qbo_display_name, notes = row
            mappings[jobsite_id] = CustomerMapping(
                jobsite_id=jobsite_id,
                qbo_customer_id=qbo_customer_id,
                qbo_display_name=qbo_display_name or "",
                notes=notes or "",
            )

    return mappings


def save_customer_override(mapping: CustomerMapping) -> None:
    """
    Save or update a customer mapping override in the database.

    Uses upsert - inserts if new, updates if exists.
    """
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO customer_mapping_overrides
                (jobsite_id, qbo_customer_id, qbo_display_name, notes, updated_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (jobsite_id) DO UPDATE SET
                qbo_customer_id = EXCLUDED.qbo_customer_id,
                qbo_display_name = EXCLUDED.qbo_display_name,
                notes = EXCLUDED.notes,
                updated_at = CURRENT_TIMESTAMP
        """, (
            mapping.jobsite_id,
            mapping.qbo_customer_id,
            mapping.qbo_display_name,
            mapping.notes,
        ))


def delete_customer_override(jobsite_id: str) -> bool:
    """
    Delete a customer mapping override.

    Returns:
        True if a row was deleted, False if not found
    """
    with db_cursor() as cursor:
        cursor.execute("""
            DELETE FROM customer_mapping_overrides
            WHERE jobsite_id = %s
        """, (jobsite_id,))
        return cursor.rowcount > 0
