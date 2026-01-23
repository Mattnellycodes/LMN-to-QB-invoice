"""QBO authentication context - request-scoped credentials."""

from flask import g

from src.qbo.auth import NotAuthenticated


def get_qbo_credentials():
    """
    Get current QBO credentials from request context.

    Returns:
        (access_token, realm_id)

    Raises:
        NotAuthenticated: If no credentials in current request context
    """
    access_token = getattr(g, "qbo_access_token", None)
    realm_id = getattr(g, "qbo_realm_id", None)

    if not access_token or not realm_id:
        raise NotAuthenticated("Not connected to QuickBooks. Please authorize first.")

    return access_token, realm_id


def set_qbo_credentials(access_token: str, realm_id: str):
    """Set QBO credentials in request context."""
    g.qbo_access_token = access_token
    g.qbo_realm_id = realm_id


def has_qbo_credentials() -> bool:
    """Check if QBO credentials are set in current request context."""
    return bool(getattr(g, "qbo_access_token", None) and getattr(g, "qbo_realm_id", None))
