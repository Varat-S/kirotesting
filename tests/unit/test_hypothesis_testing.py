"""Semantic hypothesis-testing service tests (Stage 8, Task 6).

Two clearly separated kinds of test:
* VALIDATION / GOVERNANCE LOGIC -- the service's INDEPENDENT enforcement
  (reference checks, coverage recomputation, conservative downgrades, REJECTED
  discipline, immutability, provenance). These are the substantive guarantees.
* SCRIPTED SEMANTIC JUDGEMENT -- where FakeLLMBackend supplies a proposed
  assessment. These exercise plumbing/mapping only.

Scope honesty: FakeLLMBackend returns scripted JSON. These tests do NOT and
cannot establish real-world semantic accuracy of a live model; they prove the
service never lets a plausible story bypass the governance gate. Two fictional
industries are used (a manufacturer; a SaaS provider with customer concentration).
"""

from __future__ import annotations

import io

import pytest
from reportlab.pdfgen.canvas import Canvas

from app.prompts.registry import PromptRegistry
from app.prompts.templates import HYPOTHESIS_TESTING_TEMPLATE, PROMPT_CATALOGUE
from app.schemas.llm import (
    CreditDimension,
    CreditHypothesis,
    CreditHypothesisSet,
    EvidenceEnquiryPlan,
    HypothesisVerificationStatus,
)
from app.services.analysis.enquiry_planning import (
    EnquiryBudget,
    EvidenceEnquiryCoordinator,
)
from app.services.analysis.evidence_retrieval import (
    EvidenceSearchService,
    build_corpus_from_pdf,
)
from app.services.analysis.hypothesis_testing import (
    HYPOTHESIS_TESTING_REJECTED_EVENT,
    AdjudicationRecord,
    HypothesisTestingInputs,
    HypothesisTestingRejectedError,
    HypothesisTestingService,
)
from app.services.analysis.grounding import EvidenceItem


def _adj(hid="h1", premise_id=None, supports=True):
    """A test AdjudicationRecord standing in for an authorised HumanReview."""
    return AdjudicationRecord(
        review_id="rev-1", reviewer="credit.officer",
        action="accept_ai_interpretation", hypothesis_id=hid,
        premise_id=premise_id, decision_supports=supports,
        evidence_refs=(), rationale="reviewed",
    )
from app.services.audit.log import AuditLog
from app.services.llm.client import FakeLLMBackend, LLMClient


# ---------------------------------------------------------------------------
# Harness, synthetic corpus, bundle construction
# ---------------------------------------------------------------------------


def _pdf(pages: list[list[str]]) -> bytes:
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
    ["Section 4", "Staffing costs rose; voluntary technician retention improved materially."],
]


def _service(db_session, case_id="C1"):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    backend = FakeLLMBackend()
    audit = AuditLog(db_session)
    client = LLMClient(backend, prompts=registry, session=db_session, audit=audit)
    service = HypothesisTestingService(
        client, audit=audit, case_id=case_id, review_lookup=db_session
    )
    return service, backend, audit


def _seed_adj(db_session, *, hid="h1", premise_id=None, case_id="C1"):
    """Create a real authorised HumanReview and return its AdjudicationRecord."""
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


def _hypothesis(hid="h1", **overrides) -> CreditHypothesis:
    base = dict(
        hypothesis_id=hid,
        dimension=CreditDimension.OPERATING_MODEL_COST_RESILIENCE,
        business_characteristic="Dependence on specialised technicians.",
        statement="A technician shortage could constrain capacity and margins.",
        credit_direction="negative",
        economic_reasoning="Labour scarcity raises cost and caps output.",
        potential_credit_impact="Margin and CFO volatility.",
        classification="inference",
        verification_status="unverified",
    )
    base.update(overrides)
    return CreditHypothesis(**base)


def _enquiry(hid, kind, intent, query, eid):
    return {
        "enquiry_id": eid,
        "hypothesis_id": hid,
        "dimension": CreditDimension.OPERATING_MODEL_COST_RESILIENCE.value,
        "requirement_kind": kind,
        "search_intent": intent,
        "investigative_question": f"{kind}?",
        "search_query": query,
        "rationale": "r",
        "evidence_requirement": "req",
        "priority": 2,
        "permitted_document_ids": ["MFG"],
    }


