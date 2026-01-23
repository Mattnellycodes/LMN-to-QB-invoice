"""Tests for QuickBooks Online OAuth authentication."""

import json
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.qbo.auth import (
    QBOAuthError,
    RefreshTokenExpired,
    InvalidGrant,
    CSRFError,
    save_tokens,
    load_tokens,
    clear_tokens,
    get_auth_client,
    get_authorization_url,
    exchange_code_for_tokens,
    refresh_access_token,
    get_valid_access_token,
    export_tokens_for_render,
)


# =============================================================================
# Auto-mock database functions (simulate no database configured)
# =============================================================================


@pytest.fixture(autouse=True)
def mock_database_functions():
    """Mock database functions to simulate no database configured."""
    with patch("src.qbo.auth.load_tokens_from_db", return_value=None):
        with patch("src.qbo.auth.save_tokens_to_db", return_value=False):
            with patch("src.qbo.auth.clear_tokens_from_db", return_value=False):
                yield


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def sample_tokens():
    """Sample token data for testing."""
    return {
        "access_token": "test_access_token_123",
        "refresh_token": "test_refresh_token_456",
        "realm_id": "123456789",
        "expires_at": (datetime.now() + timedelta(hours=1)).isoformat(),
        "refresh_expires_at": (datetime.now() + timedelta(days=100)).isoformat(),
    }


@pytest.fixture
def expired_access_tokens():
    """Tokens with expired access token but valid refresh token."""
    return {
        "access_token": "expired_access_token",
        "refresh_token": "valid_refresh_token",
        "realm_id": "123456789",
        "expires_at": (datetime.now() - timedelta(hours=1)).isoformat(),
        "refresh_expires_at": (datetime.now() + timedelta(days=50)).isoformat(),
    }


@pytest.fixture
def expiring_soon_tokens():
    """Tokens expiring within 5-minute buffer."""
    return {
        "access_token": "expiring_soon_access_token",
        "refresh_token": "valid_refresh_token",
        "realm_id": "123456789",
        "expires_at": (datetime.now() + timedelta(minutes=3)).isoformat(),
        "refresh_expires_at": (datetime.now() + timedelta(days=50)).isoformat(),
    }


@pytest.fixture
def expired_refresh_tokens():
    """Tokens with expired refresh token."""
    return {
        "access_token": "some_access_token",
        "refresh_token": "expired_refresh_token",
        "realm_id": "123456789",
        "expires_at": (datetime.now() - timedelta(hours=1)).isoformat(),
        "refresh_expires_at": (datetime.now() - timedelta(days=1)).isoformat(),
    }


@pytest.fixture
def mock_auth_client():
    """Mock AuthClient for testing."""
    mock = MagicMock()
    mock.access_token = "new_access_token"
    mock.refresh_token = "new_refresh_token"
    mock.intuit_tid = "test_intuit_tid_123"
    return mock


# =============================================================================
# Test Exception Classes
# =============================================================================


class TestExceptionClasses:
    """Test custom exception class hierarchy."""

    def test_qboauth_error_is_base_exception(self):
        """QBOAuthError inherits from Exception."""
        assert issubclass(QBOAuthError, Exception)

    def test_refresh_token_expired_inherits_from_qboauth_error(self):
        """RefreshTokenExpired inherits from QBOAuthError."""
        assert issubclass(RefreshTokenExpired, QBOAuthError)

    def test_invalid_grant_inherits_from_qboauth_error(self):
        """InvalidGrant inherits from QBOAuthError."""
        assert issubclass(InvalidGrant, QBOAuthError)

    def test_csrf_error_inherits_from_qboauth_error(self):
        """CSRFError inherits from QBOAuthError."""
        assert issubclass(CSRFError, QBOAuthError)

    def test_exceptions_accept_message(self):
        """All custom exceptions accept a message parameter."""
        msg = "Test error message"

        err1 = QBOAuthError(msg)
        assert str(err1) == msg

        err2 = RefreshTokenExpired(msg)
        assert str(err2) == msg

        err3 = InvalidGrant(msg)
        assert str(err3) == msg

        err4 = CSRFError(msg)
        assert str(err4) == msg


# =============================================================================
# Test Token Storage Functions
# =============================================================================


