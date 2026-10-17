"""Credit signal / impact / materiality tests (Stages 9-11, Task 7).

Test groups kept explicitly separate:
* EXECUTABLE GOVERNANCE -- the service's deterministic enforcement (status
  preservation, rejected-not-validated, no fabricated numbers, rubric
  versioning, overlap linkage, ranking, Stage 12 handoff, review routing).
* LLM-OUTPUT VALIDATION -- the optional credit_signal_assessment prompt path
  (schema-valid accepted, invalid rejected, unknown signal rejected).
* PROMPT INSTRUCTION -- template wording only.

Scope honesty: FakeLLMBackend supplies scripted opinions; these tests prove the
governance plumbing, not real economic reasoning. Two industries used
(manufacturer; subscription software).
"""

from __future__ import annotations

import pytest

from app.prompts.registry import PromptRegistry
from app.prompts.templates import CREDIT_SIGNAL_ASSESSMENT_TEMPLATE, PROMPT_CATALOGUE
from app.schemas.llm import (
    CreditDimension,
    CreditDirection,
    CreditHypothesis,
    CreditHypothesisSet,
    EvidenceAssessment,
    GovernedHypothesisTestResult,
    HypothesisVerificationStatus,
    ImpactQuantification,
    Materiality,
    PremiseCorroboration,
    PremiseStatus,
    PresentationMateriality,
)
from app.services.analysis.credit_signal_assessment import (
    RUBRIC_VERSION,
    CreditSignalAssessmentInputs,
    CreditSignalAssessmentRejectedError,
    CreditSignalAssessmentService,
    MaterialityInputs,
)
from app.services.audit.log import AuditLog
from app.services.llm.client import FakeLLMBackend, LLMClient


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _hyp(hid="h1", direction="negative", dim=CreditDimension.OPERATING_MODEL_COST_RESILIENCE,
         characteristic="Dependence on scarce technicians.", offsets=None, **ov):
    base = dict(
        hypothesis_id=hid, dimension=dim,
        business_characteristic=characteristic,
        statement=f"{characteristic} affects resilience.",
        credit_direction=direction,
        economic_reasoning="mechanism", potential_credit_impact="impact",
        classification="inference", verification_status="unverified",
        offsetting_characteristics=offsets or [],
    )
    base.update(ov)
    return CreditHypothesis(**base)


def _premise(pid, corroborated, *, outcome=False, material=True):
    return PremiseCorroboration(
        premise_id=pid, premise=pid, material=material, is_outcome_premise=outcome,
        model_status=PremiseStatus.VERIFIED,
        corroborated=corroborated,
        corroboration_basis=["deterministic_grounding"] if corroborated else [],
    )


def _governed(hid="h1", *, status="unverified", dim=CreditDimension.OPERATING_MODEL_COST_RESILIENCE,
              statement="Dependence on scarce technicians. affects resilience.",
              assessments=None, premise_corr=None, uncorroborated=None,
              unresolved=None, human_review=False, run_id="run-1"):
    return GovernedHypothesisTestResult(
        hypothesis_id=hid, dimension=dim, hypothesis_statement=statement,
        proposed_status=status, effective_status=status,
        evidence_assessments=assessments or [],
        premise_corroboration=premise_corr or [],
        uncorroborated_material_premises=uncorroborated or [],
        unresolved_evidence_requirements=unresolved or [],
        human_review_required=human_review, testing_run_id=run_id,
    )


def _assess(hyps, governed, **kw):
    service = CreditSignalAssessmentService()
    return service.assess(
        CreditSignalAssessmentInputs(
            hypotheses=CreditHypothesisSet(hypotheses=hyps), governed=governed, **kw
        )
    ).findings


# ===========================================================================
# EXECUTABLE GOVERNANCE
# ===========================================================================


def test_verified_positive_signal(db_session):
    hyp = _hyp(direction="positive")
    g = _governed(status="supported",
                  premise_corr=[_premise("p", True)],
                  assessments=[EvidenceAssessment(passage_id="x",
                                                  assessment="supports_premise", rationale="r")])
    fs = _assess([hyp], [g],
                 materiality_inputs={"h1": MaterialityInputs(cash_flow_relevant=True,
                                                             high_magnitude=True)})
    f = fs.findings[0]
    assert f.signal.assessed_direction is CreditDirection.POSITIVE
    assert f.signal.verification_status is HypothesisVerificationStatus.SUPPORTED
    assert f.materiality.materiality is Materiality.HIGH


