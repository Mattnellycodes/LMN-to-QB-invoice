"""QuickBooks Online OAuth authentication."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple

from dotenv import load_dotenv
from intuitlib.client import AuthClient
from intuitlib.enums import Scopes

load_dotenv()

# Token storage file
TOKEN_FILE = Path(__file__).parent.parent.parent / "config" / ".qbo_tokens.json"


def get_auth_client() -> AuthClient:
    """Create an AuthClient instance with credentials from environment."""
    return AuthClient(
        client_id=os.getenv("QBO_CLIENT_ID"),
        client_secret=os.getenv("QBO_CLIENT_SECRET"),
        redirect_uri=os.getenv("QBO_REDIRECT_URI"),
        environment="production",
    )


def get_authorization_url() -> str:
    """
    Get the URL for initial OAuth authorization.

    User must visit this URL in a browser to authorize the app.
    """
    auth_client = get_auth_client()
    scopes = [Scopes.ACCOUNTING]
    return auth_client.get_authorization_url(scopes)


def exchange_code_for_tokens(auth_code: str, realm_id: str) -> dict:
    """
    Exchange authorization code for access and refresh tokens.

    Call this after user authorizes via the authorization URL.
    """
    auth_client = get_auth_client()
    auth_client.get_bearer_token(auth_code, realm_id=realm_id)

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
    """Refresh the access token using the stored refresh token."""
    tokens = load_tokens()
    if not tokens:
        raise ValueError("No stored tokens found. Run initial authorization first.")

    auth_client = get_auth_client()
    auth_client.refresh(refresh_token=tokens["refresh_token"])

    tokens["access_token"] = auth_client.access_token
    tokens["refresh_token"] = auth_client.refresh_token
    tokens["expires_at"] = (datetime.now() + timedelta(hours=1)).isoformat()

    save_tokens(tokens)
    return tokens


def get_valid_access_token() -> Tuple[str, str]:
    """
    Get a valid access token, refreshing if necessary.

    Returns:
        (access_token, realm_id)
    """
    tokens = load_tokens()
    if not tokens:
        raise ValueError(
            "No stored tokens found. Run: python -m src.qbo.auth setup"
        )

    # Check if token is expired or about to expire (5 min buffer)
    expires_at = datetime.fromisoformat(tokens["expires_at"])
    if datetime.now() >= expires_at - timedelta(minutes=5):
        tokens = refresh_access_token()

    return tokens["access_token"], tokens["realm_id"]


def save_tokens(tokens: dict) -> None:
    """Save tokens to file."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(tokens, f, indent=2)


def load_tokens() -> Optional[Dict]:
    """Load tokens from file."""
    if not TOKEN_FILE.exists():
        return None
    with open(TOKEN_FILE) as f:
        return json.load(f)


def setup_oauth_interactive() -> None:
    """Interactive setup for OAuth authorization."""
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

    print("Step 1: Visit this URL in your browser to authorize:")
    print()
    print(get_authorization_url())
    print()
    print("Step 2: After authorizing, you'll be redirected to your redirect URI.")
    print("Copy the 'code' and 'realmId' parameters from the URL.")
    print()

    auth_code = input("Enter the authorization code: ").strip()
    realm_id = input("Enter the realm ID (company ID): ").strip()

    if not auth_code or not realm_id:
        print("ERROR: Both code and realm ID are required.")
        return

    try:
        tokens = exchange_code_for_tokens(auth_code, realm_id)
        print()
        print("SUCCESS! Tokens saved to:", TOKEN_FILE)
        print("Access token expires:", tokens["expires_at"])
    except Exception as e:
        print(f"ERROR: Failed to exchange code for tokens: {e}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        setup_oauth_interactive()
    else:
        print("Usage: python -m src.qbo.auth setup")
