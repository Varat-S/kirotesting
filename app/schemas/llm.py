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
* :class:`CreditHypothesis` / :class:`CreditHypothesisSet` -- the Business
  Overview Module 1 data foundation (six :class:`CreditDimension` dimensions,
  the five-category :class:`ClaimClassification`, and hypothesis verification /
  credit-direction / presentation enums). These are ADDITIVE: nothing in the
  existing extraction / analysis / challenge contracts changes.

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
from app.schemas.evidence import EvidenceSearchIntent

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
# Business Overview Module 1: credit dimensions, evidence governance and the
# independent credit-hypothesis object (data foundation ONLY -- no generation,
# no scoring, no pipeline wiring). All additions here are ADDITIVE: the existing
# ``ClaimKind`` / ``AnalyticalClaim`` / ``AnalysisResponse`` contracts are
# unchanged so historical outputs and the live pipeline keep validating.
# ---------------------------------------------------------------------------


class CreditDimension(str, Enum):
    """The six analytical credit dimensions of Business Overview Module 1.

    These are ANALYTICAL dimensions, not six standalone agents. A dimension is a
    lens the existing ORANGE specialist analysis is asked to reason along; the
    assignment of dimensions to specialist agents happens in a later task and is
    deliberately NOT encoded here. No HCA-specific conclusion or keyword is
    baked into the taxonomy -- these are general corporate-credit dimensions.
    """

    REVENUE_QUALITY_DEMAND_DEFENSIVENESS = "revenue_quality_demand_defensiveness"
    CUSTOMER_STICKINESS_CONCENTRATION = "customer_stickiness_concentration"
    PRICING_POWER_REVENUE_REALISATION = "pricing_power_revenue_realisation"
    OPERATING_MODEL_COST_RESILIENCE = "operating_model_cost_resilience"
    COMPETITIVE_DURABILITY = "competitive_durability"
    FINANCIAL_STEWARDSHIP_CAPITAL_COMMITMENTS = (
        "financial_stewardship_capital_commitments"
    )


class ClaimClassification(str, Enum):
    """Five-category evidence-governance classification (approved framework).

    This is a NEW, SEPARATE taxonomy and does NOT replace :class:`ClaimKind`
    (``fact``/``interpretation``), which stays in force for ``AnalyticalClaim``
    and every existing consumer. The five categories keep facts, management
    claims, inferences and assumptions distinguishable so none can be silently
    promoted into another:

    * ``reported_fact``   -- a figure/statement read directly from a source.
    * ``calculated_fact`` -- a deterministically derived figure (needs its
      inputs AND a calculation reference to stay traceable).
    * ``management_claim``-- an assertion attributable to management, true only
      as "management states X"; never upgraded to an established fact here.
    * ``inference``       -- an analytical conclusion drawn from evidence;
      distinct from an established fact.
    * ``assumption``      -- an unestablished premise; may never be auto-promoted
      to a verified finding.
    """

    REPORTED_FACT = "reported_fact"
    CALCULATED_FACT = "calculated_fact"
    MANAGEMENT_CLAIM = "management_claim"
    INFERENCE = "inference"
    ASSUMPTION = "assumption"


class CreditDirection(str, Enum):
    """Direction of a hypothesis's credit implication (INDEPENDENT of evidence).

    Credit direction is deliberately orthogonal to verification: a POSITIVE
    hypothesis can be ``unverified`` and a NEGATIVE hypothesis can be
    ``supported``. Nothing couples these two axes.
    """

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class PresentationMateriality(str, Enum):
    """Whether/how a hypothesis is surfaced in output (presentation decision).

    This is a PRESENTATION decision and is kept strictly separate from the
    low/medium/high analytical severity in :class:`Materiality`. The two are
    different judgements and must not be conflated.
    """

    DISPLAY = "display"
    ON_DEMAND = "on_demand"
    SUPPRESS = "suppress"


class HypothesisVerificationStatus(str, Enum):
    """Verification state of a credit hypothesis (NOT evidence grounding).

    Hypothesis verification asks whether the available evidence adequately
    supports the proposed ECONOMIC RELATIONSHIP. That is a different judgement
    from :class:`EntailmentState` (claim grounding), which asks whether a cited
    source supports a single factual claim. A newly generated hypothesis MUST
    default to ``unverified``: generation alone never establishes verification.
    """

    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    REJECTED = "rejected"
    UNVERIFIED = "unverified"


# Verification states that assert evidentiary support for the economic relation.
_SUPPORTING_STATUSES: frozenset[HypothesisVerificationStatus] = frozenset(
    {
        HypothesisVerificationStatus.SUPPORTED,
        HypothesisVerificationStatus.PARTIALLY_SUPPORTED,
    }
)


