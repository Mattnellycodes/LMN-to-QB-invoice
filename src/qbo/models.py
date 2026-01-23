"""SQLAlchemy models for QuickBooks token storage."""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class QBOToken(Base):
    """QuickBooks OAuth tokens - single row table (only one QBO connection needed)."""

    __tablename__ = "qbo_tokens"

    id = Column(Integer, primary_key=True, default=1)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=False)
    realm_id = Column(String(50), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    refresh_expires_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self) -> dict:
        """Convert to dictionary format compatible with existing token handling."""
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "realm_id": self.realm_id,
            "expires_at": self.expires_at.isoformat() if self.expires_at else "",
            "refresh_expires_at": (
                self.refresh_expires_at.isoformat() if self.refresh_expires_at else ""
            ),
        }
