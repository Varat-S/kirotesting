"""Per-specialist hypothesis investigation (steps 6-12) beneath the BO boundary.

Runs the imported analytical services for ONE BO specialist, over that
specialist's SEALED evidence packet only, and adapts the outcome into the BO
narrow-response shape so the existing deterministic validators
(``DeterministicValidator``, ``HypothesisValidator``, ``gate_parameters``) judge
it exactly as they judge the single-pass path.

    6  hypothesis generation        (model; scoped to the specialist's dimension)
    7  enquiry planning + retrieval (model plans; deterministic lexical search
                                     over the sealed packet - never wider)
    8  hypothesis testing           (model + deterministic premise corroboration)
    9-11 signals, impact, materiality (deterministic rubric; model optional)
    12 evidence completeness gate   (deterministic; scoped to the dimension)
    -- parameter classification     (model; owned parameters only, from the
                                     governed findings; gated by gate_parameters)

Invariants:

* Evidence ids are the baseline's admitted ids. Daniel's PDF corpus builder is
  not used; the search corpus is built from the packet's passages.
* ``HypothesisRecord`` stays the single authoritative store. Investigation
  objects travel as a versioned trace on each record (``investigation``).
* Status can only be lowered: the governed status of the testing stage feeds
  the BO validator, whose lexical checks may lower it further. ``supported``
  additionally needs deterministic corroboration; a genuine quotation alone is
  never enough.
* Hypotheses outside the specialist's dimension are not investigated and not
  dropped silently: they are listed in the trace as ``out_of_scope``.
* A failed stage stops the investigation and is reported as failed; it is never
  dressed up as a completed specialist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas.agentic import Materiality as BoMateriality
from app.schemas.bo import Disposition
from app.schemas.evidence import EvidenceCorpus, EvidencePassage
from app.schemas.llm import (
    CreditHypothesisSet,
    EvidenceAssessmentKind,
    HypothesisVerificationStatus,
)
from app.services.analysis.credit_signal_assessment import (
    CreditSignalAssessmentInputs,
    CreditSignalAssessmentRejectedError,
    CreditSignalAssessmentService,
)
from app.services.analysis.enquiry_planning import (
    EnquiryBudget,
    EnquiryPlanningInputs,
    EvidenceEnquiryCoordinator,
    EvidenceEnquiryPlanningRejectedError,
    EvidenceEnquiryPlanningService,
)
from app.services.analysis.evidence_completeness import (
    EvidenceCompletenessInputs,
    EvidenceCompletenessRejectedError,
    MaterialEvidenceCompletenessService,
)
from app.services.analysis.evidence_retrieval import EvidenceSearchService
from app.services.analysis.grounding import EvidenceItem
from app.services.analysis.hypothesis_generation import (
    HypothesisGenerationInputs,
    HypothesisGenerationRejectedError,
    HypothesisGenerationService,
)
from app.services.analysis.hypothesis_testing import (
    HypothesisTestingInputs,
    HypothesisTestingRejectedError,
    HypothesisTestingService,
)
from app.services.bo.indicators import IndicatorSet
from app.services.bo.plans import BoAgentSpec

INVESTIGATION_VERSION = "bo-investigation-1"
PARAMETER_PROMPT_SUFFIX = "__parameters"

# Daniel's verification vocabulary <-> BO dispositions (BO_ARCHITECTURE_V3_SPEC §4).
STATUS_TO_DISPOSITION = {
    HypothesisVerificationStatus.SUPPORTED: Disposition.SUPPORTED,
    HypothesisVerificationStatus.PARTIALLY_SUPPORTED: Disposition.QUALIFIED,
    HypothesisVerificationStatus.REJECTED: Disposition.REJECTED,
    HypothesisVerificationStatus.UNVERIFIED: Disposition.UNVERIFIED,
}
DISPOSITION_TO_STATUS = {v: k for k, v in STATUS_TO_DISPOSITION.items()}
# Strictest first: combining two judgements keeps the earliest in this order.
_STRICTNESS = (Disposition.REJECTED, Disposition.UNVERIFIED, Disposition.QUALIFIED,
               Disposition.SUPPORTED)

_CLAIM_TYPE = {"reported_fact": "fact", "calculated_fact": "calculated_fact",
               "management_claim": "management_claim", "inference": "inference",
               "assumption": "assumption"}
_SUPPORTING = {EvidenceAssessmentKind.SUPPORTS_PREMISE,
               EvidenceAssessmentKind.MEASURED_OUTCOME,
               EvidenceAssessmentKind.DESCRIBES_INITIATIVE}


def strictest(*dispositions: Disposition) -> Disposition:
    """The most conservative of several verification judgements."""
    return min(dispositions, key=_STRICTNESS.index)


def corpus_from_packet(passages: list[dict[str, Any]]) -> EvidenceCorpus:
    """A search corpus holding exactly the sealed packet's admitted passages.

    Passage ids ARE the baseline evidence ids, so anything the investigation
    cites is checked by the BO validators against the same packet.
    """
    out, docs = [], []
    for p in passages:
        eid, text = p.get("evidence_id"), p.get("text")
        if not eid or not text:
            continue
        doc = str(p.get("document_id") or "unknown")
        docs.append(doc)
        out.append(EvidencePassage(
            passage_id=eid, document_id=doc, text=text,
            section=p.get("section") or p.get("topic"),
            source_location=str(p.get("location")) if p.get("location") is not None
            else None))
    return EvidenceCorpus(passages=out, document_ids=sorted(set(docs)))


def grounding_from_indicators(indicators: IndicatorSet,
                              groups: tuple[str, ...]) -> list[EvidenceItem]:
    """Approved deterministic indicators as grounding items (no new numbers)."""
    items = []
    for t in indicators.traces:
        if groups and t.group not in groups:
            continue
        if t.value is None:
            continue
        items.append(EvidenceItem(
            evidence_id=t.parameter_result_id, value=float(t.value), unit=t.units,
            concept=t.label, period=t.period, text=t.label))
    return items


def _confidence(score: float | None) -> str:
    if score is None:
        return "medium"
    if score >= 0.75:
        return "high"
    if score >= 0.5:
        return "medium"
    return "medium_low" if score >= 0.3 else "low"


@dataclass
class StageRecord:
    stage: str
    state: str  # completed | rejected | failed | skipped
    detail: str = ""


@dataclass
class InvestigationResult:
    """What one specialist's investigation produced (before BO validation)."""

    spec_code: str
    stages: list[StageRecord] = field(default_factory=list)
    hypotheses: CreditHypothesisSet | None = None
    plan: Any = None
    bundle: Any = None
    governed: list = field(default_factory=list)
    findings: Any = None
    completeness: Any = None
    parameters: list[dict[str, Any]] = field(default_factory=list)
    id_map: dict[str, str] = field(default_factory=dict)  # original -> BO id
    out_of_scope: list[dict[str, Any]] = field(default_factory=list)
    model_calls: int = 0
    # Approved indicators this specialist could ground against, by id; used to
    # carry a premise's metric link into the BO record as a quoted value.
    metric_traces: dict[str, dict[str, Any]] = field(default_factory=dict)
    adapter_notes: dict[str, list[str]] = field(default_factory=dict)
    quote_repairs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def completed(self) -> bool:
        return bool(self.stages) and all(
            s.state in ("completed", "completed_after_retry", "skipped")
            for s in self.stages)

    @property
    def failed_stage(self) -> StageRecord | None:
        # The last failure decides: a stage rejected and then retried reports
        # the retry's outcome.
        return next((s for s in reversed(self.stages)
                     if s.state in ("rejected", "failed")), None)

    def progression(self) -> str | None:
        c = self.completeness
        return getattr(getattr(c, "progression", None), "value", None)

    def bo_response(self, spec: BoAgentSpec) -> dict[str, Any]:
        """The investigation outcome in the BO narrow-response shape."""
        items = to_bo_items(self, spec)
        self.adapter_notes = {i["hypothesis_id"]: i.pop("_adapter_notes")
                              for i in items}
        return {"hypotheses": items,
                "parameters": [dict(p) for p in self.parameters]}

    def trace_for(self, bo_id: str) -> dict[str, Any]:
        original = next((o for o, b in self.id_map.items() if b == bo_id), None)
        if original is None:
            return {}
        governed = next((g for g in self.governed if g.hypothesis_id == original), None)
        finding = _finding_for(self, original)
        executed = [
            {"enquiry_id": ex.enquiry.enquiry_id,
             "requirement_kind": ex.enquiry.requirement_kind.value,
             "query": ex.enquiry.search_query,
             "outcome": ex.result.outcome.value,
             "passage_ids": [rp.passage.passage_id for rp in ex.result.ranked_passages]}
            for ex in (self.bundle.by_hypothesis.get(original, []) if self.bundle else [])
        ]
        trace: dict[str, Any] = {
            "investigation_version": INVESTIGATION_VERSION,
            "original_hypothesis_id": original,
            "enquiries": executed,
            "progression": self.progression(),
            "adapter_notes": list(self.adapter_notes.get(bo_id, [])),
            "quote_repairs": [q for q in self.quote_repairs
                              if q["hypothesis_id"] == original],
        }
        if governed is not None:
            trace.update({
                "proposed_status": governed.proposed_status.value,
                "effective_status": governed.effective_status.value,
                "downgrade_reasons": list(governed.downgrade_reasons),
                "independently_corroborated": governed.independently_corroborated,
                "model_only_support": governed.model_only_support,
                "uncorroborated_material_premises":
                    list(governed.uncorroborated_material_premises),
                "entailment": [
                    {"claim_id": g.claim_id, "state": g.entailment_state.value,
                     "evidence_refs": list(g.evidence_refs)}
                    for g in governed.factual_grounding],
                "alternative_explanations": list(governed.alternative_explanations),
                "assessments": [
                    {"passage_id": a.passage_id, "assessment": a.assessment.value}
                    for a in governed.evidence_assessments],
            })
        if finding is not None:
            trace.update({
                "signal": {"original_direction": finding.signal.original_direction.value,
                           "assessed_direction": finding.signal.assessed_direction.value,
                           "direction_mismatch": finding.signal.direction_mismatch},
                "impact": {"channel": finding.impact.impact_channel.value,
                           "quantification": finding.impact.quantification.value,
                           "unproven_inferences": list(finding.impact.unproven_inferences)},
                "materiality": {"level": finding.materiality.materiality.value,
                                "rubric_version": finding.materiality.rubric_version,
                                "criteria": dict(finding.materiality.criteria),
                                "requires_human_review":
                                    finding.materiality.requires_human_review},
            })
        return trace


