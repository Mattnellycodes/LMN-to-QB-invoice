"""LMN credentials and token storage in database."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Tuple

from src.db.connection import db_cursor


def init_lmn_credentials_table() -> None:
    """Create the lmn_credentials table if it doesn't exist."""
    with db_cursor() as cursor:
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


def save_lmn_credentials(username: str, password: str) -> None:
    """
    Save LMN credentials to database.

    Uses upsert to ensure only one row exists (id=1).
    """
    with db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO lmn_credentials (id, username, password, updated_at)
            VALUES (1, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (id) DO UPDATE SET
                username = EXCLUDED.username,
                password = EXCLUDED.password,
                cached_token = NULL,
                token_expires_at = NULL,
                updated_at = CURRENT_TIMESTAMP
        """, (username, password))


def get_lmn_credentials() -> Optional[Tuple[str, str]]:
    """
    Get stored LMN credentials.

    Returns:
        Tuple of (username, password) or None if not configured.
    """
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT username, password FROM lmn_credentials WHERE id = 1
        """)
        row = cursor.fetchone()
        if row:
            return (row[0], row[1])
        return None


def save_lmn_token(token: str, expires_at: datetime) -> None:
    """
    Cache the LMN access token with its expiration time.

    Args:
        token: The access token from LMN OAuth
        expires_at: When the token expires
    """
    with db_cursor() as cursor:
        cursor.execute("""
            UPDATE lmn_credentials
            SET cached_token = %s, token_expires_at = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (token, expires_at))


def get_cached_token() -> Optional[str]:
    """
    Get cached LMN token if it exists and is still valid.

    Returns:
        The cached token if valid, None if expired or not present.
    """
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT cached_token, token_expires_at FROM lmn_credentials WHERE id = 1
        """)
        row = cursor.fetchone()
        if not row or not row[0] or not row[1]:
            return None

        token, expires_at = row
        # Add 5 minute buffer before expiration
        if datetime.now() < (expires_at - timedelta(minutes=5)):
            return token
        return None


def delete_lmn_credentials() -> None:
    """Delete stored LMN credentials and cached token."""
    with db_cursor() as cursor:
        cursor.execute("DELETE FROM lmn_credentials WHERE id = 1")


def has_lmn_credentials() -> bool:
    """Check if LMN credentials are stored."""
    with db_cursor() as cursor:
        cursor.execute("SELECT 1 FROM lmn_credentials WHERE id = 1")
        return cursor.fetchone() is not None