def test_verified_negative_signal(db_session):
    hyp = _hyp(direction="negative")
    g = _governed(status="supported", premise_corr=[_premise("p", True)])
    fs = _assess([hyp], [g],
                 materiality_inputs={"h1": MaterialityInputs(cash_flow_relevant=True,
                                                             high_magnitude=True)})
    f = fs.findings[0]
    assert f.signal.assessed_direction is CreditDirection.NEGATIVE
    assert f.signal.verification_status is HypothesisVerificationStatus.SUPPORTED


def test_material_but_unverified_negative_is_displayed(db_session):
    """A material unverified risk is still DISPLAY, not suppressed."""
    hyp = _hyp(direction="negative")
    g = _governed(status="unverified", uncorroborated=["key premise"])
    fs = _assess([hyp], [g],
                 materiality_inputs={"h1": MaterialityInputs(cash_flow_relevant=True,
                                                             high_magnitude=True)})
    f = fs.findings[0]
    assert f.signal.verification_status is HypothesisVerificationStatus.UNVERIFIED
    assert f.materiality.materiality is Materiality.HIGH
    assert f.materiality.presentation is PresentationMateriality.DISPLAY


def test_minor_but_supported_fact_is_not_headline(db_session):
    hyp = _hyp(direction="positive")
    g = _governed(status="supported", premise_corr=[_premise("p", True)])
    fs = _assess([hyp], [g],
                 materiality_inputs={"h1": MaterialityInputs(cash_flow_relevant=False)})
    f = fs.findings[0]
    assert f.materiality.materiality is Materiality.LOW
    assert f.materiality.presentation is PresentationMateriality.ON_DEMAND


def test_initiative_exists_but_effectiveness_uncertain(db_session):
    hyp = _hyp(direction="negative", offsets=["Training academy."])
    g = _governed(
        status="partially_supported",
        premise_corr=[_premise("exists", True), _premise("effect", False, outcome=True)],
        uncorroborated=["effect"],
    )
    fs = _assess([hyp], [g],
                 materiality_inputs={"h1": MaterialityInputs(cash_flow_relevant=True)})
    f = fs.findings[0]
    # Offset recorded but its effectiveness is an uninvestigated/unresolved item.
    assert "Training academy." in f.uninvestigated_offsets
    assert "effect" in f.uncorroborated_material_premises


def test_unsupported_causal_relationship_stays_unquantified(db_session):
    hyp = _hyp(direction="negative")
    g = _governed(status="unverified", unresolved=["need causal data"])
    fs = _assess([hyp], [g])
    f = fs.findings[0]
    assert f.impact.quantification is ImpactQuantification.UNQUANTIFIED
    assert "need causal data" in f.impact.missing_information


def test_contradiction_flags_direction_mismatch_and_review(db_session):
    hyp = _hyp(direction="negative")
    g = _governed(
        status="partially_supported",
        assessments=[EvidenceAssessment(passage_id="c",
                                        assessment="contradicts_premise", rationale="r")],
    )
    fs = _assess([hyp], [g],
                 materiality_inputs={"h1": MaterialityInputs(cash_flow_relevant=True)})
    f = fs.findings[0]
    assert f.signal.direction_mismatch is True
    assert f.required_human_review is True
    assert "c" in f.outstanding_contradictions


def test_overlapping_hypotheses_are_linked_not_deleted(db_session):
    h1 = _hyp("h1", direction="negative", characteristic="Technician dependence.")
    h2 = _hyp("h2", direction="negative", characteristic="Technician dependence.")
    fs = _assess(
        [h1, h2],
        [_governed("h1", run_id="r1"), _governed("h2", run_id="r2")],
    )
    assert len(fs.findings) == 2  # neither deleted
    ids = {f.signal.signal_id for f in fs.findings}
    for f in fs.findings:
        assert f.overlap_links  # each links to the other
        assert set(f.overlap_links) <= ids


def test_rejected_hypothesis_is_investigation_candidate_only(db_session):
    hyp = _hyp(direction="negative")
    g = _governed(status="rejected")
    fs = _assess([hyp], [g])
    assert fs.findings == []  # not a validated finding
    assert len(fs.investigation_candidates) == 1
    cand = fs.investigation_candidates[0]
    assert cand.is_investigation_candidate is True
    assert cand.verification_status is HypothesisVerificationStatus.REJECTED


def test_material_finding_with_missing_quant_is_qualitative(db_session):
    hyp = _hyp(direction="negative")
    g = _governed(status="partially_supported", premise_corr=[_premise("p", True)],
                  unresolved=["quantify the effect"])
    fs = _assess([hyp], [g],
                 materiality_inputs={"h1": MaterialityInputs(cash_flow_relevant=True,
                                                             high_magnitude=True)})
    f = fs.findings[0]
    assert f.impact.quantification is ImpactQuantification.QUALITATIVE
    assert "quantify the effect" in f.unresolved_evidence_requirements