class TestTokenStorage:
    """Test save_tokens, load_tokens, clear_tokens functions."""

    def test_save_tokens_creates_parent_directory(self, sample_tokens, tmp_path):
        """save_tokens creates parent directory if it doesn't exist."""
        token_path = tmp_path / "new_dir" / "tokens.json"

        with patch("src.qbo.auth.TOKEN_FILE", token_path):
            save_tokens(sample_tokens)

        assert token_path.parent.exists()
        assert token_path.exists()

    def test_save_tokens_writes_json(self, sample_tokens, tmp_path):
        """save_tokens writes tokens as JSON."""
        token_path = tmp_path / "tokens.json"

        with patch("src.qbo.auth.TOKEN_FILE", token_path):
            save_tokens(sample_tokens)

        with open(token_path) as f:
            saved = json.load(f)

        assert saved["access_token"] == sample_tokens["access_token"]
        assert saved["refresh_token"] == sample_tokens["refresh_token"]
        assert saved["realm_id"] == sample_tokens["realm_id"]

    def test_load_tokens_from_file(self, sample_tokens, tmp_path):
        """load_tokens reads from file when env vars not set."""
        token_path = tmp_path / "tokens.json"
        token_path.parent.mkdir(parents=True, exist_ok=True)

        with open(token_path, "w") as f:
            json.dump(sample_tokens, f)

        with patch("src.qbo.auth.TOKEN_FILE", token_path):
            with patch.dict(os.environ, {}, clear=True):
                # Ensure QBO_ACCESS_TOKEN is not set
                os.environ.pop("QBO_ACCESS_TOKEN", None)
                loaded = load_tokens()

        assert loaded["access_token"] == sample_tokens["access_token"]
        assert loaded["refresh_token"] == sample_tokens["refresh_token"]

    def test_load_tokens_prefers_env_vars(self, tmp_path):
        """load_tokens prefers environment variables over file (production mode)."""
        token_path = tmp_path / "tokens.json"
        token_path.parent.mkdir(parents=True, exist_ok=True)

        # Write different tokens to file
        file_tokens = {"access_token": "file_token", "refresh_token": "file_refresh"}
        with open(token_path, "w") as f:
            json.dump(file_tokens, f)

        env_vars = {
            "QBO_ACCESS_TOKEN": "env_access_token",
            "QBO_REFRESH_TOKEN": "env_refresh_token",
            "QBO_REALM_ID": "env_realm_id",
            "QBO_TOKEN_EXPIRES_AT": "2026-01-20T12:00:00",
            "QBO_REFRESH_EXPIRES_AT": "2026-04-20T12:00:00",
        }

        with patch("src.qbo.auth.TOKEN_FILE", token_path):
            with patch.dict(os.environ, env_vars, clear=False):
                loaded = load_tokens()

        assert loaded["access_token"] == "env_access_token"
        assert loaded["refresh_token"] == "env_refresh_token"
        assert loaded["realm_id"] == "env_realm_id"

    def test_load_tokens_returns_none_when_no_file_no_env(self, tmp_path):
        """load_tokens returns None when no file and no env vars."""
        token_path = tmp_path / "nonexistent" / "tokens.json"

        with patch("src.qbo.auth.TOKEN_FILE", token_path):
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("QBO_ACCESS_TOKEN", None)
                loaded = load_tokens()

        assert loaded is None

    def test_clear_tokens_deletes_file(self, sample_tokens, tmp_path):
        """clear_tokens deletes the token file."""
        token_path = tmp_path / "tokens.json"
        token_path.parent.mkdir(parents=True, exist_ok=True)

        with open(token_path, "w") as f:
            json.dump(sample_tokens, f)

        assert token_path.exists()

        with patch("src.qbo.auth.TOKEN_FILE", token_path):
            clear_tokens()

        assert not token_path.exists()

    def test_clear_tokens_handles_missing_file(self, tmp_path):
        """clear_tokens handles missing file gracefully."""
        token_path = tmp_path / "nonexistent_tokens.json"

        with patch("src.qbo.auth.TOKEN_FILE", token_path):
            # Should not raise an exception
            clear_tokens()


# =============================================================================
# Test Get Auth Client
# =============================================================================


