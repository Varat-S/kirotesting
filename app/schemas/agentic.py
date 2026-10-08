"""Typed contracts for the agentic credit-analysis architecture (Milestone 1.1).

These are the common typed layer that deterministic calculations and bounded
semantic agents both flow through. They implement the contracts from
``.kiro/specs/credit-memo-agentic-analysis/`` requirements 2, 3, 10, 15, 27,
31, 32, 35 and 36, and the remediation pass (run identity, claim-level
provenance, append-only lineage, score status/coverage, immutable candidate
structures).

Design invariants enforced here (so no downstream code has to re-check them):

* Every analytical artifact carries an ``analysis_run_id`` (Remediation 1): two
  executions over the same ``CanonicalEvidenceSnapshot`` never co-mingle.
* ``method`` classification is honest (Req 3): a ``deterministic`` result carries
  ``formula_id`` + ``formula_version`` and never an ``agent_run_id``; an ``llm``/
  ``hybrid`` result carries ``agent_id`` + ``agent_run_id``.
* Credit risk (``risk_signal``) and evidence quality
  (``evidence_quality``/``confidence``) are SEPARATE fields; no validator derives
  one from the other (Req 10).
* ``RiskScore`` may be ``unavailable``/``provisional`` with a ``None`` band
  (Remediation 3); missing weighted dimensions are tracked explicitly and never
  silently renormalized by the schema.
* ``TopicConclusion`` material items are typed ``ConclusionClaim`` objects, each
  carrying specific parameter + evidence IDs (Remediation 4 / Req 35).
* ``CandidateStructure`` is an immutable proposal with NO mutable ``selected``
  flag; selection lives on ``StructuringConclusion.selected_candidate_id``
  (Remediation 11 / Req 36).

The ``*_JSON_SCHEMA`` constants are derived from the Pydantic models so a backend
and the validation layer target exactly the same schema (same pattern as
:mod:`app.schemas.llm`).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# Shared enums / aliases
# ---------------------------------------------------------------------------


class Topic(str, Enum):
    """Analytical topic a parameter / agent / conclusion belongs to."""

    BUSINESS = "business"
    FINANCIAL = "financial"
    STRUCTURING = "structuring"
    CROSS_TOPIC = "cross_topic"


class Method(str, Enum):
    """How a value was produced (Req 3)."""

    DETERMINISTIC = "deterministic"
    LLM = "llm"
    HYBRID = "hybrid"


class ParameterStatus(str, Enum):
    """Allowed ``ParameterResult`` states (Req 2.1, 3.5, 31)."""

    OK = "ok"
    PROVISIONAL = "provisional"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"
    PROPOSED_NEW_CALCULATION = "proposed_new_calculation"
    REQUIRES_REVIEW = "requires_review"


class EvidenceQuality(str, Enum):
    """Evidence quality / confidence band, SEPARATE from credit risk (Req 10)."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT = "insufficient"