def test_severity_direction_verification_presentation_are_separate(db_session):
    hyp = _hyp(direction="negative")
    g = _governed(status="unverified")
    fs = _assess([hyp], [g],
                 materiality_inputs={"h1": MaterialityInputs(cash_flow_relevant=True,
                                                             high_magnitude=True)})
    f = fs.findings[0]
    # High materiality + negative direction + unverified + DISPLAY all coexist.
    assert f.materiality.materiality is Materiality.HIGH
    assert f.signal.assessed_direction is CreditDirection.NEGATIVE
    assert f.signal.verification_status is HypothesisVerificationStatus.UNVERIFIED
    assert f.materiality.presentation is PresentationMateriality.DISPLAY


def test_no_fabricated_numbers_without_validated_metric(db_session):
    hyp = _hyp(direction="negative")
    g = _governed(status="supported", premise_corr=[_premise("p", True)])
    # Even if a later model proposed a metric ref, it is dropped unless validated.
    fs = _assess([hyp], [g], validated_metric_ids=frozenset())
    f = fs.findings[0]
    assert f.impact.quantification is not ImpactQuantification.QUANTIFIED
    assert f.impact.quantified_metric_refs == []


def test_status_not_promoted(db_session):
    hyp = _hyp(direction="positive")
    g = _governed(status="partially_supported", premise_corr=[_premise("p", True)])
    fs = _assess([hyp], [g],
                 materiality_inputs={"h1": MaterialityInputs(cash_flow_relevant=True)})
    # Effective status carried verbatim; the service never upgrades it.
    assert fs.findings[0].signal.verification_status is (
        HypothesisVerificationStatus.PARTIALLY_SUPPORTED
    )


def test_rubric_version_is_deterministic_and_recorded(db_session):
    hyp = _hyp()
    g = _governed(status="unverified")
    fs = _assess([hyp], [g])
    assert fs.rubric_version == RUBRIC_VERSION
    assert fs.findings[0].materiality.rubric_version == RUBRIC_VERSION


def test_human_review_when_rubric_cannot_separate_high_medium(db_session):
    """Unknown magnitude/breadth + cash-flow relevant -> MEDIUM + review flag."""
    hyp = _hyp(direction="negative")
    g = _governed(status="unverified")
    fs = _assess([hyp], [g],
                 materiality_inputs={"h1": MaterialityInputs(cash_flow_relevant=True)})
    f = fs.findings[0]
    assert f.materiality.materiality is Materiality.MEDIUM
    assert f.materiality.requires_human_review is True
    assert f.required_human_review is True


def test_ranking_orders_high_medium_low(db_session):
    hi = _hyp("hi", direction="negative", characteristic="A")
    med = _hyp("med", direction="negative", characteristic="B")
    lo = _hyp("lo", direction="positive", characteristic="C")
    fs = _assess(
        [hi, med, lo],
        [_governed("hi", run_id="r1"), _governed("med", run_id="r2"),
         _governed("lo", run_id="r3")],
        materiality_inputs={
            "hi": MaterialityInputs(cash_flow_relevant=True, high_magnitude=True),
            "med": MaterialityInputs(cash_flow_relevant=True, high_magnitude=False,
                                     broad_or_concentrated=False),
            "lo": MaterialityInputs(cash_flow_relevant=False),
        },
    )
    levels = [f.materiality.materiality for f in fs.findings]
    assert levels == [Materiality.HIGH, Materiality.MEDIUM, Materiality.LOW]
    assert [f.rank for f in fs.findings] == [1, 2, 3]


def test_stage12_handoff_fields_present(db_session):
    hyp = _hyp(direction="negative", offsets=["Programme."])
    g = _governed(status="partially_supported",
                  uncorroborated=["effect"], unresolved=["more data"],
                  assessments=[EvidenceAssessment(passage_id="c",
                                                  assessment="contradicts_premise", rationale="r")],
                  human_review=True)
    fs = _assess([hyp], [g],
                 materiality_inputs={"h1": MaterialityInputs(cash_flow_relevant=True)})
    f = fs.findings[0]
    assert f.uncorroborated_material_premises == ["effect"]
    assert f.unresolved_evidence_requirements == ["more data"]
    assert f.outstanding_contradictions == ["c"]
    assert f.uninvestigated_offsets == ["Programme."]
    assert f.required_human_review is True
    assert f.testing_run_id == "run-1"


def test_unknown_hypothesis_reference_rejected(db_session):
    hyp = _hyp("h1")
    g = _governed("ghost")
    with pytest.raises(CreditSignalAssessmentRejectedError) as e:
        _assess([hyp], [g])
    assert e.value.stage == "service_governance"


