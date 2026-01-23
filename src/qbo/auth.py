"""QuickBooks Online OAuth authentication."""

from __future__ import annotations

import json
import logging
import os
import secrets
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
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


# Token storage file (for local development)
TOKEN_FILE = Path(__file__).parent.parent.parent / "config" / ".qbo_tokens.json"


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


def _check_refresh_token_expiry(tokens: dict) -> None:
    """Raise RefreshTokenExpired if refresh token has expired."""
    if "refresh_expires_at" in tokens:
        refresh_expires = datetime.fromisoformat(tokens["refresh_expires_at"])
        if datetime.now() >= refresh_expires:
            clear_tokens()
            raise RefreshTokenExpired(
                "Refresh token has expired (valid for ~100 days). "
                "Please re-authorize: python -m src.qbo.auth setup"
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

    save_tokens(tokens)
    return tokens


def refresh_access_token() -> dict:
    """
    Refresh the access token using the stored refresh token.

    Raises:
        RefreshTokenExpired: If refresh token has expired (~100 days)
        InvalidGrant: If token was revoked or is otherwise invalid
    """
    tokens = load_tokens()
    if not tokens:
        raise ValueError("No stored tokens found. Run initial authorization first.")

    _check_refresh_token_expiry(tokens)

    auth_client = get_auth_client()

    try:
        auth_client.refresh(refresh_token=tokens["refresh_token"])
    except AuthClientError as e:
        error_str = str(e).lower()
        if "invalid_grant" in error_str:
            logger.error(f"Invalid grant error during refresh: {e}")
            clear_tokens()
            raise InvalidGrant(
                "Refresh token is invalid or revoked. "
                "Please re-authorize: python -m src.qbo.auth setup"
            ) from e
        raise

    # Log intuit_tid if available
    if hasattr(auth_client, "intuit_tid") and auth_client.intuit_tid:
        logger.info(f"Token refresh successful. intuit_tid={auth_client.intuit_tid}")

    tokens["access_token"] = auth_client.access_token
    tokens["refresh_token"] = auth_client.refresh_token
    tokens["expires_at"] = (datetime.now() + timedelta(hours=1)).isoformat()
    # Refresh token expiry resets on each refresh
    tokens["refresh_expires_at"] = (datetime.now() + timedelta(days=100)).isoformat()

    save_tokens(tokens)
    return tokens


def get_valid_access_token() -> Tuple[str, str]:
    """
    Get a valid access token, refreshing if necessary.

    Returns:
        (access_token, realm_id)

    Raises:
        RefreshTokenExpired: If refresh token has expired
        InvalidGrant: If tokens are invalid/revoked
    """
    tokens = load_tokens()
    if not tokens:
        raise ValueError(
            "No stored tokens found. Run: python -m src.qbo.auth setup"
        )

    _check_refresh_token_expiry(tokens)

    # Check if access token is expired or about to expire (5 min buffer)
    expires_at = datetime.fromisoformat(tokens["expires_at"])
    if datetime.now() >= expires_at - timedelta(minutes=5):
        tokens = refresh_access_token()

    return tokens["access_token"], tokens["realm_id"]


def save_tokens(tokens: dict) -> None:
    """Save tokens to local JSON file with restricted permissions."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)  # Owner read/write only
    logger.info(f"Tokens saved to {TOKEN_FILE}")


def load_tokens() -> Optional[Dict]:
    """
    Load tokens from environment variables (production) or file (local).

    For production (Render), set these environment variables:
    - QBO_ACCESS_TOKEN
    - QBO_REFRESH_TOKEN
    - QBO_REALM_ID
    - QBO_TOKEN_EXPIRES_AT
    - QBO_REFRESH_EXPIRES_AT
    """
    # Check environment variables first (for Render/production)
    access_token = os.getenv("QBO_ACCESS_TOKEN")
    if access_token:
        refresh_token = os.getenv("QBO_REFRESH_TOKEN")
        realm_id = os.getenv("QBO_REALM_ID")

        if not refresh_token or not realm_id:
            logger.warning(
                "Incomplete token env vars: QBO_REFRESH_TOKEN or QBO_REALM_ID missing"
            )
            return None

        logger.debug("Loading tokens from environment variables")
        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "realm_id": realm_id,
            "expires_at": os.getenv("QBO_TOKEN_EXPIRES_AT", ""),
            "refresh_expires_at": os.getenv("QBO_REFRESH_EXPIRES_AT", ""),
        }

    # Fall back to file storage (for local development)
    if not TOKEN_FILE.exists():
        return None

    try:
        with open(TOKEN_FILE) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Corrupted token file {TOKEN_FILE}: {e}")
        return None


def clear_tokens() -> None:
    """Remove stored tokens (used when tokens are invalid)."""
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
        logger.info(f"Cleared tokens from {TOKEN_FILE}")


def export_tokens_for_render() -> None:
    """Print tokens in a format ready to copy to Render environment variables."""
    tokens = load_tokens()
    if not tokens:
        print("No tokens found. Run setup first.")
        return

    print("\n" + "=" * 60)
    print("COPY THESE TO RENDER ENVIRONMENT VARIABLES")
    print("=" * 60)
    print(f"QBO_ACCESS_TOKEN={tokens['access_token']}")
    print(f"QBO_REFRESH_TOKEN={tokens['refresh_token']}")
    print(f"QBO_REALM_ID={tokens['realm_id']}")
    print(f"QBO_TOKEN_EXPIRES_AT={tokens['expires_at']}")
    print(f"QBO_REFRESH_EXPIRES_AT={tokens['refresh_expires_at']}")
    print("=" * 60)
    print("\nNOTE: Access token expires hourly. The web service will")
    print("auto-refresh it, but you'll need to update Render env vars")
    print("if the refresh token expires (~100 days) or is revoked.")
    print()


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures OAuth callback parameters."""

    def do_GET(self):
        """Handle the OAuth callback GET request."""
        query = parse_qs(urlparse(self.path).query)
        self.server.auth_code = query.get("code", [None])[0]
        self.server.realm_id = query.get("realmId", [None])[0]
        self.server.callback_state = query.get("state", [None])[0]
        self.server.error = query.get("error", [None])[0]

        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()

        if self.server.error:
            self.wfile.write(
                b"<html><body><h1>Authorization Failed</h1>"
                b"<p>You can close this window.</p></body></html>"
            )
        else:
            self.wfile.write(
                b"<html><body><h1>Authorization Successful!</h1>"
                b"<p>You can close this window and return to the terminal.</p></body></html>"
            )

    def log_message(self, format, *args):
        """Suppress default logging."""
        pass


def setup_oauth_interactive() -> None:
    """
    Interactive OAuth setup with automatic callback capture.

    Uses a local HTTP server to capture the OAuth callback automatically.
    """
    print("QuickBooks Online OAuth Setup")
    print("=" * 40)
    print()

    # Check environment variables
    required = ["QBO_CLIENT_ID", "QBO_CLIENT_SECRET", "QBO_REDIRECT_URI"]
    missing = [var for var in required if not os.getenv(var)]
    if missing:
        print(f"ERROR: Missing environment variables: {missing}")
        print("Add these to your .env file first.")
        return

    redirect_uri = os.getenv("QBO_REDIRECT_URI", "")

    # Check if redirect URI is localhost (can use automatic callback)
    use_local_server = "localhost" in redirect_uri or "127.0.0.1" in redirect_uri

    # Generate CSRF state token
    state = secrets.token_urlsafe(32)

    if use_local_server:
        # Extract port from redirect URI
        parsed = urlparse(redirect_uri)
        port = parsed.port or 8000

        print(f"Starting local callback server on port {port}...")
        server = HTTPServer(("localhost", port), OAuthCallbackHandler)
        server.auth_code = None
        server.realm_id = None
        server.callback_state = None
        server.error = None

        # Generate auth URL with state
        auth_url = get_authorization_url(state)

        print()
        print("Opening browser for authorization...")
        print("(If browser doesn't open, visit this URL manually:)")
        print()
        print(auth_url)
        print()

        webbrowser.open(auth_url)

        print("Waiting for authorization callback...")
        server.handle_request()

        if server.error:
            print(f"\nERROR: Authorization failed: {server.error}")
            return

        if not server.auth_code or not server.realm_id:
            print("\nERROR: Did not receive authorization code or realm ID.")
            return

        # Validate CSRF state
        if server.callback_state != state:
            print("\nERROR: State mismatch - possible CSRF attack. Aborting.")
            logger.error(
                f"CSRF state mismatch: expected={state}, received={server.callback_state}"
            )
            return

        auth_code = server.auth_code
        realm_id = server.realm_id

    else:
        # Manual flow for non-localhost redirect URIs
        auth_url = get_authorization_url(state)

        print("Step 1: Visit this URL in your browser to authorize:")
        print()
        print(auth_url)
        print()
        print("Step 2: After authorizing, you'll be redirected to your redirect URI.")
        print("Copy the 'code', 'realmId', and 'state' parameters from the URL.")
        print()

        auth_code = input("Enter the authorization code: ").strip()
        realm_id = input("Enter the realm ID (company ID): ").strip()
        callback_state = input("Enter the state parameter: ").strip()

        if not auth_code or not realm_id:
            print("ERROR: Both code and realm ID are required.")
            return

        # Validate CSRF state
        if callback_state != state:
            print("\nERROR: State mismatch - possible CSRF attack. Aborting.")
            return

    try:
        tokens = exchange_code_for_tokens(auth_code, realm_id)
        print()
        print("SUCCESS! Tokens saved to:", TOKEN_FILE)
        print("Access token expires:", tokens["expires_at"])
        print("Refresh token expires:", tokens["refresh_expires_at"])

        # Offer to export for Render
        print()
        export = input("Export tokens for Render? (y/n): ").strip().lower()
        if export == "y":
            export_tokens_for_render()

    except InvalidGrant as e:
        print(f"\nERROR: {e}")
    except Exception as e:
        print(f"\nERROR: Failed to exchange code for tokens: {e}")
        logger.exception("Token exchange failed")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command == "setup":
            setup_oauth_interactive()
        elif command == "export":
            export_tokens_for_render()
        elif command == "refresh":
            try:
                tokens = refresh_access_token()
                print("Token refreshed successfully!")
                print(f"New expiry: {tokens['expires_at']}")
            except (RefreshTokenExpired, InvalidGrant) as e:
                print(f"ERROR: {e}")
        elif command == "clear":
            clear_tokens()
            print("Tokens cleared.")
        else:
            print(f"Unknown command: {command}")
            print("Usage: python -m src.qbo.auth [setup|export|refresh|clear]")
    else:
        print("Usage: python -m src.qbo.auth [setup|export|refresh|clear]")
        print()
        print("Commands:")
        print("  setup   - Interactive OAuth authorization")
        print("  export  - Print tokens for Render environment variables")
        print("  refresh - Manually refresh the access token")
        print("  clear   - Remove stored tokens")