def _full_plan(hid="h1") -> EvidenceEnquiryPlan:
    """A coverage-complete plan for a negative hypothesis (sup+con+offset)."""
    return EvidenceEnquiryPlan.model_validate(
        {
            "enquiries": [
                _enquiry(hid, "supporting", "supporting_evidence",
                         "shortage skilled machining technicians", "e_sup"),
                _enquiry(hid, "contradictory", "contradictory_evidence",
                         "technician retention improved", "e_con"),
                _enquiry(hid, "offsetting", "offsetting_capability",
                         "technical training academy technicians", "e_off"),
            ],
            "planner_mode": "llm",
            "incomplete_coverage": [],
        }
    )


def _bundle(plan: EvidenceEnquiryPlan, pages=MFG_PAGES):
    corpus = build_corpus_from_pdf("MFG", _pdf(pages))
    coordinator = EvidenceEnquiryCoordinator(EvidenceSearchService(corpus))
    return coordinator.execute(plan), corpus


def _passage_ids(bundle) -> dict[str, int]:
    """Map passage_id -> pdf_page across all retrieved passages."""
    return {
        rp.passage.passage_id: rp.passage.pdf_page
        for ex in bundle.executed
        for rp in ex.result.ranked_passages
    }


def _pid_on_page(bundle, page: int) -> str:
    for ex in bundle.executed:
        for rp in ex.result.ranked_passages:
            if rp.passage.pdf_page == page:
                return rp.passage.passage_id
    raise AssertionError(f"no passage retrieved on page {page}")


def _result(hid="h1", **overrides) -> dict:
    base = {
        "hypothesis_id": hid,
        "dimension": CreditDimension.OPERATING_MODEL_COST_RESILIENCE.value,
        "hypothesis_statement": "A technician shortage could constrain capacity.",
        "evidence_assessments": [],
        "relevant_factual_claims": [],
        "proposed_mechanism": "Training offsets technician scarcity.",
        "mechanism_premises": [],
        "alternative_explanations": [],
        "offsetting_capabilities": [],
        "offset_effectiveness_evidence": [],
        "proposed_status": "unverified",
        "justification": "j",
        "unresolved_evidence_requirements": [],
        "interpretation_confidence": 0.6,
    }
    base.update(overrides)
    return base


def _response(*results: dict) -> dict:
    return {"results": list(results)}


# ===========================================================================
# VALIDATION / GOVERNANCE LOGIC
# ===========================================================================


def test_unknown_passage_id_is_rejected(db_session):
    service, backend, _ = _service(db_session)
    bundle, _ = _bundle(_full_plan())
    backend.register(
        "analyze",
        _response(_result(evidence_assessments=[
            {"passage_id": "ghost", "assessment": "supports_premise", "rationale": "r"}
        ])),
        key="k",
    )
    with pytest.raises(HypothesisTestingRejectedError) as e:
        service.test(
            HypothesisTestingInputs(hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
                                    bundle=bundle, plan=_full_plan()),
            key="k",
        )
    assert e.value.stage == "service_governance"


def test_quoted_excerpt_must_be_in_passage(db_session):
    service, backend, _ = _service(db_session)
    bundle, _ = _bundle(_full_plan())
    pid = _pid_on_page(bundle, 2)  # the training-academy passage
    backend.register(
        "analyze",
        _response(_result(evidence_assessments=[
            {"passage_id": pid, "assessment": "describes_initiative",
             "rationale": "r", "quoted_excerpt": "a sentence that is not present"}
        ])),
        key="k",
    )
    with pytest.raises(HypothesisTestingRejectedError) as e:
        service.test(
            HypothesisTestingInputs(hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
                                    bundle=bundle, plan=_full_plan()),
            key="k",
        )
    assert e.value.stage == "service_governance"
    types = [ev.event_type for ev in AuditLog(db_session).events_for_case("C1")]
    assert HYPOTHESIS_TESTING_REJECTED_EVENT in types