class TestGetAuthClient:
    """Test get_auth_client function."""

    def test_get_auth_client_creates_auth_client(self):
        """get_auth_client creates AuthClient with env credentials."""
        env_vars = {
            "QBO_CLIENT_ID": "test_client_id",
            "QBO_CLIENT_SECRET": "test_client_secret",
            "QBO_REDIRECT_URI": "http://localhost:8000/callback",
        }

        with patch.dict(os.environ, env_vars, clear=False):
            with patch("src.qbo.auth.AuthClient") as MockAuthClient:
                get_auth_client()

                MockAuthClient.assert_called_once_with(
                    client_id="test_client_id",
                    client_secret="test_client_secret",
                    redirect_uri="http://localhost:8000/callback",
                    environment="production",
                )


# =============================================================================
# Test Get Authorization URL
# =============================================================================


class TestGetAuthorizationUrl:
    """Test get_authorization_url function."""

    def test_get_authorization_url_returns_url(self):
        """get_authorization_url returns URL from AuthClient."""
        mock_client = MagicMock()
        mock_client.get_authorization_url.return_value = "https://appcenter.intuit.com/connect/oauth2?..."

        with patch("src.qbo.auth.get_auth_client", return_value=mock_client):
            url = get_authorization_url()

        assert url == "https://appcenter.intuit.com/connect/oauth2?..."
        mock_client.get_authorization_url.assert_called_once()

    def test_get_authorization_url_passes_state(self):
        """get_authorization_url passes state token for CSRF protection."""
        mock_client = MagicMock()
        mock_client.get_authorization_url.return_value = "https://example.com"

        with patch("src.qbo.auth.get_auth_client", return_value=mock_client):
            get_authorization_url(state="csrf_token_123")

        call_args = mock_client.get_authorization_url.call_args
        assert call_args[1]["state_token"] == "csrf_token_123"


# =============================================================================
# Test Exchange Code For Tokens
# =============================================================================


class TestExchangeCodeForTokens:
    """Test exchange_code_for_tokens function."""

    def test_exchange_code_saves_and_returns_tokens(self, tmp_path, mock_auth_client):
        """exchange_code_for_tokens saves and returns tokens on success."""
        token_path = tmp_path / "tokens.json"

        with patch("src.qbo.auth.get_auth_client", return_value=mock_auth_client):
            with patch("src.qbo.auth.TOKEN_FILE", token_path):
                tokens = exchange_code_for_tokens("auth_code_123", "realm_456")

        assert tokens["access_token"] == "new_access_token"
        assert tokens["refresh_token"] == "new_refresh_token"
        assert tokens["realm_id"] == "realm_456"
        assert "expires_at" in tokens
        assert "refresh_expires_at" in tokens

        # Verify file was saved
        assert token_path.exists()

    def test_exchange_code_raises_invalid_grant_on_reused_code(self, mock_auth_client):
        """exchange_code_for_tokens raises InvalidGrant when auth code reused."""
        from intuitlib.exceptions import AuthClientError

        # Create a mock response object that AuthClientError expects
        # AuthClientError uses response.content in __str__, so set that
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.content = b"invalid_grant: code has already been used"
        mock_response.headers.get.return_value = "test-tid"
        mock_auth_client.get_bearer_token.side_effect = AuthClientError(mock_response)

        with patch("src.qbo.auth.get_auth_client", return_value=mock_auth_client):
            with pytest.raises(InvalidGrant) as exc_info:
                exchange_code_for_tokens("reused_code", "realm_123")

        assert "invalid" in str(exc_info.value).lower()

    def test_exchange_code_logs_intuit_tid(self, tmp_path, mock_auth_client, caplog):
        """exchange_code_for_tokens logs intuit_tid when available."""
        token_path = tmp_path / "tokens.json"
        mock_auth_client.intuit_tid = "tid_abc123"

        import logging

        with caplog.at_level(logging.INFO):
            with patch("src.qbo.auth.get_auth_client", return_value=mock_auth_client):
                with patch("src.qbo.auth.TOKEN_FILE", token_path):
                    exchange_code_for_tokens("code", "realm")

        assert any("intuit_tid" in record.message for record in caplog.records)


# =============================================================================
# Test Refresh Access Token
# =============================================================================