class CreditHypothesis(BaseModel):
    """An independently generated, evidence-governed credit hypothesis.

    Structured on purpose: reasoning is split across typed fields rather than
    one free-text blob, so each facet (direction, mechanism, impact, offsets,
    evidence, governance) stays independently inspectable. The model enforces
    the SCHEMA-level governance invariants it can prove; it does NOT (and cannot)
    assert that the cited evidence semantically supports the economic
    relationship -- that is later semantic work (see module notes / Req 13).

    Enforced invariants:

    * default verification is ``unverified`` (generation never verifies);
    * a ``supported``/``partially_supported`` hypothesis MUST cite >=1 supporting
      evidence id (no supported-with-no-evidence state);
    * a ``reported_fact`` classification MUST carry supporting evidence (a
      reported fact without a source is not a reported fact);
    * a ``calculated_fact`` MUST carry both supporting evidence (its inputs) and
      >=1 ``calculation_refs`` entry (traceable derivation);
    * an ``assumption`` may NOT be ``supported`` -- an unestablished premise is
      never auto-promoted to a verified finding;
    * supporting and contradictory evidence are stored in SEPARATE lists so
      contradictory evidence is preserved, never merged away;
    * ``missing_information`` stays explicit (its presence never implies zero).
    """

    model_config = ConfigDict(extra="forbid")

    # -- identification -------------------------------------------------------
    hypothesis_id: str
    dimension: CreditDimension
    business_characteristic: str
    statement: str

    # -- analytical reasoning -------------------------------------------------
    credit_direction: CreditDirection
    economic_reasoning: str
    potential_credit_impact: str
    offsetting_characteristics: list[str] = Field(default_factory=list)

    # -- evidence -------------------------------------------------------------
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradictory_evidence_ids: list[str] = Field(default_factory=list)
    additional_evidence_required: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    calculation_refs: list[str] = Field(default_factory=list)

    # -- governance -----------------------------------------------------------
    classification: ClaimClassification
    verification_status: HypothesisVerificationStatus = (
        HypothesisVerificationStatus.UNVERIFIED
    )
    # Confidence in the INTERPRETATION, kept separate from confidence in facts.
    interpretation_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    presentation_materiality: PresentationMateriality = PresentationMateriality.DISPLAY
    severity: Materiality = Materiality.MEDIUM
    human_review_required: bool = False

    @model_validator(mode="after")
    def _enforce_governance(self) -> CreditHypothesis:
        status = self.verification_status
        classification = self.classification

        # A supported / partially-supported hypothesis needs supporting evidence.
        if status in _SUPPORTING_STATUSES and not self.supporting_evidence_ids:
            raise ValueError(
                f"Hypothesis {self.hypothesis_id!r} is {status.value!r} but cites no "
                "supporting evidence; a supported hypothesis must reference evidence "
                "(Req 13.x; no supported-with-no-evidence state)."
            )

        # A reported fact must be sourced.
        if (
            classification is ClaimClassification.REPORTED_FACT
            and not self.supporting_evidence_ids
        ):
            raise ValueError(
                f"Hypothesis {self.hypothesis_id!r} is classified reported_fact but "
                "carries no supporting evidence; a reported fact must be traceable "
                "to a source."
            )

        # A calculated fact needs its inputs AND a calculation reference.
        if classification is ClaimClassification.CALCULATED_FACT:
            if not self.supporting_evidence_ids:
                raise ValueError(
                    f"Hypothesis {self.hypothesis_id!r} is classified calculated_fact "
                    "but carries no supporting (input) evidence."
                )
            if not self.calculation_refs:
                raise ValueError(
                    f"Hypothesis {self.hypothesis_id!r} is classified calculated_fact "
                    "but carries no calculation_refs; a derived figure must be "
                    "traceable to its calculation."
                )

        # An assumption is an unestablished premise: it may not be 'supported'.
        if (
            classification is ClaimClassification.ASSUMPTION
            and status is HypothesisVerificationStatus.SUPPORTED
        ):
            raise ValueError(
                f"Hypothesis {self.hypothesis_id!r} is an assumption and may not be "
                "'supported'; an unestablished premise is never auto-promoted to a "
                "verified finding."
            )

        return self


class CreditHypothesisSet(BaseModel):
    """A collection of independently generated credit hypotheses.

    Container for the per-run output of a future hypothesis-generation step. It
    is a pure data holder here (no generation logic); it exists so a fixed JSON
    Schema can validate a batch of hypotheses the same way the other LLM method
    outputs are validated before use.
    """

    model_config = ConfigDict(extra="forbid")

    hypotheses: list[CreditHypothesis] = Field(default_factory=list)

    def for_dimension(self, dimension: CreditDimension) -> list[CreditHypothesis]:
        """Return the hypotheses scoped to one credit dimension."""
        return [h for h in self.hypotheses if h.dimension is dimension]


# ---------------------------------------------------------------------------
# Evidence enquiry planning (Stage 5): translate a hypothesis into targeted
# investigative questions + search queries. The planner decides WHAT to
# investigate; it never concludes anything and never touches a hypothesis.
# ---------------------------------------------------------------------------


