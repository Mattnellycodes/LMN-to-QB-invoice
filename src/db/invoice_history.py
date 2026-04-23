"""Invoice-history DB: tracks (jobsite, date, foreman) triples already invoiced.

Schema owned by `src.db.connection.init_db`. The `date_foreman_pairs` column
stores `"<date>|<foreman>"` strings so we can use a GIN-indexed array overlap
to detect when a new upload covers work already billed.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

from src.db.connection import db_cursor

logger = logging.getLogger(__name__)


_PAIR_SEP = "|"


def _make_pairs(work_dates: List[str], foremen_by_date: Dict[str, List[str]]) -> List[str]:
    """Build "<date>|<foreman>" strings from a mapping of date -> foremen."""
    pairs: list[str] = []
    for date in work_dates:
        for foreman in foremen_by_date.get(date, []):
            pairs.append(f"{date}{_PAIR_SEP}{foreman}")
    return sorted(set(pairs))


def record_invoice_creation(
    jobsite_id: str,
    work_dates: List[str],
    foremen: List[str],
    date_foreman_pairs: List[str],
    qbo_invoice_id: str,
    qbo_invoice_number: str,
    total_amount: float,
) -> None:
    """Record a successfully created QBO invoice.

    `date_foreman_pairs` is the canonical overlap key; each entry is
    ``f"{work_date}|{foreman}"``. Callers typically build this from the parsed
    rollup's (date, foreman) set before calling.
    """
    with db_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO invoice_history
                (jobsite_id, work_dates, foremen, date_foreman_pairs,
                 qbo_invoice_id, qbo_invoice_number, total_amount, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                jobsite_id,
                sorted(set(work_dates)),
                sorted(set(foremen)),
                sorted(set(date_foreman_pairs)),
                qbo_invoice_id,
                qbo_invoice_number,
                total_amount,
                datetime.now(),
            ),
        )
    logger.info(
        "Recorded invoice history: jobsite=%s invoice#=%s pairs=%d",
        jobsite_id,
        qbo_invoice_number,
        len(set(date_foreman_pairs)),
    )


def find_already_invoiced(
    jobsite_id: str, date_foreman_pairs: List[str]
) -> List[Dict]:
    """Return prior invoices for this jobsite that overlap any (date, foreman) pair.

    Each returned dict includes the overlapping pairs from the candidate set
    so the UI can show exactly which crew-days would be duplicated.
    """
    if not date_foreman_pairs:
        return []

    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT date_foreman_pairs, qbo_invoice_number, qbo_invoice_id, created_at
            FROM invoice_history
            WHERE jobsite_id = %s AND date_foreman_pairs && %s
            """,
            (jobsite_id, sorted(set(date_foreman_pairs))),
        )
        results: list[dict] = []
        for stored_pairs, invoice_num, invoice_id, created in cursor.fetchall():
            overlap = sorted(set(date_foreman_pairs) & set(stored_pairs))
            if not overlap:
                continue
            results.append(
                {
                    "overlapping_pairs": overlap,
                    "qbo_invoice_number": invoice_num,
                    "qbo_invoice_id": invoice_id,
                    "created_at": created.isoformat() if created else None,
                }
            )
        logger.debug(
            "find_already_invoiced: jobsite=%s candidate_pairs=%d matches=%d",
            jobsite_id,
            len(date_foreman_pairs),
            len(results),
        )
        return results


def get_invoice_history(jobsite_id: Optional[str] = None) -> List[Dict]:
    """List historical invoices, newest first. Optionally filter by jobsite."""
    with db_cursor() as cursor:
        if jobsite_id:
            cursor.execute(
                """
                SELECT jobsite_id, work_dates, foremen, date_foreman_pairs,
                       qbo_invoice_id, qbo_invoice_number, total_amount, created_at
                FROM invoice_history
                WHERE jobsite_id = %s
                ORDER BY created_at DESC
                """,
                (jobsite_id,),
            )
        else:
            cursor.execute(
                """
                SELECT jobsite_id, work_dates, foremen, date_foreman_pairs,
                       qbo_invoice_id, qbo_invoice_number, total_amount, created_at
                FROM invoice_history
                ORDER BY created_at DESC
                """
            )
        return [
            {
                "jobsite_id": row[0],
                "work_dates": row[1],
                "foremen": row[2],
                "date_foreman_pairs": row[3],
                "qbo_invoice_id": row[4],
                "qbo_invoice_number": row[5],
                "total_amount": float(row[6]) if row[6] else 0.0,
                "created_at": row[7].isoformat() if row[7] else None,
            }
            for row in cursor.fetchall()
        ]
