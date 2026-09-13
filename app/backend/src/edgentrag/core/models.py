"""SQLAlchemy declarative base for application data models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared metadata root used by models and database migrations."""
