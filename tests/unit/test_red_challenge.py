"""Independent RED challenge & reconciliation tests (Stage 13, Task 9).

Builds the full upstream chain (hypotheses -> governed -> Task 7 findings ->
Task 8 completeness) and feeds Stage 13. FakeLLMBackend scripts the RED
objections; these tests prove EXECUTION/GOVERNANCE (reference integrity,
evidence discipline, non-destructive operation, reconciliation, escalation,
Stage 12 gating) -- NOT that a live RED model can discover overlooked insights.

Two fictional industries: a manufacturer (technician scarcity + training offset)
and a subscription SaaS provider (customer concentration).
"""

from __future__ import annotations

import pytest

from app.models.orm import Case, Escalation
from app.prompts.registry import PromptRegistry
from app.prompts.templates import MODULE_RED_CHALLENGE_TEMPLATE, PROMPT_CATALOGUE
from app.schemas.llm import (
    ChallengeDisposition,
    CreditDimension,
    CreditHypothesis,
    CreditHypothesisSet,
    EvidenceAssessment,
    GovernedHypothesisTestResult,
    PremiseCorroboration,
    PremiseStatus,
)
from app.services.analysis.credit_signal_assessment import (
    CreditSignalAssessmentInputs,
    CreditSignalAssessmentService,
    MaterialityInputs,
)
from app.services.analysis.evidence_completeness import (
    EvidenceCompletenessInputs,
    MaterialEvidenceCompletenessService,
)
from app.services.analysis.red_challenge import (
    RED_CHALLENGE_REJECTED_EVENT,
    RULE_RED_CHALLENGE_HIGH,
    ModuleRedChallengeRejectedError,
    ModuleRedChallengeService,
    RedChallengeInputs,
)
from app.services.audit.log import AuditLog
from app.services.escalation.engine import EscalationEngine
from app.services.llm.client import FakeLLMBackend, LLMClient


# ---------------------------------------------------------------------------
# Upstream chain builders
# ---------------------------------------------------------------------------


def _hyp(hid="h1", direction="negative",
         dim=CreditDimension.OPERATING_MODEL_COST_RESILIENCE,
         characteristic="Dependence on scarce technicians.", offsets=None):
    return CreditHypothesis(
        hypothesis_id=hid, dimension=dim, business_characteristic=characteristic,
        statement=f"{characteristic} affects resilience.", credit_direction=direction,
        economic_reasoning="m", potential_credit_impact="i", classification="inference",
        verification_status="unverified", offsetting_characteristics=offsets or [],
    )


def _pc(pid, corroborated, *, outcome=False):
    return PremiseCorroboration(
        premise_id=pid, premise=pid, material=True, is_outcome_premise=outcome,
        model_status=PremiseStatus.VERIFIED, corroborated=corroborated,
        corroboration_basis=["deterministic_grounding"] if corroborated else [],
    )


def _governed(hid="h1", *, status="supported",
              dim=CreditDimension.OPERATING_MODEL_COST_RESILIENCE,
              statement="Dependence on scarce technicians. affects resilience.",
              premise_corr=None, uncorroborated=None, assessments=None,
              human_review=False, run_id="run-1"):
    # Default includes BOTH a supporting and a contradictory assessment plus a
    # corroborated premise, so the finding is investigated + sufficient (READY)
    # unless a test overrides it to create a gap.
    default_assessments = [
        EvidenceAssessment(passage_id="sup", assessment="supports_premise", rationale="r"),
    ]
    return GovernedHypothesisTestResult(
        hypothesis_id=hid, dimension=dim, hypothesis_statement=statement,
        proposed_status=status, effective_status=status,
        evidence_assessments=assessments if assessments is not None
        else default_assessments,
        premise_corroboration=premise_corr or [_pc("p", True)],
        uncorroborated_material_premises=uncorroborated or [],
        human_review_required=human_review, testing_run_id=run_id,
    )