def _finding_for(result: InvestigationResult, original_id: str):
    if result.findings is None:
        return None
    return next((f for f in result.findings.findings
                 if f.signal.hypothesis_id == original_id), None)


def to_bo_items(result: InvestigationResult, spec: BoAgentSpec) -> list[dict[str, Any]]:
    """Map governed investigation outcomes onto BO hypothesis items.

    Deterministic; no model restates a finding. The governed status is the
    proposed disposition; ``supported`` without deterministic corroboration is
    capped at ``qualified`` (a genuine quote alone does not establish entailment).
    """
    if result.hypotheses is None:
        return []
    governed = {g.hypothesis_id: g for g in result.governed}
    items = []
    for h in result.hypotheses.hypotheses:
        bo_id = result.id_map.get(h.hypothesis_id)
        g = governed.get(h.hypothesis_id)
        if bo_id is None or g is None:
            continue
        disposition = STATUS_TO_DISPOSITION[g.effective_status]
        notes = []
        if disposition is Disposition.SUPPORTED and (
                g.model_only_support or g.uncorroborated_material_premises
                or not g.independently_corroborated):
            disposition = Disposition.QUALIFIED
            notes.append("supported_without_deterministic_corroboration")
        # Fix R3/R3a: passages a contradictory-evidence search returned for this
        # hypothesis that the testing model did not assess at all (it is told to
        # assess every one). An omission leaves their relevance unknown, so the
        # finding cannot stand as supported and a reviewer must look at them. A
        # passage the model explicitly weighed is left to the step 14 challenger.
        weighed = {a.passage_id for a in g.evidence_assessments}
        contested = sorted({
            rp.passage.passage_id
            for ex in (result.bundle.by_hypothesis.get(h.hypothesis_id, [])
                       if result.bundle else [])
            if ex.enquiry.requirement_kind.value == "contradictory"
            for rp in ex.result.ranked_passages} - weighed)
        if contested:
            notes.append("unassessed_contradictory_candidates")
            if disposition is Disposition.SUPPORTED:
                disposition = Disposition.QUALIFIED
        finding = _finding_for(result, h.hypothesis_id)
        supporting = [a.passage_id for a in g.evidence_assessments
                      if a.assessment in _SUPPORTING]
        contradicting = [a.passage_id for a in g.evidence_assessments
                         if a.assessment is EvidenceAssessmentKind.CONTRADICTS_PREMISE]
        quotes = [{"evidence_id": a.passage_id, "quote": a.quoted_excerpt}
                  for a in g.evidence_assessments
                  if a.quoted_excerpt and a.assessment in (
                      *_SUPPORTING, EvidenceAssessmentKind.CONTRADICTS_PREMISE)]
        # Effectiveness needs a CORROBORATED outcome premise; a passage the model
        # labelled "measured outcome" is not enough on its own.
        outcome_corroborated = any(
            pc.is_outcome_premise and pc.corroborated
            for pc in (g.premise_corroboration or []))
        outcomes = [a.passage_id for a in g.evidence_assessments
                    if a.assessment is EvidenceAssessmentKind.MEASURED_OUTCOME
                    ] if outcome_corroborated else []
        initiatives = [a.passage_id for a in g.evidence_assessments
                       if a.assessment is EvidenceAssessmentKind.DESCRIBES_INITIATIVE
                       and a.quoted_excerpt]
        mitigant = None
        if h.offsetting_characteristics:
            mitigant = {
                "capability": h.offsetting_characteristics[0],
                "existence": "verified" if initiatives else "unverified",
                "effectiveness": "demonstrated" if outcomes else "unproven",
                "outcome_evidence_ids": outcomes,
            }
        # A premise corroborated through an approved metric keeps that link: the
        # number, its unit, period and denominator travel as a bound value.
        quoted_values = []
        corroborated = {pc.premise_id for pc in (g.premise_corroboration or [])
                        if "deterministic_grounding" in pc.corroboration_basis}
        for idx, premise in enumerate(g.mechanism_premises):
            pid = premise.premise_id or f"{g.hypothesis_id}:premise:{idx}"
            trace = result.metric_traces.get(premise.grounding_metric_id or "")
            if trace and pid in corroborated:
                quoted_values.append(dict(trace))
        missing = list(dict.fromkeys([
            *h.missing_information, *g.unresolved_evidence_requirements,
            *([f"Contradictory-search passages not assessed: {', '.join(contested)}"]
              if contested else []),
            *(finding.impact.missing_information if finding else [])]))
        materiality = (finding.materiality.materiality.value if finding
                       else BoMateriality.LOW.value)
        direction = (finding.signal.assessed_direction.value if finding
                     else h.credit_direction.value)
        items.append({
            "hypothesis_id": bo_id,
            "claim": h.statement,
            "claim_type": _CLAIM_TYPE[h.classification.value],
            "direction": direction,
            "disposition": disposition.value,
            "confidence": _confidence(g.interpretation_confidence),
            "materiality": materiality,
            "credit_mechanism": (finding.impact.transmission_mechanism if finding
                                 else h.economic_reasoning),
            "supporting_evidence_ids": list(dict.fromkeys(supporting)),
            "contradicting_evidence_ids": list(dict.fromkeys(contradicting)),
            "evidence_quotes": quotes,
            "quoted_values": quoted_values,
            "missing_information": missing,
            "mitigant": mitigant,
            "review_required": bool(
                contested or g.human_review_required
                or (finding and finding.materiality.requires_human_review)),
            "_adapter_notes": notes,
        })
    return items


