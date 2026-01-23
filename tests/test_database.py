"""Tests for database connection, models, and database token storage."""

import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.qbo.models import Base, QBOToken


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def in_memory_engine():
    """Create an in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def db_session(in_memory_engine):
    """Create a database session for testing."""
    Session = sessionmaker(bind=in_memory_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def sample_token_data():
    """Sample token data for testing."""
    return {
        "access_token": "test_access_token_abc123",
        "refresh_token": "test_refresh_token_xyz789",
        "realm_id": "1234567890",
        "expires_at": (datetime.now() + timedelta(hours=1)).isoformat(),
        "refresh_expires_at": (datetime.now() + timedelta(days=100)).isoformat(),
    }


@pytest.fixture
def sample_qbo_token(db_session):
    """Create a sample QBOToken in the database."""
    token = QBOToken(
        id=1,
        access_token="db_access_token",
        refresh_token="db_refresh_token",
        realm_id="9876543210",
        expires_at=datetime.now() + timedelta(hours=1),
        refresh_expires_at=datetime.now() + timedelta(days=100),
    )
    db_session.add(token)
    db_session.commit()
    return token


# =============================================================================
# Test QBOToken Model
# =============================================================================


class TestQBOTokenModel:
    """Test QBOToken SQLAlchemy model."""

    def test_create_token(self, db_session):
        """Can create a QBOToken record."""
        token = QBOToken(
            id=1,
            access_token="access_123",
            refresh_token="refresh_456",
            realm_id="realm_789",
            expires_at=datetime(2026, 1, 22, 12, 0, 0),
            refresh_expires_at=datetime(2026, 5, 1, 12, 0, 0),
        )
        db_session.add(token)
        db_session.commit()

        # Query back
        saved = db_session.query(QBOToken).filter_by(id=1).first()
        assert saved is not None
        assert saved.access_token == "access_123"
        assert saved.refresh_token == "refresh_456"
        assert saved.realm_id == "realm_789"

    def test_to_dict(self, db_session):
        """to_dict returns correct dictionary format."""
        expires = datetime(2026, 1, 22, 12, 0, 0)
        refresh_expires = datetime(2026, 5, 1, 12, 0, 0)

        token = QBOToken(
            id=1,
            access_token="access_token_value",
            refresh_token="refresh_token_value",
            realm_id="realm_id_value",
            expires_at=expires,
            refresh_expires_at=refresh_expires,
        )
        db_session.add(token)
        db_session.commit()

        result = token.to_dict()

        assert result["access_token"] == "access_token_value"
        assert result["refresh_token"] == "refresh_token_value"
        assert result["realm_id"] == "realm_id_value"
        assert result["expires_at"] == expires.isoformat()
        assert result["refresh_expires_at"] == refresh_expires.isoformat()

    def test_to_dict_handles_none_dates(self, db_session):
        """to_dict handles None datetime fields gracefully."""
        token = QBOToken(
            id=1,
            access_token="access",
            refresh_token="refresh",
            realm_id="realm",
            expires_at=None,
            refresh_expires_at=None,
        )

        result = token.to_dict()

        assert result["expires_at"] == ""
        assert result["refresh_expires_at"] == ""

    def test_update_token(self, sample_qbo_token, db_session):
        """Can update an existing token."""
        sample_qbo_token.access_token = "updated_access_token"
        db_session.commit()

        saved = db_session.query(QBOToken).filter_by(id=1).first()
        assert saved.access_token == "updated_access_token"


# =============================================================================
# Test Database Functions
# =============================================================================


class TestGetDatabaseUrl:
    """Test get_database_url function."""

    def test_returns_none_when_not_set(self):
        """Returns None when DATABASE_URL not set."""
        from src.qbo.database import get_database_url

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DATABASE_URL", None)
            result = get_database_url()

        assert result is None

    def test_returns_url_unchanged_for_postgresql(self):
        """Returns URL unchanged if already postgresql://."""
        from src.qbo.database import get_database_url

        url = "postgresql://user:pass@host:5432/db"
        with patch.dict(os.environ, {"DATABASE_URL": url}):
            result = get_database_url()

        assert result == url

    def test_converts_postgres_to_postgresql(self):
        """Converts postgres:// to postgresql:// for SQLAlchemy 2.0."""
        from src.qbo.database import get_database_url

        url = "postgres://user:pass@host:5432/db"
        with patch.dict(os.environ, {"DATABASE_URL": url}):
            result = get_database_url()

        assert result == "postgresql://user:pass@host:5432/db"
        assert result.startswith("postgresql://")

    def test_only_replaces_first_postgres(self):
        """Only replaces first occurrence of postgres://."""
        from src.qbo.database import get_database_url

        # Edge case: postgres in the path (unlikely but test the replace behavior)
        url = "postgres://user:pass@host:5432/postgres_db"
        with patch.dict(os.environ, {"DATABASE_URL": url}):
            result = get_database_url()

        # Should only replace the scheme, not the db name
        assert result == "postgresql://user:pass@host:5432/postgres_db"


