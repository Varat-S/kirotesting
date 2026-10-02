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


class CanonicalEvidenceSnapshot(BaseModel):
    """Mid-pipeline canonical evidence snapshot skeleton (Req 5.1)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = SCHEMA_VERSION
    snapshot_type: str = Field(default="canonical_evidence", frozen=True)
    snapshot_version: int = 1
    case_id: str
    as_of_date: date | None = None
    evidence_cutoff_timestamp: datetime | None = None
    config_versions: dict[str, int] = Field(default_factory=dict)

    documents: list = Field(default_factory=list)
    entities: list = Field(default_factory=list)
    facts: list = Field(default_factory=list)
    financials: dict = Field(default_factory=dict)
    provenance: dict = Field(default_factory=dict)
    data_quality: dict = Field(default_factory=dict)


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
