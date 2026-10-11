"""Typed contracts for the Business Overview v2 module.

Adds only what the existing agentic contracts do not already express:

* :class:`HypothesisRecord` — an investigated favourable/adverse hypothesis with
  its evidence, disposition and presentation decision. Validity
  (``disposition``), credit direction (``direction``) and ``confidence`` are
  independent fields; nothing here derives one from another.
* :class:`RetrievalAttempt` — one recorded evidence search over admitted material.
* :class:`CalculationTrace` — numerator/denominator/unit/period metadata of a
  deterministic indicator, bound to its ``ParameterResult``.
* :class:`AcceptedBusinessOverview` — the structured handoff later consumed by
  Structuring and the Credit Orchestrator.

``Materiality``, ``AcceptanceState``, ``ParameterResult``, ``ConclusionClaim``,
``ChallengeFinding`` and ``RiskScore`` are reused from :mod:`app.schemas.agentic`.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.agentic import AcceptanceState, Materiality


class ClaimType(str, Enum):
    FACT = "fact"
    CALCULATED_FACT = "calculated_fact"
    MANAGEMENT_CLAIM = "management_claim"
    INFERENCE = "inference"
    ASSUMPTION = "assumption"


class Direction(str, Enum):
    """Credit direction of a hypothesis — independent of its validity."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    MIXED = "mixed"
    NEUTRAL = "neutral"


class Disposition(str, Enum):
    SUPPORTED = "supported"
    QUALIFIED = "qualified"
    REJECTED = "rejected"
    UNVERIFIED = "unverified"


class Admissibility(str, Enum):
    """How a hypothesis may be used downstream (derived deterministically)."""

    ESTABLISHED = "established"
    ATTRIBUTED = "attributed"  # must be presented as a management claim
    QUALIFIED_NARRATIVE = "qualified_narrative"
    AUDIT_ONLY = "audit_only"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    MEDIUM_LOW = "medium_low"
    LOW = "low"


class DisplayPriority(str, Enum):
    DISPLAY = "display"
    ON_DEMAND = "on_demand"
    SUPPRESS = "suppress"


class MitigantEffectiveness(str, Enum):
    DEMONSTRATED = "demonstrated"
    UNPROVEN = "unproven"
    CONTRADICTED = "contradicted"


class MitigantAssessment(BaseModel):
    """A company-specific mitigating capability and whether it is proven."""

    model_config = ConfigDict(extra="forbid")

    capability: str
    existence: str = "unverified"  # verified | unverified
    effectiveness: MitigantEffectiveness = MitigantEffectiveness.UNPROVEN
    outcome_evidence_ids: list[str] = Field(default_factory=list)


class HypothesisRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    analysis_run_id: str
    agent_id: str
    agent_run_id: str
    topic: str = "business"
    section: str  # BO1..BO6
    claim: str
    claim_type: ClaimType
    direction: Direction
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    evidence_quotes: list[dict[str, Any]] = Field(default_factory=list)
    parameter_result_ids: list[str] = Field(default_factory=list)
    quoted_values: list[dict[str, Any]] = Field(default_factory=list)
    disposition: Disposition
    admissibility: Admissibility
    confidence: Confidence = Confidence.MEDIUM
    materiality: Materiality = Materiality.MEDIUM
    credit_mechanism: str | None = None
    missing_information: list[str] = Field(default_factory=list)
    display_priority: DisplayPriority = DisplayPriority.ON_DEMAND
    review_required: bool = False
    corrects_hypothesis_id: str | None = None
    parameter_id: str | None = None
    mitigant: MitigantAssessment | None = None
    # BO6 only: the validated primary hypotheses an interaction is built from.
    component_hypothesis_ids: list[str] = Field(default_factory=list)
    interaction_kind: str | None = None
    # What the deterministic validator changed or noted (never free model text).
    validation_notes: list[str] = Field(default_factory=list)
    # Kind of support, re-derived from the verified quotes (Layer D):
    # company_specific_disclosure | deterministic_calculation |
    # management_statement | generic_risk_disclosure | general_statement |
    # inference | none.
    evidence_basis: str = "none"
    # What the model originally stated, kept for the audit trail.
    model_stated: dict[str, Any] = Field(default_factory=dict)
    # Heuristic review flags raised by the inferential and sufficiency rules.
    review_flags: list[dict[str, Any]] = Field(default_factory=list)
    # BO6: the causal path between the component hypotheses.
    transmission_mechanism: str | None = None
    # Questions handed to the Financial Overview; never BO conclusions.
    fo_handoffs: list[dict[str, Any]] = Field(default_factory=list)
    # Audit records of quote-recovery attempts (original, request, outcome).
    recovery: list[dict[str, Any]] = Field(default_factory=list)
    supersedes_id: str | None = None
    acceptance_state: AcceptanceState = AcceptanceState.ACCEPTED

    @property
    def usable(self) -> bool:
        """May inform the orchestrator's narrative (never rejected/unverified)."""
        return (
            self.acceptance_state is AcceptanceState.ACCEPTED
            and self.admissibility is not Admissibility.AUDIT_ONLY
        )

    def evidence_ids(self) -> list[str]:
        return sorted({*self.supporting_evidence_ids, *self.contradicting_evidence_ids})


class RetrievalAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    analysis_run_id: str
    agent_id: str
    query_id: str
    purpose: str
    terms: list[str]
    origin: str = "plan"  # plan | agent_request
    status: str  # performed | no_results
    passages_searched: int = 0
    results: list[dict[str, Any]] = Field(default_factory=list)
    hypothesis_id: str | None = None
    round_index: int = 0

    @property
    def is_gap(self) -> bool:
        return self.status == "no_results"


class CalculationTrace(BaseModel):
    """Traceable metadata of one deterministic indicator (never LLM arithmetic)."""

    model_config = ConfigDict(extra="forbid")

    parameter_result_id: str
    parameter_id: str
    label: str
    value: float | None
    units: str | None
    period: str | None
    numerator: dict[str, Any] | None = None
    denominator: dict[str, Any] | None = None
    source_fact_ids: list[str] = Field(default_factory=list)
    formula_id: str
    definition_version: str
    calculation_state: str
    caveat: str | None = None
    # Which specialists receive it (growth, payer, workforce, ...).
    group: str = "general"


class AcceptedBusinessOverview(BaseModel):
    """Structured BO handoff for Structuring and the Credit Orchestrator."""

    model_config = ConfigDict(extra="forbid")

    handoff_id: str
    schema_version: str = "bo-handoff-1.0"
    analysis_run_id: str
    case_id: str
    evidence_snapshot_version: int
    business_topic_conclusion_id: str | None
    conclusion_status: str  # accepted | accepted_with_qualification | human_review_required | unavailable
    business_risk_score_id: str | None
    business_risk_score_status: str
    business_risk_band: int | None = None
    accepted_finding_ids: list[str] = Field(default_factory=list)
    material_risk_ids: list[str] = Field(default_factory=list)
    business_strengths: list[dict[str, Any]] = Field(default_factory=list)
    operational_vulnerabilities: list[dict[str, Any]] = Field(default_factory=list)
    validated_mitigants: list[dict[str, Any]] = Field(default_factory=list)
    unproven_mitigants: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_challenges: list[dict[str, Any]] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    human_review_status: str = "pending"  # pending | accepted | rejected
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    execution_mode: str = "offline_simulation"  # offline_simulation | live_model
    agent_registry_version: str
    agent_registry_hash: str
    prompt_hashes: dict[str, str] = Field(default_factory=dict)
    scoring_config_version: int
    scoring_config_hash: str
    config_versions: dict[str, int] = Field(default_factory=dict)
    content_hash: str | None = None
