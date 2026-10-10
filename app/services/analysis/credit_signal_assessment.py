"""Credit signal identification, impact assessment & materiality (Stages 9-11).

Consumes the governed, evidence-tested hypotheses from Tasks 6-6C and turns them
into structured, prioritised credit findings for the Stage 12 completeness
review. It never generates a memo, never promotes a verification status, never
invents numbers, and never nets a positive offset against a negative exposure.

Pipeline inside this service:

* Stage 9 -- :meth:`_build_signal`: one :class:`CreditSignal` per governed
  result. The effective verification status is carried verbatim. The ORIGINAL
  hypothesis direction is kept distinct from the assessed signal direction so a
  mismatch is FLAGGED (``direction_mismatch``), never silently overwritten. A
  REJECTED hypothesis never becomes a validated positive/negative signal -- it
  is recorded as an ``is_investigation_candidate`` instead.
* Stage 10 -- :meth:`_impact`: a :class:`CreditImpactAssessment` with the
  explicit observed-fact / corroborated / inference / assumption / missing
  split. Quantified impact is permitted ONLY against supplied validated metric
  ids (the model may propose words, never numbers).
* Stage 11 -- :meth:`_materiality`: a deterministic, VERSIONED provisional
  triage rubric mapping qualitative criteria to LOW/MEDIUM/HIGH. Materiality is
  independent of verification, direction, model confidence, citation count and
  presentation. When the rubric cannot defensibly separate HIGH from MEDIUM it
  marks ``requires_human_review`` rather than fabricating precision.

The LLM (optional) proposes impact narrative + a provisional materiality via the
versioned ``credit_signal_assessment`` prompt; the service enforces the rubric,
reference and governance constraints independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from app.core.hashing import content_hash
from app.schemas.llm import (
    CreditDirection,
    CreditFindingSet,
    CreditHypothesis,
    CreditHypothesisSet,
    CreditImpactAssessment,
    CreditSignal,
    CreditSignalAssessmentResponse,
    GovernedHypothesisTestResult,
    HypothesisVerificationStatus,
    ImpactChannel,
    ImpactQuantification,
    Materiality,
    MaterialityAssessment,
    PresentationMateriality,
    PrioritisedCreditFinding,
    ProposedImpactAssessment,
)
from app.services.audit.log import ActorType, AuditLog

CREDIT_SIGNAL_PROMPT_NAME = "credit_signal_assessment"
CREDIT_SIGNAL_REJECTED_EVENT = "credit_signal_assessment_rejected"

# A dimension -> default impact channel hint (reused if the model proposes none).
_DIMENSION_CHANNEL: dict[str, ImpactChannel] = {
    "revenue_quality_demand_defensiveness": ImpactChannel.REVENUE_SIDE,
    "customer_stickiness_concentration": ImpactChannel.REVENUE_SIDE,
    "pricing_power_revenue_realisation": ImpactChannel.REVENUE_SIDE,
    "operating_model_cost_resilience": ImpactChannel.OPERATING,
    "competitive_durability": ImpactChannel.REVENUE_SIDE,
    "financial_stewardship_capital_commitments": ImpactChannel.CASH_FLOW_CAPITAL,
}

# ---------------------------------------------------------------------------
# Provisional analytical triage rubric (versioned; NOT a bank rating method).
# Qualitative criteria only -- no probabilities, weights, thresholds or score.
# Changing this dict changes RUBRIC_VERSION via its content hash.
# ---------------------------------------------------------------------------
MATERIALITY_RUBRIC: dict = {
    "label": "PROVISIONAL ANALYTICAL TRIAGE — NOT A BANK RATING METHODOLOGY",
    "criteria": [
        "potential_magnitude",      # how large the credit impact could be
        "cash_flow_relevance",      # relevance to CFO stability / repayment
        "breadth_of_exposure",      # how much of the business is exposed
        "concentration",            # concentration of the exposure
        "timing_persistence",       # how soon / how lasting
        "offsets_present",          # company-specific offsets exist
        "evidence_uncertainty",     # unresolved evidence requirements
    ],
    # Qualitative mapping: a finding is HIGH when it is cash-flow-relevant AND
    # (large magnitude OR broad/concentrated exposure); MEDIUM when it is
    # cash-flow-relevant but neither large nor broad; LOW otherwise. Direction,
    # verification and citations are deliberately NOT inputs.
    "high": "cash_flow_relevant AND (high_magnitude OR broad_or_concentrated)",
    "medium": "cash_flow_relevant AND NOT (high_magnitude OR broad_or_concentrated)",
    "low": "not cash_flow_relevant",
}
RUBRIC_VERSION = "triage-" + content_hash(MATERIALITY_RUBRIC)[:12]


class CreditSignalAssessmentRejectedError(ValueError):
    """Raised when the proposed impact output fails validation/governance."""

    def __init__(self, message: str, *, stage: str, run=None) -> None:
        super().__init__(message)
        self.stage = stage
        self.run = run


@dataclass
class MaterialityInputs:
    """Caller-stated qualitative criteria for one signal (triage inputs).

    These are deterministic triage facts the orchestrator supplies (e.g. from
    the hypothesis/metrics), NOT model confidence. Unknown criteria default
    conservatively (uncertain -> flagged for review when it would decide HIGH).
    """

    high_magnitude: bool | None = None
    cash_flow_relevant: bool | None = None
    broad_or_concentrated: bool | None = None


@dataclass
class CreditSignalAssessmentInputs:
    """Inputs for Stages 9-11."""

    hypotheses: CreditHypothesisSet
    governed: list[GovernedHypothesisTestResult]
    # Validated metric ids the impact assessment may reference for a QUANTIFIED
    # impact. Never widened; a ref outside this set is rejected.
    validated_metric_ids: frozenset[str] = frozenset()
    # Optional per-signal triage criteria keyed by hypothesis_id.
    materiality_inputs: dict[str, MaterialityInputs] = field(default_factory=dict)

    def governed_by_hypothesis(self) -> dict[str, GovernedHypothesisTestResult]:
        return {g.hypothesis_id: g for g in self.governed}


@dataclass
class CreditSignalAssessmentResult:
    findings: CreditFindingSet
    run: object | None = None


class CreditSignalAssessmentService:
    """Produce prioritised credit findings from governed test results."""

    def __init__(
        self,
        client=None,
        *,
        audit: AuditLog | None = None,
        case_id: str | None = None,
    ) -> None:
        self._client = client
        self._audit = audit
        self._case_id = case_id

    def assess(
        self,
        inputs: CreditSignalAssessmentInputs,
        *,
        case_version: int | None = None,
        key: str | None = None,
        use_llm: bool = False,
    ) -> CreditSignalAssessmentResult:
        governed_by_id = inputs.governed_by_hypothesis()
        hyp_by_id = {h.hypothesis_id: h for h in inputs.hypotheses.hypotheses}

        # Every governed result must reference a known hypothesis.
        unknown = [g.hypothesis_id for g in inputs.governed if g.hypothesis_id not in hyp_by_id]
        if unknown:
            raise CreditSignalAssessmentRejectedError(
                f"governed results reference unknown hypotheses {sorted(unknown)!r}",
                stage="service_governance",
            )

        # Stage 9: build signals (+ investigation candidates for REJECTED).
        signals: list[CreditSignal] = []
        candidates: list[CreditSignal] = []
        for g in inputs.governed:
            sig = self._build_signal(g, hyp_by_id[g.hypothesis_id])
            (candidates if sig.is_investigation_candidate else signals).append(sig)

        # Optional LLM impact narrative (validated), else deterministic.
        proposed_by_id: dict[str, ProposedImpactAssessment] = {}
        run = None
        if use_llm and self._client is not None and signals:
            proposed_by_id, run = self._propose_impacts(
                signals, inputs, case_version=case_version, key=key
            )

        # Stages 10-11 per signal.
        findings: list[PrioritisedCreditFinding] = []
        for sig in signals:
            g = governed_by_id[sig.hypothesis_id]
            impact = self._impact(sig, g, inputs, proposed_by_id.get(sig.signal_id))
            materiality = self._materiality(sig, g, inputs, impact)
            findings.append(self._finding(sig, g, impact, materiality))

        # Overlap / double-count linkage (recorded, never deleted).
        self._link_overlaps(findings)

        # Deterministic ranking by materiality then direction then id.
        findings = self._rank(findings)

        return CreditSignalAssessmentResult(
            findings=CreditFindingSet(
                findings=findings,
                investigation_candidates=candidates,
                rubric_version=RUBRIC_VERSION,
            ),
            run=run,
        )

    # -- Stage 9 --------------------------------------------------------------

    @staticmethod
    def _build_signal(
        g: GovernedHypothesisTestResult, hypothesis: CreditHypothesis
    ) -> CreditSignal:
        rejected = g.effective_status is HypothesisVerificationStatus.REJECTED
        # Assessed direction defaults to the original; a reviewer/later stage may
        # revise. A contradiction-dominated result flags a potential mismatch.
        assessed = hypothesis.credit_direction
        mismatch = False
        has_contradiction = bool(
            [a for a in g.evidence_assessments
             if a.assessment.value == "contradicts_premise"]
        )
        if has_contradiction and assessed is not CreditDirection.NEUTRAL:
            mismatch = True  # flag only; never overwrite the original direction
        supporting = [
            a.passage_id for a in g.evidence_assessments
            if a.assessment.value == "supports_premise"
        ]
        contradictory = [
            a.passage_id for a in g.evidence_assessments
            if a.assessment.value == "contradicts_premise"
        ]
        return CreditSignal(
            signal_id=content_hash({"sig": g.hypothesis_id, "run": g.testing_run_id}),
            hypothesis_id=g.hypothesis_id,
            dimension=g.dimension,
            business_characteristic=hypothesis.business_characteristic,
            signal_statement=g.hypothesis_statement,
            original_direction=hypothesis.credit_direction,
            assessed_direction=assessed,
            direction_mismatch=mismatch,
            proposed_mechanism=next(
                (p.premise for p in g.mechanism_premises), hypothesis.economic_reasoning
            ),
            verification_status=g.effective_status,
            supporting_evidence_ids=supporting,
            contradictory_evidence_ids=contradictory,
            unresolved_limitations=list(g.uncorroborated_material_premises),
            offsetting_characteristics=list(hypothesis.offsetting_characteristics),
            is_investigation_candidate=rejected,
        )

    # -- Stage 10 -------------------------------------------------------------

    def _impact(
        self,
        sig: CreditSignal,
        g: GovernedHypothesisTestResult,
        inputs: CreditSignalAssessmentInputs,
        proposed: ProposedImpactAssessment | None,
    ) -> CreditImpactAssessment:
        channel = (
            proposed.impact_channel
            if proposed is not None
            else _DIMENSION_CHANNEL.get(g.dimension.value, ImpactChannel.OTHER)
        )
        # Corroborated premises come from the GOVERNED result, never the model.
        corroborated = [
            pc.premise for pc in g.premise_corroboration if pc.corroborated
        ]
        # Quantified only against VALIDATED metric ids; drop anything else.
        refs = [
            r for r in (proposed.quantified_metric_refs if proposed else [])
            if r in inputs.validated_metric_ids
        ]
        quant = (
            ImpactQuantification.QUANTIFIED if refs
            else ImpactQuantification.QUALITATIVE if corroborated
            else ImpactQuantification.UNQUANTIFIED
        )
        return CreditImpactAssessment(
            signal_id=sig.signal_id,
            impact_channel=channel,
            transmission_mechanism=(
                proposed.transmission_mechanism if proposed else sig.proposed_mechanism
            ),
            observed_facts=(proposed.observed_facts if proposed else []),
            corroborated_premises=corroborated,
            unproven_inferences=(
                proposed.unproven_inferences if proposed
                else list(g.alternative_explanations)
            ),
            material_assumptions=(proposed.material_assumptions if proposed else []),
            missing_information=list(g.unresolved_evidence_requirements),
            quantification=quant,
            quantified_metric_refs=refs,
            potential_repayment_implication=(
                proposed.potential_repayment_implication if proposed else ""
            ),
        )

    # -- Stage 11 -------------------------------------------------------------

    def _materiality(
        self,
        sig: CreditSignal,
        g: GovernedHypothesisTestResult,
        inputs: CreditSignalAssessmentInputs,
        impact: CreditImpactAssessment,
    ) -> MaterialityAssessment:
        mi = inputs.materiality_inputs.get(sig.hypothesis_id, MaterialityInputs())
        cash_flow_relevant = (
            mi.cash_flow_relevant
            if mi.cash_flow_relevant is not None
            else impact.impact_channel is not ImpactChannel.OTHER
        )
        high_magnitude = bool(mi.high_magnitude)
        broad = bool(mi.broad_or_concentrated)

        criteria = {
            "cash_flow_relevant": cash_flow_relevant,
            "high_magnitude": high_magnitude,
            "broad_or_concentrated": broad,
            "offsets_present": bool(sig.offsetting_characteristics),
            "evidence_uncertainty": bool(
                g.uncorroborated_material_premises
                or g.unresolved_evidence_requirements
            ),
        }

        requires_review = False
        if not cash_flow_relevant:
            level = Materiality.LOW
        elif high_magnitude or broad:
            level = Materiality.HIGH
        else:
            # Cash-flow relevant but neither clearly large nor broad: MEDIUM.
            # If magnitude/breadth are UNKNOWN (not explicitly False), the rubric
            # cannot defensibly separate HIGH from MEDIUM -> flag for review.
            if mi.high_magnitude is None and mi.broad_or_concentrated is None:
                requires_review = True
            level = Materiality.MEDIUM

        # Presentation is SEPARATE from materiality. A material (non-LOW) finding
        # is DISPLAYED even when unverified; a LOW finding defaults to ON_DEMAND.
        presentation = (
            PresentationMateriality.DISPLAY
            if level is not Materiality.LOW
            else PresentationMateriality.ON_DEMAND
        )

        rationale = (
            f"cash_flow_relevant={cash_flow_relevant}, high_magnitude={high_magnitude}, "
            f"broad_or_concentrated={broad} -> {level.value} "
            "(provisional triage; direction/verification/citations excluded)."
        )
        return MaterialityAssessment(
            signal_id=sig.signal_id,
            materiality=level,
            rationale=rationale,
            rubric_version=RUBRIC_VERSION,
            criteria=criteria,
            presentation=presentation,
            requires_human_review=requires_review,
        )

    # -- finding assembly + Stage 12 handoff ----------------------------------

    def _finding(
        self,
        sig: CreditSignal,
        g: GovernedHypothesisTestResult,
        impact: CreditImpactAssessment,
        materiality: MaterialityAssessment,
    ) -> PrioritisedCreditFinding:
        required_review = bool(
            g.human_review_required
            or materiality.requires_human_review
            or sig.direction_mismatch
        )
        return PrioritisedCreditFinding(
            signal=sig,
            impact=impact,
            materiality=materiality,
            rank=0,  # set by _rank
            uncorroborated_material_premises=list(g.uncorroborated_material_premises),
            unresolved_evidence_requirements=list(g.unresolved_evidence_requirements),
            outstanding_contradictions=list(sig.contradictory_evidence_ids),
            uninvestigated_offsets=[
                o for o in sig.offsetting_characteristics
            ],
            required_human_review=required_review,
            testing_run_id=g.testing_run_id,
        )

    @staticmethod
    def _link_overlaps(findings: list[PrioritisedCreditFinding]) -> None:
        """Record overlap when two findings share dimension + characteristic.

        Overlap is LINKED (both directions) so a reviewer can judge potential
        double-counting; neither signal is deleted or netted.
        """
        for i, a in enumerate(findings):
            for b in findings[i + 1:]:
                same = (
                    a.signal.dimension is b.signal.dimension
                    and a.signal.business_characteristic.strip().lower()
                    == b.signal.business_characteristic.strip().lower()
                )
                if same:
                    a.overlap_links.append(b.signal.signal_id)
                    b.overlap_links.append(a.signal.signal_id)

    @staticmethod
    def _rank(
        findings: list[PrioritisedCreditFinding],
    ) -> list[PrioritisedCreditFinding]:
        order = {Materiality.HIGH: 0, Materiality.MEDIUM: 1, Materiality.LOW: 2}
        dir_order = {
            CreditDirection.NEGATIVE: 0,
            CreditDirection.POSITIVE: 1,
            CreditDirection.NEUTRAL: 2,
        }
        ranked = sorted(
            findings,
            key=lambda f: (
                order[f.materiality.materiality],
                dir_order[f.signal.assessed_direction],
                f.signal.signal_id,
            ),
        )
        for idx, f in enumerate(ranked, 1):
            f.rank = idx
        return ranked

    # -- optional LLM impact proposal (validated) -----------------------------

    def _propose_impacts(self, signals, inputs, *, case_version, key):
        payload = {
            "signals": [
                {
                    "signal_id": s.signal_id,
                    "dimension": s.dimension.value,
                    "signal_statement": s.signal_statement,
                    "assessed_direction": s.assessed_direction.value,
                    "verification_status": s.verification_status.value,
                    "offsetting_characteristics": s.offsetting_characteristics,
                }
                for s in signals
            ],
            "validated_metric_ids": sorted(inputs.validated_metric_ids),
        }
        run = self._client.analyze(
            payload,
            case_id=self._case_id,
            case_version=case_version,
            prompt_name=CREDIT_SIGNAL_PROMPT_NAME,
            key=key,
        )
        if not run.is_valid or run.parsed is None:
            raise CreditSignalAssessmentRejectedError(
                "credit-signal-assessment output failed JSON-Schema validation.",
                stage="json_schema",
                run=run,
            )
        try:
            response = CreditSignalAssessmentResponse.model_validate(run.parsed)
        except ValidationError as exc:
            self._record_rejection(run, stage="pydantic_governance", detail=str(exc))
            raise CreditSignalAssessmentRejectedError(
                "credit-signal-assessment output violated a Pydantic invariant.",
                stage="pydantic_governance",
                run=run,
            ) from exc

        known = {s.signal_id for s in signals}
        unknown = [a.signal_id for a in response.assessments if a.signal_id not in known]
        if unknown:
            detail = f"assessment references unknown signal(s) {sorted(unknown)!r}"
            self._record_rejection(run, stage="service_governance", detail=detail)
            raise CreditSignalAssessmentRejectedError(
                detail, stage="service_governance", run=run
            )
        return {a.signal_id: a for a in response.assessments}, run

    def _record_rejection(self, run, *, stage: str, detail: str) -> None:
        if self._audit is None:
            return
        self._audit.record(
            CREDIT_SIGNAL_REJECTED_EVENT,
            case_id=self._case_id,
            actor_type=ActorType.SYSTEM,
            after={"run_id": run.run_id, "prompt_id": run.prompt_id,
                   "stage": stage, "detail": detail},
            reason=f"Credit-signal-assessment output rejected (stage={stage}).",
            linked_objects=[f"model_run:{run.run_id}"],
        )
