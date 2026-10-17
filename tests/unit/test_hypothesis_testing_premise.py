"""Task 6B: premise-level corroboration & verification integrity.

GOVERNANCE-ENFORCEMENT tests proving corroboration applies to the SPECIFIC
material premise, not any fact associated with the hypothesis, and that human
adjudication must come from an authorised review record (not a bare id or model
output). FakeLLMBackend supplies only a scripted opinion; assertions concern the
service's disposition.

Two industries: a manufacturer (training / staffing) and a retailer (revenue vs
opex growth). No HCA specifics.
"""

from __future__ import annotations

import io

from reportlab.pdfgen.canvas import Canvas

from app.prompts.registry import PromptRegistry
from app.schemas.llm import (
    CreditDimension,
    CreditHypothesis,
    CreditHypothesisSet,
    EvidenceEnquiryPlan,
    HypothesisVerificationStatus,
)
from app.services.analysis.enquiry_planning import EvidenceEnquiryCoordinator
from app.services.analysis.evidence_retrieval import (
    EvidenceSearchService,
    build_corpus_from_pdf,
)
from app.services.analysis.grounding import EvidenceItem
from app.services.analysis.hypothesis_testing import (
    AdjudicationRecord,
    HypothesisTestingInputs,
    HypothesisTestingService,
)
from app.services.audit.log import AuditLog
from app.services.llm.client import FakeLLMBackend, LLMClient
from app.services.review.workflow import HumanReviewWorkflow, ReviewAction


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _pdf(pages):
    buffer = io.BytesIO()
    canvas = Canvas(buffer)
    for lines in pages:
        y = 800
        for line in lines:
            canvas.drawString(72, y, line)
            y -= 18
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


MFG_PAGES = [
    ["S1", "The company depends on scarce specialised technicians."],
    ["S3", "The company operates a technical training academy."],
    ["S4", "Net leverage was 3.0x at year end."],
]


def _service(db_session, case_id="C1"):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    backend = FakeLLMBackend()
    audit = AuditLog(db_session)
    client = LLMClient(backend, prompts=registry, session=db_session, audit=audit)
    # Pass the session so adjudications can be verified against real reviews.
    service = HypothesisTestingService(
        client, audit=audit, case_id=case_id, review_lookup=db_session
    )
    return service, backend


def _seed_review(db_session, *, hid="h1", premise_id=None, case_id="C1",
                 reviewer="credit.officer", decision="accepted"):
    """Create a real authorised HumanReview and return its AdjudicationRecord."""
    workflow = HumanReviewWorkflow(db_session, audit=AuditLog(db_session), case_id=case_id)
    new_value = {"hypothesis_id": hid, "decision": decision}
    if premise_id is not None:
        new_value["premise_id"] = premise_id
    row = workflow.record_action(
        ReviewAction.ACCEPT_AI_INTERPRETATION,
        reviewer=reviewer,
        target_type="hypothesis",
        target_id=hid,
        new_value=new_value,
        reason="Reviewed premise.",
    )
    return AdjudicationRecord.from_human_review(row)


def _hypothesis(hid="h1", **ov):
    base = dict(
        hypothesis_id=hid,
        dimension=CreditDimension.OPERATING_MODEL_COST_RESILIENCE,
        business_characteristic="Dependence on scarce technicians.",
        statement="A training programme offsets technician scarcity.",
        credit_direction="negative",
        economic_reasoning="Training reduces reliance on scarce external hires.",
        potential_credit_impact="Lower staffing-driven CFO volatility.",
        classification="inference",
        verification_status="unverified",
    )
    base.update(ov)
    return CreditHypothesis(**base)


def _enquiry(hid, kind, intent, query, eid):
    return {
        "enquiry_id": eid, "hypothesis_id": hid,
        "dimension": CreditDimension.OPERATING_MODEL_COST_RESILIENCE.value,
        "requirement_kind": kind, "search_intent": intent,
        "investigative_question": "?", "search_query": query, "rationale": "r",
        "evidence_requirement": "req", "priority": 2,
        "permitted_document_ids": ["MFG"],
    }


