"""Shared pytest fixtures and helpers for the test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.models.base import create_engine_and_session, init_db


@pytest.fixture()
def db_session() -> Iterator[Session]:
    """Provide an isolated in-memory SQLite session with all tables created.

    Uses a StaticPool-backed in-memory engine so the whole test shares one
    database. Rolls back and closes on teardown.
    """
    engine, session_factory = create_engine_and_session("sqlite://")
    init_db(engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()