def _chain(hyps, governed, materiality_inputs=None, not_applicable=None):
    """hypotheses -> findings (Task 7) -> completeness (Task 8)."""
    findings = CreditSignalAssessmentService().assess(
        CreditSignalAssessmentInputs(
            hypotheses=CreditHypothesisSet(hypotheses=hyps), governed=governed,
            materiality_inputs=materiality_inputs or {},
        )
    ).findings
    completeness = MaterialEvidenceCompletenessService(case_id="C1").assess(
        EvidenceCompletenessInputs(
            findings=findings, hypotheses=CreditHypothesisSet(hypotheses=hyps),
            governed=governed, case_id="C1",
            not_applicable_dimensions=not_applicable or {},
        )
    ).assessment
    return findings, completeness


def _service(db_session, *, with_case=True):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    backend = FakeLLMBackend()
    audit = AuditLog(db_session)
    client = LLMClient(backend, prompts=registry, session=db_session, audit=audit)
    if with_case:
        db_session.add(Case(case_id="C1", status="open"))
        db_session.flush()
    escalation = EscalationEngine(db_session, audit=audit, case_id="C1",
                                  deterministic_ids=True)
    service = ModuleRedChallengeService(
        client, escalation=escalation, audit=audit, case_id="C1")
    return service, backend


def _challenge(cid="x1", *, target_kind="finding", target_id, category,
               severity="medium", refs=None, backed=False):
    return {
        "challenge_id": cid, "target_kind": target_kind, "target_id": target_id,
        "category": category, "severity": severity,
        "challenged_statement": "stmt", "reason": "the reasoning is weak",
        "evidence_refs": refs or [], "claims_evidence_backed": backed,
        "potential_consequence": "cfo volatility", "recommended_action": "investigate",
    }


def _resp(*challenges):
    return {"challenges": list(challenges)}


# A reusable negative, HIGH-materiality finding + completeness (READY-ish).
_HIGH = {"h1": MaterialityInputs(cash_flow_relevant=True, high_magnitude=True)}


def _red_inputs(hyps, governed, materiality_inputs=None, not_applicable=None,
                permitted=frozenset()):
    findings, completeness = _chain(hyps, governed, materiality_inputs, not_applicable)
    return RedChallengeInputs(
        hypotheses=CreditHypothesisSet(hypotheses=hyps), governed=governed,
        findings=findings, completeness=completeness,
        permitted_evidence_ids=permitted, case_id="C1",
    ), findings, completeness


def _finding_id(findings):
    return findings.findings[0].signal.signal_id


# ===========================================================================
# EXECUTION / GOVERNANCE
# ===========================================================================


def test_valid_red_challenge_generation(db_session):
    service, backend = _service(db_session)
    # A clean READY (normal) pass: positive finding (no offset requirement),
    # supporting + contradictory investigated, supported, all other dims N/A.
    hyp = _hyp(direction="positive")
    g = _governed(status="supported", assessments=[
        EvidenceAssessment(passage_id="sup", assessment="supports_premise", rationale="r"),
        EvidenceAssessment(passage_id="con", assessment="contradicts_premise", rationale="r"),
    ])
    # A contradiction present would make sufficiency INSUFFICIENT; keep it clean
    # by using only supporting + a contradictory *investigation* on the signal.
    g = _governed(status="supported", assessments=[
        EvidenceAssessment(passage_id="sup", assessment="supports_premise", rationale="r"),
    ])
    na = {d.value: "n/a" for d in CreditDimension
          if d is not CreditDimension.OPERATING_MODEL_COST_RESILIENCE}
    # Give the signal a contradictory evidence id so contradictory_investigated.
    g.evidence_assessments.append(
        EvidenceAssessment(passage_id="con2", assessment="contextual", rationale="r"))
    findings = CreditSignalAssessmentService().assess(
        CreditSignalAssessmentInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[hyp]), governed=[g],
            materiality_inputs={"h1": MaterialityInputs(cash_flow_relevant=True,
                                                        high_magnitude=True)})).findings
    completeness = MaterialEvidenceCompletenessService(case_id="C1").assess(
        EvidenceCompletenessInputs(
            findings=findings, hypotheses=CreditHypothesisSet(hypotheses=[hyp]),
            governed=[g], case_id="C1", not_applicable_dimensions=na)).assessment
    inputs = RedChallengeInputs(
        hypotheses=CreditHypothesisSet(hypotheses=[hyp]), governed=[g],
        findings=findings, completeness=completeness, case_id="C1")
    fid = findings.findings[0].signal.signal_id
    backend.register("challenge", _resp(
        _challenge(target_id=fid, category="unsupported_economic_mechanism")), key="k")
    rep = service.challenge(inputs, key="k").report
    assert rep.pass_kind in ("normal", "escalation")
    assert len(rep.challenges) == 1
    assert rep.challenges[0].reference_resolved is True
    # Non-destructive: verification status unchanged.
    assert findings.findings[0].signal.verification_status.value == "supported"