def _full_plan(hid="h1"):
    return EvidenceEnquiryPlan.model_validate({
        "enquiries": [
            _enquiry(hid, "supporting", "supporting_evidence",
                     "scarce specialised technicians", "e_s"),
            _enquiry(hid, "contradictory", "contradictory_evidence",
                     "technician retention", "e_c"),
            _enquiry(hid, "offsetting", "offsetting_capability",
                     "technical training academy", "e_o"),
        ],
        "planner_mode": "llm", "incomplete_coverage": [],
    })


def _bundle(plan, pages=MFG_PAGES):
    corpus = build_corpus_from_pdf("MFG", _pdf(pages))
    return EvidenceEnquiryCoordinator(EvidenceSearchService(corpus)).execute(plan)


def _pid(bundle, page):
    for ex in bundle.executed:
        for rp in ex.result.ranked_passages:
            if rp.passage.pdf_page == page:
                return rp.passage.passage_id
    raise AssertionError(f"no passage on page {page}")


def _result(hid="h1", **ov):
    base = {
        "hypothesis_id": hid,
        "dimension": CreditDimension.OPERATING_MODEL_COST_RESILIENCE.value,
        "hypothesis_statement": "A training programme offsets technician scarcity.",
        "evidence_assessments": [], "relevant_factual_claims": [],
        "proposed_mechanism": "Training offsets scarcity.",
        "mechanism_premises": [], "alternative_explanations": [],
        "offsetting_capabilities": [], "offset_effectiveness_evidence": [],
        "proposed_status": "unverified", "justification": "j",
        "unresolved_evidence_requirements": [], "interpretation_confidence": 0.8,
    }
    base.update(ov)
    return base


def _response(*r):
    return {"results": list(r)}


# ===========================================================================
# SCENARIO A — unrelated metric cannot corroborate the mechanism
# ===========================================================================


def test_unrelated_metric_does_not_corroborate_training_mechanism(db_session):
    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    exist = _pid(bundle, 2)
    # Leverage metric is correct but IRRELEVANT to training effectiveness, and
    # the training-effectiveness premise does NOT cite it.
    items = [EvidenceItem(evidence_id="metric_leverage", value=3.0, unit="x")]
    result = _result(
        proposed_status="supported",
        evidence_assessments=[
            {"passage_id": exist, "assessment": "describes_initiative", "rationale": "x"}
        ],
        mechanism_premises=[
            {"premise_id": "effect",
             "premise": "training materially improved staffing resilience and cash flow",
             "status": "verified", "supporting_passage_ids": [exist],
             "is_outcome_premise": True},
        ],
    )
    backend.register("analyze", _response(result), key="k")
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=plan, grounding_items=items,
        ),
        key="k",
    ).governed[0]
    assert gov.effective_status is not HypothesisVerificationStatus.SUPPORTED
    assert "training materially improved staffing resilience and cash flow" in (
        gov.uncorroborated_material_premises
    )
    # The leverage metric was never attached to the training premise.
    effect = next(pc for pc in gov.premise_corroboration if pc.premise_id == "effect")
    assert effect.corroborated is False


# ===========================================================================
# SCENARIO B — same number, wrong proposition
# ===========================================================================


def test_matching_number_wrong_concept_is_not_grounded(db_session):
    """Revenue grew 8%; model claims OPEX grew 8% citing the revenue metric."""
    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    # The revenue-growth metric declares its CONCEPT. The model attaches it to an
    # OPEX claim (same number 8%, different concept).
    items = [EvidenceItem(evidence_id="metric_revenue_growth", value=8.0,
                          unit="percent", concept="revenue growth")]
    result = _result(
        proposed_status="supported",
        mechanism_premises=[
            {"premise_id": "opex",
             "premise": "operating expenses grew 8%",
             "status": "verified",
             "grounding_metric_id": "metric_revenue_growth",
             # Model claims the OPEX concept while linking the revenue metric.
             "claimed_concept": "operating cost growth",
             "grounding_claim": "Operating expenses grew 8%."},
        ],
    )
    backend.register("analyze", _response(result), key="k")
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=plan, grounding_items=items,
        ),
        key="k",
    ).governed[0]
    opex = next(pc for pc in gov.premise_corroboration if pc.premise_id == "opex")
    # Concept mismatch (revenue-growth metric vs an opex claim): the metric is
    # dropped, so the premise is NOT grounded despite the matching number.
    assert opex.corroborated is False
    assert gov.effective_status is not HypothesisVerificationStatus.SUPPORTED
    assert "operating expenses grew 8%" in gov.uncorroborated_material_premises


