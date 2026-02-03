"""Database connection management."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

import psycopg2
from psycopg2.extensions import connection as PgConnection


def get_database_url() -> str:
    """Get database URL from environment."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise ValueError(
            "DATABASE_URL environment variable not set. "
            "Set it in .env or configure on Render."
        )
    return url


def get_connection() -> PgConnection:
    """Get a database connection."""
    return psycopg2.connect(get_database_url())


@contextmanager
def db_cursor() -> Generator:
    """Context manager for database cursor with auto-commit."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def init_db() -> None:
    """Initialize database tables."""
    with db_cursor() as cursor:
        # Customer mapping overrides table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS customer_mapping_overrides (
                jobsite_id VARCHAR(50) PRIMARY KEY,
                qbo_customer_id VARCHAR(50) NOT NULL,
                qbo_display_name VARCHAR(255),
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Invoice history table - tracks which timesheets have been invoiced
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS invoice_history (
                id SERIAL PRIMARY KEY,
                jobsite_id VARCHAR(50) NOT NULL,
                timesheet_ids TEXT[] NOT NULL,
                work_dates TEXT[] NOT NULL,
                qbo_invoice_id VARCHAR(50) NOT NULL,
                qbo_invoice_number VARCHAR(50),
                total_amount DECIMAL(10, 2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Index for fast timesheet lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_invoice_history_timesheet_ids
            ON invoice_history USING GIN (timesheet_ids)
        """)

        # Index for jobsite + date lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_invoice_history_jobsite_dates
            ON invoice_history (jobsite_id)
        """)

    print("Database initialized: customer_mapping_overrides, invoice_history tables ready")
