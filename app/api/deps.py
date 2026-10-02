"""FastAPI dependencies shared across API routes.

Provides a per-request SQLAlchemy session bound to the configured database and a
raw file store rooted under the configured data directory. Core service logic
lives in ``app/services`` and is fully unit-testable without HTTP; these
dependencies only wire the services into the request lifecycle.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.core import get_settings
from app.models.base import create_engine_and_session, init_db
from app.services.ingestion import RawFileStore


@lru_cache
def _session_factory() -> sessionmaker:
    settings = get_settings()
    engine, factory = create_engine_and_session(settings.database_url)
    init_db(engine)
    return factory


def get_session() -> Iterator[Session]:
    """Yield a request-scoped session, committing on success."""
    factory = _session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_raw_store() -> RawFileStore:
    """Return a raw file store rooted at ``<data_dir>/raw`` (originals)."""
    settings = get_settings()
    return RawFileStore(Path(settings.data_dir) / "raw")