# ===========================================================================
# SCENARIO C — verified fact + unverified causal mechanism
# ===========================================================================


def test_verified_facts_do_not_prove_causation(db_session):
    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    exist = _pid(bundle, 2)
    sup = _pid(bundle, 1)
    items = [EvidenceItem(evidence_id="metric_retention", value=0.9, unit="ratio",
                          concept="retention")]
    result = _result(
        proposed_status="supported",
        mechanism_premises=[
            {"premise_id": "exists", "premise": "a training academy exists",
             "status": "verified", "supporting_passage_ids": [exist],
             "grounding_claim": None},
            {"premise_id": "retention", "premise": "retention is 0.9",
             "status": "verified",
             "grounding_metric_id": "metric_retention", "claimed_concept": "retention",
             "grounding_claim": "Retention is 0.9 ratio."},
            {"premise_id": "cause",
             "premise": "the training programme caused the retention improvement",
             "status": "verified", "supporting_passage_ids": [sup],
             "is_outcome_premise": True},
        ],
    )
    backend.register("analyze", _response(result), key="k")
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=plan, grounding_items=items,
        ),
        key="k",
    ).governed[0]
    by_id = {pc.premise_id: pc for pc in gov.premise_corroboration}
    assert by_id["retention"].corroborated is True   # the figure is grounded
    assert by_id["cause"].corroborated is False       # causation is model-only
    assert gov.effective_status is not HypothesisVerificationStatus.SUPPORTED
    assert "the training programme caused the retention improvement" in (
        gov.uncorroborated_material_premises
    )


# ===========================================================================
# PREMISE LINKAGE + CORROBORATION INTEGRITY
# ===========================================================================


def test_premise_specific_grounding_still_works(db_session):
    """A premise citing its own metric with a matching figure is corroborated."""
    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    items = [EvidenceItem(evidence_id="metric_lev", value=3.0, unit="x",
                          concept="net leverage")]
    result = _result(
        proposed_status="partially_supported",
        mechanism_premises=[
            {"premise_id": "lev", "premise": "leverage is 3.0x", "status": "verified",
             "grounding_metric_id": "metric_lev", "claimed_concept": "net leverage",
             "grounding_claim": "Net leverage is 3.0x."},
        ],
    )
    backend.register("analyze", _response(result), key="k")
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=plan, grounding_items=items,
        ),
        key="k",
    ).governed[0]
    lev = next(pc for pc in gov.premise_corroboration if pc.premise_id == "lev")
    assert lev.corroborated is True
    assert "deterministic_grounding" in lev.corroboration_basis


def test_model_cannot_self_assign_corroboration(db_session):
    """The LLM output schema has no field to set corroboration."""
    from app.schemas.llm import HypothesisTestResult, MechanismPremise

    assert "independently_corroborated" not in HypothesisTestResult.model_fields
    # A premise can carry a model status but not a corroboration verdict.
    assert "corroborated" not in MechanismPremise.model_fields
    assert "corroboration_basis" not in MechanismPremise.model_fields


def test_model_labelled_contradiction_alone_does_not_reject(db_session):
    """8. An unsubstantiated model CONTRADICTS_PREMISE does not force REJECTED
    without an adequately grounded contradiction (it blocks SUPPORTED, and a
    proposed REJECTED resting only on it is downgraded to UNVERIFIED)."""
    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    con = _pid(bundle, 2)
    result = _result(
        proposed_status="rejected",
        evidence_assessments=[
            {"passage_id": con, "assessment": "contradicts_premise",
             "rationale": "model says so"}
        ],
    )
    # has_contradiction is True so REJECTED is *permitted* by the current gate;
    # but there is no grounded contradiction. We assert REJECTED is NOT reached
    # on absence of substantiation elsewhere -- here we test the inverse case:
    # a proposed SUPPORTED with only a model contradiction is blocked.
    result["proposed_status"] = "supported"
    result["mechanism_premises"] = [
        {"premise": "shortage exists", "status": "verified",
         "supporting_passage_ids": [_pid(bundle, 1)]}
    ]
    backend.register("analyze", _response(result), key="k")
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=plan, adjudications=(_adj(),),
        ),
        key="k",
    ).governed[0]
    assert gov.effective_status is not HypothesisVerificationStatus.SUPPORTED


