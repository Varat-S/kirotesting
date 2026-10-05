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


class EvidencePreview(Base):
    """Saved deterministic review view; separate from credit memo snapshots."""

    __tablename__ = "evidence_previews"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), primary_key=True)
    evidence_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


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


class CaseEntity(Base):
    """Case-specific roles over a reusable global legal entity."""

    __tablename__ = "case_entities"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), primary_key=True)
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("entities.entity_id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    borrower_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    guarantor_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    expected_consolidation_scope: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    entity: Mapped["Entity"] = relationship()


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

    original_name: Mapped[str | None] = mapped_column(String, nullable=True)
    mapping_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mapping_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    mapping_status: Mapped[str | None] = mapped_column(String, nullable=True)
    mapping_candidates: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

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
    xbrl_context_id: Mapped[str | None] = mapped_column(String, nullable=True)
    dimensions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    inline_element_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sec_accession: Mapped[str | None] = mapped_column(String, nullable=True)
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
    xbrl_context_id: Mapped[str | None] = mapped_column(String, nullable=True)
    inline_element_id: Mapped[str | None] = mapped_column(String, nullable=True)
    presentation_role: Mapped[str | None] = mapped_column(String, nullable=True)
    sec_accession: Mapped[str | None] = mapped_column(String, nullable=True)

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
    # SHA-256 over the canonical payload recorded at finalization. Any later
    # read can re-hash the payload and compare to prove the finalized snapshot
    # has not drifted (Req 16.2). NULL until finalized.
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class HumanReview(Base):
    """A non-destructive human-review action (Req 15.1-15.6; task 7.1).

    Every reviewer action (verify a source, resolve a conflicting figure,
    approve an accounting adjustment, approve assumptions, confirm the peer set,
    resolve contradictory evidence, accept/reject an AI interpretation, modify
    risk materiality, or sign off the final recommendation) is captured as an
    ADDITIVE row. Nothing is ever overwritten or deleted: ``prior_value`` and
    ``new_value`` are both retained so before/after stays visible/recoverable
    (Req 15.2, 15.3). The paired ``human_review`` audit event is emitted by the
    service.

    ``requires_sign_off`` marks the gated actions (final recommendation,
    material accounting adjustments, resolved conflicts, facility structure
    recommendations, policy exceptions, judgment-heavy risk rankings) per
    Req 15.4. ``signed_off`` records explicit sign-off (Req 15.6).
    """

    __tablename__ = "human_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    review_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    case_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # One of the ReviewAction values (verify_source, resolve_conflict, ...).
    action: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # What the action targets (e.g. "fact:...", "escalation:...", "risk:...").
    target_type: Mapped[str | None] = mapped_column(String, nullable=True)
    target_id: Mapped[str | None] = mapped_column(String, nullable=True)
    reviewer: Mapped[str] = mapped_column(String, nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    prior_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    new_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    linked_evidence: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_sign_off: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    signed_off: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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
    mismatch_dimensions: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False
    )
    # ``numeric`` for value comparison, ``non_numeric`` for a contradiction record.
    record_kind: Mapped[str] = mapped_column(String, default="numeric", nullable=False)
    selected_fact_id: Mapped[str | None] = mapped_column(String, nullable=True)
    selected_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    selection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    selection_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
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


class MetricDefinition(Base):
    """A versioned metric definition (Req 8.2, 8.3; Milestone 5 task 5.1).

    A definition states which components are included/excluded (e.g. what
    "EBITDA", "net debt", "FCF", "liquidity" comprise) and is identified by
    ``metric_definition_id`` + ``version``. Changing a definition creates a NEW
    row (append-only); prior rows are never rewritten, so historical metric
    outputs stay linked to the exact definition version they used (Req 8.8).
    """

    __tablename__ = "metric_definitions"
    __table_args__ = (
        UniqueConstraint(
            "metric_definition_id", "version", name="uq_metric_def_version"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    metric_definition_id: Mapped[str] = mapped_column(
        String, nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    formula_id: Mapped[str] = mapped_column(String, nullable=False)
    # Which components the metric includes/excludes + the input fact names.
    components: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class Metric(Base):
    """A persisted deterministic metric result (Req 8.2; task 5.2).

    Stores ``metric_definition_id``, ``metric_definition_version``,
    ``formula_id``, inputs, input fact IDs, result OR an explicit state, period,
    units, and ``engine_version``. A bad-denominator outcome records an explicit
    ``explicit_state`` (``not_meaningful``/``missing_input``/``requires_review``)
    and leaves ``result`` NULL -- a missing/not-meaningful metric is NEVER a
    coerced zero (Req 8.4). The recorded version linkage makes historical runs
    reproducible and immune to later definition changes (Req 8.6, 8.8).
    """

    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    metric_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    metric_definition_id: Mapped[str] = mapped_column(String, nullable=False)
    metric_definition_version: Mapped[int] = mapped_column(Integer, nullable=False)
    formula_id: Mapped[str] = mapped_column(String, nullable=False)
    engine_version: Mapped[str] = mapped_column(String, nullable=False)
    inputs: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    input_fact_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    result: Mapped[float | None] = mapped_column(Float, nullable=True)
    explicit_state: Mapped[str | None] = mapped_column(String, nullable=True)
    period: Mapped[str | None] = mapped_column(String, nullable=True)
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    units: Mapped[str | None] = mapped_column(String, nullable=True)
    evidence_quality: Mapped[str] = mapped_column(
        String, default="unverified", nullable=False
    )
    review_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class Benchmark(Base):
    """A persisted small-cohort-aware peer benchmark result (Req 10; task 5.4).

    Shape per design.md "Benchmark result". Records ``benchmark_method``,
    ``sample_size``, cohort definition + version, and benchmark date. Percentiles
    (p90/p95) are only populated when the sample permits and ``percentiles_reliable``
    is otherwise False so unstable percentiles are suppressed/labelled (Req 10.1,
    10.2). The borrower is never double-counted in its own cohort (Req 10.5). A
    benchmark is an ANOMALY SIGNAL only, never a credit threshold (Req 10.7).
    """

    __tablename__ = "benchmarks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    metric_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    cohort_definition: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    cohort_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    benchmark_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    benchmark_method: Mapped[str] = mapped_column(String, nullable=False)
    borrower_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_peer_values: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    median: Mapped[float | None] = mapped_column(Float, nullable=True)
    minimum: Mapped[float | None] = mapped_column(Float, nullable=True)
    maximum: Mapped[float | None] = mapped_column(Float, nullable=True)
    p25: Mapped[float | None] = mapped_column(Float, nullable=True)
    p75: Mapped[float | None] = mapped_column(Float, nullable=True)
    p90: Mapped[float | None] = mapped_column(Float, nullable=True)
    p95: Mapped[float | None] = mapped_column(Float, nullable=True)
    percentiles_reliable: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class Rule(Base):
    """A policy/escalation rule identity (Req 11; task 5.5, 5.6).

    A rule is identified by a stable ``rule_id`` (e.g. ``R-TREND-LEV-01``) and a
    ``concept`` keeping the THREE separate ideas distinct: ``policy_threshold``,
    ``peer_benchmark``, and ``historical_deterioration`` (plus ``data_integrity``,
    ``evidence``, ``ai_deterministic_conflict`` for the escalation categories).
    The concrete, versioned threshold logic lives in :class:`RuleVersion`.
    """

    __tablename__ = "rules"

    rule_id: Mapped[str] = mapped_column(String, primary_key=True)
    concept: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class RuleVersion(Base):
    """A versioned rule definition (Req 11.5, 11.6; task 5.5).

    Each change to a rule creates a NEW version row + an audit event; prior rows
    are never rewritten, so a historical run stays linked to the exact rule
    version in force at run time. ``config_kind``/``config_version`` link the
    rule to the versioned configuration artifact that supplies its thresholds
    (e.g. ``policy``/``trend_rules``/``escalation_rules``) so no threshold is
    buried in code (Req 11.3).
    """

    __tablename__ = "rule_versions"
    __table_args__ = (UniqueConstraint("rule_id", "version", name="uq_rule_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_id: Mapped[str] = mapped_column(
        ForeignKey("rules.rule_id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    config_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class Escalation(Base):
    """A persisted escalation record (Req 14.3; task 5.6).

    Carries every required field: ``escalation_id, case_id, severity, rule_id,
    reason, triggered_at, evidence_refs[], status, resolution``. An escalation
    CANNOT disappear unresolved (Req 14.5): resolution is additive -- the record
    moves from ``open`` to ``resolved`` and records who/when/why, but is never
    deleted. ``mandatory`` escalations keep the case status reflecting them
    until resolved (Req 14.7). ``category`` keeps the four categories distinct
    (data integrity / financial rules / AI-deterministic conflict / evidence)
    and ``concept`` records which of the THREE financial-rule ideas fired.
    """

    __tablename__ = "escalations"

    escalation_id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    severity: Mapped[str] = mapped_column(String, nullable=False)
    category: Mapped[str] = mapped_column(String, nullable=False)
    concept: Mapped[str | None] = mapped_column(String, nullable=True)
    rule_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    rule_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    mandatory: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    triggered_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String, default="open", nullable=False)
    resolution: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class PromptVersion(Base):
    """A versioned prompt artifact (Req 19.1, 19.5; task 6.2).

    A prompt is identified by ``name`` + ``version`` (e.g. ``business_analysis``
    at version 1 surfaced as ``business_analysis_v1.0``) and carries a canonical
    content hash of its template + fixed response schema. Registering changed
    content allocates a NEW version row (append-only); prior rows are never
    rewritten, so historical LLM runs stay linked to the exact prompt version
    used. A prompt update sets ``needs_regression`` so the change signals that
    regression tests must run (Req 19.5) -- the signal is real and queryable
    rather than silently applied.
    """

    __tablename__ = "prompt_versions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_prompt_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # Human-facing identifier, e.g. ``business_analysis_v1.0`` (Req 19.1).
    prompt_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    # Fixed response JSON Schema this prompt's output is validated against.
    response_schema: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
    # Which LLMClient method this prompt drives (extract/analyze/challenge).
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    # True while a prompt update has not yet been cleared by regression tests.
    needs_regression: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class ModelRun(Base):
    """Full logging of a single LLM run (Req 19.2; task 6.1).

    Records everything needed to reproduce and audit a generative call WITHOUT
    claiming byte-exact regeneration (Req 19.4): prompt ID/version/hash, model
    ID + configuration, the case version, the evidence IDs supplied, the RAW
    response, the PARSED response, and the validation outcome. A rejected
    (schema-invalid) run is still recorded with ``validation_outcome='rejected'``
    and the parsed response left NULL so the rejection is itself auditable.
    """

    __tablename__ = "model_runs"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    case_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    case_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Which LLMClient method ran: extract | analyze | challenge.
    method: Mapped[str] = mapped_column(String, nullable=False, index=True)
    prompt_id: Mapped[str] = mapped_column(String, nullable=False)
    prompt_name: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_hash: Mapped[str] = mapped_column(String, nullable=False)
    model_id: Mapped[str] = mapped_column(String, nullable=False)
    model_config_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # ``valid`` | ``rejected`` (schema validation outcome, Req 19.4 / 12.5).
    validation_outcome: Mapped[str] = mapped_column(String, nullable=False)
    validation_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=utcnow, nullable=False
    )


class ClaimGrounding(Base):
    """A claim-grounding result with the citation/entailment split (Req 13.6-13.8).

    The headline guarantee of Milestone 6: a claim is NEVER grounded solely
    because a citation ID is attached. The two signals are stored INDEPENDENTLY:

    * ``citation_present`` -- a boolean about whether ``evidence_refs`` is
      non-empty and references a real evidence object (check A).
    * ``entailment_state`` -- a SEPARATE judgment of whether the cited evidence
      actually supports the claim (check B), one of ``supported |
      partially_supported | unsupported | contradictory | not_verifiable``.

    ``judge`` records whether entailment was decided ``deterministic``, by an
    ``llm`` judge, or by a ``human``; ``adjudicated`` flags a high-severity
    factual claim that a human reviewer has adjudicated.
    """

    __tablename__ = "claim_groundings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    claim_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    citation_present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    entailment_state: Mapped[str] = mapped_column(String, nullable=False)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    judge: Mapped[str] = mapped_column(String, nullable=False)
    adjudicated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class StageArtifact(Base):
    """Immutable processing output with a recorded checksum and chain link."""

    __tablename__ = "stage_artifacts"
    case_id: Mapped[str] = mapped_column(String, primary_key=True)
    evidence_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(String, nullable=False)
    previous_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    chain_hash: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


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
