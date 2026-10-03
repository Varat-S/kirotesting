"""Ingestion tests: immutable storage, hashing, versioning, audit (Req 1, 25).

Covers task 2.5 ingestion acceptance:
* identical bytes -> identical hash (Req 1.7);
* no mutation of the stored original (Req 1.4);
* an audit event per ingested file (Req 1.10 / 18.2);
* corrected upload creates a NEW record with supersedes (Req 1.8);
* filename sanitization / path-traversal rejection and type restriction (25.3).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.services.audit.log import AuditLog
from app.services.ingestion import (
    IngestionService,
    RawFileStore,
    UnsafeFilenameError,
    UnsupportedFileTypeError,
    sha256_bytes,
)

CUTOFF = datetime(2024, 3, 1, tzinfo=timezone.utc)
AVAILABLE = datetime(2024, 2, 1, tzinfo=timezone.utc)


def _service(session: Session, tmp_path: Path) -> IngestionService:
    store = RawFileStore(tmp_path / "raw")
    return IngestionService(session, store, audit=AuditLog(session))


def _make_case(service: IngestionService, case_id: str = "DAL_2024") -> str:
    service.create_case(
        case_id,
        as_of_date=date(2024, 2, 29),
        evidence_cutoff_timestamp=CUTOFF,
    )
    return case_id


def test_identical_bytes_yield_identical_hash(
    db_session: Session, tmp_path: Path
) -> None:
    """Req 1.7: re-uploading identical bytes produces the same SHA-256."""
    service = _service(db_session, tmp_path)
    case_id = _make_case(service)
    data = b"%PDF-1.7 identical content"

    first = service.ingest_document(
        case_id, data=data, filename="10K.pdf", available_at=AVAILABLE
    )
    second = service.ingest_document(
        case_id, data=data, filename="10K-copy.pdf", available_at=AVAILABLE
    )

    assert first.document.sha256 == second.document.sha256 == sha256_bytes(data)
    # Two distinct DocumentRecords, one shared content-addressed object.
    assert first.document.document_id != second.document.document_id
    assert first.newly_stored_bytes is True
    assert second.newly_stored_bytes is False
    assert first.document.storage_path == second.document.storage_path


def test_original_bytes_are_not_mutated(
    db_session: Session, tmp_path: Path
) -> None:
    """Req 1.4: the stored original is byte-for-byte identical and untouched."""
    service = _service(db_session, tmp_path)
    case_id = _make_case(service)
    data = b"raw,csv,bytes\n1,2,3\n"

    result = service.ingest_document(
        case_id, data=data, filename="facts.csv", available_at=AVAILABLE
    )
    stored = Path(result.document.storage_path).read_bytes()
    assert stored == data


def test_each_ingested_file_emits_audit_event(
    db_session: Session, tmp_path: Path
) -> None:
    """Req 1.10: ingestion emits a document_ingested audit event per file."""
    service = _service(db_session, tmp_path)
    audit = AuditLog(db_session)
    case_id = _make_case(service)

    service.ingest_document(
        case_id, data=b"json-a{}", filename="a.json", available_at=AVAILABLE
    )
    service.ingest_document(
        case_id, data=b"json-b{}", filename="b.json", available_at=AVAILABLE
    )

    types = [e.event_type for e in audit.events_for_case(case_id)]
    assert types.count("case_created") == 1
    assert types.count("document_ingested") == 2


def test_corrected_upload_creates_new_record_with_supersedes(
    db_session: Session, tmp_path: Path
) -> None:
    """Req 1.8: a correction creates a NEW record + supersedes, prior intact."""
    service = _service(db_session, tmp_path)
    audit = AuditLog(db_session)
    case_id = _make_case(service)

    original = service.ingest_document(
        case_id, data=b"v1 content", filename="10Q.pdf", available_at=AVAILABLE
    )
    corrected = service.ingest_document(
        case_id,
        data=b"v2 corrected content",
        filename="10Q.pdf",
        available_at=AVAILABLE,
        supersedes=original.document.document_id,
    )

    assert corrected.document.version == 2
    assert corrected.document.supersedes == original.document.document_id
    assert corrected.superseded_document_id == original.document.document_id

    # Prior record left intact (still version 1, no supersedes).
    assert original.document.version == 1
    assert original.document.supersedes is None

    types = [e.event_type for e in audit.events_for_case(case_id)]
    assert "document_superseded" in types


def test_path_traversal_filename_rejected(
    db_session: Session, tmp_path: Path
) -> None:
    """Req 25.3: path-traversal filenames are rejected."""
    service = _service(db_session, tmp_path)
    case_id = _make_case(service)
    for bad in ("../../etc/passwd", "/abs/evil.pdf", "a/b/evil.pdf"):
        with pytest.raises(UnsafeFilenameError):
            service.ingest_document(
                case_id, data=b"x", filename=bad, available_at=AVAILABLE
            )


def test_unsupported_file_type_rejected(
    db_session: Session, tmp_path: Path
) -> None:
    """Req 1.3 / 25.3: file types are restricted to allowed evidence formats."""
    service = _service(db_session, tmp_path)
    case_id = _make_case(service)
    with pytest.raises(UnsupportedFileTypeError):
        service.ingest_document(
            case_id, data=b"x", filename="malware.exe", available_at=AVAILABLE
        )


def test_create_case_requires_unique_id(
    db_session: Session, tmp_path: Path
) -> None:
    """Req 1.1: case_id must be unique."""
    from app.services.ingestion import CaseAlreadyExistsError

    service = _service(db_session, tmp_path)
    _make_case(service)
    with pytest.raises(CaseAlreadyExistsError):
        _make_case(service)
