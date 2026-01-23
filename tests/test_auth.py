"""Tests for QuickBooks Online OAuth authentication (session-based)."""

import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.qbo.auth import (
    QBOAuthError,
    RefreshTokenExpired,
    InvalidGrant,
    CSRFError,
    NotAuthenticated,
    get_auth_client,
    get_authorization_url,
    exchange_code_for_tokens,
    refresh_access_token,
    get_valid_tokens,
    get_access_token_and_realm,
    is_token_valid,
    get_token_status,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def valid_tokens():
    """Valid token data for testing."""
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

    def test_not_authenticated_inherits_from_qboauth_error(self):
        """NotAuthenticated inherits from QBOAuthError."""
        assert issubclass(NotAuthenticated, QBOAuthError)

    def test_exceptions_accept_message(self):
        """All custom exceptions accept a message parameter."""
        msg = "Test error message"

        assert str(QBOAuthError(msg)) == msg
        assert str(RefreshTokenExpired(msg)) == msg
        assert str(InvalidGrant(msg)) == msg
        assert str(CSRFError(msg)) == msg
        assert str(NotAuthenticated(msg)) == msg


# =============================================================================
# Test Get Auth Client
# =============================================================================


class TestGetAuthClient:
    """Test get_auth_client function."""

    def test_creates_auth_client_with_env_credentials(self):
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

    def test_raises_error_when_client_id_missing(self):
        """Raises QBOAuthError when QBO_CLIENT_ID missing."""
        env_vars = {
            "QBO_CLIENT_SECRET": "secret",
            "QBO_REDIRECT_URI": "http://localhost/qbo/callback",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            with pytest.raises(QBOAuthError) as exc_info:
                get_auth_client()

        assert "QBO_CLIENT_ID" in str(exc_info.value)

    def test_raises_error_when_multiple_vars_missing(self):
        """Raises QBOAuthError listing all missing variables."""
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(QBOAuthError) as exc_info:
                get_auth_client()

        error_msg = str(exc_info.value)
        assert "QBO_CLIENT_ID" in error_msg
        assert "QBO_CLIENT_SECRET" in error_msg
        assert "QBO_REDIRECT_URI" in error_msg


# =============================================================================
# Test Get Authorization URL
# =============================================================================


class TestGetAuthorizationUrl:
    """Test get_authorization_url function."""

    def test_returns_url_from_auth_client(self):
        """get_authorization_url returns URL from AuthClient."""
        mock_client = MagicMock()
        mock_client.get_authorization_url.return_value = "https://appcenter.intuit.com/connect/oauth2?..."

        with patch("src.qbo.auth.get_auth_client", return_value=mock_client):
            url = get_authorization_url()

        assert url == "https://appcenter.intuit.com/connect/oauth2?..."
        mock_client.get_authorization_url.assert_called_once()

    def test_passes_state_for_csrf_protection(self):
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

    def test_returns_tokens_on_success(self, mock_auth_client):
        """exchange_code_for_tokens returns tokens dict on success."""
        with patch("src.qbo.auth.get_auth_client", return_value=mock_auth_client):
            tokens = exchange_code_for_tokens("auth_code_123", "realm_456")

        assert tokens["access_token"] == "new_access_token"
        assert tokens["refresh_token"] == "new_refresh_token"
        assert tokens["realm_id"] == "realm_456"
        assert "expires_at" in tokens
        assert "refresh_expires_at" in tokens

    def test_raises_invalid_grant_on_reused_code(self, mock_auth_client):
        """exchange_code_for_tokens raises InvalidGrant when auth code reused."""
        from intuitlib.exceptions import AuthClientError

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.content = b"invalid_grant: code has already been used"
        mock_response.headers.get.return_value = "test-tid"
        mock_auth_client.get_bearer_token.side_effect = AuthClientError(mock_response)

        with patch("src.qbo.auth.get_auth_client", return_value=mock_auth_client):
            with pytest.raises(InvalidGrant) as exc_info:
                exchange_code_for_tokens("reused_code", "realm_123")

        assert "invalid" in str(exc_info.value).lower()

    def test_logs_intuit_tid(self, mock_auth_client, caplog):
        """exchange_code_for_tokens logs intuit_tid when available."""
        mock_auth_client.intuit_tid = "tid_abc123"

        import logging

        with caplog.at_level(logging.INFO):
            with patch("src.qbo.auth.get_auth_client", return_value=mock_auth_client):
                exchange_code_for_tokens("code", "realm")

        assert any("intuit_tid" in record.message for record in caplog.records)


# =============================================================================
# Test Refresh Access Token
# =============================================================================


class TestRefreshAccessToken:
    """Test refresh_access_token function."""

    def test_updates_tokens_on_success(self, valid_tokens, mock_auth_client):
        """refresh_access_token returns updated tokens on success."""
        with patch("src.qbo.auth.get_auth_client", return_value=mock_auth_client):
            result = refresh_access_token(valid_tokens)

        assert result["access_token"] == "new_access_token"
        assert result["refresh_token"] == "new_refresh_token"

    def test_raises_not_authenticated_when_no_tokens(self):
        """refresh_access_token raises NotAuthenticated when no tokens provided."""
        with pytest.raises(NotAuthenticated):
            refresh_access_token(None)

        with pytest.raises(NotAuthenticated):
            refresh_access_token({})

    def test_raises_refresh_token_expired(self, expired_refresh_tokens):
        """refresh_access_token raises RefreshTokenExpired when refresh token expired."""
        with pytest.raises(RefreshTokenExpired) as exc_info:
            refresh_access_token(expired_refresh_tokens)

        assert "expired" in str(exc_info.value).lower()

    def test_raises_invalid_grant_when_revoked(self, valid_tokens, mock_auth_client):
        """refresh_access_token raises InvalidGrant when token revoked."""
        from intuitlib.exceptions import AuthClientError

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.content = b"invalid_grant: token revoked"
        mock_response.headers.get.return_value = "test-tid"
        mock_auth_client.refresh.side_effect = AuthClientError(mock_response)

        with patch("src.qbo.auth.get_auth_client", return_value=mock_auth_client):
            with pytest.raises(InvalidGrant):
                refresh_access_token(valid_tokens)


# =============================================================================
# Test Get Valid Tokens
# =============================================================================


class TestGetValidTokens:
    """Test get_valid_tokens function."""

    def test_returns_valid_tokens_without_refresh(self, valid_tokens):
        """get_valid_tokens returns valid tokens without refresh."""
        with patch("src.qbo.auth.refresh_access_token") as mock_refresh:
            result = get_valid_tokens(valid_tokens)

        mock_refresh.assert_not_called()
        assert result == valid_tokens

    def test_refreshes_expired_access_token(self, expired_access_tokens):
        """get_valid_tokens refreshes expired access token."""
        refreshed = expired_access_tokens.copy()
        refreshed["access_token"] = "refreshed_token"

        with patch("src.qbo.auth.refresh_access_token", return_value=refreshed) as mock_refresh:
            result = get_valid_tokens(expired_access_tokens)

        mock_refresh.assert_called_once()
        assert result["access_token"] == "refreshed_token"

    def test_refreshes_within_5_minute_buffer(self, expiring_soon_tokens):
        """get_valid_tokens refreshes within 5-minute buffer."""
        refreshed = expiring_soon_tokens.copy()
        refreshed["access_token"] = "refreshed_early"

        with patch("src.qbo.auth.refresh_access_token", return_value=refreshed) as mock_refresh:
            result = get_valid_tokens(expiring_soon_tokens)

        mock_refresh.assert_called_once()
        assert result["access_token"] == "refreshed_early"

    def test_raises_not_authenticated_when_no_tokens(self):
        """get_valid_tokens raises NotAuthenticated when no tokens."""
        with pytest.raises(NotAuthenticated):
            get_valid_tokens(None)

        with pytest.raises(NotAuthenticated):
            get_valid_tokens({})

        with pytest.raises(NotAuthenticated):
            get_valid_tokens({"refresh_token": "only_refresh"})


# =============================================================================
# Test Get Access Token And Realm
# =============================================================================


class TestGetAccessTokenAndRealm:
    """Test get_access_token_and_realm function."""

    def test_returns_tuple(self, valid_tokens):
        """get_access_token_and_realm returns (access_token, realm_id)."""
        with patch("src.qbo.auth.get_valid_tokens", return_value=valid_tokens):
            access_token, realm_id = get_access_token_and_realm(valid_tokens)

        assert access_token == valid_tokens["access_token"]
        assert realm_id == valid_tokens["realm_id"]


# =============================================================================
# Test Is Token Valid
# =============================================================================


class TestIsTokenValid:
    """Test is_token_valid function."""

    def test_returns_true_for_valid_tokens(self, valid_tokens):
        """is_token_valid returns True for non-expired tokens."""
        assert is_token_valid(valid_tokens) is True

    def test_returns_false_for_expired_tokens(self, expired_access_tokens):
        """is_token_valid returns False for expired tokens."""
        assert is_token_valid(expired_access_tokens) is False

    def test_returns_false_for_none(self):
        """is_token_valid returns False for None."""
        assert is_token_valid(None) is False

    def test_returns_false_for_empty_dict(self):
        """is_token_valid returns False for empty dict."""
        assert is_token_valid({}) is False

    def test_returns_false_for_missing_access_token(self):
        """is_token_valid returns False when access_token missing."""
        tokens = {"refresh_token": "refresh", "realm_id": "123"}
        assert is_token_valid(tokens) is False

    def test_returns_false_for_invalid_expires_at(self):
        """is_token_valid returns False for invalid expires_at format."""
        tokens = {
            "access_token": "token",
            "expires_at": "not-a-date",
        }
        assert is_token_valid(tokens) is False


# =============================================================================
# Test Get Token Status
# =============================================================================


class TestGetTokenStatus:
    """Test get_token_status function."""

    def test_returns_not_connected_for_none(self):
        """get_token_status returns not connected for None."""
        status = get_token_status(None)
        assert status["connected"] is False
        assert "Not connected" in status["message"]

    def test_returns_connected_with_details(self, valid_tokens):
        """get_token_status returns connected with token details."""
        status = get_token_status(valid_tokens)
        assert status["connected"] is True
        assert status["realm_id"] == "123456789"
        assert status["access_token_valid"] is True
        assert status["refresh_token_valid"] is True

    def test_shows_expired_access_token(self, expired_access_tokens):
        """get_token_status shows expired access token."""
        status = get_token_status(expired_access_tokens)
        assert status["connected"] is True
        assert status["access_token_valid"] is False
        assert status["refresh_token_valid"] is True

    def test_handles_parse_error_gracefully(self):
        """get_token_status handles parse errors gracefully."""
        tokens = {
            "access_token": "token",
            "realm_id": "123",
            "expires_at": "invalid-date",
        }
        status = get_token_status(tokens)
        assert status["connected"] is True
        assert "error" in status