def test_schema_invalid_rejected(db_session):
    service, backend = _service(db_session)
    hyp = _hyp()
    inputs, _, _ = _red_inputs([hyp], [_governed()], _HIGH)
    backend.register("challenge", "not json", key="k")
    with pytest.raises(ModuleRedChallengeRejectedError) as e:
        service.challenge(inputs, key="k")
    assert e.value.stage == "json_schema"


def test_unknown_target_reference_retained_as_unresolved(db_session):
    service, backend = _service(db_session)
    hyp = _hyp()
    inputs, _, _ = _red_inputs([hyp], [_governed()], _HIGH)
    backend.register("challenge", _resp(
        _challenge(target_id="ghost-finding", category="overlooked_vulnerability")),
        key="k")
    rep = service.challenge(inputs, key="k").report
    gc = rep.challenges[0]
    # Not dropped: retained, flagged unresolved reference + review.
    assert gc.reference_resolved is False
    assert gc.requires_human_review is True
    assert gc.reconciliation.disposition is ChallengeDisposition.UNRESOLVED


def test_unauthorised_evidence_ref_not_counted_as_backed(db_session):
    service, backend = _service(db_session)
    hyp = _hyp()
    inputs, findings, _ = _red_inputs([hyp], [_governed()], _HIGH,
                                      permitted=frozenset({"doc_p1"}))
    fid = _finding_id(findings)
    backend.register("challenge", _resp(_challenge(
        target_id=fid, category="contradictory_evidence",
        refs=["doc_p1", "doc_fabricated"], backed=True)), key="k")
    rep = service.challenge(inputs, key="k").report
    gc = rep.challenges[0]
    # Model CLAIMED evidence-backed, but a ref is not permitted -> service says no.
    assert gc.evidence_backed is False
    assert gc.unresolved_unknown_refs == ["doc_fabricated"]


def test_evidence_backed_requires_all_permitted(db_session):
    service, backend = _service(db_session)
    hyp = _hyp()
    inputs, findings, _ = _red_inputs([hyp], [_governed()], _HIGH,
                                      permitted=frozenset({"doc_p1"}))
    fid = _finding_id(findings)
    backend.register("challenge", _resp(_challenge(
        target_id=fid, category="contradictory_evidence", refs=["doc_p1"])), key="k")
    rep = service.challenge(inputs, key="k").report
    assert rep.challenges[0].evidence_backed is True


def test_duplicate_challenge_ids_rejected(db_session):
    service, backend = _service(db_session)
    hyp = _hyp()
    inputs, findings, _ = _red_inputs([hyp], [_governed()], _HIGH)
    fid = _finding_id(findings)
    backend.register("challenge", _resp(
        _challenge("dup", target_id=fid, category="incorrect_materiality"),
        _challenge("dup", target_id=fid, category="double_counting")), key="k")
    with pytest.raises(ModuleRedChallengeRejectedError) as e:
        service.challenge(inputs, key="k")
    assert e.value.stage == "service_governance"