class EvidenceRequirementKind(str, Enum):
    """The investigative PERSPECTIVE an enquiry serves (planning taxonomy).

    Distinct from :class:`~app.schemas.evidence.EvidenceSearchIntent` (which is
    the retrieval-layer label). ``effectiveness`` has no retrieval-intent
    equivalent and maps to a general investigation at search time: it asks
    whether a found initiative actually improved outcomes, NOT merely whether it
    exists -- the existence/outcome distinction the planner must preserve.
    """

    SUPPORTING = "supporting"
    CONTRADICTORY = "contradictory"
    OFFSETTING = "offsetting"
    EFFECTIVENESS = "effectiveness"


class EvidenceEnquiry(BaseModel):
    """One targeted investigative enquiry derived from a credit hypothesis.

    An enquiry is a QUESTION plus the search query that operationalises it, with
    the rationale that ties it back to the hypothesis's economic mechanism. It
    is a plan to investigate, never a finding: it must not assert that any
    document contains an answer.
    """

    model_config = ConfigDict(extra="forbid")

    enquiry_id: str
    hypothesis_id: str
    dimension: CreditDimension
    requirement_kind: EvidenceRequirementKind
    # Retrieval-layer intent carried verbatim so the coordinator can build an
    # EvidenceSearchRequest without re-deriving it.
    search_intent: EvidenceSearchIntent
    investigative_question: str
    search_query: str
    rationale: str
    evidence_requirement: str
    priority: int = Field(default=3, ge=1, le=5)
    permitted_document_ids: list[str] = Field(default_factory=list)


class EvidenceEnquiryPlan(BaseModel):
    """A full, typed enquiry plan for one or more hypotheses.

    ``incomplete_coverage`` records investigative perspectives that were
    intentionally dropped because a bound was hit (never silently discarded).
    ``planner_mode`` distinguishes genuine generative planning from the
    reduced-capability deterministic fallback.
    """

    model_config = ConfigDict(extra="forbid")

    enquiries: list[EvidenceEnquiry] = Field(default_factory=list)
    planner_mode: str = "llm"
    incomplete_coverage: list[dict] = Field(default_factory=list)

    def for_hypothesis(self, hypothesis_id: str) -> list[EvidenceEnquiry]:
        return [e for e in self.enquiries if e.hypothesis_id == hypothesis_id]


# ---------------------------------------------------------------------------
# Hypothesis testing (Stage 8): evaluate a hypothesis against retrieved
# candidate evidence. Kept SEPARATE from the generation output -- a test result
# never overwrites a CreditHypothesis (original immutability).
# ---------------------------------------------------------------------------


class EvidenceAssessmentKind(str, Enum):
    """What a passage ACTUALLY establishes, independent of its search intent.

    The enquiry's search intent (why we looked) must never dictate this: a
    passage found by a contradictory search may in fact support a premise, and
    vice versa. ``describes_initiative`` and ``measured_outcome`` are kept
    distinct so a disclosed programme is never conflated with a demonstrated
    financial result.
    """

    SUPPORTS_PREMISE = "supports_premise"
    CONTRADICTS_PREMISE = "contradicts_premise"
    CONTEXTUAL = "contextual"
    DESCRIBES_INITIATIVE = "describes_initiative"
    MEASURED_OUTCOME = "measured_outcome"
    IRRELEVANT = "irrelevant"


class PremiseStatus(str, Enum):
    """Whether a single factual premise of the mechanism is established."""

    VERIFIED = "verified"
    PARTIALLY_VERIFIED = "partially_verified"
    UNVERIFIED = "unverified"
    CONTRADICTED = "contradicted"


class EvidenceAssessment(BaseModel):
    """Layer B: what one retrieved passage actually establishes for a hypothesis.

    ``passage_id`` must reference a real retrieved passage; ``quoted_excerpt``
    (when present) is checked by the service to be contained in that passage.
    ``search_intent`` records the intent the passage was retrieved under purely
    for traceability -- it does NOT constrain ``assessment``.
    """

    model_config = ConfigDict(extra="forbid")

    passage_id: str
    assessment: EvidenceAssessmentKind
    rationale: str
    quoted_excerpt: str | None = None
    search_intent: str | None = None


class MechanismPremise(BaseModel):
    """Layer C: one proposition in the hypothesis's economic mechanism.

    E.g. "the programme exists", "graduates enter the workforce", "staffing
    shortages measurably improved", "the improvement is financially material".
    Each premise is individually statused so support for an early premise is
    never read as support for a later (outcome/materiality) premise.

    Task 6B: evidence is linked PER PREMISE. ``supporting_passage_ids`` /
    ``contradictory_passage_ids`` and any cited ``grounding_claim`` apply to THIS
    premise only -- the service never attaches an undifferentiated evidence pool
    to a premise. ``material`` marks premises essential to the conclusion;
    ``is_outcome_premise`` marks effectiveness/outcome premises (existence of an
    initiative is never its effect). ``grounding_claim`` is the specific factual
    proposition (optionally a numeric figure) to be checked by deterministic
    grounding against this premise's cited evidence.
    """

    model_config = ConfigDict(extra="forbid")

    premise: str
    status: PremiseStatus
    supporting_passage_ids: list[str] = Field(default_factory=list)
    contradictory_passage_ids: list[str] = Field(default_factory=list)
    grounding_claim: str | None = None
    is_outcome_premise: bool = False
    material: bool = True
    premise_id: str | None = None
    # Task 6C: an explicit, controlled link to the approved metric/fact this
    # premise claims to be grounded by, plus the expected concept/attributes the
    # service must confirm against the metric's TRUSTED metadata. A numeric match
    # alone never grounds a premise -- the concept link must validate.
    grounding_metric_id: str | None = None
    claimed_concept: str | None = None
    claimed_entity: str | None = None
    claimed_period: str | None = None
    claimed_scale: str | None = None
    claimed_currency: str | None = None
    claimed_accounting_basis: str | None = None


