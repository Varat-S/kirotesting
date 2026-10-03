"""Case creation and immutable document ingestion (Requirement 1, 20, 25).

The :class:`IngestionService` provides two operations:

* :meth:`create_case` -- assign a unique ``case_id`` and require both an
  ``as_of_date`` and an ``evidence_cutoff_timestamp`` (Req 1.1, 1.2). Emits a
  ``case_created`` audit event (Req 1.10 / 18.2).
* :meth:`ingest_document` -- store the original bytes **unchanged** in a
  content-addressed store, compute the SHA-256, build a ``DocumentRecord`` with
  the full metadata set, enforce temporal eligibility for historical runs, and
  emit ``document_ingested`` / ``document_superseded`` audit events
  (Req 1.3-1.10, 20, 25.3).

Immutability & versioning (Req 1.4, 1.7, 1.8):

* Originals are never mutated or overwritten; storage is content-addressed.
* Identical bytes re-uploaded yield the same hash and reuse the stored object.
* A corrected upload does not overwrite the prior record: it creates a NEW
  ``Document`` row with ``version`` incremented and ``supersedes`` pointing at
  the record it replaces, leaving the prior record intact.

Temporal leakage (Req 1.9, 20): ingestion of an item whose ``available_at`` is
after the case ``evidence_cutoff_timestamp`` is rejected for a historical run
via the reusable predicate in :mod:`app.core.temporal` (keyed on
``available_at``, never ``retrieved_at``).

Security (Req 25.3): filenames are sanitized and path traversal is rejected;
file types are restricted to the allowed evidence formats; the on-disk path is
content-addressed and never derived from the untrusted filename.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.temporal import EligibilityResult, check_temporal_eligibility
from app.models.base import utcnow
from app.models.orm import Case, Document
from app.services.audit.log import ActorType, AuditLog, EventType
from app.services.ingestion.storage import (
    RawFileStore,
    sanitize_filename,
    validate_extension,
)


class CaseAlreadyExistsError(ValueError):
    """Raised when creating a case whose ``case_id`` already exists (Req 1.1)."""


class TemporalLeakageError(ValueError):
    """Raised when a historical run would ingest a future-dated item (Req 1.9/20.4).

    Carries the machine-readable :class:`EligibilityResult` so a later
    escalation engine (Milestone 5) can consume the reason.
    """

    def __init__(self, result: EligibilityResult) -> None:
        self.result = result
        super().__init__(
            f"Item is ineligible for a historical run: {result.reason.value}"
        )


@dataclass(frozen=True)
class IngestResult:
    """Outcome of an ingestion call."""

    document: Document
    newly_stored_bytes: bool
    superseded_document_id: str | None


class IngestionService:
    """Create cases and ingest immutable, versioned source documents."""

    def __init__(
        self,
        session: Session,
        store: RawFileStore,
        audit: AuditLog | None = None,
    ) -> None:
        self._session = session
        self._store = store
        self._audit = audit

    # -- case creation --------------------------------------------------------

    def create_case(
        self,
        case_id: str,
        *,
        as_of_date: date,
        evidence_cutoff_timestamp: datetime,
        actor_id: str | None = None,
    ) -> Case:
        """Create a case with a unique id, as-of date, and evidence cutoff.

        Both ``as_of_date`` and ``evidence_cutoff_timestamp`` are required
        (Req 1.1, 1.2); they are not optional and are not silently defaulted.
        """
        if self._session.get(Case, case_id) is not None:
            raise CaseAlreadyExistsError(f"Case {case_id!r} already exists.")

        case = Case(
            case_id=case_id,
            as_of_date=as_of_date,
            evidence_cutoff_timestamp=evidence_cutoff_timestamp,
            status="open",
        )
        self._session.add(case)
        self._session.flush()

        if self._audit is not None:
            self._audit.record(
                EventType.CASE_CREATED,
                case_id=case_id,
                actor_type=ActorType.SYSTEM,
                actor_id=actor_id,
                after={
                    "case_id": case_id,
                    "as_of_date": as_of_date.isoformat(),
                    "evidence_cutoff_timestamp": evidence_cutoff_timestamp.isoformat(),
                },
                linked_objects=[case_id],
            )
        return case

    # -- document ingestion ---------------------------------------------------

    def ingest_document(
        self,
        case_id: str,
        *,
        data: bytes,
        filename: str,
        source: str | None = None,
        document_type: str | None = None,
        issuer: str | None = None,
        entity_id: str | None = None,
        period: str | None = None,
        source_authority: str | None = None,
        observed_date: date | None = None,
        published_at: datetime | None = None,
        available_at: datetime | None = None,
        retrieved_at: datetime | None = None,
        supersedes: str | None = None,
        enforce_cutoff: bool = True,
        actor_id: str | None = None,
    ) -> IngestResult:
        """Store raw bytes immutably and record a ``DocumentRecord``.

        * ``available_at`` governs temporal eligibility; if omitted it falls
          back to ``published_at`` (both are "when it became knowable"). When
          ``enforce_cutoff`` is True (historical run) and the item fails the
          availability-by-cutoff check, a :class:`TemporalLeakageError` is
          raised and nothing is persisted (Req 1.9, 20.3, 20.4).
        * ``supersedes`` links a corrected upload to the record it replaces; a
          NEW record is created with an incremented ``version`` and the prior
          record is left intact (Req 1.8).
        """
        case = self._session.get(Case, case_id)
        if case is None:
            raise ValueError(f"Unknown case_id {case_id!r}; create the case first.")

        safe_name = sanitize_filename(filename)
        ext = validate_extension(safe_name)

        effective_available = available_at or published_at

        # Temporal-leakage control for historical runs (Req 1.9 / 20).
        if enforce_cutoff and case.evidence_cutoff_timestamp is not None:
            result = check_temporal_eligibility(
                effective_available, case.evidence_cutoff_timestamp
            )
            if not result:
                raise TemporalLeakageError(result)

        # Store original bytes unchanged, content-addressed (Req 1.4, 1.7).
        stored = self._store.store(data, ext)

        # Validate / resolve supersedes linkage (Req 1.8).
        version = 1
        superseded_id: str | None = None
        if supersedes is not None:
            prior = self._session.get(Document, supersedes)
            if prior is None:
                raise ValueError(
                    f"supersedes references unknown document {supersedes!r}."
                )
            if prior.case_id != case_id:
                raise ValueError(
                    "supersedes must reference a document in the same case."
                )
            version = (prior.version or 1) + 1
            superseded_id = prior.document_id

        document = Document(
            document_id=str(uuid.uuid4()),
            case_id=case_id,
            filename=safe_name,
            document_type=document_type,
            issuer=issuer,
            entity_id=entity_id,
            period=period,
            source=source,
            observed_date=observed_date,
            published_at=published_at,
            available_at=effective_available,
            retrieved_at=retrieved_at or utcnow(),
            sha256=stored.sha256,
            storage_path=stored.storage_path,
            source_authority=source_authority,
            version=version,
            supersedes=superseded_id,
        )
        self._session.add(document)
        self._session.flush()

        if self._audit is not None:
            self._audit.record(
                EventType.DOCUMENT_INGESTED,
                case_id=case_id,
                actor_type=ActorType.SYSTEM,
                actor_id=actor_id,
                after={
                    "document_id": document.document_id,
                    "filename": safe_name,
                    "sha256": stored.sha256,
                    "version": version,
                    "source_authority": source_authority,
                },
                linked_objects=[document.document_id],
            )
            if superseded_id is not None:
                self._audit.record(
                    EventType.DOCUMENT_SUPERSEDED,
                    case_id=case_id,
                    actor_type=ActorType.SYSTEM,
                    actor_id=actor_id,
                    before={"document_id": superseded_id},
                    after={"document_id": document.document_id, "version": version},
                    reason="corrected document uploaded",
                    linked_objects=[superseded_id, document.document_id],
                )

        return IngestResult(
            document=document,
            newly_stored_bytes=stored.newly_written,
            superseded_document_id=superseded_id,
        )

    # -- read helpers ---------------------------------------------------------

    def documents_for_case(self, case_id: str) -> list[Document]:
        """Return all documents for a case, oldest first (read-only helper)."""
        stmt = (
            select(Document)
            .where(Document.case_id == case_id)
            .order_by(Document.ingested_at.asc(), Document.document_id.asc())
        )
        return list(self._session.execute(stmt).scalars().all())
