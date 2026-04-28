"""LMN authentication via the accounting API token endpoint."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

LMN_TOKEN_URL = "https://accounting-api.golmn.com/token"


class LMNAuthError(Exception):
    """Error during LMN authentication."""

    pass


def authenticate(username: str, password: str) -> tuple[str, datetime]:
    """
    Authenticate with LMN's accounting API.

    Args:
        username: LMN account username (email)
        password: LMN account password

    Returns:
        Tuple of (access_token, expires_at)

    Raises:
        LMNAuthError: If authentication fails
    """
    data = (
        f"grant_type=password"
        f"&username={requests.utils.quote(username)}"
        f"&password={requests.utils.quote(password)}"
    )

    try:
        response = requests.post(
            LMN_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )

        if response.status_code != 200:
            error_data = {}
            try:
                error_data = response.json() if response.content else {}
            except Exception:
                pass
            error_msg = error_data.get(
                "error_description", error_data.get("error", "Unknown error")
            )
            logger.error(
                f"LMN auth failed: status={response.status_code}, response={response.text[:500]}"
            )
            raise LMNAuthError(f"Authentication failed: {error_msg}")

        token_data = response.json()
        access_token = token_data.get("access_token")
        expires_in = token_data.get("expires_in", 36000)  # Default ~10 hours

        if not access_token:
            raise LMNAuthError("No access token in response")

        expires_at = datetime.now() + timedelta(seconds=expires_in)
        logger.info(f"LMN authentication successful, token expires at {expires_at}")

        return access_token, expires_at

    except requests.RequestException as e:
        raise LMNAuthError(f"Network error during authentication: {e}")


def get_valid_token() -> Optional[str]:
    """
    Get a valid LMN API token.

    Priority:
    1. Cached token from database (if not expired)
    2. Authenticate using LMN_EMAIL/LMN_PASSWORD env vars (cached to DB)
    3. Fall back to LMN_API_TOKEN environment variable

    Returns:
        A valid access token, or None if no token available.
    """
    # Try cached token first, then authenticate and cache
    try:
        from src.db.lmn_credentials import get_cached_token, save_lmn_token

        cached = get_cached_token()
        if cached:
            logger.debug("Using cached LMN token")
            return cached

        email = os.getenv("LMN_EMAIL")
        password = os.getenv("LMN_PASSWORD")
        if email and password:
            try:
                token, expires_at = authenticate(email, password)
                save_lmn_token(token, expires_at)
                logger.info("Authenticated with LMN using env credentials")
                return token
            except LMNAuthError as e:
                logger.warning(f"Failed to authenticate with LMN env credentials: {e}")

    except Exception as e:
        logger.debug(f"Database not available for LMN token cache: {e}")

    # No database — authenticate directly without caching
    email = os.getenv("LMN_EMAIL")
    password = os.getenv("LMN_PASSWORD")
    if email and password:
        try:
            token, _ = authenticate(email, password)
            return token
        except LMNAuthError as e:
            logger.warning(f"Failed to authenticate with LMN env credentials: {e}")

    # Fall back to static token env var
    env_token = os.getenv("LMN_API_TOKEN")
    if env_token:
        logger.debug("Using LMN_API_TOKEN from environment")
        return env_token

    return None