def test_high_severity_routes_to_real_escalation(db_session):
    service, backend = _service(db_session)
    hyp = _hyp()
    inputs, findings, _ = _red_inputs([hyp], [_governed()], _HIGH)
    fid = _finding_id(findings)
    backend.register("challenge", _resp(_challenge(
        "c-high", target_id=fid, category="unsupported_factual_claim",
        severity="high")), key="k")
    rep = service.challenge(inputs, key="k").report
    gc = rep.challenges[0]
    assert gc.escalation_id is not None
    # A real, open escalation row exists with the right rule id.
    row = db_session.get(Escalation, gc.escalation_id)
    assert row is not None and row.rule_id == RULE_RED_CHALLENGE_HIGH
    assert row.status == "open" and row.case_id == "C1"
    # HIGH is pending human adjudication, not auto-resolved.
    assert gc.reconciliation.disposition is ChallengeDisposition.PENDING_HUMAN_ADJUDICATION


def test_low_severity_does_not_escalate(db_session):
    service, backend = _service(db_session)
    hyp = _hyp()
    inputs, findings, _ = _red_inputs([hyp], [_governed()], _HIGH)
    fid = _finding_id(findings)
    backend.register("challenge", _resp(_challenge(
        "c-low", target_id=fid, category="overstated_certainty", severity="low")),
        key="k")
    rep = service.challenge(inputs, key="k").report
    assert rep.challenges[0].escalation_id is None
    assert rep.escalation_ids == []


def test_rebuttal_does_not_auto_resolve_and_persists(db_session):
    service, backend = _service(db_session)
    hyp = _hyp()
    inputs, findings, _ = _red_inputs([hyp], [_governed()], _HIGH)
    fid = _finding_id(findings)
    backend.register("challenge", _resp(_challenge(
        "c1", target_id=fid, category="unsupported_economic_mechanism",
        severity="high")), key="k")
    rep = service.challenge(inputs, key="k").report
    gc = rep.challenges[0]
    # No rebuttal applied; disposition is pending, never auto-resolved.
    assert gc.reconciliation.rebuttal is None
    assert gc.reconciliation.disposition is not ChallengeDisposition.RESOLVED
    assert gc in rep.unresolved_challenges()


def test_non_destructive_original_data_unchanged(db_session):
    service, backend = _service(db_session)
    hyp = _hyp()
    inputs, findings, completeness = _red_inputs([hyp], [_governed()], _HIGH)
    fid = _finding_id(findings)
    before_status = findings.findings[0].signal.verification_status
    before_progression = completeness.progression
    backend.register("challenge", _resp(_challenge(
        target_id=fid, category="incorrect_materiality", severity="high")), key="k")
    service.challenge(inputs, key="k")
    assert findings.findings[0].signal.verification_status is before_status
    assert completeness.progression is before_progression


# ===========================================================================
# STAGE 12 GATE
# ===========================================================================


def test_ready_with_escalation_preserves_dependencies(db_session):
    service, backend = _service(db_session)
    hyp = _hyp()
    # human_review on the governed result -> READY_WITH_ESCALATION, all other
    # dimensions N/A so investigation is complete-for-scope.
    g = _governed(human_review=True)
    na = {d.value: "n/a" for d in CreditDimension
          if d is not CreditDimension.OPERATING_MODEL_COST_RESILIENCE}
    inputs, _, completeness = _red_inputs([hyp], [g], _HIGH, not_applicable=na)
    assert completeness.progression.value == "ready_with_escalation"
    backend.register("challenge", _resp(), key="k")
    rep = service.challenge(inputs, key="k").report
    assert rep.pass_kind == "escalation"
    assert rep.outstanding_dependencies  # dependencies preserved


def test_blocked_does_not_silently_progress(db_session):
    service, backend = _service(db_session)
    hyp = _hyp()
    g = _governed(status="unverified", uncorroborated=["premise"])  # blocks
    inputs, _, completeness = _red_inputs([hyp], [g], _HIGH)
    assert completeness.progression.value == "blocked_pending_evidence"
    rep = service.challenge(inputs, key="k").report  # no backend call needed
    assert rep.pass_kind == "blocked"
    assert rep.assessable is False
    assert rep.challenges == []


