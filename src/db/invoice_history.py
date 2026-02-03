"""Database operations for invoice history tracking."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from src.db.connection import db_cursor


def record_invoice_creation(
    jobsite_id: str,
    timesheet_ids: List[str],
    work_dates: List[str],
    qbo_invoice_id: str,
    qbo_invoice_number: str,
    total_amount: float,
) -> None:
    """
    Record that an invoice was created in QBO.

    Only call this AFTER successful invoice creation in QuickBooks.

    Args:
        jobsite_id: The LMN jobsite ID
        timesheet_ids: List of timesheet IDs included in this invoice
        work_dates: List of work dates (YYYY-MM-DD format)
        qbo_invoice_id: The QuickBooks invoice ID
        qbo_invoice_number: The QuickBooks invoice number
        total_amount: The invoice total
    """
    with db_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO invoice_history
                (jobsite_id, timesheet_ids, work_dates, qbo_invoice_id,
                 qbo_invoice_number, total_amount, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                jobsite_id,
                timesheet_ids,
                work_dates,
                qbo_invoice_id,
                qbo_invoice_number,
                total_amount,
                datetime.now(),
            ),
        )


def find_already_invoiced_timesheets(timesheet_ids: List[str]) -> List[Dict]:
    """
    Check if any timesheet IDs have already been invoiced.

    Args:
        timesheet_ids: List of timesheet IDs to check

    Returns:
        List of dicts with info about previously invoiced timesheets:
        [{"timesheet_id": "...", "qbo_invoice_number": "...", "created_at": "..."}]
    """
    if not timesheet_ids:
        return []

    with db_cursor() as cursor:
        # Use ANY to check if any timesheet_id is in the stored arrays
        cursor.execute(
            """
            SELECT timesheet_ids, qbo_invoice_number, qbo_invoice_id, created_at
            FROM invoice_history
            WHERE timesheet_ids && %s
            """,
            (timesheet_ids,),
        )

        results = []
        for row in cursor.fetchall():
            stored_ids, invoice_num, invoice_id, created = row
            # Find which of our timesheet_ids were already invoiced
            for ts_id in timesheet_ids:
                if ts_id in stored_ids:
                    results.append({
                        "timesheet_id": ts_id,
                        "qbo_invoice_number": invoice_num,
                        "qbo_invoice_id": invoice_id,
                        "created_at": created.isoformat() if created else None,
                    })

        return results


def find_overlapping_dates(
    jobsite_id: str, work_dates: List[str]
) -> Optional[Dict]:
    """
    Check if any work dates for a jobsite overlap with previous invoices.

    Args:
        jobsite_id: The LMN jobsite ID
        work_dates: List of work dates (YYYY-MM-DD format)

    Returns:
        Dict with overlap info if found, None otherwise
    """
    if not work_dates:
        return None

    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT work_dates, qbo_invoice_number, created_at
            FROM invoice_history
            WHERE jobsite_id = %s
            AND work_dates && %s
            """,
            (jobsite_id, work_dates),
        )

        row = cursor.fetchone()
        if row:
            stored_dates, invoice_num, created = row
            overlapping = set(work_dates) & set(stored_dates)
            return {
                "overlapping_dates": list(overlapping),
                "qbo_invoice_number": invoice_num,
                "created_at": created.isoformat() if created else None,
            }

    return None


def get_invoice_history(jobsite_id: Optional[str] = None) -> List[Dict]:
    """
    Get invoice history, optionally filtered by jobsite.

    Args:
        jobsite_id: Optional filter by jobsite ID

    Returns:
        List of invoice history records
    """
    with db_cursor() as cursor:
        if jobsite_id:
            cursor.execute(
                """
                SELECT jobsite_id, timesheet_ids, work_dates, qbo_invoice_id,
                       qbo_invoice_number, total_amount, created_at
                FROM invoice_history
                WHERE jobsite_id = %s
                ORDER BY created_at DESC
                """,
                (jobsite_id,),
            )
        else:
            cursor.execute(
                """
                SELECT jobsite_id, timesheet_ids, work_dates, qbo_invoice_id,
                       qbo_invoice_number, total_amount, created_at
                FROM invoice_history
                ORDER BY created_at DESC
                """
            )

        return [
            {
                "jobsite_id": row[0],
                "timesheet_ids": row[1],
                "work_dates": row[2],
                "qbo_invoice_id": row[3],
                "qbo_invoice_number": row[4],
                "total_amount": float(row[5]) if row[5] else 0,
                "created_at": row[6].isoformat() if row[6] else None,
            }
            for row in cursor.fetchall()
        ]