class FactualGroundingRecord(BaseModel):
    """Layer A: the grounding verdict for one factual assertion (from the
    deterministic :class:`GroundingEvaluator`, not the testing model)."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    claim_text: str
    citation_present: bool
    entailment_state: EntailmentState
    evidence_refs: list[str] = Field(default_factory=list)
    is_grounded: bool
    premise_id: str | None = None


class PremiseCorroboration(BaseModel):
    """Per-premise corroboration outcome (Task 6B).

    ``corroborated`` is True ONLY when THIS premise is established by a non-model
    channel: its own cited evidence deterministically grounds its
    ``grounding_claim`` (``grounding``), or an authorised human adjudication
    (``human_adjudication``) targets this premise. Model-labelled ``status`` and
    model-cited passage ids never set it.
    """

    model_config = ConfigDict(extra="forbid")

    premise_id: str
    premise: str
    material: bool
    is_outcome_premise: bool
    model_status: PremiseStatus
    corroborated: bool
    corroboration_basis: list[str] = Field(default_factory=list)
    grounding: FactualGroundingRecord | None = None
    notes: list[str] = Field(default_factory=list)


class HypothesisTestResult(BaseModel):
    """The model-PROPOSED evaluation of one hypothesis (pre-governance).

    This is the structured LLM output. The service computes the EFFECTIVE verdict
    separately (see :class:`GovernedHypothesisTestResult`); the model may propose
    but never finalises a status.
    """

    model_config = ConfigDict(extra="forbid")

    # identification
    hypothesis_id: str
    dimension: CreditDimension
    hypothesis_statement: str

    # evidence evaluation (Layer B)
    evidence_assessments: list[EvidenceAssessment] = Field(default_factory=list)
    relevant_factual_claims: list[str] = Field(default_factory=list)

    # economic reasoning (Layer C)
    proposed_mechanism: str
    mechanism_premises: list[MechanismPremise] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    offsetting_capabilities: list[str] = Field(default_factory=list)
    offset_effectiveness_evidence: list[str] = Field(default_factory=list)

    # conclusion (PROPOSED only)
    proposed_status: HypothesisVerificationStatus
    justification: str
    unresolved_evidence_requirements: list[str] = Field(default_factory=list)
    interpretation_confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    def supporting_passage_ids(self) -> list[str]:
        return [
            a.passage_id
            for a in self.evidence_assessments
            if a.assessment is EvidenceAssessmentKind.SUPPORTS_PREMISE
        ]

    def contradictory_passage_ids(self) -> list[str]:
        return [
            a.passage_id
            for a in self.evidence_assessments
            if a.assessment is EvidenceAssessmentKind.CONTRADICTS_PREMISE
        ]


class HypothesisTestResponse(BaseModel):
    """The full hypothesis-testing model output (one result per hypothesis)."""

    model_config = ConfigDict(extra="forbid")

    results: list[HypothesisTestResult] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Stages 9-11: credit signal -> impact assessment -> materiality prioritisation.
# These consume governed test results; they never overwrite the hypothesis and
# never promote verification status or invent numbers.
# ---------------------------------------------------------------------------


class ImpactChannel(str, Enum):
    """The economic transmission channel of a credit signal (Stage 10)."""

    REVENUE_SIDE = "revenue_side"
    OPERATING = "operating"
    CASH_FLOW_CAPITAL = "cash_flow_capital"
    OTHER = "other"


class ImpactQuantification(str, Enum):
    """Whether the credit impact is quantified from validated metrics or not.

    ``quantified`` is permitted ONLY when the impact references a validated
    deterministic metric id; the LLM may never invent a numeric estimate.
    """

    QUANTIFIED = "quantified"
    QUALITATIVE = "qualitative"
    UNQUANTIFIED = "unquantified"


class CreditSignal(BaseModel):
    """Stage 9: an economically meaningful finding derived from a tested hypothesis.

    Carries the effective verification status (never re-derived here) and keeps
    the ORIGINAL hypothesis direction distinct from the assessed signal
    direction, so a direction mismatch is flagged for review rather than
    silently overwritten.
    """

    model_config = ConfigDict(extra="forbid")

    signal_id: str
    hypothesis_id: str
    dimension: CreditDimension
    business_characteristic: str
    signal_statement: str
    original_direction: CreditDirection
    assessed_direction: CreditDirection
    direction_mismatch: bool = False
    proposed_mechanism: str = ""
    verification_status: HypothesisVerificationStatus = (
        HypothesisVerificationStatus.UNVERIFIED
    )
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradictory_evidence_ids: list[str] = Field(default_factory=list)
    unresolved_limitations: list[str] = Field(default_factory=list)
    offsetting_characteristics: list[str] = Field(default_factory=list)
    # A signal derived from a REJECTED hypothesis is NOT a validated finding; it
    # is only an investigation candidate (never presented as positive/negative).
    is_investigation_candidate: bool = False


class CreditImpactAssessment(BaseModel):
    """Stage 10: how a signal could affect repayment resilience.

    The fact / corroborated-premise / inference / assumption / missing split is
    kept explicit so a possibility is never stated as a proven effect.
    """

    model_config = ConfigDict(extra="forbid")

    signal_id: str
    impact_channel: ImpactChannel
    transmission_mechanism: str
    observed_facts: list[str] = Field(default_factory=list)
    corroborated_premises: list[str] = Field(default_factory=list)
    unproven_inferences: list[str] = Field(default_factory=list)
    material_assumptions: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    quantification: ImpactQuantification = ImpactQuantification.UNQUANTIFIED
    quantified_metric_refs: list[str] = Field(default_factory=list)
    potential_repayment_implication: str = ""

    @model_validator(mode="after")
    def _quantified_needs_metric(self) -> CreditImpactAssessment:
        if (
            self.quantification is ImpactQuantification.QUANTIFIED
            and not self.quantified_metric_refs
        ):
            raise ValueError(
                f"Impact for {self.signal_id!r} is 'quantified' but references no "
                "validated metric; the LLM may not invent a numeric estimate."
            )
        return self


class MaterialityAssessment(BaseModel):
    """Stage 11: provisional analytical triage of a finding's credit significance.

    Materiality is INDEPENDENT of verification confidence, direction, LLM
    confidence, citation count and presentation. ``rubric_version`` records the
    exact triage rubric used. ``requires_human_review`` is set when the rubric
    cannot defensibly separate HIGH from MEDIUM.
    """

    model_config = ConfigDict(extra="forbid")

    signal_id: str
    materiality: Materiality
    rationale: str
    rubric_version: str
    criteria: dict = Field(default_factory=dict)
    presentation: PresentationMateriality = PresentationMateriality.DISPLAY
    requires_human_review: bool = False


class PrioritisedCreditFinding(BaseModel):
    """A signal + impact + materiality, with the full evidence/Stage-12 trail."""

    model_config = ConfigDict(extra="forbid")

    signal: CreditSignal
    impact: CreditImpactAssessment
    materiality: MaterialityAssessment
    rank: int
    # Stage 12 handoff (see CreditFindingSet for the aggregate view).
    uncorroborated_material_premises: list[str] = Field(default_factory=list)
    unresolved_evidence_requirements: list[str] = Field(default_factory=list)
    outstanding_contradictions: list[str] = Field(default_factory=list)
    uninvestigated_offsets: list[str] = Field(default_factory=list)
    required_human_review: bool = False
    # Links to other signals that share the same underlying exposure (overlap /
    # potential double-count), recorded rather than silently merged.
    overlap_links: list[str] = Field(default_factory=list)
    testing_run_id: str | None = None


class CreditFindingSet(BaseModel):
    """The ordered set of prioritised findings plus the rubric provenance."""

    model_config = ConfigDict(extra="forbid")

    findings: list[PrioritisedCreditFinding] = Field(default_factory=list)
    investigation_candidates: list[CreditSignal] = Field(default_factory=list)
    rubric_version: str = ""

    def material_findings(self) -> list[PrioritisedCreditFinding]:
        return [
            f for f in self.findings
            if f.materiality.materiality is not Materiality.LOW
        ]


class ProposedImpactAssessment(BaseModel):
    """The LLM-PROPOSED impact + materiality for one signal (pre-governance)."""

    model_config = ConfigDict(extra="forbid")

    signal_id: str
    impact_channel: ImpactChannel
    transmission_mechanism: str
    observed_facts: list[str] = Field(default_factory=list)
    unproven_inferences: list[str] = Field(default_factory=list)
    material_assumptions: list[str] = Field(default_factory=list)
    quantified_metric_refs: list[str] = Field(default_factory=list)
    potential_repayment_implication: str = ""
    proposed_materiality: Materiality = Materiality.MEDIUM
    materiality_rationale: str = ""


class CreditSignalAssessmentResponse(BaseModel):
    """The full credit-signal-assessment model output (one per signal)."""

    model_config = ConfigDict(extra="forbid")

    assessments: list[ProposedImpactAssessment] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Stage 12: Material Evidence Completeness. Three SEPARATE assessments (source
# package / investigation / evidence sufficiency) feed a conservative
# progression recommendation. Deterministic; no module score; no LLM.
# ---------------------------------------------------------------------------


class GapCategory(str, Enum):
    """Why a material gap exists (kept distinct so none is collapsed)."""

    NO_SUPPORTING_INVESTIGATION = "no_supporting_investigation"
    NO_CONTRADICTORY_INVESTIGATION = "no_contradictory_investigation"
    OFFSET_NOT_INVESTIGATED = "offset_not_investigated"
    OFFSET_EFFECTIVENESS_UNVERIFIED = "offset_effectiveness_unverified"
    UNCORROBORATED_MATERIAL_PREMISE = "uncorroborated_material_premise"
    UNRESOLVED_CONTRADICTION = "unresolved_contradiction"
    OUTSTANDING_EVIDENCE_REQUIREMENT = "outstanding_evidence_requirement"
    SOURCE_MISSING_OR_UNSEARCHABLE = "source_missing_or_unsearchable"
    DIMENSION_NOT_ASSESSED = "dimension_not_assessed"
    DIMENSION_MATERIALLY_INCOMPLETE = "dimension_materially_incomplete"
    POTENTIAL_INVESTIGATION_GAP = "potential_investigation_gap"
    HUMAN_REVIEW_OUTSTANDING = "human_review_outstanding"


class OffsetInvestigationStatus(str, Enum):
    """The six distinct offset states (Task 8 §7); never conflated."""

    NOT_INVESTIGATED = "not_investigated"
    INVESTIGATED_NO_MATCH = "investigated_no_match"
    POTENTIAL_OFFSET_DISCOVERED = "potential_offset_discovered"
    EXISTENCE_SUPPORTED = "existence_supported"
    EFFECTIVENESS_SUPPORTED = "effectiveness_supported"
    EFFECTIVENESS_UNVERIFIED = "effectiveness_unverified"
    NOT_APPLICABLE = "not_applicable"


class DimensionApplicability(str, Enum):
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    APPLICABILITY_UNCERTAIN = "applicability_uncertain"


class DimensionCoverageStatus(str, Enum):
    ASSESSED = "assessed"
    MATERIALLY_INCOMPLETE = "materially_incomplete"
    NOT_ASSESSED = "not_assessed"
    NOT_APPLICABLE = "not_applicable"


class InvestigationStatus(str, Enum):
    COMPLETE_FOR_ASSESSED_SCOPE = "complete_for_assessed_scope"
    PARTIAL = "partial"
    NOT_ASSESSABLE = "not_assessable"


class EvidenceSufficiency(str, Enum):
    SUFFICIENT_FOR_PRELIMINARY_ANALYSIS = "sufficient_for_preliminary_analysis"
    LIMITED = "limited"
    INSUFFICIENT = "insufficient"


class ProgressionRecommendation(str, Enum):
    """READY_FOR_RED_CHALLENGE is NOT lending approval or memo sign-off."""

    READY_FOR_RED_CHALLENGE = "ready_for_red_challenge"
    READY_WITH_ESCALATION = "ready_with_escalation"
    BLOCKED_PENDING_EVIDENCE = "blocked_pending_evidence"
    NOT_ASSESSABLE = "not_assessable"


class MaterialEvidenceGap(BaseModel):
    """One material completeness gap, with its evidence trail and follow-up."""

    model_config = ConfigDict(extra="forbid")

    gap_id: str
    finding_id: str | None = None
    hypothesis_id: str | None = None
    dimension: CreditDimension | None = None
    category: GapCategory
    description: str
    materiality: Materiality
    related_ids: list[str] = Field(default_factory=list)
    evidence_available: list[str] = Field(default_factory=list)
    evidence_required: list[str] = Field(default_factory=list)
    recommended_follow_up: str = ""
    requires_human_review: bool = False
    blocks_progression: bool = False


class DimensionCoverageAssessment(BaseModel):
    """Coverage of one of the six credit dimensions (Task 8 §5)."""

    model_config = ConfigDict(extra="forbid")

    dimension: CreditDimension
    applicability: DimensionApplicability
    coverage_status: DimensionCoverageStatus
    evaluated_hypotheses: list[str] = Field(default_factory=list)
    material_gaps: list[str] = Field(default_factory=list)
    reason: str = ""


class FindingCompletenessAssessment(BaseModel):
    """Per-finding completeness (Task 8 §4)."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str
    hypothesis_id: str
    dimension: CreditDimension
    materiality: Materiality
    verification_status: HypothesisVerificationStatus
    investigation_status: InvestigationStatus
    evidence_sufficiency: EvidenceSufficiency
    supporting_investigated: bool
    contradictory_investigated: bool
    offset_status: OffsetInvestigationStatus
    unresolved_requirements: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    requires_human_review: bool = False


