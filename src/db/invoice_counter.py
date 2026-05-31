"""Self-assigned invoice number sequence.

QBO does not auto-number invoices created via the API when the company has
"Custom Transaction Numbers" turned ON — it returns a blank DocNumber. We
therefore mint our own numbers from a counter we control, in a namespace
("26-") that can't collide with hand-keyed numeric invoices.
"""

from __future__ import annotations

from src.db.connection import db_cursor

INVOICE_NUMBER_PREFIX = "26-"


def claim_next_invoice_number() -> str:
    """Atomically claim and return the next invoice number, e.g. "26-00000".

    The counter row holds the next sequence to issue (seeded at 0, so the first
    invoice is "26-00000"). We lock the row, read it, then advance it, so
    concurrent callers can never claim the same number.
    """
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT counter FROM invoice_number_counter WHERE id = 1 FOR UPDATE"
        )
        next_seq = cursor.fetchone()[0]
        cursor.execute(
            "UPDATE invoice_number_counter SET counter = counter + 1 WHERE id = 1"
        )
    return f"{INVOICE_NUMBER_PREFIX}{next_seq:05d}"