class TestRefreshAccessToken:
    """Test refresh_access_token function."""

    def test_refresh_updates_stored_tokens(self, sample_tokens, tmp_path, mock_auth_client):
        """refresh_access_token updates stored tokens on success."""
        token_path = tmp_path / "tokens.json"
        token_path.parent.mkdir(parents=True, exist_ok=True)

        with open(token_path, "w") as f:
            json.dump(sample_tokens, f)

        with patch("src.qbo.auth.get_auth_client", return_value=mock_auth_client):
            with patch("src.qbo.auth.TOKEN_FILE", token_path):
                with patch.dict(os.environ, {}, clear=True):
                    os.environ.pop("QBO_ACCESS_TOKEN", None)
                    tokens = refresh_access_token()

        assert tokens["access_token"] == "new_access_token"
        assert tokens["refresh_token"] == "new_refresh_token"

    def test_refresh_raises_value_error_when_no_tokens(self, tmp_path):
        """refresh_access_token raises ValueError when no tokens stored."""
        token_path = tmp_path / "nonexistent_tokens.json"

        with patch("src.qbo.auth.TOKEN_FILE", token_path):
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("QBO_ACCESS_TOKEN", None)
                with pytest.raises(ValueError) as exc_info:
                    refresh_access_token()

        assert "no stored tokens" in str(exc_info.value).lower()

    def test_refresh_raises_refresh_token_expired(self, expired_refresh_tokens, tmp_path):
        """refresh_access_token raises RefreshTokenExpired when refresh token expired."""
        token_path = tmp_path / "tokens.json"
        token_path.parent.mkdir(parents=True, exist_ok=True)

        with open(token_path, "w") as f:
            json.dump(expired_refresh_tokens, f)

        with patch("src.qbo.auth.TOKEN_FILE", token_path):
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("QBO_ACCESS_TOKEN", None)
                with pytest.raises(RefreshTokenExpired) as exc_info:
                    refresh_access_token()

        assert "expired" in str(exc_info.value).lower()
        # Tokens should be cleared
        assert not token_path.exists()

    def test_refresh_raises_invalid_grant_when_revoked(self, sample_tokens, tmp_path, mock_auth_client):
        """refresh_access_token raises InvalidGrant when token revoked."""
        from intuitlib.exceptions import AuthClientError

        token_path = tmp_path / "tokens.json"
        token_path.parent.mkdir(parents=True, exist_ok=True)

        with open(token_path, "w") as f:
            json.dump(sample_tokens, f)

        # Create a mock response object that AuthClientError expects
        # AuthClientError uses response.content in __str__, so set that
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.content = b"invalid_grant: token revoked"
        mock_response.headers.get.return_value = "test-tid"
        mock_auth_client.refresh.side_effect = AuthClientError(mock_response)

        with patch("src.qbo.auth.get_auth_client", return_value=mock_auth_client):
            with patch("src.qbo.auth.TOKEN_FILE", token_path):
                with patch.dict(os.environ, {}, clear=True):
                    os.environ.pop("QBO_ACCESS_TOKEN", None)
                    with pytest.raises(InvalidGrant):
                        refresh_access_token()

        # Tokens should be cleared on invalid grant
        assert not token_path.exists()


# =============================================================================
# Test Get Valid Access Token
# =============================================================================