class TestIsDatabaseConfigured:
    """Test is_database_configured function."""

    def test_returns_false_when_not_configured(self):
        """Returns False when DATABASE_URL not set."""
        from src.qbo.database import is_database_configured

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DATABASE_URL", None)
            result = is_database_configured()

        assert result is False

    def test_returns_true_when_configured(self):
        """Returns True when DATABASE_URL is set."""
        from src.qbo.database import is_database_configured

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://localhost/test"}):
            result = is_database_configured()

        assert result is True


class TestInitDb:
    """Test init_db function."""

    def test_returns_false_when_no_url(self):
        """Returns False when DATABASE_URL not set."""
        from src.qbo import database

        # Reset global state
        database._engine = None
        database._SessionLocal = None

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DATABASE_URL", None)
            result = database.init_db()

        assert result is False

    def test_returns_true_with_valid_url(self):
        """Returns True with valid database URL."""
        from src.qbo import database

        # Reset global state
        database._engine = None
        database._SessionLocal = None

        # Use SQLite in-memory for testing
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}):
            result = database.init_db()

        assert result is True
        assert database._engine is not None
        assert database._SessionLocal is not None

        # Cleanup
        database._engine = None
        database._SessionLocal = None

    def test_returns_false_on_connection_error(self):
        """Returns False when database connection fails."""
        from src.qbo import database

        # Reset global state
        database._engine = None
        database._SessionLocal = None

        # Use invalid URL to trigger error
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://invalid:invalid@nonexistent:5432/db"}):
            with patch("src.qbo.database.create_engine") as mock_engine:
                mock_engine.side_effect = Exception("Connection failed")
                result = database.init_db()

        assert result is False

        # Cleanup
        database._engine = None
        database._SessionLocal = None


class TestGetSession:
    """Test get_session context manager."""

    def test_yields_none_when_not_configured(self):
        """Yields None when database not configured."""
        from src.qbo import database

        # Reset global state
        database._engine = None
        database._SessionLocal = None

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("DATABASE_URL", None)
            with database.get_session() as session:
                assert session is None

    def test_yields_session_when_configured(self):
        """Yields session when database is configured."""
        from src.qbo import database

        # Reset global state
        database._engine = None
        database._SessionLocal = None

        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}):
            with database.get_session() as session:
                assert session is not None

        # Cleanup
        database._engine = None
        database._SessionLocal = None

    def test_rollback_on_exception(self):
        """Rolls back session on exception."""
        from src.qbo import database

        # Reset global state
        database._engine = None
        database._SessionLocal = None

        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}):
            database.init_db()

            mock_session = MagicMock()
            database._SessionLocal = MagicMock(return_value=mock_session)

            with pytest.raises(ValueError):
                with database.get_session() as _session:
                    raise ValueError("Test error")

            mock_session.rollback.assert_called_once()
            mock_session.close.assert_called_once()

        # Cleanup
        database._engine = None
        database._SessionLocal = None


# =============================================================================
# Test Auth Database Functions
# =============================================================================


class TestLoadTokensFromDb:
    """Test load_tokens_from_db function."""

    def test_returns_none_when_db_not_configured(self):
        """Returns None when database not configured."""
        from src.qbo.auth import load_tokens_from_db

        with patch("src.qbo.database.get_database_url", return_value=None):
            result = load_tokens_from_db()

        assert result is None

    def test_returns_none_when_no_token_exists(self):
        """Returns None when no token in database."""
        from src.qbo.auth import load_tokens_from_db
        from contextlib import contextmanager

        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.first.return_value = None

        @contextmanager
        def mock_get_session():
            yield mock_session

        with patch("src.qbo.database.get_database_url", return_value="sqlite:///:memory:"):
            with patch("src.qbo.database.get_session", mock_get_session):
                result = load_tokens_from_db()

        assert result is None

    def test_returns_token_dict_when_exists(self):
        """Returns token dictionary when token exists."""
        from src.qbo.auth import load_tokens_from_db
        from contextlib import contextmanager

        mock_token = MagicMock()
        mock_token.to_dict.return_value = {
            "access_token": "db_access",
            "refresh_token": "db_refresh",
            "realm_id": "12345",
            "expires_at": "2026-01-22T12:00:00",
            "refresh_expires_at": "2026-05-01T12:00:00",
        }

        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.first.return_value = mock_token

        @contextmanager
        def mock_get_session():
            yield mock_session

        with patch("src.qbo.database.get_database_url", return_value="sqlite:///:memory:"):
            with patch("src.qbo.database.get_session", mock_get_session):
                result = load_tokens_from_db()

        assert result["access_token"] == "db_access"
        assert result["realm_id"] == "12345"