class SpecialistInvestigation:
    """Run steps 6-12 (+ parameter classification) for one BO specialist."""

    def __init__(self, spec: BoAgentSpec, *, client, audit=None, case_id=None,
                 review_lookup=None, budget: EnquiryBudget | None = None) -> None:
        if spec.dimension is None:
            raise ValueError(f"{spec.code} has no dimension; use a v3 roster spec.")
        self.spec = spec
        self._client = client
        self._gen = HypothesisGenerationService(client, audit=audit, case_id=case_id)
        self._plan = EvidenceEnquiryPlanningService(client, audit=audit, case_id=case_id)
        self._client_audit = (audit, case_id, review_lookup)
        self._signal = CreditSignalAssessmentService(client, audit=audit, case_id=case_id)
        self._complete = MaterialEvidenceCompletenessService(audit=audit, case_id=case_id)
        self._case_id = case_id
        self._budget = budget or EnquiryBudget()

    def run(self, *, packet_passages: list[dict[str, Any]], indicators: IndicatorSet,
            value_sets: dict[str, list[str]], case_version: int,
            keys: dict[str, str] | None = None,
            entity_aliases: tuple[str, ...] = ()) -> InvestigationResult:
        keys = keys or {}
        spec = self.spec
        result = InvestigationResult(spec_code=spec.code)
        corpus = corpus_from_packet(packet_passages)
        # The baseline's bounded quote recovery (layout gaps inside the cited
        # passage), applied per quote instead of rejecting the whole stage.
        from app.services.bo.recovery import recover_quote

        audit, case_id, review_lookup = self._client_audit
        self._test = HypothesisTestingService(
            self._client, audit=audit, case_id=case_id, review_lookup=review_lookup,
            quote_repair=lambda quote, pid: recover_quote(
                quote=quote, cited_evidence_id=pid, corpus=packet_passages))
        grounding = grounding_from_indicators(indicators, spec.indicator_groups)
        metric_ids = frozenset(i.evidence_id for i in grounding)
        result.metric_traces = {
            t.parameter_result_id: {
                "parameter_result_id": t.parameter_result_id, "value": t.value,
                "unit": t.units, "period": t.period,
                "denominator": (t.denominator or {}).get("name")}
            for t in indicators.traces if t.parameter_result_id in metric_ids}

        def stage(name, state, detail=""):
            result.stages.append(StageRecord(name, state, detail))

        # Step 6 -- hypothesis generation, scoped to this specialist's dimension.
        try:
            generated = self._gen.generate(
                HypothesisGenerationInputs(
                    evidence_ids=sorted(p.passage_id for p in corpus.passages),
                    calculation_refs=sorted(metric_ids),
                    metrics=[{"metric_id": i.evidence_id, "label": i.concept,
                              "value": i.value, "unit": i.unit, "period": i.period}
                             for i in grounding],
                    snippets=[{"evidence_id": p.passage_id, "text": p.text}
                              for p in corpus.passages],
                    dimension_scope=[spec.dimension]),
                case_version=case_version, key=keys.get("generate"))
            result.model_calls += 1
        except HypothesisGenerationRejectedError as exc:
            result.model_calls += 1
            stage("6_generation", "rejected", f"{exc.stage}: {exc}")
            return result
        in_scope = [h for h in generated.hypotheses.hypotheses
                    if h.dimension.value == spec.dimension]
        result.out_of_scope = [
            {"hypothesis_id": h.hypothesis_id, "dimension": h.dimension.value,
             "statement": h.statement}
            for h in generated.hypotheses.hypotheses if h.dimension.value != spec.dimension]
        result.hypotheses = CreditHypothesisSet(hypotheses=in_scope)
        result.id_map = {h.hypothesis_id: f"{spec.code}-H{i:03d}"
                         for i, h in enumerate(in_scope, start=1)}
        stage("6_generation", "completed",
              f"{len(in_scope)} in scope, {len(result.out_of_scope)} out of scope")
        if not in_scope:
            stage("7_planning", "skipped", "no in-scope hypotheses")
            return self._gate(result, case_version)

        # Step 7 -- enquiry planning (model) + retrieval over the packet only.
        planning = EnquiryPlanningInputs(hypotheses=result.hypotheses,
                                         permitted_document_ids=list(corpus.document_ids))
        try:
            planned = self._plan.plan(planning, case_version=case_version,
                                      key=keys.get("plan"))
            result.model_calls += 1
        except EvidenceEnquiryPlanningRejectedError as exc:
            result.model_calls += 1
            if exc.stage != "coverage":
                stage("7_planning", "rejected", f"{exc.stage}: {exc}")
                return result
            # One bounded follow-up (as the single-pass path has): the planner is
            # told exactly which mandatory perspectives it omitted. The coverage
            # rule itself is unchanged; a second omission fails the stage.
            stage("7_planning", "rejected", f"coverage; one re-plan: {exc}")
            planning.context = {"coverage_feedback": (
                "Your previous plan was rejected because it omitted mandatory "
                f"investigative perspectives: {exc}. Return the COMPLETE plan again, "
                "with supporting and contradictory enquiries for every hypothesis, "
                "an offsetting enquiry for every material negative hypothesis and an "
                "effectiveness enquiry wherever an offset is named.")}
            try:
                planned = self._plan.plan(planning, case_version=case_version,
                                          key=keys.get("plan_retry", keys.get("plan")))
                result.model_calls += 1
            except EvidenceEnquiryPlanningRejectedError as retry_exc:
                result.model_calls += 1
                stage("7_planning_retry", "rejected", f"{retry_exc.stage}: {retry_exc}")
                return result
            result.stages[-1] = StageRecord(
                "7_planning", "completed_after_retry", result.stages[-1].detail)
        result.plan = planned.plan
        stage("7_planning", "completed", f"{len(planned.plan.enquiries)} enquiries")
        try:
            coordinator = EvidenceEnquiryCoordinator(EvidenceSearchService(corpus),
                                                     budget=self._budget)
            result.bundle = coordinator.execute(result.plan)
        except Exception as exc:  # a technical failure is a failure, not success
            stage("7_retrieval", "failed", type(exc).__name__)
            return result
        stage("7_retrieval", "completed",
              f"{len(result.bundle.executed)} executed, "
              f"{len(result.bundle.unresolved_enquiries)} unresolved")

        # Step 8 -- testing (model) under deterministic corroboration.
        try:
            tested = self._test.test(
                HypothesisTestingInputs(
                    hypotheses=result.hypotheses, bundle=result.bundle,
                    plan=result.plan, grounding_items=grounding, budget=self._budget,
                    entity_aliases=tuple(entity_aliases)),
                case_version=case_version, key=keys.get("test"))
            result.model_calls += 1
        except HypothesisTestingRejectedError as exc:
            result.model_calls += 1
            stage("8_testing", "rejected", f"{exc.stage}: {exc}")
            return result
        result.governed = list(tested.governed)
        result.quote_repairs = list(tested.quote_repairs)
        stage("8_testing", "completed", f"{len(result.governed)} governed")

        # Steps 9-11 -- signals, impact, deterministic materiality rubric.
        try:
            assessed = self._signal.assess(
                CreditSignalAssessmentInputs(
                    hypotheses=result.hypotheses, governed=result.governed,
                    validated_metric_ids=metric_ids, materiality_inputs={}),
                case_version=case_version, key=keys.get("signal"), use_llm=False)
        except CreditSignalAssessmentRejectedError as exc:
            stage("9_11_signals", "rejected", f"{exc.stage}: {exc}")
            return result
        result.findings = assessed.findings
        stage("9_11_signals", "completed",
              f"{len(assessed.findings.findings)} findings, "
              f"{len(assessed.findings.investigation_candidates)} candidates")

        result = self._gate(result, case_version)
        if result.failed_stage:
            return result
        return self._classify_parameters(result, value_sets, case_version, keys)

    def _gate(self, result: InvestigationResult, case_version: int) -> InvestigationResult:
        """Step 12: deterministic evidence-completeness gate for this dimension."""
        from app.schemas.llm import CreditFindingSet

        try:
            gated = self._complete.assess(EvidenceCompletenessInputs(
                findings=result.findings or CreditFindingSet(),
                hypotheses=result.hypotheses or CreditHypothesisSet(),
                governed=result.governed, plan=result.plan, bundle=result.bundle,
                case_id=self._case_id, case_version=case_version,
                dimension_scope=(self.spec.dimension,)))
        except EvidenceCompletenessRejectedError as exc:
            result.stages.append(StageRecord("12_completeness", "rejected", exc.stage))
            return result
        result.completeness = gated.assessment
        result.stages.append(StageRecord(
            "12_completeness", "completed",
            f"progression={gated.assessment.progression.value}; "
            f"{len(gated.assessment.material_gaps)} gap(s)"))
        return result

    def _classify_parameters(self, result, value_sets, case_version, keys):
        """Owned parameters from the governed findings (model proposes; gated)."""
        spec = self.spec
        items = to_bo_items(result, spec)
        run = self._client.analyze(
            {"owned_parameters": list(spec.owned_parameters),
             "parameter_value_sets": {p: value_sets.get(p, []) for p in spec.owned_parameters},
             "governed_hypotheses": [
                 {k: i[k] for k in ("hypothesis_id", "claim", "claim_type",
                                    "direction", "disposition", "materiality")}
                 for i in items]},
            evidence_ids=sorted({e for i in items for e in i["supporting_evidence_ids"]}),
            case_id=self._case_id, case_version=case_version,
            prompt_name=spec.agent_id + PARAMETER_PROMPT_SUFFIX,
            key=keys.get("parameters"))
        result.model_calls += 1
        if not run.is_valid or run.parsed is None:
            result.stages.append(StageRecord("parameters", "rejected", "json_schema"))
            return result
        result.parameters = [p for p in run.parsed.get("parameters") or []
                             if isinstance(p, dict)]
        result.stages.append(StageRecord(
            "parameters", "completed", f"{len(result.parameters)} proposed"))
        return result