class TestGetValidAccessToken:
    """Test get_valid_access_token function."""

    def test_returns_valid_token_without_refresh(self, sample_tokens, tmp_path):
        """get_valid_access_token returns valid token without refresh."""
        token_path = tmp_path / "tokens.json"
        token_path.parent.mkdir(parents=True, exist_ok=True)

        with open(token_path, "w") as f:
            json.dump(sample_tokens, f)

        with patch("src.qbo.auth.TOKEN_FILE", token_path):
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("QBO_ACCESS_TOKEN", None)
                with patch("src.qbo.auth.refresh_access_token") as mock_refresh:
                    access_token, realm_id = get_valid_access_token()

        mock_refresh.assert_not_called()
        assert access_token == sample_tokens["access_token"]
        assert realm_id == sample_tokens["realm_id"]

    def test_refreshes_expired_access_token(self, expired_access_tokens, tmp_path, mock_auth_client):
        """get_valid_access_token refreshes expired access token."""
        token_path = tmp_path / "tokens.json"
        token_path.parent.mkdir(parents=True, exist_ok=True)

        with open(token_path, "w") as f:
            json.dump(expired_access_tokens, f)

        refreshed_tokens = {
            "access_token": "refreshed_token",
            "refresh_token": "new_refresh",
            "realm_id": "123456789",
            "expires_at": (datetime.now() + timedelta(hours=1)).isoformat(),
            "refresh_expires_at": (datetime.now() + timedelta(days=100)).isoformat(),
        }

        with patch("src.qbo.auth.TOKEN_FILE", token_path):
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("QBO_ACCESS_TOKEN", None)
                with patch("src.qbo.auth.refresh_access_token", return_value=refreshed_tokens):
                    access_token, realm_id = get_valid_access_token()

        assert access_token == "refreshed_token"

    def test_refreshes_within_5_minute_buffer(self, expiring_soon_tokens, tmp_path):
        """get_valid_access_token refreshes within 5-minute buffer."""
        token_path = tmp_path / "tokens.json"
        token_path.parent.mkdir(parents=True, exist_ok=True)

        with open(token_path, "w") as f:
            json.dump(expiring_soon_tokens, f)

        refreshed_tokens = {
            "access_token": "refreshed_early",
            "refresh_token": "new_refresh",
            "realm_id": "123456789",
            "expires_at": (datetime.now() + timedelta(hours=1)).isoformat(),
            "refresh_expires_at": (datetime.now() + timedelta(days=100)).isoformat(),
        }

        with patch("src.qbo.auth.TOKEN_FILE", token_path):
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("QBO_ACCESS_TOKEN", None)
                with patch("src.qbo.auth.refresh_access_token", return_value=refreshed_tokens) as mock_refresh:
                    access_token, _ = get_valid_access_token()

        mock_refresh.assert_called_once()
        assert access_token == "refreshed_early"

    def test_raises_value_error_when_no_tokens(self, tmp_path):
        """get_valid_access_token raises ValueError when no tokens."""
        token_path = tmp_path / "nonexistent.json"

        with patch("src.qbo.auth.TOKEN_FILE", token_path):
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("QBO_ACCESS_TOKEN", None)
                with pytest.raises(ValueError) as exc_info:
                    get_valid_access_token()

        assert "no stored tokens" in str(exc_info.value).lower()

    def test_raises_refresh_token_expired(self, expired_refresh_tokens, tmp_path):
        """get_valid_access_token raises RefreshTokenExpired when refresh token expired."""
        token_path = tmp_path / "tokens.json"
        token_path.parent.mkdir(parents=True, exist_ok=True)

        with open(token_path, "w") as f:
            json.dump(expired_refresh_tokens, f)

        with patch("src.qbo.auth.TOKEN_FILE", token_path):
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("QBO_ACCESS_TOKEN", None)
                with pytest.raises(RefreshTokenExpired):
                    get_valid_access_token()


# =============================================================================
# Test Export Tokens For Render
# =============================================================================


class TestExportTokensForRender:
    """Test export_tokens_for_render function."""

    def test_export_prints_env_var_format(self, sample_tokens, tmp_path, capsys):
        """export_tokens_for_render prints env var format."""
        token_path = tmp_path / "tokens.json"
        token_path.parent.mkdir(parents=True, exist_ok=True)

        with open(token_path, "w") as f:
            json.dump(sample_tokens, f)

        with patch("src.qbo.auth.TOKEN_FILE", token_path):
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("QBO_ACCESS_TOKEN", None)
                export_tokens_for_render()

        captured = capsys.readouterr()
        assert f"QBO_ACCESS_TOKEN={sample_tokens['access_token']}" in captured.out
        assert f"QBO_REFRESH_TOKEN={sample_tokens['refresh_token']}" in captured.out
        assert f"QBO_REALM_ID={sample_tokens['realm_id']}" in captured.out
        assert "RENDER" in captured.out

    def test_export_handles_missing_tokens(self, tmp_path, capsys):
        """export_tokens_for_render handles missing tokens gracefully."""
        token_path = tmp_path / "nonexistent.json"

        with patch("src.qbo.auth.TOKEN_FILE", token_path):
            with patch.dict(os.environ, {}, clear=True):
                os.environ.pop("QBO_ACCESS_TOKEN", None)
                export_tokens_for_render()

        captured = capsys.readouterr()
        assert "No tokens found" in captured.out
