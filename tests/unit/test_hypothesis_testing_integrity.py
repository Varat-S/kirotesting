"""Task 6A: hypothesis-verification integrity (self-corroboration loophole).

These are GOVERNANCE-ENFORCEMENT tests: they prove the service refuses to let a
model's own labels (SUPPORTS_PREMISE assessments, VERIFIED premise statuses,
verbatim excerpts) promote a hypothesis to SUPPORTED or PARTIALLY_SUPPORTED
without INDEPENDENT corroboration (deterministic grounding or recorded human
adjudication). FakeLLMBackend here supplies only a scripted *opinion*; the
assertions concern the service's disposition, not model accuracy.

Two industries: a manufacturer (training programme) and a logistics carrier
(fuel-cost pass-through). No HCA/healthcare specifics.
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
from app.services.analysis.enquiry_planning import (
    EvidenceEnquiryCoordinator,
)
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


def _adj(hid="h1", premise_id=None, supports=True):
    """A test AdjudicationRecord standing in for an authorised HumanReview."""
    return AdjudicationRecord(
        review_id="rev-1", reviewer="credit.officer",
        action="accept_ai_interpretation", hypothesis_id=hid,
        premise_id=premise_id, decision_supports=supports,
        evidence_refs=(), rationale="reviewed",
    )


# ---------------------------------------------------------------------------
# Harness / synthetic corpus
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
    ["Section 1", "The company faces a shortage of skilled machining technicians."],
    ["Section 3", "The company operates a technical training academy for technicians."],
    ["Section 4", "Net leverage was 3.0x at year end."],
]


def _service(db_session, case_id="C1"):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    backend = FakeLLMBackend()
    audit = AuditLog(db_session)
    client = LLMClient(backend, prompts=registry, session=db_session, audit=audit)
    return HypothesisTestingService(
        client, audit=audit, case_id=case_id, review_lookup=db_session
    ), backend


def _seed_adj(db_session, *, hid="h1", premise_id=None, case_id="C1"):
    from app.services.review.workflow import HumanReviewWorkflow, ReviewAction

    workflow = HumanReviewWorkflow(db_session, audit=AuditLog(db_session), case_id=case_id)
    new_value = {"hypothesis_id": hid, "decision": "accepted"}
    if premise_id is not None:
        new_value["premise_id"] = premise_id
    row = workflow.record_action(
        ReviewAction.ACCEPT_AI_INTERPRETATION,
        reviewer="credit.officer", target_type="hypothesis", target_id=hid,
        new_value=new_value, reason="reviewed",
    )
    return AdjudicationRecord.from_human_review(row)


def _hypothesis(hid="h1", **ov):
    base = dict(
        hypothesis_id=hid,
        dimension=CreditDimension.OPERATING_MODEL_COST_RESILIENCE,
        business_characteristic="Dependence on specialised technicians.",
        statement="Training offsets a technician shortage, improving resilience.",
        credit_direction="negative",
        economic_reasoning="A training pipeline reduces reliance on scarce hires.",
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
                     "shortage skilled machining technicians", "e_s"),
            _enquiry(hid, "contradictory", "contradictory_evidence",
                     "technician retention", "e_c"),
            _enquiry(hid, "offsetting", "offsetting_capability",
                     "technical training academy technicians", "e_o"),
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
        "hypothesis_statement": "Training offsets a technician shortage.",
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


def _run(service, backend, result, *, hyp=None, plan=None, items=None,
         adjudications=(), key="k"):
    backend.register("analyze", _response(result), key=key)
    plan = plan or _full_plan()
    bundle = _bundle(plan)
    inputs = HypothesisTestingInputs(
        hypotheses=CreditHypothesisSet(hypotheses=[hyp or _hypothesis()]),
        bundle=bundle, plan=plan, grounding_items=items or [],
        adjudications=adjudications,
    )
    return service.test(inputs, key=key).governed[0], bundle


# ===========================================================================
# THE CORE LOOPHOLE (adversarial)
# ===========================================================================


def test_all_premises_model_verified_but_grounding_not_verifiable_is_not_supported(db_session):
    """1. Model labels every premise VERIFIED; Layer A grounding is not_verifiable.

    This is the exact self-corroboration loophole. The service must NOT return
    SUPPORTED on model self-labelling alone.
    """
    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    exist = _pid(bundle, 2)
    result = _result(
        proposed_status="supported",
        relevant_factual_claims=["Training improves workforce resilience."],
        evidence_assessments=[
            {"passage_id": exist, "assessment": "supports_premise",
             "rationale": "model says training helps"}
        ],
        mechanism_premises=[
            {"premise": "training improves resilience", "status": "verified",
             "supporting_passage_ids": [exist]}
        ],
    )
    backend.register("analyze", _response(result), key="k")
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=plan,
        ),
        key="k",
    ).governed[0]

    assert gov.proposed_status is HypothesisVerificationStatus.SUPPORTED
    assert gov.effective_status is HypothesisVerificationStatus.UNVERIFIED
    assert gov.independently_corroborated is False
    assert gov.model_only_support is True
    assert any("independent corroboration" in r for r in gov.downgrade_reasons)
    assert gov.human_review_required


def test_supports_premise_on_existence_only_is_not_supported(db_session):
    """2. Model marks a passage SUPPORTS_PREMISE though it only shows existence."""
    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    exist = _pid(bundle, 2)  # the "training academy" (existence) passage
    result = _result(
        proposed_status="supported",
        evidence_assessments=[
            {"passage_id": exist, "assessment": "supports_premise",
             "rationale": "model asserts this supports the mechanism"}
        ],
        mechanism_premises=[
            {"premise": "programme exists", "status": "verified",
             "supporting_passage_ids": [exist]}
        ],
    )
    backend.register("analyze", _response(result), key="k")
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=plan,
        ),
        key="k",
    ).governed[0]
    assert gov.effective_status is not HypothesisVerificationStatus.SUPPORTED
    assert gov.independently_corroborated is False


def test_verbatim_excerpt_does_not_establish_outcome(db_session):
    """3. A verbatim excerpt proves the quote exists, not the financial outcome."""
    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    exist = _pid(bundle, 2)
    result = _result(
        proposed_status="supported",
        evidence_assessments=[
            {"passage_id": exist, "assessment": "supports_premise",
             "rationale": "verbatim",
             "quoted_excerpt": "technical training academy"}  # really in the text
        ],
        mechanism_premises=[
            {"premise": "recruitment costs fell", "status": "verified",
             "supporting_passage_ids": [exist], "is_outcome_premise": True},
        ],
    )
    backend.register("analyze", _response(result), key="k")
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=plan,
        ),
        key="k",
    ).governed[0]
    # Excerpt is genuinely in the passage (no reference rejection), yet the
    # outcome claim is only model-asserted -> not SUPPORTED.
    assert gov.effective_status is not HypothesisVerificationStatus.SUPPORTED
    assert gov.independently_corroborated is False


def test_complete_coverage_but_insufficient_evidence_not_supported(db_session):
    """4. Coverage complete, yet no independent factual/economic corroboration."""
    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    sup = _pid(bundle, 1)
    result = _result(
        proposed_status="supported",
        evidence_assessments=[
            {"passage_id": sup, "assessment": "supports_premise", "rationale": "x"}
        ],
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
        ),
        key="k",
    ).governed[0]
    assert gov.coverage_gaps == []  # coverage IS complete
    assert gov.effective_status is HypothesisVerificationStatus.UNVERIFIED
    assert any("independent corroboration" in r for r in gov.downgrade_reasons)


def test_metric_grounds_fact_but_not_causation(db_session):
    """5. A validated metric grounds a numeric premise but NOT the causal premise.

    The leverage figure premise cites the metric and is deterministically
    grounded. The CAUSAL/outcome premise ("training lowered leverage") is a
    separate, model-only premise -> the hypothesis cannot be SUPPORTED, because
    a material premise lacks independent corroboration. Grounding a number never
    auto-proves causation.
    """
    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    sup = _pid(bundle, 1)
    items = [EvidenceItem(evidence_id="metric_leverage", value=3.0, unit="x",
                          concept="net leverage")]
    result = _result(
        proposed_status="supported",
        evidence_assessments=[
            {"passage_id": sup, "assessment": "supports_premise", "rationale": "x"}
        ],
        mechanism_premises=[
            {"premise_id": "fig", "premise": "leverage is 3.0x",
             "status": "verified", "grounding_metric_id": "metric_leverage",
             "claimed_concept": "net leverage",
             "grounding_claim": "Net leverage is 3.0x."},
            {"premise_id": "cause", "premise": "training causally lowered leverage",
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
    assert by_id["fig"].grounding.is_grounded is True       # the figure is grounded
    assert by_id["fig"].corroborated is True
    assert by_id["cause"].corroborated is False             # causation is model-only
    assert gov.effective_status is not HypothesisVerificationStatus.SUPPORTED
    assert "training causally lowered leverage" in gov.uncorroborated_material_premises
    assert gov.human_review_required


def test_management_assertion_not_treated_as_outcome(db_session):
    """6. A management/initiative description is not a demonstrated outcome."""
    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    exist = _pid(bundle, 2)
    result = _result(
        proposed_status="supported",
        evidence_assessments=[
            {"passage_id": exist, "assessment": "describes_initiative",
             "rationale": "management describes a programme"}
        ],
        mechanism_premises=[
            {"premise": "programme exists", "status": "verified",
             "supporting_passage_ids": [exist]},
        ],
    )
    backend.register("analyze", _response(result), key="k")
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=plan,
        ),
        key="k",
    ).governed[0]
    assert gov.effective_status is HypothesisVerificationStatus.UNVERIFIED
    assert gov.human_review_required  # rests on a described initiative only


def test_unresolved_contradiction_blocks_supported(db_session):
    """7. A credible contradictory passage blocks a proposed SUPPORTED."""
    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    sup = _pid(bundle, 1)
    con = _pid(bundle, 2)
    result = _result(
        proposed_status="supported",
        evidence_assessments=[
            {"passage_id": sup, "assessment": "supports_premise", "rationale": "x"},
            {"passage_id": con, "assessment": "contradicts_premise", "rationale": "y"},
        ],
    )
    # Add a material premise so there is something an adjudication can corroborate.
    result["mechanism_premises"] = [
        {"premise": "shortage exists", "status": "verified",
         "supporting_passage_ids": [sup]}
    ]
    backend.register("analyze", _response(result), key="k")
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=plan,
            adjudications=(_seed_adj(db_session),),  # verified adjudication, yet...
        ),
        key="k",
    ).governed[0]
    # ...the unresolved contradiction still blocks SUPPORTED.
    assert gov.effective_status is not HypothesisVerificationStatus.SUPPORTED
    assert any("contradictory" in r for r in gov.downgrade_reasons)


def test_partial_requires_genuine_component_not_model_label(db_session):
    """8. PARTIALLY_SUPPORTED needs independent corroboration, not a model label."""
    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    sup = _pid(bundle, 1)
    result = _result(
        proposed_status="partially_supported",
        evidence_assessments=[
            {"passage_id": sup, "assessment": "supports_premise", "rationale": "x"}
        ],
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
        ),
        key="k",
    ).governed[0]
    assert gov.effective_status is HypothesisVerificationStatus.UNVERIFIED
    assert any("only model judgement present" in r for r in gov.downgrade_reasons)


def test_unverified_floor_when_nothing_established(db_session):
    """9. Neither factual premise nor mechanism established -> UNVERIFIED."""
    service, backend = _service(db_session)
    gov, _ = _run(service, backend, _result(proposed_status="partially_supported"))
    assert gov.effective_status is HypothesisVerificationStatus.UNVERIFIED


def test_human_adjudication_can_support_without_disabling_governance(db_session):
    """10. Genuine corroboration (human) is accepted; other blockers still apply."""
    service, backend = _service(db_session)
    plan = _full_plan()
    bundle = _bundle(plan)
    sup = _pid(bundle, 1)
    # Fully supported path: adjudicated + supporting assessment + verified
    # premise + no contradiction + complete coverage + no unresolved items.
    result = _result(
        proposed_status="supported",
        evidence_assessments=[
            {"passage_id": sup, "assessment": "supports_premise", "rationale": "x"}
        ],
        mechanism_premises=[
            {"premise": "shortage exists", "status": "verified",
             "supporting_passage_ids": [sup]}
        ],
    )
    backend.register("analyze", _response(result), key="ok")
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=plan,
            adjudications=(_seed_adj(db_session),),  # verified authorised review
        ),
        key="ok",
    ).governed[0]
    assert gov.effective_status is HypothesisVerificationStatus.SUPPORTED
    assert "human_adjudication" in gov.corroboration_basis

    # ...but the SAME corroboration does NOT override a contradiction (governance
    # still active) -- covered by test_unresolved_contradiction_blocks_supported.


def test_model_cannot_spoof_corroboration_via_output(db_session):
    """A model result has no field that can set independent corroboration.

    The corroboration flag is computed by the service from deterministic
    grounding + caller-supplied adjudication; the LLM schema exposes no channel
    to assert it.
    """
    from app.schemas.llm import HypothesisTestResult

    fields = set(HypothesisTestResult.model_fields)
    for forbidden in (
        "independently_corroborated", "corroboration_basis",
        "effective_status", "human_adjudicated", "human_review_required",
    ):
        assert forbidden not in fields


def test_second_industry_logistics_model_only_not_supported(db_session):
    """Cross-industry: fuel pass-through, model-only support -> not SUPPORTED."""
    service, backend = _service(db_session)
    log_pages = [
        ["Overview", "Fuel is a large share of operating cost."],
        ["Pricing", "Contracts include fuel surcharge pass-through clauses."],
        ["Risk", "A fuel spike could outpace contractual adjustments."],
    ]
    hyp = _hypothesis(
        hid="log1",
        dimension=CreditDimension.PRICING_POWER_REVENUE_REALISATION,
        business_characteristic="Fuel cost exposure.",
        statement="Surcharge clauses offset fuel inflation, protecting margins.",
    )
    plan = EvidenceEnquiryPlan.model_validate({
        "enquiries": [
            {**_enquiry("log1", "supporting", "supporting_evidence",
                        "fuel surcharge pass-through clauses", "s"),
             "dimension": CreditDimension.PRICING_POWER_REVENUE_REALISATION.value,
             "permitted_document_ids": ["LOG"]},
            {**_enquiry("log1", "contradictory", "contradictory_evidence",
                        "fuel spike outpace adjustments", "c"),
             "dimension": CreditDimension.PRICING_POWER_REVENUE_REALISATION.value,
             "permitted_document_ids": ["LOG"]},
            {**_enquiry("log1", "offsetting", "offsetting_capability",
                        "surcharge clauses", "o"),
             "dimension": CreditDimension.PRICING_POWER_REVENUE_REALISATION.value,
             "permitted_document_ids": ["LOG"]},
        ],
        "planner_mode": "llm", "incomplete_coverage": [],
    })
    corpus = build_corpus_from_pdf("LOG", _pdf(log_pages))
    bundle = EvidenceEnquiryCoordinator(EvidenceSearchService(corpus)).execute(plan)
    clause = next(
        rp.passage.passage_id for ex in bundle.executed
        for rp in ex.result.ranked_passages if rp.passage.pdf_page == 2
    )
    result = _result(
        hid="log1",
        dimension=CreditDimension.PRICING_POWER_REVENUE_REALISATION.value,
        hypothesis_statement="Surcharge clauses offset fuel inflation.",
        proposed_status="supported",
        evidence_assessments=[
            {"passage_id": clause, "assessment": "describes_initiative",
             "rationale": "clauses exist"}
        ],
        mechanism_premises=[
            {"premise": "surcharge clauses exist", "status": "verified",
             "supporting_passage_ids": [clause]},
            {"premise": "pass-through materially protected margins",
             "status": "verified", "supporting_passage_ids": [clause],
             "is_outcome_premise": True},
        ],
    )
    backend.register("analyze", _response(result), key="log")
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[hyp]),
            bundle=bundle, plan=plan,
        ),
        key="log",
    ).governed[0]
    assert gov.dimension is CreditDimension.PRICING_POWER_REVENUE_REALISATION
    # Clause existence is model-asserted; outcome is model-asserted; no
    # independent corroboration -> not SUPPORTED.
    assert gov.effective_status is HypothesisVerificationStatus.UNVERIFIED
