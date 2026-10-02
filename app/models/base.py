"""SQLAlchemy declarative base and SQLite engine/session setup.

For the PoC the default engine is a local SQLite database (see
``app/core/config.py``). Tests may inject an in-memory or temp-file SQLite
database via :func:`create_engine_and_session`.

This module provides:

* :class:`Base` -- the declarative base all ORM models inherit from.
* :func:`create_engine_and_session` -- build an engine + session factory for a
  given database URL (used by the app and by tests).
* :func:`init_db` -- create all tables for a bound engine.

SQLite needs ``check_same_thread=False`` for use across threads (e.g. the
FastAPI test client) and benefits from a ``StaticPool`` for in-memory URLs so
every connection shares the same database.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """DateTime that always stores/returns timezone-aware UTC values.

    SQLite drops tzinfo, so we normalize on the way in and re-attach UTC on the
    way out. This keeps audit timestamps and freshness comparisons consistent.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def process_result_value(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def create_engine_and_session(database_url: str) -> tuple[Engine, sessionmaker]:
    """Create a SQLAlchemy engine and a bound session factory.

    Handles SQLite-specific connection arguments for both file-based and
    in-memory URLs.
    """
    connect_args: dict = {}
    engine_kwargs: dict = {"future": True}

    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        # Share a single in-memory database across connections for tests.
        if ":memory:" in database_url or database_url == "sqlite://":
            engine_kwargs["poolclass"] = StaticPool

    engine = create_engine(database_url, connect_args=connect_args, **engine_kwargs)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine, session_factory


def init_db(engine: Engine) -> None:
    """Create all tables registered on :class:`Base`'s metadata."""
    # Import models so they register with Base.metadata before create_all.
    from app import models  # noqa: F401  (side-effect import)

    Base.metadata.create_all(engine)
