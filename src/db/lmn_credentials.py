"""LMN token cache in database. Schema lives in src/db/connection.py:init_db."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from src.db.connection import db_cursor

logger = logging.getLogger(__name__)


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
    logger.debug("Cached LMN token (expires %s)", expires_at)


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