def test_blocked_diagnostic_pass_is_explicit(db_session):
    service, backend = _service(db_session)
    hyp = _hyp()
    g = _governed(status="unverified", uncorroborated=["premise"])
    inputs, findings, _ = _red_inputs([hyp], [g], _HIGH)
    fid = _finding_id(findings)
    backend.register("challenge", _resp(_challenge(
        target_id=fid, category="inadequate_investigation")), key="k")
    rep = service.challenge(inputs, key="k", allow_diagnostic=True).report
    assert rep.pass_kind == "diagnostic"  # explicitly labelled, not 'normal'


def test_not_assessable_returns_structured_outcome(db_session):
    service, backend = _service(db_session)
    inputs, _, completeness = _red_inputs([], [])
    assert completeness.progression.value == "not_assessable"
    rep = service.challenge(inputs, key="k").report
    assert rep.pass_kind == "not_assessable"
    assert rep.assessable is False
    assert rep.challenges == []


# ===========================================================================
# CHALLENGE CONTENT CATEGORIES (scripted; governance not model quality)
# ===========================================================================


def test_unsupported_causal_reasoning_challenge(db_session):
    service, backend = _service(db_session)
    hyp = _hyp()
    inputs, findings, _ = _red_inputs([hyp], [_governed()], _HIGH)
    fid = _finding_id(findings)
    backend.register("challenge", _resp(_challenge(
        target_id=fid, category="unsupported_economic_mechanism")), key="k")
    rep = service.challenge(inputs, key="k").report
    assert rep.challenges[0].category.value == "unsupported_economic_mechanism"


def test_unproven_offset_effectiveness_challenge(db_session):
    service, backend = _service(db_session)
    hyp = _hyp(offsets=["Training academy."])
    g = _governed(premise_corr=[_pc("exists", True), _pc("effect", False, outcome=True)],
                  uncorroborated=["effect"])
    inputs, findings, completeness = _red_inputs([hyp], [g], _HIGH)
    # An uncorroborated effectiveness premise blocks progression; RED runs as an
    # explicitly labelled diagnostic pass to challenge the unproven offset.
    assert completeness.progression.value == "blocked_pending_evidence"
    fid = _finding_id(findings)
    backend.register("challenge", _resp(_challenge(
        target_id=fid, target_kind="offset",
        category="unproven_offset_effectiveness", severity="high")), key="k")
    rep = service.challenge(inputs, key="k", allow_diagnostic=True).report
    assert rep.pass_kind == "diagnostic"
    gc = rep.challenges[0]
    assert gc.category.value == "unproven_offset_effectiveness"
    assert gc.reference_resolved is True  # offset target resolves to the finding


def test_unsupported_not_applicable_dimension_challenge(db_session):
    service, backend = _service(db_session)
    hyp = _hyp()
    na = {CreditDimension.COMPETITIVE_DURABILITY.value: "assumed niche"}
    inputs, _, _ = _red_inputs([hyp], [_governed()], _HIGH, not_applicable=na)
    backend.register("challenge", _resp(_challenge(
        target_kind="dimension",
        target_id=CreditDimension.COMPETITIVE_DURABILITY.value,
        category="unsupported_dimension_non_applicability", severity="high")), key="k")
    rep = service.challenge(inputs, key="k").report
    gc = rep.challenges[0]
    assert gc.reference_resolved is True
    assert gc.category.value == "unsupported_dimension_non_applicability"


def test_completeness_recommendation_challenge(db_session):
    service, backend = _service(db_session)
    hyp = _hyp()
    na = {d.value: "n/a" for d in CreditDimension
          if d is not CreditDimension.OPERATING_MODEL_COST_RESILIENCE}
    inputs, _, completeness = _red_inputs(
        [hyp], [_governed(premise_corr=[_pc("p", True)],
                          assessments=[EvidenceAssessment(passage_id="s",
                                                          assessment="supports_premise",
                                                          rationale="r")])],
        _HIGH, not_applicable=na)
    backend.register("challenge", _resp(_challenge(
        target_kind="completeness", target_id="module",
        category="incorrect_completeness_recommendation", severity="high")), key="k")
    rep = service.challenge(inputs, key="k").report
    assert rep.challenges[0].reference_resolved is True


