"""Pydantic + JSON Schema contracts for all LLM method outputs (Milestone 6).

Every ``LLMClient`` method (``extract``/``analyze``/``challenge``) must return
structured JSON that is validated against a FIXED schema BEFORE use (Req 19.4,
12.5, 13.2). This module is the single source of truth for those schemas:

* :class:`ExtractionResponse` -- qualitative extraction output (task 6.3,
  Req 3.3/3.4/3.6). AI facts carry fact name, value/statement, period, source
  ref, confidence/status and NEVER auto-enter ``verified``.
* :class:`AnalysisResponse` -- generative credit analysis (task 6.4, Req 12).
  ``business_overview, repayment_analysis, key_risks, mitigants,
  data_limitations, questions_for_human``; each claim carries evidence IDs, a
  fact-vs-interpretation flag, materiality and uncertainty.
* :class:`ChallengeResponse` -- second-pass critic output (task 6.5, Req 13.1-
  13.5); challenges carry ``claim_id, issue_type, severity, reason,
  evidence_refs``.
* :class:`ClaimGroundingResult` -- the citation-presence vs entailment split
  (task 6.5, Req 13.6-13.8).

The ``*_JSON_SCHEMA`` constants are the fixed JSON Schemas used to validate raw
model output with the ``jsonschema`` library (the schema the fake backend and a
future real provider both target). They are derived from the Pydantic models so
the two never drift.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.enums import FactStatus

# AI-extracted facts may only enter in NON-verified states (Req 3.4). ``verified``
# is reachable only through deterministic reconciliation, never from the model.
AI_ALLOWED_STATUSES: frozenset[str] = frozenset(
    {
        FactStatus.UNVERIFIED.value,
        FactStatus.CONFLICTING.value,
        FactStatus.MISSING.value,
        FactStatus.NOT_APPLICABLE.value,
        FactStatus.NOT_DISCLOSED.value,
        FactStatus.STALE.value,
    }
)


# ---------------------------------------------------------------------------
# Qualitative extraction (task 6.3)
# ---------------------------------------------------------------------------


class QualitativeTopic(str, Enum):
    """The qualitative content the extractor targets (Req 3.3)."""

    MANAGEMENT = "management"
    OWNERSHIP = "ownership"
    STRUCTURE = "corporate_structure"
    COMPETITIVE_POSITION = "competitive_position"
    INDUSTRY_RISK = "industry_risk"
    QUALITATIVE_DEBT_TERMS = "qualitative_debt_terms"


class ExtractionSourceRef(BaseModel):
    """A minimal source reference carried by an AI-extracted fact (Req 3.5)."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    page: int | None = None
    table: str | None = None
    row_label: str | None = None
    cell: str | None = None