def test_supported_blocked_by_incomplete_coverage(db_session):
    """A directly constructed plan missing the offset perspective cannot be SUPPORTED."""
    service, backend, _ = _service(db_session)
    # Plan WITHOUT the offsetting enquiry -> coverage gap for a negative hypothesis.
    partial = EvidenceEnquiryPlan.model_validate(
        {
            "enquiries": [
                _enquiry("h1", "supporting", "supporting_evidence",
                         "shortage skilled machining technicians", "e_sup"),
                _enquiry("h1", "contradictory", "contradictory_evidence",
                         "retention", "e_con"),
            ],
            "planner_mode": "llm",
            "incomplete_coverage": [],
        }
    )
    bundle, _ = _bundle(partial)
    pid = _pid_on_page(bundle, 1)
    backend.register(
        "analyze",
        _response(_result(
            proposed_status="supported",
            evidence_assessments=[
                {"passage_id": pid, "assessment": "supports_premise", "rationale": "r"}
            ],
            mechanism_premises=[
                {"premise": "shortage exists", "status": "verified",
                 "supporting_passage_ids": [pid]}
            ],
        )),
        key="k",
    )
    result = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=partial,
        ),
        key="k",
    )
    gov = result.governed[0]
    assert gov.proposed_status is HypothesisVerificationStatus.SUPPORTED
    assert gov.effective_status is not HypothesisVerificationStatus.SUPPORTED
    assert any("coverage is incomplete" in r for r in gov.downgrade_reasons)
    assert gov.coverage_gaps  # offsetting missing


def test_directly_constructed_plan_without_plan_arg_still_checks_coverage(db_session):
    """Even with no plan passed, coverage is recomputed from the bundle."""
    service, backend, _ = _service(db_session)
    partial = EvidenceEnquiryPlan.model_validate(
        {
            "enquiries": [
                _enquiry("h1", "supporting", "supporting_evidence",
                         "shortage skilled machining technicians", "e_sup"),
            ],
            "planner_mode": "llm",
            "incomplete_coverage": [],
        }
    )
    bundle, _ = _bundle(partial)
    pid = _pid_on_page(bundle, 1)
    backend.register(
        "analyze",
        _response(_result(
            proposed_status="supported",
            evidence_assessments=[
                {"passage_id": pid, "assessment": "supports_premise", "rationale": "r"}
            ],
        )),
        key="k",
    )
    # NOTE: plan=None -> coverage reconstructed from the executed bundle.
    result = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]), bundle=bundle,
        ),
        key="k",
    )
    gov = result.governed[0]
    assert gov.effective_status is not HypothesisVerificationStatus.SUPPORTED
    # Missing contradictory + offsetting detected without a plan object.
    missing = {g["missing_perspective"] for g in gov.coverage_gaps}
    assert {"contradictory", "offsetting"} <= missing


def test_existence_without_outcome_cannot_be_supported(db_session):
    """An initiative exists but its effectiveness is unverified -> not SUPPORTED."""
    service, backend, _ = _service(db_session)
    bundle, _ = _bundle(_full_plan())
    exist_pid = _pid_on_page(bundle, 2)
    backend.register(
        "analyze",
        _response(_result(
            proposed_status="supported",
            evidence_assessments=[
                {"passage_id": exist_pid, "assessment": "describes_initiative",
                 "rationale": "programme exists"}
            ],
            mechanism_premises=[
                {"premise": "training academy exists", "status": "verified",
                 "supporting_passage_ids": [exist_pid]},
                {"premise": "staffing shortage measurably improved",
                 "status": "unverified", "is_outcome_premise": True},
            ],
        )),
        key="k",
    )
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=_full_plan(),
        ),
        key="k",
    ).governed[0]
    # Not SUPPORTED. With only model-asserted support (no independent
    # corroboration) and an unverified outcome premise, the conservative floor
    # is UNVERIFIED (Task 6A).
    assert gov.effective_status is HypothesisVerificationStatus.UNVERIFIED
    assert gov.independently_corroborated is False
    assert any(
        "existence != effect" in r or "outcome" in r or "independent corroboration" in r
        for r in gov.downgrade_reasons
    )


def test_documented_outcome_premise_separate_from_existence(db_session):
    """Existence and measured-outcome assessments are tracked distinctly."""
    service, backend, _ = _service(db_session)
    bundle, _ = _bundle(_full_plan())
    exist = _pid_on_page(bundle, 2)
    outcome = _pid_on_page(bundle, 3)
    backend.register(
        "analyze",
        _response(_result(
            proposed_status="partially_supported",
            evidence_assessments=[
                {"passage_id": exist, "assessment": "describes_initiative", "rationale": "x"},
                {"passage_id": outcome, "assessment": "measured_outcome", "rationale": "y"},
            ],
            mechanism_premises=[
                {"premise_id": "exists", "premise": "academy exists",
                 "status": "verified", "supporting_passage_ids": [exist]},
                {"premise_id": "outcome", "premise": "retention improved",
                 "status": "partially_verified", "supporting_passage_ids": [outcome],
                 "is_outcome_premise": True},
            ],
        )),
        key="k",
    )
    # Authorised adjudication (a REAL review) corroborates ONLY the existence
    # premise; the outcome premise remains model-asserted. Existence and outcome
    # are tracked distinctly -> PARTIALLY_SUPPORTED (not SUPPORTED).
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=_full_plan(),
            adjudications=(_seed_adj(db_session, premise_id="exists"),),
        ),
        key="k",
    ).governed[0]
    kinds = {a.assessment.value for a in gov.evidence_assessments}
    assert "describes_initiative" in kinds and "measured_outcome" in kinds
    assert gov.effective_status is HypothesisVerificationStatus.PARTIALLY_SUPPORTED
    assert "retention improved" in gov.uncorroborated_material_premises