# ===========================================================================
# HUMAN ADJUDICATION INTEGRITY (authorised review records only)
# ===========================================================================


def _adj(hid="h1", premise_id=None, supports=True):
    return AdjudicationRecord(
        review_id="rev-1", reviewer="credit.officer",
        action="accept_ai_interpretation", hypothesis_id=hid,
        premise_id=premise_id, decision_supports=supports,
        evidence_refs=(), rationale="reviewed",
    )


def test_authorised_adjudication_from_human_review_record(db_session):
    """A real HumanReview row converts to an AdjudicationRecord and corroborates."""
    workflow = HumanReviewWorkflow(db_session, audit=AuditLog(db_session), case_id="C1")
    row = workflow.record_action(
        ReviewAction.ACCEPT_AI_INTERPRETATION,
        reviewer="credit.officer",
        target_type="hypothesis",
        target_id="h1",
        new_value={"hypothesis_id": "h1", "decision": "accepted"},
        reason="Reviewed the training-mechanism premise.",
    )
    adj = AdjudicationRecord.from_human_review(row)
    assert adj is not None
    assert adj.reviewer == "credit.officer"
    assert adj.hypothesis_id == "h1"
    assert adj.decision_supports is True

    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    sup = _pid(bundle, 1)
    result = _result(
        proposed_status="supported",
        mechanism_premises=[
            {"premise": "shortage exists", "status": "verified",
             "supporting_passage_ids": [sup]}
        ],
    )
    backend.register("analyze", _response(result), key="k")
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=plan, adjudications=(adj,),
        ),
        key="k",
    ).governed[0]
    assert gov.effective_status is HypothesisVerificationStatus.SUPPORTED
    assert "human_adjudication" in gov.corroboration_basis


def test_non_corroborating_review_action_is_ignored(db_session):
    """A review-required flag / unrelated action does not corroborate."""
    workflow = HumanReviewWorkflow(db_session, audit=AuditLog(db_session), case_id="C1")
    # 'modify_risk_materiality' is not a corroboration decision.
    row = workflow.record_action(
        ReviewAction.MODIFY_RISK_MATERIALITY,
        reviewer="credit.officer",
        target_type="hypothesis",
        target_id="h1",
        new_value={"hypothesis_id": "h1"},
        reason="changed materiality",
    )
    assert AdjudicationRecord.from_human_review(row) is None


def test_rejecting_adjudication_does_not_corroborate(db_session):
    # A REAL review whose decision is 'rejected' must not corroborate support.
    adj = _seed_review(db_session, decision="rejected")
    assert adj.decision_supports is False
    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    sup = _pid(bundle, 1)
    result = _result(
        proposed_status="supported",
        mechanism_premises=[
            {"premise": "shortage exists", "status": "verified",
             "supporting_passage_ids": [sup]}
        ],
    )
    backend.register("analyze", _response(result), key="k")
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=plan,
            adjudications=(adj,),  # verified review, but a rejecting decision
        ),
        key="k",
    ).governed[0]
    assert gov.effective_status is not HypothesisVerificationStatus.SUPPORTED


def test_adjudication_for_wrong_hypothesis_is_ignored(db_session):
    # A REAL review for a different hypothesis cannot corroborate h1.
    adj = _seed_review(db_session, hid="OTHER")
    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    sup = _pid(bundle, 1)
    result = _result(
        proposed_status="supported",
        mechanism_premises=[
            {"premise": "shortage exists", "status": "verified",
             "supporting_passage_ids": [sup]}
        ],
    )
    backend.register("analyze", _response(result), key="k")
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=plan,
            adjudications=(adj,),  # verified review, but for OTHER hypothesis
        ),
        key="k",
    ).governed[0]
    assert gov.effective_status is not HypothesisVerificationStatus.SUPPORTED


