"""Tests for QBO authentication context."""

import pytest
from flask import Flask

from src.qbo.auth import NotAuthenticated
from src.qbo.context import get_qbo_credentials, set_qbo_credentials, has_qbo_credentials


@pytest.fixture
def app():
    """Create Flask app for testing."""
    app = Flask(__name__)
    app.config["TESTING"] = True
    return app


class TestGetQboCredentials:
    """Test get_qbo_credentials function."""

    def test_returns_credentials_when_set(self, app):
        """Returns (access_token, realm_id) when credentials are set."""
        with app.app_context():
            set_qbo_credentials("test_token", "test_realm")
            access_token, realm_id = get_qbo_credentials()

        assert access_token == "test_token"
        assert realm_id == "test_realm"

    def test_raises_not_authenticated_when_not_set(self, app):
        """Raises NotAuthenticated when credentials not set."""
        with app.app_context():
            with pytest.raises(NotAuthenticated) as exc_info:
                get_qbo_credentials()

        assert "Not connected" in str(exc_info.value)

    def test_raises_when_only_token_set(self, app):
        """Raises NotAuthenticated when only access_token is set."""
        with app.app_context():
            from flask import g
            g.qbo_access_token = "token"
            # realm_id not set

            with pytest.raises(NotAuthenticated):
                get_qbo_credentials()

    def test_raises_when_only_realm_set(self, app):
        """Raises NotAuthenticated when only realm_id is set."""
        with app.app_context():
            from flask import g
            g.qbo_realm_id = "realm"
            # access_token not set

            with pytest.raises(NotAuthenticated):
                get_qbo_credentials()


class TestSetQboCredentials:
    """Test set_qbo_credentials function."""

    def test_sets_credentials_in_context(self, app):
        """Sets credentials in Flask g object."""
        with app.app_context():
            set_qbo_credentials("my_token", "my_realm")

            from flask import g
            assert g.qbo_access_token == "my_token"
            assert g.qbo_realm_id == "my_realm"


class TestHasQboCredentials:
    """Test has_qbo_credentials function."""

    def test_returns_true_when_both_set(self, app):
        """Returns True when both token and realm are set."""
        with app.app_context():
            set_qbo_credentials("token", "realm")
            assert has_qbo_credentials() is True

    def test_returns_false_when_not_set(self, app):
        """Returns False when credentials not set."""
        with app.app_context():
            assert has_qbo_credentials() is False

    def test_returns_false_when_only_token_set(self, app):
        """Returns False when only token is set."""
        with app.app_context():
            from flask import g
            g.qbo_access_token = "token"
            assert has_qbo_credentials() is False

    def test_returns_false_when_only_realm_set(self, app):
        """Returns False when only realm is set."""
        with app.app_context():
            from flask import g
            g.qbo_realm_id = "realm"
            assert has_qbo_credentials() is False