def test_rejected_requires_contradiction_not_absence(db_session):
    """A proposed REJECTED with no contradicting assessment -> UNVERIFIED."""
    service, backend, _ = _service(db_session)
    bundle, _ = _bundle(_full_plan())
    backend.register(
        "analyze",
        _response(_result(
            proposed_status="rejected",
            unresolved_evidence_requirements=["no data found"],
        )),
        key="k",
    )
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=_full_plan(),
        ),
        key="k",
    ).governed[0]
    assert gov.effective_status is HypothesisVerificationStatus.UNVERIFIED
    assert any("not grounds for rejection" in r for r in gov.downgrade_reasons)


def test_rejected_allowed_with_real_contradiction(db_session):
    service, backend, _ = _service(db_session)
    bundle, _ = _bundle(_full_plan())
    pid = _pid_on_page(bundle, 3)
    backend.register(
        "analyze",
        _response(_result(
            proposed_status="rejected",
            evidence_assessments=[
                {"passage_id": pid, "assessment": "contradicts_premise",
                 "rationale": "retention actually improved, counter to shortage risk"}
            ],
        )),
        key="k",
    )
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=_full_plan(),
        ),
        key="k",
    ).governed[0]
    assert gov.effective_status is HypothesisVerificationStatus.REJECTED


def test_material_contradiction_blocks_supported(db_session):
    service, backend, _ = _service(db_session)
    bundle, _ = _bundle(_full_plan())
    sup = _pid_on_page(bundle, 1)
    con = _pid_on_page(bundle, 3)
    backend.register(
        "analyze",
        _response(_result(
            proposed_status="supported",
            evidence_assessments=[
                {"passage_id": sup, "assessment": "supports_premise", "rationale": "x"},
                {"passage_id": con, "assessment": "contradicts_premise", "rationale": "y"},
            ],
            mechanism_premises=[
                {"premise": "shortage exists", "status": "verified",
                 "supporting_passage_ids": [sup]}
            ],
        )),
        key="k",
    )
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=_full_plan(),
        ),
        key="k",
    ).governed[0]
    assert gov.effective_status is not HypothesisVerificationStatus.SUPPORTED
    assert gov.human_review_required


def test_insufficient_info_is_unverified_not_rejected(db_session):
    service, backend, _ = _service(db_session)
    bundle, _ = _bundle(_full_plan())
    backend.register(
        "analyze",
        _response(_result(
            proposed_status="unverified",
            unresolved_evidence_requirements=["need outcome data"],
        )),
        key="k",
    )
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=_full_plan(),
        ),
        key="k",
    ).governed[0]
    assert gov.effective_status is HypothesisVerificationStatus.UNVERIFIED


def test_no_match_does_not_establish_absence(db_session):
    """A no-match offset search is unresolved, never a rejection or 'absent'."""
    service, backend, _ = _service(db_session)
    # Offset query that will not match the corpus terms.
    plan = EvidenceEnquiryPlan.model_validate(
        {
            "enquiries": [
                _enquiry("h1", "supporting", "supporting_evidence",
                         "shortage skilled machining technicians", "e_sup"),
                _enquiry("h1", "contradictory", "contradictory_evidence",
                         "retention improved", "e_con"),
                _enquiry("h1", "offsetting", "offsetting_capability",
                         "zzzxylophonequarknonsense", "e_off"),
            ],
            "planner_mode": "llm", "incomplete_coverage": [],
        }
    )
    bundle, _ = _bundle(plan)
    assert bundle.unresolved_enquiries  # offset enquiry returned no match
    backend.register(
        "analyze",
        _response(_result(proposed_status="rejected")),  # no contradiction present
        key="k",
    )
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=plan,
        ),
        key="k",
    ).governed[0]
    # Absence of an offset match is NOT a rejection.
    assert gov.effective_status is HypothesisVerificationStatus.UNVERIFIED


