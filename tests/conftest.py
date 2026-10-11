"""Shared pytest fixtures and helpers for the test suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from app.models.base import create_engine_and_session, init_db


@pytest.fixture(autouse=True)
def no_live_sec_requests(monkeypatch):
    """SEC tests must inject a transport; accidental production I/O fails closed."""
    from app.services.acquisition.sec_edgar import UrllibTransport

    def blocked(*args, **kwargs):
        raise AssertionError("Live SEC network requests are forbidden in tests.")

    monkeypatch.setattr(UrllibTransport, "get", blocked)


@pytest.fixture(autouse=True)
def no_local_env_file(monkeypatch):
    """Tests never read a developer's ``.env`` (it may hold real provider keys).

    Settings are built from defaults and explicit arguments only, so a locally
    configured provider cannot change a test's outcome or leak into its output.
    """
    from app.core.config import Settings, get_settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