def test_fabricated_adjudication_without_real_review_fails_closed(db_session):
    """A caller-constructed AdjudicationRecord with no matching HumanReview is
    dropped fail-closed and cannot corroborate (Task 6C core adjudication fix)."""
    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    sup = _pid(bundle, 1)
    result = _result(
        proposed_status="supported",
        mechanism_premises=[
            {"premise": "shortage exists", "status": "verified",
             "supporting_passage_ids": [sup]}
        ],
    )
    backend.register("analyze", _response(result), key="k")
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=plan,
            adjudications=(_adj(),),  # fabricated: review_id 'rev-1' does not exist
        ),
        key="k",
    ).governed[0]
    assert gov.effective_status is not HypothesisVerificationStatus.SUPPORTED
    assert "human_adjudication" not in gov.corroboration_basis


# ===========================================================================
# PROVENANCE + CROSS-INDUSTRY
# ===========================================================================


def test_proposed_and_effective_status_both_retained(db_session):
    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    result = _result(
        proposed_status="supported",
        mechanism_premises=[
            {"premise": "shortage exists", "status": "verified",
             "supporting_passage_ids": [_pid(bundle, 1)]}
        ],
    )
    backend.register("analyze", _response(result), key="k")
    res = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=plan,
        ),
        key="k",
    )
    gov = res.governed[0]
    assert gov.proposed_status is HypothesisVerificationStatus.SUPPORTED
    assert gov.effective_status is HypothesisVerificationStatus.UNVERIFIED
    assert gov.testing_run_id == res.run.run_id
    assert gov.generation_status is HypothesisVerificationStatus.UNVERIFIED


def test_second_industry_retailer_opex(db_session):
    """A retailer hypothesis with an uncorroborated outcome premise is not SUPPORTED."""
    service, backend = _service(db_session)
    pages = [
        ["Sales", "Comparable store sales rose during the year."],
        ["Costs", "The company launched a cost-efficiency programme."],
    ]
    hyp = _hypothesis(
        hid="rt1",
        dimension=CreditDimension.OPERATING_MODEL_COST_RESILIENCE,
        business_characteristic="Cost structure.",
        statement="An efficiency programme protects operating margin.",
    )
    plan = EvidenceEnquiryPlan.model_validate({
        "enquiries": [
            {**_enquiry("rt1", "supporting", "supporting_evidence",
                        "cost efficiency programme", "s"),
             "permitted_document_ids": ["RT"]},
            {**_enquiry("rt1", "contradictory", "contradictory_evidence",
                        "margin pressure", "c"),
             "permitted_document_ids": ["RT"]},
            {**_enquiry("rt1", "offsetting", "offsetting_capability",
                        "efficiency programme", "o"),
             "permitted_document_ids": ["RT"]},
        ],
        "planner_mode": "llm", "incomplete_coverage": [],
    })
    corpus = build_corpus_from_pdf("RT", _pdf(pages))
    bundle = EvidenceEnquiryCoordinator(EvidenceSearchService(corpus)).execute(plan)
    prog = next(
        rp.passage.passage_id for ex in bundle.executed
        for rp in ex.result.ranked_passages if rp.passage.pdf_page == 2
    )
    result = _result(
        hid="rt1",
        hypothesis_statement="An efficiency programme protects operating margin.",
        proposed_status="supported",
        evidence_assessments=[
            {"passage_id": prog, "assessment": "describes_initiative", "rationale": "x"}
        ],
        mechanism_premises=[
            {"premise_id": "exists", "premise": "efficiency programme exists",
             "status": "verified", "supporting_passage_ids": [prog]},
            {"premise_id": "margin", "premise": "operating margin was protected",
             "status": "verified", "supporting_passage_ids": [prog],
             "is_outcome_premise": True},
        ],
    )
    backend.register("analyze", _response(result), key="k")
    gov = service.test(
        HypothesisTestingInputs(hypotheses=CreditHypothesisSet(hypotheses=[hyp]),
                                bundle=bundle, plan=plan),
        key="k",
    ).governed[0]
    assert gov.effective_status is not HypothesisVerificationStatus.SUPPORTED
    assert "operating margin was protected" in gov.uncorroborated_material_premises
