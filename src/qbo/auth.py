"""QuickBooks Online OAuth authentication - session-based."""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from intuitlib.client import AuthClient
from intuitlib.enums import Scopes
from intuitlib.exceptions import AuthClientError

load_dotenv()

logger = logging.getLogger(__name__)


# Exception classes
class QBOAuthError(Exception):
    """Base OAuth error."""

    pass


class RefreshTokenExpired(QBOAuthError):
    """Refresh token expired (~100 days) - re-authorization required."""

    pass


class InvalidGrant(QBOAuthError):
    """Token revoked or auth code reused."""

    pass


class CSRFError(QBOAuthError):
    """State parameter mismatch - possible CSRF attack."""

    pass


class NotAuthenticated(QBOAuthError):
    """User not authenticated - needs to complete OAuth."""

    pass


def get_auth_client() -> AuthClient:
    """Create an AuthClient instance with credentials from environment."""
    client_id = os.getenv("QBO_CLIENT_ID")
    client_secret = os.getenv("QBO_CLIENT_SECRET")
    redirect_uri = os.getenv("QBO_REDIRECT_URI")

    missing = []
    if not client_id:
        missing.append("QBO_CLIENT_ID")
    if not client_secret:
        missing.append("QBO_CLIENT_SECRET")
    if not redirect_uri:
        missing.append("QBO_REDIRECT_URI")

    if missing:
        raise QBOAuthError(f"Missing required environment variables: {', '.join(missing)}")

    return AuthClient(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        environment="production",
    )


def get_authorization_url(state: Optional[str] = None) -> str:
    """
    Get the URL for initial OAuth authorization.

    Args:
        state: Optional CSRF protection token. If provided, must be validated
               when handling the callback.
    """
    auth_client = get_auth_client()
    scopes = [Scopes.ACCOUNTING]
    return auth_client.get_authorization_url(scopes, state_token=state)


def exchange_code_for_tokens(auth_code: str, realm_id: str) -> dict:
    """
    Exchange authorization code for access and refresh tokens.

    Call this after user authorizes via the authorization URL.
    Returns tokens dict to be stored in session.
    """
    auth_client = get_auth_client()

    try:
        auth_client.get_bearer_token(auth_code, realm_id=realm_id)
    except AuthClientError as e:
        if "invalid_grant" in str(e).lower():
            logger.error(f"Invalid grant error during token exchange: {e}")
            raise InvalidGrant(
                "Authorization code is invalid or already used. "
                "Please restart the OAuth flow."
            ) from e
        raise

    # Log intuit_tid if available for support reference
    if hasattr(auth_client, "intuit_tid") and auth_client.intuit_tid:
        logger.info(f"Token exchange successful. intuit_tid={auth_client.intuit_tid}")

    tokens = {
        "access_token": auth_client.access_token,
        "refresh_token": auth_client.refresh_token,
        "realm_id": realm_id,
        "expires_at": (datetime.now() + timedelta(hours=1)).isoformat(),
        "refresh_expires_at": (datetime.now() + timedelta(days=100)).isoformat(),
    }

    return tokens


def refresh_access_token(tokens: dict) -> dict:
    """
    Refresh the access token using the refresh token.

    Args:
        tokens: Current tokens dict from session

    Returns:
        Updated tokens dict to store back in session

    Raises:
        RefreshTokenExpired: If refresh token has expired (~100 days)
        InvalidGrant: If token was revoked or is otherwise invalid
    """
    if not tokens:
        raise NotAuthenticated("No tokens provided. Please connect to QuickBooks.")

    # Check refresh token expiry
    if "refresh_expires_at" in tokens:
        refresh_expires = datetime.fromisoformat(tokens["refresh_expires_at"])
        if datetime.now() >= refresh_expires:
            raise RefreshTokenExpired(
                "Refresh token has expired. Please reconnect to QuickBooks."
            )

    auth_client = get_auth_client()

    try:
        auth_client.refresh(refresh_token=tokens["refresh_token"])
    except AuthClientError as e:
        error_str = str(e).lower()
        if "invalid_grant" in error_str:
            logger.error(f"Invalid grant error during refresh: {e}")
            raise InvalidGrant(
                "Refresh token is invalid or revoked. Please reconnect to QuickBooks."
            ) from e
        raise

    # Log intuit_tid if available
    if hasattr(auth_client, "intuit_tid") and auth_client.intuit_tid:
        logger.info(f"Token refresh successful. intuit_tid={auth_client.intuit_tid}")

    # Update tokens
    tokens["access_token"] = auth_client.access_token
    tokens["refresh_token"] = auth_client.refresh_token
    tokens["expires_at"] = (datetime.now() + timedelta(hours=1)).isoformat()
    # Refresh token expiry resets on each refresh
    tokens["refresh_expires_at"] = (datetime.now() + timedelta(days=100)).isoformat()

    return tokens


def get_valid_tokens(tokens: dict) -> dict:
    """
    Ensure tokens are valid, refreshing if necessary.

    Args:
        tokens: Current tokens dict from session

    Returns:
        Valid tokens dict (may be refreshed)

    Raises:
        NotAuthenticated: If no tokens provided
        RefreshTokenExpired: If refresh token has expired
        InvalidGrant: If tokens are invalid/revoked
    """
    if not tokens or not tokens.get("access_token"):
        raise NotAuthenticated("Not connected to QuickBooks. Please authorize first.")

    # Check if access token is expired or about to expire (5 min buffer)
    expires_at = datetime.fromisoformat(tokens["expires_at"])
    if datetime.now() >= expires_at - timedelta(minutes=5):
        logger.info("Access token expired, refreshing...")
        tokens = refresh_access_token(tokens)

    return tokens


def get_access_token_and_realm(tokens: dict) -> Tuple[str, str]:
    """
    Get valid access token and realm ID from tokens dict.

    Convenience function that validates and returns just what's needed for API calls.

    Returns:
        (access_token, realm_id)
    """
    valid_tokens = get_valid_tokens(tokens)
    return valid_tokens["access_token"], valid_tokens["realm_id"]


def is_token_valid(tokens: Optional[dict]) -> bool:
    """Check if tokens exist and access token hasn't expired."""
    if not tokens or not tokens.get("access_token"):
        return False

    try:
        expires_at = datetime.fromisoformat(tokens.get("expires_at", ""))
        return datetime.now() < expires_at
    except (ValueError, TypeError):
        return False


def get_token_status(tokens: Optional[dict]) -> dict:
    """Get detailed token status for debugging/display."""
    if not tokens:
        return {"connected": False, "message": "Not connected to QuickBooks"}

    try:
        expires_at_str = tokens.get("expires_at", "")
        refresh_expires_at_str = tokens.get("refresh_expires_at", "")

        now = datetime.now()
        access_valid = False
        refresh_valid = False

        if expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str)
            access_valid = now < expires_at

        if refresh_expires_at_str:
            refresh_expires_at = datetime.fromisoformat(refresh_expires_at_str)
            refresh_valid = now < refresh_expires_at

        return {
            "connected": True,
            "realm_id": tokens.get("realm_id"),
            "access_token_valid": access_valid,
            "access_token_expires": expires_at_str,
            "refresh_token_valid": refresh_valid,
            "refresh_token_expires": refresh_expires_at_str,
        }
    except Exception as e:
        return {
            "connected": True,
            "realm_id": tokens.get("realm_id"),
            "error": f"Could not parse token expiry: {e}",
        }