def test_second_industry_software_concentration(db_session):
    hyp = _hyp("sw1", direction="negative",
               dim=CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION,
               characteristic="Top-customer revenue concentration.")
    g = _governed("sw1", dim=CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION,
                  statement="Top-customer revenue concentration. affects resilience.",
                  status="partially_supported", premise_corr=[_premise("conc", True)])
    fs = _assess([hyp], [g],
                 materiality_inputs={"sw1": MaterialityInputs(cash_flow_relevant=True,
                                                              broad_or_concentrated=True)})
    f = fs.findings[0]
    assert f.signal.dimension is CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION
    assert f.materiality.materiality is Materiality.HIGH


# ===========================================================================
# LLM-OUTPUT VALIDATION (optional proposal path)
# ===========================================================================


def _llm_service(db_session):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    backend = FakeLLMBackend()
    audit = AuditLog(db_session)
    client = LLMClient(backend, prompts=registry, session=db_session, audit=audit)
    return CreditSignalAssessmentService(client, audit=audit, case_id="C1"), backend


def test_llm_proposal_accepted_but_governed(db_session):
    service, backend = _llm_service(db_session)
    hyp = _hyp(direction="negative")
    g = _governed(status="unverified")
    inputs = CreditSignalAssessmentInputs(
        hypotheses=CreditHypothesisSet(hypotheses=[hyp]), governed=[g],
        materiality_inputs={"h1": MaterialityInputs(cash_flow_relevant=True,
                                                    high_magnitude=True)},
    )
    # Build the signal id the service will compute, to key the scripted response.
    from app.core.hashing import content_hash
    sig_id = content_hash({"sig": "h1", "run": "run-1"})
    backend.register("analyze", {"assessments": [{
        "signal_id": sig_id, "impact_channel": "operating",
        "transmission_mechanism": "labour scarcity raises cost",
        "observed_facts": [], "unproven_inferences": ["margins may fall"],
        "material_assumptions": [], "quantified_metric_refs": ["metric_bogus"],
        "potential_repayment_implication": "possible CFO pressure",
        "proposed_materiality": "high", "materiality_rationale": "big",
    }]}, key="k")
    fs = service.assess(inputs, key="k", use_llm=True).findings
    f = fs.findings[0]
    # The model proposed a metric ref, but it is NOT validated -> dropped.
    assert f.impact.quantification is not ImpactQuantification.QUANTIFIED
    assert f.impact.quantified_metric_refs == []
    # The service's rubric decides materiality, not the model's "high".
    assert f.materiality.rubric_version == RUBRIC_VERSION


def test_llm_unknown_signal_rejected(db_session):
    service, backend = _llm_service(db_session)
    hyp = _hyp(direction="negative")
    g = _governed(status="unverified")
    inputs = CreditSignalAssessmentInputs(
        hypotheses=CreditHypothesisSet(hypotheses=[hyp]), governed=[g])
    backend.register("analyze", {"assessments": [{
        "signal_id": "not-a-real-signal", "impact_channel": "operating",
        "transmission_mechanism": "x", "proposed_materiality": "high",
    }]}, key="k")
    with pytest.raises(CreditSignalAssessmentRejectedError) as e:
        service.assess(inputs, key="k", use_llm=True)
    assert e.value.stage == "service_governance"


def test_llm_malformed_rejected(db_session):
    service, backend = _llm_service(db_session)
    hyp = _hyp(direction="negative")
    g = _governed(status="unverified")
    inputs = CreditSignalAssessmentInputs(
        hypotheses=CreditHypothesisSet(hypotheses=[hyp]), governed=[g])
    backend.register("analyze", "not json", key="k")
    with pytest.raises(CreditSignalAssessmentRejectedError) as e:
        service.assess(inputs, key="k", use_llm=True)
    assert e.value.stage == "json_schema"


# ===========================================================================
# PROMPT INSTRUCTION (string only)
# ===========================================================================


def test_prompt_registers(db_session):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    p = registry.latest("credit_signal_assessment")
    assert p is not None and p.prompt_id == "credit_signal_assessment_v1.0"
    assert [e["name"] for e in PROMPT_CATALOGUE].count("credit_signal_assessment") == 1


def test_prompt_text_forbids_invented_numbers_and_netting():
    text = CREDIT_SIGNAL_ASSESSMENT_TEMPLATE.lower()
    assert "never invent a numeric estimate" in text
    assert "do not net a positive offset against a negative exposure" in text
    assert "do not treat correlation as causation" in text
    assert "do not conflate operating cash flow" in text