def test_citation_alone_is_not_factual_grounding(db_session):
    """Layer A: a premise grounded only against a TEXT passage is not grounded.

    The premise cites a real passage and has a narrative grounding_claim; the
    deterministic evaluator returns not_verifiable (no numeric/pre-stated
    verdict), so is_grounded is False and the premise is not corroborated.
    """
    service, backend, _ = _service(db_session)
    bundle, _ = _bundle(_full_plan())
    pid = _pid_on_page(bundle, 2)
    backend.register(
        "analyze",
        _response(_result(
            mechanism_premises=[
                {"premise_id": "exists", "premise": "a training academy exists",
                 "status": "verified", "supporting_passage_ids": [pid],
                 "grounding_claim": "The company operates a training academy."}
            ],
        )),
        key="k",
    )
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=_full_plan(),
        ),
        key="k",
    ).governed[0]
    pc = gov.premise_corroboration[0]
    assert pc.grounding is not None
    assert pc.grounding.citation_present is True
    assert pc.grounding.is_grounded is False  # text-only -> not_verifiable
    assert pc.corroborated is False


def test_invalid_hypothesis_reference_rejected(db_session):
    service, backend, _ = _service(db_session)
    bundle, _ = _bundle(_full_plan())
    backend.register("analyze", _response(_result(hid="ghost")), key="k")
    with pytest.raises(HypothesisTestingRejectedError) as e:
        service.test(
            HypothesisTestingInputs(
                hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
                bundle=bundle, plan=_full_plan(),
            ),
            key="k",
        )
    assert e.value.stage == "service_governance"


def test_malformed_output_rejected(db_session):
    service, backend, _ = _service(db_session)
    bundle, _ = _bundle(_full_plan())
    backend.register("analyze", "not json", key="k")
    with pytest.raises(HypothesisTestingRejectedError) as e:
        service.test(
            HypothesisTestingInputs(
                hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
                bundle=bundle, plan=_full_plan(),
            ),
            key="k",
        )
    assert e.value.stage == "json_schema"


def test_original_hypothesis_unchanged(db_session):
    service, backend, _ = _service(db_session)
    hset = CreditHypothesisSet(hypotheses=[_hypothesis()])
    bundle, _ = _bundle(_full_plan())
    pid = _pid_on_page(bundle, 1)
    backend.register(
        "analyze",
        _response(_result(
            proposed_status="supported",
            evidence_assessments=[
                {"passage_id": pid, "assessment": "supports_premise", "rationale": "x"}
            ],
        )),
        key="k",
    )
    gov = service.test(
        HypothesisTestingInputs(hypotheses=hset, bundle=bundle, plan=_full_plan()),
        key="k",
    ).governed[0]
    # The generated hypothesis is untouched; its status stays UNVERIFIED.
    assert hset.hypotheses[0].verification_status is HypothesisVerificationStatus.UNVERIFIED
    assert gov.generation_status is HypothesisVerificationStatus.UNVERIFIED
    assert gov.hypothesis_id == "h1"


def test_provenance_retained(db_session):
    service, backend, _ = _service(db_session)
    bundle, _ = _bundle(_full_plan())
    backend.register("analyze", _response(_result()), key="k")
    result = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=_full_plan(),
        ),
        key="k",
    )
    assert result.run.prompt_id == "hypothesis_testing_v1.0"
    assert result.governed[0].testing_run_id == result.run.run_id


