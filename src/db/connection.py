"""Database connection management."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator

import psycopg2
from psycopg2.extensions import connection as PgConnection

logger = logging.getLogger(__name__)


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
        logger.exception("Database operation failed; rolled back")
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

        # Invoice history table — tracks (jobsite, date, foreman) triples
        # already invoiced to QBO. date_foreman_pairs stores "date|foreman"
        # strings enabling GIN-indexed overlap queries for duplicate detection.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS invoice_history (
                id SERIAL PRIMARY KEY,
                jobsite_id VARCHAR(50) NOT NULL,
                work_dates TEXT[] NOT NULL,
                foremen TEXT[] NOT NULL,
                date_foreman_pairs TEXT[] NOT NULL,
                qbo_invoice_id VARCHAR(50) NOT NULL,
                qbo_invoice_number VARCHAR(50),
                total_amount DECIMAL(10, 2),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_invoice_history_jobsite
            ON invoice_history (jobsite_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_invoice_history_pairs
            ON invoice_history USING GIN (date_foreman_pairs)
        """)

        # LMN credentials table - stores username/password and cached token
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS lmn_credentials (
                id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                cached_token TEXT,
                token_expires_at TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Item mapping overrides table - persists user-confirmed mappings from
        # LMN service/material/rate names to QBO Product/Service ItemRefs.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS item_mapping_overrides (
                lmn_item_name VARCHAR(255) PRIMARY KEY,
                qbo_item_id VARCHAR(50) NOT NULL,
                qbo_item_name VARCHAR(255),
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

    logger.info(
        "Database initialized: customer_mapping_overrides, invoice_history, "
        "lmn_credentials, item_mapping_overrides tables ready"
    )
