from app.services.bo.hybrid import (
    HybridCandidate,
    HybridInput,
    InvestigationMode,
    execute_investigation_mode,
    select_for_investigation,
)
from app.services.bo.m3_evaluation import evaluate_architecture
from app.services.bo.ownership import screen_ownership
from app.services.bo.semantic_entailment import (
    DeterministicCheck,
    SemanticAssessment,
    SemanticEntailmentEvaluator,
    SemanticEvidence,
)


def _candidate(hid="h1", **updates):
    values = dict(hypothesis_id=hid, claim="Material contract renewal risk",
                  dimension="customer_stickiness_concentration", direction="negative",
                  materiality="high", claim_type="inference",
                  supporting_evidence_ids=("e1",))
    values.update(updates)
    return HybridCandidate(**values)


def test_semantic_entailment_accepts_a_structured_supported_paraphrase():
    evaluator = SemanticEntailmentEvaluator(
        lambda payload: {"assessment": "supported", "reasoning": "Same fact, paraphrased.",
                         "evidence_ids": ["e1"]},
        model="recorded-judge", prompt_version="judge-1")
    result = evaluator.evaluate(
        "Future success partly depends on contract renewal.",
        [SemanticEvidence("e1", "Our future success will depend, in part, on renewal.")],
        approved_evidence_ids={"e1"})
    assert result.established and result.judge_model == "recorded-judge"


def test_semantic_entailment_cannot_override_a_deterministic_mismatch():
    called = False

    def judge(payload):
        nonlocal called
        called = True
        return {"assessment": "supported", "reasoning": "yes", "evidence_ids": ["e1"]}

    result = SemanticEntailmentEvaluator(
        judge, model="mock", prompt_version="judge-1").evaluate(
            "Revenue was 51%.", [SemanticEvidence("e1", "Revenue was 48.9%.")],
            approved_evidence_ids={"e1"}, deterministic_checks=(
                DeterministicCheck("numeric", False, "51% != 48.9%", True),))
    assert result.assessment is SemanticAssessment.CONTRADICTED
    assert not called


def test_semantic_entailment_fails_closed_on_malformed_or_unapproved_output():
    bad = SemanticEntailmentEvaluator(
        lambda payload: {"assessment": "supported", "reasoning": "yes",
                         "evidence_ids": ["invented"]},
        model="mock", prompt_version="judge-1")
    result = bad.evaluate("Claim", [SemanticEvidence("e1", "Text")],
                          approved_evidence_ids={"e1"})
    assert result.assessment is SemanticAssessment.UNCERTAIN
    assert result.malformed_output and not result.established


def test_hybrid_selects_weak_material_claims_and_deduplicates():
    first = _candidate()
    unsupported = _candidate("h2", claim="A material risk without a citation",
                             supporting_evidence_ids=(), deterministic_valid=False)
    duplicate = _candidate("h3", claim="Material contract renewal risk")
    result = select_for_investigation([first, unsupported, duplicate])
    assert set(result.selected_ids) == {"h1", "h2"}
    h2 = next(d for d in result.decisions if d.hypothesis_id == "h2")
    assert "weak_or_ambiguous_provenance" in h2.reasons
    assert "failed_deterministic_validation" in h2.reasons
    assert result.decisions[-1].duplicate_of == "h1"


def test_hybrid_execution_merges_investigated_and_unselected_with_lineage():
    quiet = _candidate("h2", claim="Immaterial descriptive fact", direction="neutral",
                       materiality="low", claim_type="fact")
    inputs = [HybridInput(_candidate(), {"id": "h1", "stage": "broad"}),
              HybridInput(quiet, {"id": "h2", "stage": "broad"})]
    result = execute_investigation_mode(
        InvestigationMode.HYBRID, inputs,
        lambda selected: [{"id": item.candidate.hypothesis_id, "stage": "deep"}
                          for item in selected],
        output_id=lambda item: item["id"])
    assert result.outputs == [{"id": "h1", "stage": "deep"},
                              {"id": "h2", "stage": "broad"}]
    assert result.lineage[0]["source"] == "selective_investigation"


def test_semantic_ownership_routes_representative_adversarial_claims():
    cases = [
        ("M1S2", "Hurricane exposure threatens all facilities", "M2"),
        ("M1S4", "Board governance is weak", "M3"),
        ("M1S5", "Acquisition strategy destroys value", "M4"),
        ("M1S3", "Payer mix improved", "M1S2"),
    ]
    for owner, claim, destination in cases:
        decision = screen_ownership(owner, claim)
        assert not decision.in_scope and decision.suggested_destination == destination
    assert screen_ownership(
        "M1S3", "Higher negotiated reimbursement rates improved unit revenue").in_scope
    assert screen_ownership(
        "M1S2", "Facilities in Texas serve managed-care customers").in_scope


def test_three_way_metrics_keep_recall_and_unsupported_acceptance_separate():
    cases = [
        {"case_id": "risk", "material": True, "expected_detected": True,
         "expected_accepted": True, "requires_contradiction_search": True},
        {"case_id": "leap", "material": True, "expected_detected": True,
         "expected_accepted": False},
    ]
    score = evaluate_architecture(cases, [
        {"case_id": "risk", "detected": True, "accepted": True,
         "evidence_complete": True, "contradiction_checked": True},
        {"case_id": "leap", "detected": True, "accepted": False,
         "evidence_complete": False},
    ], usage={"model_calls": 2, "runtime_ms": 4})
    assert score["material_risk_recall"] == {"numerator": 2, "denominator": 2}
    assert score["unsupported_claim_acceptance"] == {"numerator": 0,
                                                       "denominator": 1}