class TestSaveTokensToDb:
    """Test save_tokens_to_db function."""

    def test_returns_false_when_db_not_configured(self, sample_token_data):
        """Returns False when database not configured."""
        from src.qbo.auth import save_tokens_to_db

        with patch("src.qbo.database.get_database_url", return_value=None):
            result = save_tokens_to_db(sample_token_data)

        assert result is False

    def test_updates_existing_token(self, sample_token_data):
        """Updates existing token when one exists."""
        from src.qbo.auth import save_tokens_to_db
        from contextlib import contextmanager

        mock_existing = MagicMock()
        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.first.return_value = mock_existing

        @contextmanager
        def mock_get_session():
            yield mock_session

        with patch("src.qbo.database.get_database_url", return_value="sqlite:///:memory:"):
            with patch("src.qbo.database.get_session", mock_get_session):
                result = save_tokens_to_db(sample_token_data)

        assert result is True
        assert mock_existing.access_token == sample_token_data["access_token"]
        mock_session.commit.assert_called_once()

    def test_creates_new_token_when_none_exists(self, sample_token_data):
        """Creates new token when none exists."""
        from src.qbo.auth import save_tokens_to_db
        from contextlib import contextmanager

        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.first.return_value = None

        @contextmanager
        def mock_get_session():
            yield mock_session

        with patch("src.qbo.database.get_database_url", return_value="sqlite:///:memory:"):
            with patch("src.qbo.database.get_session", mock_get_session):
                result = save_tokens_to_db(sample_token_data)

        assert result is True
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()

    def test_handles_datetime_objects(self):
        """Handles datetime objects in token data."""
        from src.qbo.auth import save_tokens_to_db
        from contextlib import contextmanager

        token_data = {
            "access_token": "access",
            "refresh_token": "refresh",
            "realm_id": "12345",
            "expires_at": datetime(2026, 1, 22, 12, 0, 0),
            "refresh_expires_at": datetime(2026, 5, 1, 12, 0, 0),
        }

        mock_session = MagicMock()
        mock_session.query.return_value.filter_by.return_value.first.return_value = None

        @contextmanager
        def mock_get_session():
            yield mock_session

        with patch("src.qbo.database.get_database_url", return_value="sqlite:///:memory:"):
            with patch("src.qbo.database.get_session", mock_get_session):
                result = save_tokens_to_db(token_data)

        assert result is True


class TestClearTokensFromDb:
    """Test clear_tokens_from_db function."""

    def test_returns_false_when_db_not_configured(self):
        """Returns False when database not configured."""
        from src.qbo.auth import clear_tokens_from_db

        with patch("src.qbo.database.get_database_url", return_value=None):
            result = clear_tokens_from_db()

        assert result is False

    def test_deletes_token_and_commits(self):
        """Deletes token and commits transaction."""
        from src.qbo.auth import clear_tokens_from_db
        from contextlib import contextmanager

        mock_session = MagicMock()

        @contextmanager
        def mock_get_session():
            yield mock_session

        with patch("src.qbo.database.get_database_url", return_value="sqlite:///:memory:"):
            with patch("src.qbo.database.get_session", mock_get_session):
                result = clear_tokens_from_db()

        assert result is True
        mock_session.query.return_value.filter_by.return_value.delete.assert_called_once()
        mock_session.commit.assert_called_once()


# =============================================================================
# Test Get Auth Client Validation
# =============================================================================


class TestGetAuthClientValidation:
    """Test get_auth_client environment variable validation."""

    def test_raises_error_when_client_id_missing(self):
        """Raises QBOAuthError when QBO_CLIENT_ID missing."""
        from src.qbo.auth import get_auth_client, QBOAuthError

        env_vars = {
            "QBO_CLIENT_SECRET": "secret",
            "QBO_REDIRECT_URI": "http://localhost/callback",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            with pytest.raises(QBOAuthError) as exc_info:
                get_auth_client()

        assert "QBO_CLIENT_ID" in str(exc_info.value)

    def test_raises_error_when_multiple_vars_missing(self):
        """Raises QBOAuthError listing all missing variables."""
        from src.qbo.auth import get_auth_client, QBOAuthError

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(QBOAuthError) as exc_info:
                get_auth_client()

        error_msg = str(exc_info.value)
        assert "QBO_CLIENT_ID" in error_msg
        assert "QBO_CLIENT_SECRET" in error_msg
        assert "QBO_REDIRECT_URI" in error_msg
