"""LMN OAuth2 authentication using Resource Owner Password Credentials grant."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# LMN OAuth2 configuration
LMN_AUTH_URL = "https://accounting-api.golmn.com/token"


class LMNAuthError(Exception):
    """Error during LMN authentication."""
    pass


def authenticate(username: str, password: str) -> tuple[str, datetime]:
    """
    Authenticate with LMN using Resource Owner Password Credentials grant.

    Args:
        username: LMN account username (email)
        password: LMN account password

    Returns:
        Tuple of (access_token, expires_at)

    Raises:
        LMNAuthError: If authentication fails
    """
    data = {
        "grant_type": "password",
        "username": username,
        "password": password,
    }

    try:
        response = requests.post(
            LMN_AUTH_URL,
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
            error_msg = error_data.get("error_description", error_data.get("error", "Unknown error"))
            logger.error(f"LMN auth failed: status={response.status_code}, response={response.text[:500]}")
            raise LMNAuthError(f"Authentication failed: {error_msg}")

        token_data = response.json()
        access_token = token_data.get("access_token")
        expires_in = token_data.get("expires_in", 64800)  # Default ~18 hours

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
    2. Re-authenticate using stored credentials
    3. Fall back to LMN_API_TOKEN environment variable

    Returns:
        A valid access token, or None if no token available.
    """
    # Try cached token first
    try:
        from src.db.lmn_credentials import get_cached_token, get_lmn_credentials, save_lmn_token

        cached = get_cached_token()
        if cached:
            logger.debug("Using cached LMN token")
            return cached

        # Try to authenticate with stored credentials
        credentials = get_lmn_credentials()
        if credentials:
            username, password = credentials
            try:
                token, expires_at = authenticate(username, password)
                save_lmn_token(token, expires_at)
                logger.info("Refreshed LMN token using stored credentials")
                return token
            except LMNAuthError as e:
                logger.warning(f"Failed to refresh LMN token: {e}")

    except Exception as e:
        # Database not available - fall through to env var
        logger.debug(f"Database not available for LMN credentials: {e}")

    # Fall back to environment variable
    env_token = os.getenv("LMN_API_TOKEN")
    if env_token:
        logger.debug("Using LMN_API_TOKEN from environment")
        return env_token

    return None


def test_token(token: str) -> bool:
    """
    Test if an LMN token is valid by making a test API call.

    Args:
        token: The access token to test

    Returns:
        True if the token is valid, False otherwise
    """
    from src.lmn.api import LMN_API_URL

    try:
        response = requests.get(
            LMN_API_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        return response.status_code == 200
    except requests.RequestException:
        return False