class ExtractedQualitativeFact(BaseModel):
    """One AI-extracted qualitative fact (Req 3.3, 3.4, 3.6).

    Carries fact name, value/statement, period, >=1 source reference, and a
    confidence/status. The status MUST be a non-verified state (Req 3.4): the
    model may never promote its own output to ``verified``.
    """

    model_config = ConfigDict(extra="forbid")

    fact_id: str
    name: str
    topic: QualitativeTopic
    statement: str
    period: str | None = None
    status: str = FactStatus.UNVERIFIED.value
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source_refs: list[ExtractionSourceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> ExtractedQualitativeFact:
        if not self.source_refs:
            raise ValueError(
                f"Extracted fact {self.fact_id!r} must carry >=1 source reference "
                "(no unsupported inference, Req 3.4)."
            )
        if self.status not in AI_ALLOWED_STATUSES:
            raise ValueError(
                f"AI-extracted fact {self.fact_id!r} may not use status "
                f"{self.status!r}; AI facts never auto-verify (Req 3.4)."
            )
        return self


class ExtractionResponse(BaseModel):
    """The full qualitative-extraction response (task 6.3)."""

    model_config = ConfigDict(extra="forbid")

    facts: list[ExtractedQualitativeFact] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Generative credit analysis (task 6.4)
# ---------------------------------------------------------------------------


class ClaimKind(str, Enum):
    """Fact-vs-interpretation designation for an analytical claim (Req 12.3)."""

    FACT = "fact"
    INTERPRETATION = "interpretation"


class Materiality(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AnalyticalClaim(BaseModel):
    """One analytical claim with full traceability metadata (Req 12.3, 12.6).

    Every claim carries claim text, evidence IDs, a fact-vs-interpretation
    designation, materiality and an uncertainty/caveat. A ``fact`` claim must
    cite evidence (Req 12.6).
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    text: str
    kind: ClaimKind
    evidence_ids: list[str] = Field(default_factory=list)
    materiality: Materiality = Materiality.MEDIUM
    uncertainty: str | None = None

    @model_validator(mode="after")
    def _fact_claims_cite_evidence(self) -> AnalyticalClaim:
        if self.kind is ClaimKind.FACT and not self.evidence_ids:
            raise ValueError(
                f"Factual claim {self.claim_id!r} must carry evidence IDs "
                "(Req 12.6): no unsupported factual claim may enter the memo."
            )
        return self


class AnalysisResponse(BaseModel):
    """Schema-validated generative credit analysis (Req 12.2).

    The six required sections are each a list of claims. ``data_limitations``
    and ``questions_for_human`` surface uncertainty rather than fabricating
    confident prose (Req 12.4, 12.7).
    """

    model_config = ConfigDict(extra="forbid")

    business_overview: list[AnalyticalClaim] = Field(default_factory=list)
    repayment_analysis: list[AnalyticalClaim] = Field(default_factory=list)
    key_risks: list[AnalyticalClaim] = Field(default_factory=list)
    mitigants: list[AnalyticalClaim] = Field(default_factory=list)
    data_limitations: list[str] = Field(default_factory=list)
    questions_for_human: list[str] = Field(default_factory=list)

    def all_claims(self) -> list[AnalyticalClaim]:
        """Return every claim across the four claim-bearing sections."""
        return [
            *self.business_overview,
            *self.repayment_analysis,
            *self.key_risks,
            *self.mitigants,
        ]


# ---------------------------------------------------------------------------
# Challenge layer (task 6.5)
# ---------------------------------------------------------------------------


class IssueType(str, Enum):
    """The challenge tests a second-pass critic performs (Req 13.1)."""

    UNSUPPORTED = "unsupported_claim"
    METRIC_CONTRADICTION = "metric_contradiction"
    OMITTED_RISK = "omitted_risk"
    MITIGANT_RELEVANCE = "mitigant_relevance"
    MISSING_EVIDENCE = "missing_evidence"
    ALTERNATIVE_EXPLANATION = "alternative_explanation"
    OVERSTATED_CERTAINTY = "overstated_certainty"


class ChallengeSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Challenge(BaseModel):
    """A single challenge finding (Req 13.2)."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    issue_type: IssueType
    severity: ChallengeSeverity
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)


class ChallengeResponse(BaseModel):
    """The full challenge-pass response (task 6.5)."""

    model_config = ConfigDict(extra="forbid")

    challenges: list[Challenge] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Claim grounding (task 6.5, Req 13.6-13.8)
# ---------------------------------------------------------------------------


class EntailmentState(str, Enum):
    """Grounding states (Req 13.7)."""

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTORY = "contradictory"
    NOT_VERIFIABLE = "not_verifiable"


class JudgeKind(str, Enum):
    """Who/what decided entailment (Req 13.8)."""

    DETERMINISTIC = "deterministic"
    LLM = "llm"
    HUMAN = "human"


class ClaimGroundingResult(BaseModel):
    """The two-part grounding verdict for one claim (Req 13.6-13.8).

    ``citation_present`` (check A) and ``entailment_state`` (check B) are
    INDEPENDENT. ``is_grounded`` is True ONLY when BOTH a citation is present AND
    entailment is ``supported`` -- a citation ID alone is never enough (Req 13.8).
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    citation_present: bool
    entailment_state: EntailmentState
    evidence_refs: list[str] = Field(default_factory=list)
    judge: JudgeKind = JudgeKind.DETERMINISTIC
    adjudicated: bool = False
    detail: str | None = None

    @property
    def is_grounded(self) -> bool:
        """A claim is grounded only if cited AND entailment is ``supported``."""
        return (
            self.citation_present
            and self.entailment_state is EntailmentState.SUPPORTED
        )


# ---------------------------------------------------------------------------
# Fixed JSON Schemas (validate raw model output before use)
# ---------------------------------------------------------------------------

EXTRACTION_JSON_SCHEMA: dict[str, Any] = ExtractionResponse.model_json_schema()
ANALYSIS_JSON_SCHEMA: dict[str, Any] = AnalysisResponse.model_json_schema()
CHALLENGE_JSON_SCHEMA: dict[str, Any] = ChallengeResponse.model_json_schema()
