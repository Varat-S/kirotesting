"""Snapshot schemas (skeletons) for Milestone 1 (Req 5.1, 5.5, 16.1).

Two DISTINCT named snapshot objects (design.md):

* :class:`CanonicalEvidenceSnapshot` -- mid-pipeline evidence object. Holds
  documents, entities, facts, normalized financials, provenance, and
  data-quality states. Only a skeleton here; full assembly is Milestone 4.
* :class:`FinalCaseSnapshot` -- post-analysis immutable case object. References
  a ``CanonicalEvidenceSnapshot`` version. Only a skeleton here; full assembly
  is Milestone 7.

Both carry ``schema_version`` and ``config_versions`` (Req 5.5 / 19.6) and both
can be validated against a versioned JSON Schema via :func:`validate_snapshot`
("validate on write", Req 5.5 / 16.1). The JSON Schemas are exported for the
registry under :mod:`app.schemas.json_schema`.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1.0"


class EvidenceSnapshotRef(BaseModel):
    """Reference from a FinalCaseSnapshot to a CanonicalEvidenceSnapshot."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    snapshot_version: int


class DataQualityState(BaseModel):
    """Per-field data-quality state in the snapshot (Req 5.2, 5.4).

    Each field records its resolved value, its distinct ``state`` (one of the
    fact-status values; ``missing`` is never numerical zero), its provenance
    (source refs), data freshness, and the reconciliation that produced the
    state. Conflicts carry BOTH values + BOTH source refs (no silent merge).
    """

    model_config = ConfigDict(extra="forbid")

    field: str
    state: str
    value: float | None = None
    values: list[float] = Field(default_factory=list)
    source_refs: list[dict] = Field(default_factory=list)
    data_freshness: datetime | None = None
    comparison_method: str | None = None
    absolute_delta: float | None = None
    relative_delta: float | None = None
    near_zero_floor: float | None = None
    tolerance: float | None = None
    tolerance_version: int | None = None
    mismatch_dimensions: list[str] = Field(default_factory=list)
    detail: str | None = None
    selected_fact_id: str | None = None
    selection_reason: str | None = None
    selection_version: int | None = None


class CanonicalEvidenceSnapshot(BaseModel):
    """Mid-pipeline canonical evidence snapshot (Req 5.1, 5.3, 5.5, 5.6).

    Contains documents, entities, facts, normalized financials, provenance, and
    per-field data-quality states (Req 5.1). Each field in ``data_quality``
    carries value, state and provenance (Req 5.2). Normalized financials
    (formula inputs) are stored independently from any derived metric (Req 5.3).
    The snapshot records which ``config_versions`` it was built under (Req 19.6)
    and is validated against a versioned JSON Schema on write (Req 5.5).
    Downstream objects reference it by ``snapshot_version`` (Req 5.6).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    snapshot_type: str = Field(default="canonical_evidence", frozen=True)
    snapshot_version: int = 1
    case_id: str
    as_of_date: date | None = None
    evidence_cutoff_timestamp: datetime | None = None
    config_versions: dict[str, int] = Field(default_factory=dict)

    documents: list[dict] = Field(default_factory=list)
    entities: list[dict] = Field(default_factory=list)
    facts: list[dict] = Field(default_factory=list)
    # Normalized financials (formula inputs) kept independent of derived metrics.
    financials: dict = Field(default_factory=dict)
    provenance: dict = Field(default_factory=dict)
    # Per-field data-quality states keyed by field name.
    data_quality: dict[str, DataQualityState] = Field(default_factory=dict)


class FinalCaseSnapshot(BaseModel):
    """Final immutable case snapshot skeleton (Req 16.1).

    References a ``CanonicalEvidenceSnapshot`` by version and records the config,
    metric-definition, rule, and prompt/model versions it was built under.
    ``supersedes_snapshot`` links a new version to its predecessor (Req 16.3).
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    snapshot_type: str = Field(default="final_case", frozen=True)
    snapshot_version: int = 1
    supersedes_snapshot: int | None = None
    evidence_snapshot_ref: EvidenceSnapshotRef

    config_versions: dict[str, int] = Field(default_factory=dict)
    metric_definition_versions: dict[str, int] = Field(default_factory=dict)
    rule_versions: dict[str, int] = Field(default_factory=dict)
    prompt_model_versions: dict[str, str] = Field(default_factory=dict)

    metrics: dict = Field(default_factory=dict)
    benchmarks: dict = Field(default_factory=dict)
    business_analysis: dict = Field(default_factory=dict)
    financial_analysis: dict = Field(default_factory=dict)
    risks: list = Field(default_factory=list)
    mitigants: list = Field(default_factory=list)
    exceptions: list = Field(default_factory=list)
    escalations: list = Field(default_factory=list)
    human_reviews: list = Field(default_factory=list)
    recommendation: dict = Field(default_factory=lambda: {"status": "draft"})
    audit_metadata: dict = Field(default_factory=dict)
    finalized: bool = False
