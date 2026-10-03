"""Temporal-leakage rejection tests across data types (Req 1.9, 20).

Covers task 2.5 leakage acceptance: a historical run rejects future-dated items
for filings, news, peer filings, and market data, and eligibility is keyed on
``available_at`` (not ``retrieved_at``).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.core.temporal import (
    EligibilityReason,
    check_temporal_eligibility,
    is_eligible,
)
from app.services.audit.log import AuditLog
from app.services.ingestion import (
    IngestionService,
    RawFileStore,
    TemporalLeakageError,
)

CUTOFF = datetime(2024, 3, 1, tzinfo=timezone.utc)
BEFORE = datetime(2024, 2, 15, tzinfo=timezone.utc)
AFTER = datetime(2024, 4, 1, tzinfo=timezone.utc)


def _service(session: Session, tmp_path: Path) -> IngestionService:
    store = RawFileStore(tmp_path / "raw")
    service = IngestionService(session, store, audit=AuditLog(session))
    service.create_case(
        "DAL_2024",
        as_of_date=date(2024, 2, 29),
        evidence_cutoff_timestamp=CUTOFF,
    )
    return service


# -- the reusable predicate (applies to every data type) ---------------------


def test_predicate_eligible_when_available_before_cutoff() -> None:
    result = check_temporal_eligibility(BEFORE, CUTOFF)
    assert result.eligible is True
    assert result.reason is EligibilityReason.ELIGIBLE


def test_predicate_rejects_future_dated() -> None:
    result = check_temporal_eligibility(AFTER, CUTOFF)
    assert result.eligible is False
    assert result.reason is EligibilityReason.FUTURE_DATED


def test_predicate_rejects_unknown_available_at() -> None:
    result = check_temporal_eligibility(None, CUTOFF)
    assert result.eligible is False
    assert result.reason is EligibilityReason.MISSING_AVAILABLE_AT


def test_eligibility_ignores_retrieved_at() -> None:
    """Req 20.3: eligibility is based on available_at, never retrieved_at.

    An item available before the cutoff is eligible regardless of when it was
    retrieved; the predicate does not even accept retrieved_at.
    """
    assert is_eligible(BEFORE, CUTOFF) is True
    # A future-available item stays ineligible no matter the retrieval time.
    assert is_eligible(AFTER, CUTOFF) is False


# -- ingestion-level rejection across data types ------------------------------


@pytest.mark.parametrize(
    ("source", "filename"),
    [
        ("sec_filing", "future_10K.html"),
        ("news", "future_news.json"),
        ("peer_filing", "peer_10Q.xbrl"),
        ("market_data", "prices.csv"),
    ],
)
def test_historical_run_rejects_future_item(
    db_session: Session, tmp_path: Path, source: str, filename: str
) -> None:
    """Req 20.4: future-dated filings/news/peer/market data are rejected."""
    service = _service(db_session, tmp_path)
    with pytest.raises(TemporalLeakageError) as excinfo:
        service.ingest_document(
            "DAL_2024",
            data=b"future content",
            filename=filename,
            source=source,
            available_at=AFTER,
        )
    assert excinfo.value.result.reason is EligibilityReason.FUTURE_DATED


def test_future_item_retrieved_now_still_rejected(
    db_session: Session, tmp_path: Path
) -> None:
    """A future-available item retrieved 'now' is still rejected (Req 20.3)."""
    service = _service(db_session, tmp_path)
    with pytest.raises(TemporalLeakageError):
        service.ingest_document(
            "DAL_2024",
            data=b"x",
            filename="late.pdf",
            available_at=AFTER,
            retrieved_at=datetime.now(timezone.utc),
        )


def test_eligible_item_is_ingested(db_session: Session, tmp_path: Path) -> None:
    service = _service(db_session, tmp_path)
    result = service.ingest_document(
        "DAL_2024", data=b"ok", filename="10K.pdf", available_at=BEFORE
    )
    assert result.document.document_id is not None