def test_grounding_items_scope_not_widened(db_session):
    """A metric grounds a premise ONLY when that premise explicitly cites it.

    Task 6B: a supplied metric is no longer pooled across all claims. A premise
    that cites metric_x and claims "3.0x" is grounded; a premise that does NOT
    cite the metric (text passage only) is not grounded by it.
    """
    service, backend, _ = _service(db_session)
    bundle, _ = _bundle(_full_plan())
    pid = _pid_on_page(bundle, 1)
    items = [EvidenceItem(evidence_id="metric_x", value=3.0, unit="x",
                          concept="net leverage")]
    backend.register(
        "analyze",
        _response(_result(
            mechanism_premises=[
                {"premise_id": "lev", "premise": "leverage is 3.0x",
                 "status": "verified", "grounding_metric_id": "metric_x",
                 "claimed_concept": "net leverage",
                 "grounding_claim": "Net leverage is 3.0x."},
                {"premise_id": "cov", "premise": "coverage is high",
                 "status": "verified", "supporting_passage_ids": [pid],
                 "grounding_claim": "Coverage is 9.9x."},
            ],
        )),
        key="k",
    )
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hypothesis()]),
            bundle=bundle, plan=_full_plan(), grounding_items=items,
        ),
        key="k",
    ).governed[0]
    by_id = {pc.premise_id: pc for pc in gov.premise_corroboration}
    assert by_id["lev"].grounding.is_grounded is True   # cites the metric
    assert by_id["cov"].grounding.is_grounded is False  # text passage only


# ===========================================================================
# CROSS-INDUSTRY (different evidence assessments)
# ===========================================================================


def test_second_industry_customer_concentration(db_session):
    service, backend, _ = _service(db_session)
    saas_pages = [
        ["Risk", "A single customer accounts for a large share of recurring revenue."],
        ["Retention", "Net revenue retention exceeded 120 percent across cohorts."],
    ]
    hyp = _hypothesis(
        hid="saas1",
        dimension=CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION,
        business_characteristic="Customer concentration.",
        statement="Concentration risks a revenue cliff on churn.",
    )
    plan = EvidenceEnquiryPlan.model_validate(
        {
            "enquiries": [
                {**_enquiry("saas1", "supporting", "supporting_evidence",
                            "single customer large share recurring revenue", "s"),
                 "dimension": CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION.value,
                 "permitted_document_ids": ["SAAS"]},
                {**_enquiry("saas1", "contradictory", "contradictory_evidence",
                            "net revenue retention cohorts", "c"),
                 "dimension": CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION.value,
                 "permitted_document_ids": ["SAAS"]},
                {**_enquiry("saas1", "offsetting", "offsetting_capability",
                            "net revenue retention switching costs", "o"),
                 "dimension": CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION.value,
                 "permitted_document_ids": ["SAAS"]},
            ],
            "planner_mode": "llm", "incomplete_coverage": [],
        }
    )
    corpus = build_corpus_from_pdf("SAAS", _pdf(saas_pages))
    bundle = EvidenceEnquiryCoordinator(EvidenceSearchService(corpus)).execute(plan)
    conc = _pid_on_page(bundle, 1)
    retention = _pid_on_page(bundle, 2)
    backend.register(
        "analyze",
        _response(_result(
            hid="saas1",
            dimension=CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION.value,
            hypothesis_statement="Concentration risks a revenue cliff on churn.",
            proposed_status="partially_supported",
            evidence_assessments=[
                {"passage_id": conc, "assessment": "supports_premise",
                 "rationale": "concentration confirmed"},
                {"passage_id": retention, "assessment": "contradicts_premise",
                 "rationale": "high NRR mitigates churn risk"},
            ],
            mechanism_premises=[
                {"premise": "revenue is concentrated", "status": "verified",
                 "supporting_passage_ids": [conc]},
            ],
        )),
        key="k",
    )
    gov = service.test(
        HypothesisTestingInputs(hypotheses=CreditHypothesisSet(hypotheses=[hyp]),
                                bundle=bundle, plan=plan),
        key="k",
    ).governed[0]
    assert gov.dimension is CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION
    kinds = {a.assessment.value for a in gov.evidence_assessments}
    assert {"supports_premise", "contradicts_premise"} <= kinds
    assert gov.human_review_required  # contradiction present


# ===========================================================================
# PROMPT + REGISTRATION
# ===========================================================================


def test_prompt_registers(db_session):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    p = registry.latest("hypothesis_testing")
    assert p is not None and p.prompt_id == "hypothesis_testing_v1.0"
    assert [e["name"] for e in PROMPT_CATALOGUE].count("hypothesis_testing") == 1


def test_prompt_text_separates_existence_from_effectiveness():
    text = HYPOTHESIS_TESTING_TEMPLATE.lower()
    assert "existence" in text and "effectiveness" in text
    assert "judge the text, not the query" in text
    assert "not evidence of absence" in text or "not grounds to reject" in text


def test_prompt_text_industry_general():
    text = HYPOTHESIS_TESTING_TEMPLATE.lower()
    for banned in ("hca", "galen", "hospital", "nursing"):
        assert banned not in text
