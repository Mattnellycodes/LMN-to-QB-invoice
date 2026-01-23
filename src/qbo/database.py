"""Database connection and initialization for token storage."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.qbo.models import Base

logger = logging.getLogger(__name__)

# Global engine instance (lazy initialized)
_engine = None
_SessionLocal = None


def get_database_url() -> Optional[str]:
    """
    Get DATABASE_URL, converting postgres:// to postgresql:// for SQLAlchemy 2.0.

    Render provides postgres:// URLs but SQLAlchemy 2.0 requires postgresql://.
    """
    url = os.getenv("DATABASE_URL")
    if url and url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def init_db() -> bool:
    """
    Initialize database engine and create tables if needed.

    Returns:
        True if database initialized successfully, False otherwise.
    """
    global _engine, _SessionLocal

    url = get_database_url()
    if not url:
        logger.debug("DATABASE_URL not set, skipping database initialization")
        return False

    try:
        _engine = create_engine(url, pool_pre_ping=True)
        _SessionLocal = sessionmaker(bind=_engine)

        # Create tables if they don't exist
        Base.metadata.create_all(_engine)
        logger.info("Database initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        return False


def get_engine():
    """Get the database engine, initializing if needed."""
    global _engine
    if _engine is None:
        init_db()
    return _engine


@contextmanager
def get_session() -> Generator[Optional[Session], None, None]:
    """
    Get a database session as a context manager.

    Usage:
        with get_session() as session:
            if session:
                # do database operations
                session.commit()

    Yields:
        Session object or None if database not configured.
    """
    global _SessionLocal

    if _SessionLocal is None:
        if not init_db():
            yield None
            return

    if _SessionLocal is None:
        yield None
        return

    session = _SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def is_database_configured() -> bool:
    """Check if database is configured and available."""
    return get_database_url() is not None
