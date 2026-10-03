"""SQLAlchemy ORM entities for the core evidence model (Milestone 1).

Tables (per design.md "Relational schema"):

* ``cases``            -- a credit case with as-of date and evidence cutoff.
* ``entities``         -- :class:`EntityRecord` registry (Req 2.1, 2.5).
* ``documents``        -- ingested source documents (skeleton for M2).
* ``facts``            -- extracted / canonical financial & qualitative facts
                          with the enriched metadata schema (Req 4.1).
* ``fact_source_refs`` -- one-to-many provenance refs for each fact (Req 3.5).
* ``snapshots``        -- CanonicalEvidenceSnapshot / FinalCaseSnapshot rows
                          with ``supersedes`` linkage (Req 5, 16).
* ``reconciliation_records`` -- zero-safe reconciliation results, preserving
                          both values + both source refs (Req 7, Milestone 4).
* ``fact_corrections`` -- non-destructive human corrections of conflicts
                          (Req 7.11, 7.12, 15.2, 15.3).
* ``audit_events``     -- append-only audit log (Req 18); immutability is
                          enforced structurally in :mod:`app.services.audit`.
* ``config_versions``  -- versioned + hashed configuration artifacts (Req 19.6).

Design note on ``missing`` vs zero (Req 4.4 / 5.4): numeric values are stored in
nullable columns and the authoritative data-quality state lives in ``status``.
A ``missing`` fact has ``normalized_value IS NULL`` and never a coerced ``0``.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UTCDateTime, utcnow


class Case(Base):
    """A credit case. Carries an explicit as-of date and evidence cutoff."""

    __tablename__ = "cases"

    case_id: Mapped[str] = mapped_column(String, primary_key=True)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    evidence_cutoff_timestamp: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True
    )
    status: Mapped[str] = mapped_column(String, default="open", nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )

    entities: Mapped[list["Entity"]] = relationship(back_populates="case")
    documents: Mapped[list["Document"]] = relationship(back_populates="case")
    facts: Mapped[list["Fact"]] = relationship(back_populates="case")


class Entity(Base):
    """EntityRecord registry row (Requirement 2.1, 2.5).

    The legal borrower and its consolidated/ultimate parent are preserved as
    *distinct* rows: a borrower has ``borrower_flag=True`` and a
    ``parent_entity_id`` pointing at the parent, which is a separate row.
    """

    __tablename__ = "entities"

    entity_id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("cases.case_id"), nullable=True, index=True
    )
    legal_name: Mapped[str] = mapped_column(String, nullable=False)
    aliases: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    tickers: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    entity_type: Mapped[str] = mapped_column(String, nullable=False)
    parent_entity_id: Mapped[str | None] = mapped_column(
        ForeignKey("entities.entity_id"), nullable=True
    )
    borrower_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    guarantor_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    jurisdiction: Mapped[str | None] = mapped_column(String, nullable=True)
    source_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    case: Mapped["Case | None"] = relationship(back_populates="entities")
    parent: Mapped["Entity | None"] = relationship(
        remote_side=[entity_id], backref="children"
    )


class Document(Base):
    """Ingested source document (skeleton; populated fully in Milestone 2)."""

    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("cases.case_id"), nullable=True, index=True
    )
    filename: Mapped[str | None] = mapped_column(String, nullable=True)
    document_type: Mapped[str | None] = mapped_column(String, nullable=True)
    issuer: Mapped[str | None] = mapped_column(String, nullable=True)
    entity_id: Mapped[str | None] = mapped_column(
        ForeignKey("entities.entity_id"), nullable=True, index=True
    )
    period: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    observed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # ``available_at`` (a.k.a. published/availability date) drives temporal
    # eligibility (Req 20.3); ``retrieved_at`` records when the pipeline
    # downloaded it and must never drive eligibility.
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    available_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    retrieved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    storage_path: Mapped[str | None] = mapped_column(String, nullable=True)
    source_authority: Mapped[str | None] = mapped_column(String, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    supersedes: Mapped[str | None] = mapped_column(
        ForeignKey("documents.document_id"), nullable=True
    )

    case: Mapped["Case | None"] = relationship(back_populates="documents")


class Fact(Base):
    """ExtractedFact / CanonicalFact row with the enriched schema (Req 4.1).

    Numeric columns are nullable: a ``missing`` fact leaves
    ``normalized_value``/``raw_value`` NULL. The data-quality state in
    ``status`` is authoritative and ``missing`` is never numerical zero
    (Req 4.4 / 5.4). The paired :class:`FactSourceRef` rows provide the
    "every factual field carries >=1 source reference" guarantee (Req 3.5).
    """

    __tablename__ = "facts"

    fact_id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("cases.case_id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)

    # Raw vs normalized value/unit preserved separately (Req 4.4).
    raw_value: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_unit: Mapped[str | None] = mapped_column(String, nullable=True)
    normalized_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    normalized_unit: Mapped[str | None] = mapped_column(String, nullable=True)

    currency: Mapped[str | None] = mapped_column(String, nullable=True)
    scale: Mapped[str | None] = mapped_column(String, nullable=True)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_type: Mapped[str | None] = mapped_column(String, nullable=True)
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    accounting_basis: Mapped[str | None] = mapped_column(String, nullable=True)
    consolidation_scope: Mapped[str | None] = mapped_column(String, nullable=True)
    entity_id: Mapped[str | None] = mapped_column(
        ForeignKey("entities.entity_id"), nullable=True, index=True
    )
    reporting_entity_name: Mapped[str | None] = mapped_column(String, nullable=True)
    restated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    taxonomy_concept: Mapped[str | None] = mapped_column(String, nullable=True)
    source_label: Mapped[str | None] = mapped_column(String, nullable=True)
    data_freshness: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    normalization_method: Mapped[str | None] = mapped_column(String, nullable=True)
    definition_version: Mapped[str | None] = mapped_column(String, nullable=True)

    status: Mapped[str] = mapped_column(String, nullable=False)
    extraction_method: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )

    case: Mapped["Case | None"] = relationship(back_populates="facts")
    source_refs: Mapped[list["FactSourceRef"]] = relationship(
        back_populates="fact", cascade="all, delete-orphan"
    )


class FactSourceRef(Base):
    """Provenance reference for a fact (SourceRef persisted, Req 3.5)."""

    __tablename__ = "fact_source_refs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fact_id: Mapped[str] = mapped_column(
        ForeignKey("facts.fact_id"), nullable=False, index=True
    )
    document_id: Mapped[str | None] = mapped_column(String, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    table: Mapped[str | None] = mapped_column(String, nullable=True)
    taxonomy_concept: Mapped[str | None] = mapped_column(String, nullable=True)
    row_label: Mapped[str | None] = mapped_column(String, nullable=True)
    cell: Mapped[str | None] = mapped_column(String, nullable=True)

    fact: Mapped["Fact"] = relationship(back_populates="source_refs")


class Snapshot(Base):
    """A persisted snapshot row (CanonicalEvidenceSnapshot or FinalCaseSnapshot).

    ``snapshot_type`` distinguishes the two named objects. ``supersedes`` links a
    new version to its predecessor so finalized snapshots are never rewritten in
    place (Req 16.3); the full assembly happens in later milestones.
    """

    __tablename__ = "snapshots"
    __table_args__ = (
        UniqueConstraint(
            "case_id", "snapshot_type", "snapshot_version", name="uq_snapshot_version"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    snapshot_type: Mapped[str] = mapped_column(String, nullable=False)
    snapshot_version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String, nullable=False)
    supersedes: Mapped[int | None] = mapped_column(
        ForeignKey("snapshots.id"), nullable=True
    )
    finalized: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    config_versions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class ReconciliationRecord(Base):
    """A persisted reconciliation result (zero-safe, design.md "Reconciliation result").

    One row per reconciled numerical field. Carries the shape required by
    design.md: ``field, values[], source_refs[], absolute_delta, relative_delta?,
    near_zero_floor, comparison_method, tolerance_version, resolved_state``.

    Conflicts are NEVER silently merged: both observed values and both sets of
    source references are stored on the record so a reviewer sees exactly what
    disagreed (Req 7.9, 7.10). ``resolved_state`` holds the resolved fact state
    (``verified``/``conflicting``/``unverified``/``missing``). The record is the
    forward-compatible signal the Milestone 5 escalation engine consumes; this
    milestone does NOT build that engine.
    """

    __tablename__ = "reconciliation_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    field: Mapped[str] = mapped_column(String, nullable=False, index=True)
    values: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    source_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    fact_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    absolute_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    relative_delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    near_zero_floor: Mapped[float | None] = mapped_column(Float, nullable=True)
    comparison_method: Mapped[str] = mapped_column(String, nullable=False)
    tolerance: Mapped[float | None] = mapped_column(Float, nullable=True)
    tolerance_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolved_state: Mapped[str] = mapped_column(String, nullable=False)
    # Mismatch dimensions when comparison_method is definition_mismatch.
    mismatch_dimensions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # ``numeric`` for value comparison, ``non_numeric`` for a contradiction record.
    record_kind: Mapped[str] = mapped_column(
        String, default="numeric", nullable=False
    )
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class FactCorrection(Base):
    """A non-destructive human correction of a reconciliation/conflict (task 4.3).

    A correction NEVER deletes or overwrites the original conflict. It is an
    additive row that references the :class:`ReconciliationRecord` it resolves
    and captures the before/after states so both remain recoverable
    (Req 7.11, 7.12, 15.2, 15.3). The paired ``fact_human_corrected`` audit
    event is emitted by the service.
    """

    __tablename__ = "fact_corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    reconciliation_id: Mapped[int] = mapped_column(
        ForeignKey("reconciliation_records.id"), nullable=False, index=True
    )
    field: Mapped[str] = mapped_column(String, nullable=False)
    before_state: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    after_state: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    corrected_by: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class AuditEvent(Base):
    """Append-only audit event (Requirement 18).

    Immutability is enforced structurally: the audit service exposes only an
    append path, and an ORM event guard (see :mod:`app.services.audit`) raises
    if any persisted event is mutated or deleted.
    """

    __tablename__ = "audit_events"

    event_id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    actor_type: Mapped[str] = mapped_column(String, nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    linked_objects: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


class ConfigVersion(Base):
    """A versioned + hashed configuration artifact (Requirement 19.6).

    Each row records one artifact kind (tolerances, policy, peers, metric defs,
    trend rules, escalation rules, parser precedence, source profiles) at a
    specific version with a content hash. Changing content changes the hash and
    produces a new version row; existing rows are never rewritten.
    """

    __tablename__ = "config_versions"
    __table_args__ = (
        UniqueConstraint("artifact_kind", "version", name="uq_config_kind_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    artifact_kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