class ModuleEvidenceCompletenessAssessment(BaseModel):
    """The whole-module completeness verdict (Task 8 §10).

    The three completeness types are stored SEPARATELY and the progression is
    derived from both investigation coverage and evidence sufficiency. A
    ``READY_FOR_RED_CHALLENGE`` recommendation is NOT lending approval.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str | None = None
    assessment_version: str
    rubric_version: str = ""
    # Source-package completeness (reused, not rebuilt): a serialised view of the
    # existing CompletenessAssessment, or None if not supplied.
    source_package: dict | None = None
    source_package_complete: bool | None = None
    dimension_coverage: list[DimensionCoverageAssessment] = Field(default_factory=list)
    finding_completeness: list[FindingCompletenessAssessment] = Field(
        default_factory=list
    )
    material_gaps: list[MaterialEvidenceGap] = Field(default_factory=list)
    investigation_candidates: list[str] = Field(default_factory=list)
    human_review_requirements: list[str] = Field(default_factory=list)
    overall_investigation_status: InvestigationStatus
    overall_evidence_sufficiency: EvidenceSufficiency
    progression: ProgressionRecommendation
    audit_provenance: dict = Field(default_factory=dict)

    def blocking_gaps(self) -> list[MaterialEvidenceGap]:
        return [g for g in self.material_gaps if g.blocks_progression]


# ---------------------------------------------------------------------------
# Stage 13: Independent RED challenge & reconciliation. The challenger PROPOSES
# objections; the service governs references/evidence-discipline, never changes
# a verification status, and never auto-resolves a challenge via a model
# rebuttal. A challenge is an allegation requiring evaluation, not a finding.
# ---------------------------------------------------------------------------


class RedChallengeCategory(str, Enum):
    """What a RED objection alleges (kept distinct; not collapsed)."""

    UNSUPPORTED_FACTUAL_CLAIM = "unsupported_factual_claim"
    UNSUPPORTED_ECONOMIC_MECHANISM = "unsupported_economic_mechanism"
    CONTRADICTORY_EVIDENCE = "contradictory_evidence"
    OVERSTATED_CERTAINTY = "overstated_certainty"
    INCORRECT_MATERIALITY = "incorrect_materiality"
    OVERLOOKED_VULNERABILITY = "overlooked_vulnerability"
    MISSING_COMPANY_SPECIFIC_OFFSET = "missing_company_specific_offset"
    UNPROVEN_OFFSET_EFFECTIVENESS = "unproven_offset_effectiveness"
    INADEQUATE_INVESTIGATION = "inadequate_investigation"
    UNSUPPORTED_DIMENSION_NON_APPLICABILITY = "unsupported_dimension_non_applicability"
    INCORRECT_COMPLETENESS_RECOMMENDATION = "incorrect_completeness_recommendation"
    DOUBLE_COUNTING = "double_counting"


class RedTargetKind(str, Enum):
    """What a challenge targets (so references can be resolved per-kind)."""

    HYPOTHESIS = "hypothesis"
    FINDING = "finding"
    DIMENSION = "dimension"
    COMPLETENESS = "completeness"
    OFFSET = "offset"


class ChallengeDisposition(str, Enum):
    """Reconciliation state of a challenge (never auto-resolved by a rebuttal)."""

    UNRESOLVED = "unresolved"
    PARTIALLY_ADDRESSED = "partially_addressed"
    RESOLVED = "resolved"
    PENDING_HUMAN_ADJUDICATION = "pending_human_adjudication"


class RedChallenge(BaseModel):
    """One model-PROPOSED RED objection (pre-governance)."""

    model_config = ConfigDict(extra="forbid")

    challenge_id: str
    target_kind: RedTargetKind
    target_id: str
    category: RedChallengeCategory
    severity: ChallengeSeverity
    challenged_statement: str
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    # The model's own claim about whether its objection is backed by cited
    # evidence; the SERVICE re-checks this and never trusts it blindly.
    claims_evidence_backed: bool = False
    potential_consequence: str = ""
    recommended_action: str = ""


class RedChallengeResponse(BaseModel):
    """The full RED challenge model output."""

    model_config = ConfigDict(extra="forbid")

    challenges: list[RedChallenge] = Field(default_factory=list)


class ChallengeReconciliation(BaseModel):
    """The reconciliation record for one challenge (non-destructive)."""

    model_config = ConfigDict(extra="forbid")

    challenge_id: str
    objection: str
    supporting_evidence_refs: list[str] = Field(default_factory=list)
    rebuttal: str | None = None
    rebuttal_is_evidence_backed: bool = False
    disposition: ChallengeDisposition = ChallengeDisposition.UNRESOLVED
    corrective_action: str = ""
    responsible_party: str | None = None
    provenance: dict = Field(default_factory=dict)


class GovernedRedChallenge(BaseModel):
    """A RED challenge after service governance.

    ``evidence_backed`` is the SERVICE's determination (cited refs resolve to
    permitted evidence), distinct from the model's ``claims_evidence_backed``.
    ``reference_resolved`` is False when the target id is unknown (then the
    challenge is retained as an explicitly UNRESOLVED objection, not dropped).
    """

    model_config = ConfigDict(extra="forbid")

    challenge_id: str
    target_kind: RedTargetKind
    target_id: str
    category: RedChallengeCategory
    severity: ChallengeSeverity
    challenged_statement: str
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)
    reference_resolved: bool = True
    evidence_backed: bool = False
    unresolved_unknown_refs: list[str] = Field(default_factory=list)
    potential_consequence: str = ""
    recommended_action: str = ""
    reconciliation: ChallengeReconciliation
    escalation_id: str | None = None
    requires_human_review: bool = False


class ModuleRedChallengeReport(BaseModel):
    """The full Stage 13 output (non-destructive; original data untouched)."""

    model_config = ConfigDict(extra="forbid")

    case_id: str | None = None
    challenge_prompt_version: str = ""
    red_run_id: str | None = None
    progression_gate: str = ""
    pass_kind: str = "normal"  # normal | escalation | diagnostic | not_assessable
    challenges: list[GovernedRedChallenge] = Field(default_factory=list)
    escalation_ids: list[str] = Field(default_factory=list)
    outstanding_dependencies: list[str] = Field(default_factory=list)
    assessable: bool = True
    audit_provenance: dict = Field(default_factory=dict)

    def unresolved_challenges(self) -> list[GovernedRedChallenge]:
        return [
            c for c in self.challenges
            if c.reconciliation.disposition is not ChallengeDisposition.RESOLVED
        ]


class GovernedHypothesisTestResult(BaseModel):
    """The service's FINAL, governance-checked verdict for one hypothesis.

    Keeps the model's ``proposed_status`` AND the service's ``effective_status``
    distinct, with every reason the proposal was downgraded. Never overwrites the
    original hypothesis; links back by id and run provenance.
    """

    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    dimension: CreditDimension
    hypothesis_statement: str
    proposed_status: HypothesisVerificationStatus
    effective_status: HypothesisVerificationStatus
    downgrade_reasons: list[str] = Field(default_factory=list)
    factual_grounding: list[FactualGroundingRecord] = Field(default_factory=list)
    evidence_assessments: list[EvidenceAssessment] = Field(default_factory=list)
    mechanism_premises: list[MechanismPremise] = Field(default_factory=list)
    alternative_explanations: list[str] = Field(default_factory=list)
    unresolved_evidence_requirements: list[str] = Field(default_factory=list)
    coverage_gaps: list[dict] = Field(default_factory=list)
    # Independent corroboration (Task 6A): whether ANY material factual premise
    # is established by a NON-model channel (deterministic grounding or recorded
    # human adjudication). Model-labelled premise statuses and model
    # SUPPORTS_PREMISE assessments do NOT count. ``corroboration_basis`` names
    # the channel(s): e.g. "deterministic_grounding", "human_adjudication".
    independently_corroborated: bool = False
    corroboration_basis: list[str] = Field(default_factory=list)
    model_only_support: bool = False
    premise_corroboration: list[PremiseCorroboration] = Field(default_factory=list)
    uncorroborated_material_premises: list[str] = Field(default_factory=list)
    justification: str = ""
    interpretation_confidence: float | None = None
    human_review_required: bool = False
    testing_run_id: str | None = None
    generation_status: HypothesisVerificationStatus = (
        HypothesisVerificationStatus.UNVERIFIED
    )


# ---------------------------------------------------------------------------
# Fixed JSON Schemas (validate raw model output before use)
# ---------------------------------------------------------------------------

EXTRACTION_JSON_SCHEMA: dict[str, Any] = ExtractionResponse.model_json_schema()
ANALYSIS_JSON_SCHEMA: dict[str, Any] = AnalysisResponse.model_json_schema()
CHALLENGE_JSON_SCHEMA: dict[str, Any] = ChallengeResponse.model_json_schema()
# Fixed schema for a batch of credit hypotheses (parallels the above; the
# hypothesis-generation step that targets it is a later task).
CREDIT_HYPOTHESIS_JSON_SCHEMA: dict[str, Any] = CreditHypothesisSet.model_json_schema()
# Fixed schema for an evidence enquiry plan (Stage 5 planning output).
EVIDENCE_ENQUIRY_PLAN_JSON_SCHEMA: dict[str, Any] = (
    EvidenceEnquiryPlan.model_json_schema()
)
# Fixed schema for the hypothesis-testing model output (Stage 8).
HYPOTHESIS_TESTING_JSON_SCHEMA: dict[str, Any] = (
    HypothesisTestResponse.model_json_schema()
)
# Fixed schema for the credit-signal-assessment model output (Stages 10-11).
CREDIT_SIGNAL_ASSESSMENT_JSON_SCHEMA: dict[str, Any] = (
    CreditSignalAssessmentResponse.model_json_schema()
)
# Fixed schema for the independent RED challenge model output (Stage 13).
MODULE_RED_CHALLENGE_JSON_SCHEMA: dict[str, Any] = (
    RedChallengeResponse.model_json_schema()
)