class AcceptanceState(str, Enum):
    """Append-only lineage acceptance state (Remediations 12-13)."""

    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class Materiality(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Credit-risk band is a simple normalized scale (ILLUSTRATIVE — NOT BANK POLICY).
# 1 = low risk / strong, 2 = acceptable, 3 = elevated, 4 = high risk (Req 9.4).
RiskBand = Literal[1, 2, 3, 4]


class RunStatus(str, Enum):
    """Lifecycle status of an :class:`AgenticAnalysisRun` (Req 27.2)."""

    CREATED = "created"
    RUNNING = "running"
    PARTIALLY_FAILED = "partially_failed"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    SUPERSEDED = "superseded"


class ScoreKind(str, Enum):
    """The five deterministic score kinds (Req 9.2)."""

    BUSINESS = "business"
    FINANCIAL = "financial"
    OBLIGOR = "obligor"
    STRUCTURE_PROTECTION = "structure_protection"
    FACILITY = "facility"


class ScoreStatus(str, Enum):
    """A score may be unavailable/provisional when evidence is insufficient (Remediation 3)."""

    FINAL = "final"
    PROVISIONAL = "provisional"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class ModelTier(str, Enum):
    """Model-tier routing (Req 17.4)."""

    NARROW = "narrow"
    ORCHESTRATOR = "orchestrator"
    CHALLENGE = "challenge"


class ValidationStatus(str, Enum):
    """Outcome of deterministic validation of an agent run (Req 8)."""

    VALID = "valid"
    REJECTED = "rejected"
    ERROR = "error"
    SKIPPED = "skipped"


class ExecutionStatus(str, Enum):
    """Typed per-task execution outcome returned by the executor child task.

    Provider exceptions are caught INSIDE the child task and surfaced as one of
    these values so they never escape to cancel sibling tasks (Remediation 5 /
    Req 30).
    """

    OK = "ok"
    REJECTED = "rejected"
    ERROR = "error"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class ClaimCategory(str, Enum):
    """Category of a conclusion claim (Remediation 4 / Req 35)."""

    STRENGTH = "strength"
    WEAKNESS = "weakness"
    DRIVER = "driver"
    RISK = "risk"
    ASSESSMENT = "assessment"
    MITIGANT = "mitigant"
    LIMITATION = "limitation"


class ChallengeSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    MATERIAL = "material"


# ---------------------------------------------------------------------------
# AgenticAnalysisRun — first-class execution identity (Remediation 1 / Req 27)
# ---------------------------------------------------------------------------


class AgenticAnalysisRun(BaseModel):
    """One complete invocation of the agentic downstream pipeline (Req 27).

    Every analytical artifact produced by a run carries this ``analysis_run_id``
    so that repeated analyses of the same ``CanonicalEvidenceSnapshot`` (force
    regenerate, model/prompt A/B, config experiment, resumed partial failure,
    historical evaluation, manual fresh analysis) remain distinct and never leak
    into one another. The accepted ``FinalCaseSnapshot`` identifies exactly one
    accepted ``analysis_run_id``.
    """

    model_config = ConfigDict(extra="forbid")

    analysis_run_id: str
    case_id: str
    evidence_snapshot_version: int
    status: RunStatus = RunStatus.CREATED
    analysis_mode: Literal["agentic"] = "agentic"
    started_at: datetime
    completed_at: datetime | None = None
    parent_analysis_run_id: str | None = None
    supersedes_analysis_run_id: str | None = None
    router_version: str
    router_hash: str | None = None
    agent_registry_version: str
    agent_registry_hash: str
    scoring_config_version: int
    scoring_config_hash: str
    model_configuration: dict[str, Any] = Field(default_factory=dict)
    config_versions: dict[str, int] = Field(default_factory=dict)
    total_input_tokens: int | None = None
    total_output_tokens: int | None = None
    total_tokens: int | None = None
    failure_state: str | None = None


# ---------------------------------------------------------------------------
# ParameterResult — the single typed analytical datum (Req 2)
# ---------------------------------------------------------------------------


class ParameterResult(BaseModel):
    """One typed, traceable analytical result (Req 2).

    Deterministic calculations and (validated) agent outputs both land here. The
    ``method`` honestly reflects origin, and credit risk (``risk_signal``) is
    kept strictly separate from evidence quality (``evidence_quality`` /
    ``confidence``) so weak evidence can never mechanically move a risk band.
    """

    model_config = ConfigDict(extra="forbid")

    parameter_result_id: str
    analysis_run_id: str
    parameter_id: str
    topic: Topic
    value: Any | None = None
    value_type: str  # number|ratio|category|enum|text|schedule|bool
    method: Method
    status: ParameterStatus
    risk_signal: RiskBand | None = None  # credit risk — SEPARATE from evidence quality
    evidence_quality: EvidenceQuality | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    materiality: Materiality | None = None
    source_fact_ids: list[str] = Field(default_factory=list)
    source_parameter_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    formula_id: str | None = None
    formula_version: str | None = None
    agent_id: str | None = None
    agent_run_id: str | None = None
    prompt_id: str | None = None
    model_id: str | None = None
    input_hash: str | None = None
    contradictions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    notes: str | None = None
    # append-only lineage / acceptance (Remediations 12-13)
    supersedes_id: str | None = None
    parent_id: str | None = None
    rerun_of: str | None = None
    acceptance_state: AcceptanceState = AcceptanceState.ACCEPTED

    @model_validator(mode="after")
    def _validate_method_origin(self) -> ParameterResult:
        if self.method is Method.DETERMINISTIC:
            if not self.formula_id or not self.formula_version:
                raise ValueError(
                    f"Deterministic ParameterResult {self.parameter_result_id!r} "
                    "must carry formula_id and formula_version (Req 2.2)."
                )
            if self.agent_run_id is not None:
                raise ValueError(
                    f"Deterministic ParameterResult {self.parameter_result_id!r} "
                    "must NOT carry an agent_run_id (Req 2.2)."
                )
        else:  # llm or hybrid
            if not self.agent_id or not self.agent_run_id:
                raise ValueError(
                    f"{self.method.value} ParameterResult "
                    f"{self.parameter_result_id!r} must carry agent_id and "
                    "agent_run_id (Req 2 / design invariant)."
                )
        return self


# ---------------------------------------------------------------------------
# AgentTask / AgentRun / AgentExecutionResult (Req 19.2-19.3, 30)
# ---------------------------------------------------------------------------


class AgentTask(BaseModel):
    """A unit of agent work scheduled on the DAG (Req 19.3)."""

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    analysis_run_id: str
    topic: Topic
    task_type: str  # extract|interpret|classify|orchestrate|challenge|mitigant
    dependencies: list[str] = Field(default_factory=list)
    packet_ref: str  # evidence packet hash/id
    prompt_name: str
    response_schema_ref: str
    model_tier: ModelTier
    case_id: str
    snapshot_version: int
    rerun_of: str | None = None
    rerun_reason: str | None = None


class AgentRun(BaseModel):
    """Persisted record of one agent execution (Req 19.2).

    Carries enough metadata to reconstruct the run: owning analysis run, agent
    identity + definition hash, prompt/model identity, routed input hash +
    evidence IDs, raw/parsed response, validation outcome, DAG lineage, usage and
    error/rerun/cache metadata.
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    analysis_run_id: str
    agent_id: str
    agent_definition_hash: str  # semantic identity (Remediation 7)
    topic: Topic
    case_id: str
    snapshot_version: int
    prompt_id: str
    prompt_version: int | None = None
    prompt_hash: str
    response_schema_hash: str | None = None
    model_id: str
    model_config_payload: dict[str, Any] = Field(default_factory=dict)
    input_hash: str
    input_evidence_ids: list[str] = Field(default_factory=list)
    raw_response: str | None = None
    parsed_response: dict[str, Any] | None = None
    validation_status: ValidationStatus
    validation_detail: str | None = None
    parent_run_ids: list[str] = Field(default_factory=list)
    execution_wave: int
    latency_ms: int | None = None
    usage: dict[str, Any] | None = None  # {input_tokens, output_tokens, total_tokens}
    error_state: str | None = None
    rerun_reason: str | None = None
    reused_from_cache: bool = False


class AgentExecutionResult(BaseModel):
    """Typed result the executor child task ALWAYS returns (Remediation 5 / Req 30).

    Provider refusals/errors/timeouts are captured here rather than raised, so a
    failing task never cancels its concurrent siblings.
    """

    model_config = ConfigDict(extra="forbid")

    agent_id: str
    analysis_run_id: str
    status: ExecutionStatus
    parsed: dict[str, Any] | None = None
    raw_response: str | None = None
    error_type: str | None = None
    usage: dict[str, Any] | None = None
    latency_ms: int | None = None


# ---------------------------------------------------------------------------
# ConclusionClaim / TopicConclusion / StructuringConclusion (Remediation 4 / Req 35)
# ---------------------------------------------------------------------------


class ConclusionClaim(BaseModel):
    """An individually identified material claim inside a conclusion (Req 35).

    Each material claim carries specific ParameterResult IDs and evidence IDs so
    a memo sentence traces to exact parameters/evidence, and a
    ``ChallengeFinding`` can target an exact ``claim_id``.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    category: ClaimCategory
    text: str
    parameter_result_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    materiality: Materiality = Materiality.MEDIUM
    uncertainty: str | None = None


class TopicConclusion(BaseModel):
    """A topic orchestrator's synthesized conclusion (Req 11.3, 12.4, 35).

    ``overall_assessment`` and all material items are typed ``ConclusionClaim``s;
    orchestrators synthesize but never compute (quote-not-calculate, Req 34).
    """

    model_config = ConfigDict(extra="forbid")

    topic: Topic
    analysis_run_id: str
    overall_assessment: ConclusionClaim
    strengths: list[ConclusionClaim] = Field(default_factory=list)
    weaknesses: list[ConclusionClaim] = Field(default_factory=list)
    key_drivers: list[ConclusionClaim] = Field(default_factory=list)
    material_risks: list[ConclusionClaim] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    unresolved_contradictions: list[str] = Field(default_factory=list)
    score_reference: str  # RiskScore id/version
    orchestrator_run_id: str
    challenge_status: Literal["clean", "reran", "escalated"] = "clean"
    # append-only lineage
    supersedes_id: str | None = None
    acceptance_state: AcceptanceState = AcceptanceState.ACCEPTED


class StructuringConclusion(TopicConclusion):
    """Structuring conclusion; selection lives here, not on the candidate (Req 36)."""

    selected_candidate_id: str | None = None


# ---------------------------------------------------------------------------
# ChallengeFinding (Req 15.1)
# ---------------------------------------------------------------------------


class ChallengeFinding(BaseModel):
    """A defect identified by a challenge agent (Req 15).

    Challenge agents identify defects only; they NEVER modify parameters or
    scores. ``target`` points to an exact ``ConclusionClaim.claim_id`` where
    possible, and ``requested_rerun_scope`` names the agents/parameters to rerun.
    """

    model_config = ConfigDict(extra="forbid")

    challenge_id: str
    analysis_run_id: str
    target: str | None = None  # exact ConclusionClaim.claim_id where possible
    affected_agent_ids: list[str] = Field(default_factory=list)
    affected_parameter_ids: list[str] = Field(default_factory=list)
    issue_type: str
    severity: ChallengeSeverity
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)
    requires_reanalysis: bool = False
    requested_rerun_scope: list[str] = Field(default_factory=list)
    rerun_of: str | None = None