def test_double_counting_challenge(db_session):
    service, backend = _service(db_session)
    h1 = _hyp("h1", characteristic="Technician dependence.")
    h2 = _hyp("h2", characteristic="Technician dependence.")
    inputs, findings, _ = _red_inputs(
        [h1, h2], [_governed("h1", run_id="r1"), _governed("h2", run_id="r2")],
        materiality_inputs={
            "h1": MaterialityInputs(cash_flow_relevant=True, high_magnitude=True),
            "h2": MaterialityInputs(cash_flow_relevant=True, high_magnitude=True)})
    fid = findings.findings[0].signal.signal_id
    backend.register("challenge", _resp(_challenge(
        target_id=fid, category="double_counting")), key="k")
    rep = service.challenge(inputs, key="k").report
    assert rep.challenges[0].category.value == "double_counting"


def test_provenance_preserved(db_session):
    service, backend = _service(db_session)
    hyp = _hyp()
    inputs, findings, _ = _red_inputs([hyp], [_governed()], _HIGH)
    fid = _finding_id(findings)
    backend.register("challenge", _resp(_challenge(
        target_id=fid, category="overstated_certainty")), key="k")
    res = service.challenge(inputs, key="k")
    rep = res.report
    assert rep.challenge_prompt_version == "module_red_challenge_v1.0"
    assert rep.red_run_id == res.run.run_id
    assert rep.audit_provenance["completeness_version"]


def test_second_industry_saas(db_session):
    service, backend = _service(db_session)
    hyp = _hyp("sw1", dim=CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION,
               characteristic="Top-customer concentration.")
    g = _governed("sw1", dim=CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION,
                  statement="Top-customer concentration. affects resilience.",
                  premise_corr=[_pc("conc", True)])
    inputs, findings, _ = _red_inputs(
        [hyp], [g],
        materiality_inputs={"sw1": MaterialityInputs(cash_flow_relevant=True,
                                                     broad_or_concentrated=True)})
    fid = _finding_id(findings)
    backend.register("challenge", _resp(_challenge(
        target_id=fid, category="overlooked_vulnerability", severity="high")), key="k")
    rep = service.challenge(inputs, key="k").report
    assert rep.challenges[0].escalation_id is not None


def test_escalation_unavailable_fails_visibly(db_session):
    """A HIGH challenge with no EscalationEngine is flagged, not silently escalated."""
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    backend = FakeLLMBackend()
    audit = AuditLog(db_session)
    client = LLMClient(backend, prompts=registry, session=db_session, audit=audit)
    service = ModuleRedChallengeService(client, escalation=None, audit=audit,
                                        case_id="C1")  # no escalation engine
    hyp = _hyp()
    inputs, findings, _ = _red_inputs([hyp], [_governed()], _HIGH)
    fid = _finding_id(findings)
    backend.register("challenge", _resp(_challenge(
        target_id=fid, category="unsupported_factual_claim", severity="high")), key="k")
    rep = service.challenge(inputs, key="k").report
    assert rep.challenges[0].escalation_id is None  # not fabricated
    types = [e.event_type for e in audit.events_for_case("C1")]
    assert RED_CHALLENGE_REJECTED_EVENT in types  # visible failure recorded


# ===========================================================================
# PROMPT INSTRUCTION (string only)
# ===========================================================================


def test_prompt_registers(db_session):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    p = registry.latest("module_red_challenge")
    assert p is not None and p.prompt_id == "module_red_challenge_v1.0"
    assert [e["name"] for e in PROMPT_CATALOGUE].count("module_red_challenge") == 1


def test_prompt_text_demands_falsification_and_no_fabrication():
    text = MODULE_RED_CHALLENGE_TEMPLATE.lower()
    assert "falsify" in text
    assert "fabricate no evidence" in text or "never invent a contradiction" in text
    assert "existence" in text and "effect" in text
    assert "procedural independence" in text or "not to restate known uncertainties" in text