# ---------------------------------------------------------------------------
# CandidateStructure / CandidateFeasibility (Remediation 11 / Req 36)
# ---------------------------------------------------------------------------


class CandidateStructure(BaseModel):
    """An IMMUTABLE proposed transaction structure (Req 36).

    There is deliberately NO mutable ``selected`` flag; a selection is recorded
    on ``StructuringConclusion.selected_candidate_id`` and feasibility lives in a
    separate append-only :class:`CandidateFeasibility`.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    analysis_run_id: str
    facility_amount: float | None = None
    tenor_months: int | None = None
    amortization: dict[str, Any] | None = None
    covenant_package: list[dict[str, Any]] = Field(default_factory=list)
    collateral: list[dict[str, Any]] = Field(default_factory=list)
    guarantees: list[dict[str, Any]] = Field(default_factory=list)
    liquidity_protection: dict[str, Any] | None = None
    cash_sweep: dict[str, Any] | None = None
    hedge_requirements: list[dict[str, Any]] = Field(default_factory=list)
    reporting_requirements: list[dict[str, Any]] = Field(default_factory=list)
    other_protections: list[dict[str, Any]] = Field(default_factory=list)


class CandidateFeasibility(BaseModel):
    """A versioned/append-only deterministic feasibility result (Req 36.3).

    ``feasible`` is set ONLY by the deterministic structuring engine; an
    infeasible candidate cannot be selected.
    """

    model_config = ConfigDict(extra="forbid")

    feasibility_id: str
    analysis_run_id: str
    candidate_id: str
    feasible: bool
    feasibility_detail: dict[str, Any] = Field(default_factory=dict)
    supersedes_id: str | None = None
    acceptance_state: AcceptanceState = AcceptanceState.ACCEPTED


# ---------------------------------------------------------------------------
# RiskScore (Req 9 / Remediation 3)
# ---------------------------------------------------------------------------


class RiskScore(BaseModel):
    """A deterministic score produced only by the scoring subsystem (Req 9).

    No LLM assigns or modifies an official score. A score may be
    ``unavailable``/``provisional`` with a ``None`` band when required evidence is
    insufficient; missing weighted dimensions are tracked explicitly and are
    never silently renormalized (Remediation 3).
    """

    model_config = ConfigDict(extra="forbid")

    score_id: str
    analysis_run_id: str
    kind: ScoreKind
    status: ScoreStatus
    band: RiskBand | None = None
    scoring_config_version: int
    scoring_config_hash: str
    contributing_parameter_ids: list[str] = Field(default_factory=list)
    missing_required_parameter_ids: list[str] = Field(default_factory=list)
    critical_missing_parameter_ids: list[str] = Field(default_factory=list)
    coverage_weight: float | None = Field(default=None, ge=0.0, le=1.0)
    applied_overlays: list[str] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)
    method: Literal["deterministic"] = "deterministic"
    supersedes_id: str | None = None
    acceptance_state: AcceptanceState = AcceptanceState.ACCEPTED

    @model_validator(mode="after")
    def _validate_band_status(self) -> RiskScore:
        if self.status in (ScoreStatus.FINAL, ScoreStatus.PROVISIONAL):
            if self.band is None:
                raise ValueError(
                    f"RiskScore {self.score_id!r} with status {self.status.value!r} "
                    "must carry a band (Remediation 3)."
                )
        else:  # unavailable / not_applicable
            if self.band is not None:
                raise ValueError(
                    f"RiskScore {self.score_id!r} with status {self.status.value!r} "
                    "must NOT carry a band (Remediation 3)."
                )
        return self


# ---------------------------------------------------------------------------
# EvidencePacket (Req 4)
# ---------------------------------------------------------------------------


class EvidencePacket(BaseModel):
    """An immutable, narrowly-routed evidence packet for one agent (Req 4).

    The packet is hashed (``packet_hash``) and persisted so a historical agent
    run can be reconstructed. Evidence is data, never instructions (Req 26).
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    snapshot_version: int
    router_version: str
    analysis_run_id: str
    agent_id: str
    facts: list[dict[str, Any]] = Field(default_factory=list)
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    narrative_evidence: list[dict[str, Any]] = Field(default_factory=list)
    entity_relationships: list[dict[str, Any]] = Field(default_factory=list)
    facility_terms: list[dict[str, Any]] = Field(default_factory=list)
    covenant_terms: list[dict[str, Any]] = Field(default_factory=list)
    open_conflicts: list[dict[str, Any]] = Field(default_factory=list)
    data_limitations: list[dict[str, Any]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    packet_hash: str | None = None  # content_hash over the above, excl. this field


# ---------------------------------------------------------------------------
# Derived JSON Schemas for agent outputs (same pattern as app.schemas.llm)
# ---------------------------------------------------------------------------

TOPIC_CONCLUSION_JSON_SCHEMA: dict[str, Any] = TopicConclusion.model_json_schema()
STRUCTURING_CONCLUSION_JSON_SCHEMA: dict[str, Any] = (
    StructuringConclusion.model_json_schema()
)
CHALLENGE_FINDING_JSON_SCHEMA: dict[str, Any] = ChallengeFinding.model_json_schema()
CONCLUSION_CLAIM_JSON_SCHEMA: dict[str, Any] = ConclusionClaim.model_json_schema()
CANDIDATE_STRUCTURE_JSON_SCHEMA: dict[str, Any] = (
    CandidateStructure.model_json_schema()
)


__all__ = [
    # enums / aliases
    "Topic",
    "Method",
    "ParameterStatus",
    "EvidenceQuality",
    "AcceptanceState",
    "Materiality",
    "RiskBand",
    "RunStatus",
    "ScoreKind",
    "ScoreStatus",
    "ModelTier",
    "ValidationStatus",
    "ExecutionStatus",
    "ClaimCategory",
    "ChallengeSeverity",
    # contracts
    "AgenticAnalysisRun",
    "ParameterResult",
    "AgentTask",
    "AgentRun",
    "AgentExecutionResult",
    "ConclusionClaim",
    "TopicConclusion",
    "StructuringConclusion",
    "ChallengeFinding",
    "CandidateStructure",
    "CandidateFeasibility",
    "RiskScore",
    "EvidencePacket",
    # schemas
    "TOPIC_CONCLUSION_JSON_SCHEMA",
    "STRUCTURING_CONCLUSION_JSON_SCHEMA",
    "CHALLENGE_FINDING_JSON_SCHEMA",
    "CONCLUSION_CLAIM_JSON_SCHEMA",
    "CANDIDATE_STRUCTURE_JSON_SCHEMA",
]
