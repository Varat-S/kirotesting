"""Evidence enquiry planning + coordination tests (Stage 5, Task 5).

Test groups (kept explicitly separate):
* EXECUTABLE VALIDATION -- real registry/client/schema path for the planner
  (registration/hash, schema-valid plan accepted, malformed/governance-invalid
  rejected, bounds enforced, hypothesis status untouched).
* RETRIEVAL INTEGRATION -- the coordinator runs plans through the real Task-4
  EvidenceSearchService over synthetic PDFs; grouping, provenance, no-match /
  unsearchable / unavailable handling, determinism, cross-section discovery.
* PROMPT INSTRUCTION -- assertions on the template STRING only.

Scope honesty: FakeLLMBackend supplies scripted enquiries, so these prove
plumbing/validation/retrieval wiring -- NOT live-model enquiry reasoning. Two
fictional industries are used (a manufacturer and a logistics carrier); no HCA
specifics.
"""

from __future__ import annotations

import io

import pytest
from reportlab.pdfgen.canvas import Canvas

from app.prompts.registry import PromptRegistry
from app.prompts.templates import (
    EVIDENCE_ENQUIRY_PLANNING_TEMPLATE,
    PROMPT_CATALOGUE,
)
from app.schemas.evidence import EvidenceSearchIntent, EvidenceSearchOutcome
from app.schemas.llm import (
    CreditDimension,
    CreditHypothesis,
    CreditHypothesisSet,
    EvidenceEnquiryPlan,
    EvidenceRequirementKind,
    HypothesisVerificationStatus,
)
from app.services.analysis.enquiry_planning import (
    ENQUIRY_PLANNING_REJECTED_EVENT,
    EnquiryBudget,
    EnquiryPlanningInputs,
    EvidenceEnquiryCoordinator,
    EvidenceEnquiryPlanningRejectedError,
    EvidenceEnquiryPlanningService,
)
from app.services.analysis.evidence_retrieval import (
    CorpusBuilder,
    EvidenceSearchService,
    build_corpus_from_pdf,
)
from app.services.audit.log import AuditLog
from app.services.llm.client import FakeLLMBackend, LLMClient

PROMPT_NAME = "evidence_enquiry_planning"


# ---------------------------------------------------------------------------
# Harness + fixtures
# ---------------------------------------------------------------------------


def _service(db_session, case_id="C1", budget=None):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    backend = FakeLLMBackend()
    audit = AuditLog(db_session)
    client = LLMClient(backend, prompts=registry, session=db_session, audit=audit)
    service = EvidenceEnquiryPlanningService(
        client, audit=audit, case_id=case_id, budget=budget
    )
    return service, backend, audit


def _hypothesis(hid="h1", **overrides) -> CreditHypothesis:
    base = dict(
        hypothesis_id=hid,
        dimension=CreditDimension.OPERATING_MODEL_COST_RESILIENCE,
        business_characteristic="Dependence on specialised machining technicians.",
        statement="A technician shortage could constrain capacity and margins.",
        credit_direction="negative",
        economic_reasoning="Labour scarcity raises cost and caps output.",
        potential_credit_impact="Margin and CFO volatility.",
        classification="inference",
        verification_status="unverified",
    )
    base.update(overrides)
    return CreditHypothesis(**base)


def _hset(*h: CreditHypothesis) -> CreditHypothesisSet:
    return CreditHypothesisSet(hypotheses=list(h) or [_hypothesis()])


def _enquiry(hid="h1", **overrides) -> dict:
    base = {
        "enquiry_id": "q1",
        "hypothesis_id": hid,
        "dimension": CreditDimension.OPERATING_MODEL_COST_RESILIENCE.value,
        "requirement_kind": "offsetting",
        "search_intent": "offsetting_capability",
        "investigative_question": "Does the firm run a technician training programme?",
        "search_query": "apprenticeship training academy technicians",
        "rationale": "A training pipeline could offset the technician shortage.",
        "evidence_requirement": "existence of a workforce initiative",
        "priority": 2,
        "permitted_document_ids": ["MFG"],
    }
    base.update(overrides)
    return base


def _plan(*enquiries: dict, planner_mode="llm", incomplete=None) -> dict:
    return {
        "enquiries": list(enquiries),
        "planner_mode": planner_mode,
        "incomplete_coverage": incomplete or [],
    }


def _plan_obj(*enquiries: dict, hid="h1", docs=("MFG",)) -> EvidenceEnquiryPlan:
    """Build a coverage-COMPLETE EvidenceEnquiryPlan directly for coordinator tests.

    The coordinator takes a validated plan; its tests should not be gated by the
    planner's coverage policy. We pad with the mandatory perspectives for a
    negative-no-offset hypothesis (supporting + contradictory + offsetting) so
    the plan is complete, then append the test's specific enquiries.
    """
    docs = list(docs)
    base = [
        _enquiry(hid, enquiry_id=f"cov_sup_{hid}", requirement_kind="supporting",
                 search_intent="supporting_evidence", search_query="supporting terms",
                 permitted_document_ids=docs),
        _enquiry(hid, enquiry_id=f"cov_con_{hid}", requirement_kind="contradictory",
                 search_intent="contradictory_evidence", search_query="contradictory terms",
                 permitted_document_ids=docs),
        _enquiry(hid, enquiry_id=f"cov_off_{hid}", requirement_kind="offsetting",
                 search_intent="offsetting_capability", search_query="offset terms",
                 permitted_document_ids=docs),
    ]
    return EvidenceEnquiryPlan.model_validate(_plan(*base, *enquiries))


# Synthetic multi-section PDFs (native text).
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


MANUFACTURER_PAGES = [
    [
        "Section 1 Operations",
        "The company faces a shortage of skilled machining technicians.",
    ],
    [
        "Section 2 Governance",
        "The board met four times during the year.",
    ],
    [
        "Section 3 Workforce",
        "The company operates a technical training academy and apprenticeship programme.",
    ],
    [
        "Section 4 Employee Costs",
        "Staffing costs rose and voluntary employee retention improved year on year.",
    ],
]
LOGISTICS_PAGES = [
    [
        "Overview",
        "Fuel is a large share of total operating cost for the carrier.",
    ],
    [
        "Risk",
        "A fuel price spike would raise unit costs faster than contract rates adjust.",
    ],
]


def _mfg_pdf() -> bytes:
    return _pdf(MANUFACTURER_PAGES)


# ===========================================================================
# EXECUTABLE VALIDATION
# ===========================================================================


def test_prompt_registers_versioned_and_hashed(db_session):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    prompt = registry.latest(PROMPT_NAME)
    assert prompt is not None
    assert prompt.prompt_id == "evidence_enquiry_planning_v1.0"
    assert prompt.version == 1
    assert prompt.content_hash
    assert [e["name"] for e in PROMPT_CATALOGUE].count(PROMPT_NAME) == 1


def test_existing_prompts_unaffected(db_session):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    for name in ("qualitative_extraction", "business_analysis", "challenge",
                 "hypothesis_generation"):
        p = registry.latest(name)
        assert p is not None and p.version == 1


def test_valid_plan_is_accepted(db_session):
    service, backend, _ = _service(db_session)
    backend.register(
        "analyze",
        _plan(
            _enquiry(enquiry_id="q1", requirement_kind="supporting",
                     search_intent="supporting_evidence"),
            _enquiry(enquiry_id="q2", requirement_kind="contradictory",
                     search_intent="contradictory_evidence"),
            _enquiry(enquiry_id="q3", requirement_kind="offsetting",
                     search_intent="offsetting_capability"),
        ),
        key="ok",
    )
    result = service.plan(
        EnquiryPlanningInputs(hypotheses=_hset(), permitted_document_ids=["MFG"]),
        key="ok",
    )
    assert result.plan.planner_mode == "llm"
    assert len(result.plan.enquiries) == 3
    assert not result.plan.incomplete_coverage  # negative hypothesis fully covered
    assert result.run.prompt_id == "evidence_enquiry_planning_v1.0"


def test_malformed_json_rejected(db_session):
    service, backend, _ = _service(db_session)
    backend.register("analyze", "not json", key="bad")
    with pytest.raises(EvidenceEnquiryPlanningRejectedError) as e:
        service.plan(EnquiryPlanningInputs(hypotheses=_hset(),
                                           permitted_document_ids=["MFG"]), key="bad")
    assert e.value.stage == "json_schema"


def test_structurally_invalid_rejected(db_session):
    service, backend, _ = _service(db_session)
    bad = _enquiry()
    del bad["search_query"]  # required
    backend.register("analyze", _plan(bad), key="bad")
    with pytest.raises(EvidenceEnquiryPlanningRejectedError) as e:
        service.plan(EnquiryPlanningInputs(hypotheses=_hset(),
                                           permitted_document_ids=["MFG"]), key="bad")
    assert e.value.stage == "json_schema"


def test_unknown_hypothesis_reference_rejected(db_session):
    service, backend, audit = _service(db_session)
    backend.register("analyze", _plan(_enquiry(hid="ghost")), key="ghost")
    with pytest.raises(EvidenceEnquiryPlanningRejectedError) as e:
        service.plan(EnquiryPlanningInputs(hypotheses=_hset(),
                                           permitted_document_ids=["MFG"]), key="ghost")
    assert e.value.stage == "service_governance"
    assert ENQUIRY_PLANNING_REJECTED_EVENT in [
        ev.event_type for ev in audit.events_for_case("C1")
    ]


def test_unpermitted_document_scope_rejected(db_session):
    service, backend, _ = _service(db_session)
    backend.register(
        "analyze", _plan(_enquiry(permitted_document_ids=["SECRET_DOC"])), key="scope"
    )
    with pytest.raises(EvidenceEnquiryPlanningRejectedError) as e:
        service.plan(
            EnquiryPlanningInputs(hypotheses=_hset(), permitted_document_ids=["MFG"]),
            key="scope",
        )
    assert e.value.stage == "service_governance"


def test_duplicate_enquiry_ids_rejected(db_session):
    service, backend, _ = _service(db_session)
    backend.register(
        "analyze", _plan(_enquiry(enquiry_id="dup"), _enquiry(enquiry_id="dup")),
        key="dup",
    )
    with pytest.raises(EvidenceEnquiryPlanningRejectedError) as e:
        service.plan(EnquiryPlanningInputs(hypotheses=_hset(),
                                           permitted_document_ids=["MFG"]), key="dup")
    assert e.value.stage == "service_governance"


def test_per_hypothesis_budget_enforced(db_session):
    service, backend, _ = _service(
        db_session, budget=EnquiryBudget(max_enquiries_per_hypothesis=1)
    )
    backend.register(
        "analyze",
        _plan(_enquiry(enquiry_id="q1"), _enquiry(enquiry_id="q2")),
        key="budget",
    )
    with pytest.raises(EvidenceEnquiryPlanningRejectedError) as e:
        service.plan(EnquiryPlanningInputs(hypotheses=_hset(),
                                           permitted_document_ids=["MFG"]), key="budget")
    assert e.value.stage == "service_governance"


def test_hypothesis_status_is_not_modified_by_planner(db_session):
    service, backend, _ = _service(db_session)
    hset = _hset(_hypothesis())
    backend.register(
        "analyze",
        _plan(
            _enquiry(enquiry_id="s", requirement_kind="supporting",
                     search_intent="supporting_evidence"),
            _enquiry(enquiry_id="c", requirement_kind="contradictory",
                     search_intent="contradictory_evidence"),
            _enquiry(enquiry_id="o", requirement_kind="offsetting",
                     search_intent="offsetting_capability"),
        ),
        key="ok",
    )
    service.plan(EnquiryPlanningInputs(hypotheses=hset,
                                       permitted_document_ids=["MFG"]), key="ok")
    # The planner never mutates the hypotheses it was handed.
    assert all(
        h.verification_status is HypothesisVerificationStatus.UNVERIFIED
        for h in hset.hypotheses
    )


def test_deterministic_fallback_is_labelled_reduced(db_session):
    service, _, _ = _service(db_session)
    result = service.plan_deterministic(
        EnquiryPlanningInputs(hypotheses=_hset(), permitted_document_ids=["MFG"])
    )
    plan = result.plan
    assert plan.planner_mode == "deterministic_fallback"  # NOT presented as generative
    kinds = {e.requirement_kind for e in plan.enquiries}
    # Supporting + contradictory always; offsetting proactively (negative
    # exposure, even with no named offset); effectiveness NOT present because no
    # offset has been found yet.
    assert EvidenceRequirementKind.SUPPORTING in kinds
    assert EvidenceRequirementKind.CONTRADICTORY in kinds
    assert EvidenceRequirementKind.OFFSETTING in kinds
    assert EvidenceRequirementKind.EFFECTIVENESS not in kinds


def test_deterministic_fallback_records_incomplete_coverage(db_session):
    service, _, _ = _service(
        db_session, budget=EnquiryBudget(max_enquiries_per_hypothesis=2)
    )
    # Named offset -> all four perspectives apply; a budget of 2 cannot fit them.
    named = _hypothesis(offsetting_characteristics=["Training academy."])
    result = service.plan_deterministic(
        EnquiryPlanningInputs(hypotheses=_hset(named), permitted_document_ids=["MFG"])
    )
    # Offset + effectiveness dropped by the budget are recorded, not discarded.
    assert result.plan.incomplete_coverage
    dropped = result.plan.incomplete_coverage[0]["dropped_perspectives"]
    assert "offsetting" in dropped and "effectiveness" in dropped


# ===========================================================================
# RETRIEVAL INTEGRATION (coordinator + real EvidenceSearchService)
# ===========================================================================


def _coordinator(corpus):
    return EvidenceEnquiryCoordinator(EvidenceSearchService(corpus))


def test_coordinator_groups_results_by_hypothesis(db_session):
    # Coordinator test: feed a coverage-complete plan object directly.
    plan = _plan_obj(
        _enquiry(enquiry_id="q1", search_query="skilled machining technicians shortage",
                 requirement_kind="supporting", search_intent="supporting_evidence"),
        _enquiry(enquiry_id="q2", search_query="technical training academy apprenticeship",
                 requirement_kind="offsetting", search_intent="offsetting_capability"),
    )
    bundle = _coordinator(build_corpus_from_pdf("MFG", _mfg_pdf())).execute(plan)
    assert set(bundle.by_hypothesis) == {"h1"}
    # 3 padded coverage enquiries + 2 test enquiries.
    assert len(bundle.by_hypothesis["h1"]) == 5
    assert bundle.retrieval_version


def test_cross_section_discovery_keeps_existence_and_outcome_separate(db_session):
    """Offset programme (p3) and its outcome (p4) are found by distinct enquiries.

    NOT a claim of live-model discovery -- enquiries are scripted here.
    """
    plan = _plan_obj(
        _enquiry(enquiry_id="vuln", search_query="shortage skilled machining technicians",
                 requirement_kind="supporting", search_intent="supporting_evidence"),
        _enquiry(enquiry_id="offset", search_query="technical training academy apprenticeship programme",
                 requirement_kind="offsetting", search_intent="offsetting_capability"),
        _enquiry(enquiry_id="effect", search_query="staffing costs employee retention outcomes",
                 requirement_kind="effectiveness", search_intent="general_investigation"),
    )
    bundle = _coordinator(build_corpus_from_pdf("MFG", _mfg_pdf())).execute(plan)

    by_id = {ex.enquiry.enquiry_id: ex for ex in bundle.executed}
    # Offset existence located on the workforce page (page 3).
    offset = by_id["offset"].result
    assert offset.outcome is EvidenceSearchOutcome.MATCHES_FOUND
    assert offset.ranked_passages[0].passage.pdf_page == 3
    # Outcome/effectiveness enquiry is a SEPARATE enquiry (page 4), not conflated
    # with the programme's existence.
    effect = by_id["effect"].result
    assert effect.outcome is EvidenceSearchOutcome.MATCHES_FOUND
    assert effect.ranked_passages[0].passage.pdf_page == 4
    assert by_id["offset"].enquiry.requirement_kind is EvidenceRequirementKind.OFFSETTING
    assert by_id["effect"].enquiry.requirement_kind is EvidenceRequirementKind.EFFECTIVENESS


def test_second_industry_logistics(db_session):
    plan = _plan_obj(
        _enquiry(enquiry_id="l1", hid="log1",
                 dimension=CreditDimension.PRICING_POWER_REVENUE_REALISATION.value,
                 search_query="fuel operating cost share",
                 requirement_kind="supporting", search_intent="supporting_evidence",
                 permitted_document_ids=["LOG"]),
        hid="log1",
        docs=("LOG",),
    )
    bundle = _coordinator(build_corpus_from_pdf("LOG", _pdf(LOGISTICS_PAGES))).execute(plan)
    hits = [
        ex for ex in bundle.by_hypothesis["log1"]
        if ex.result.outcome is EvidenceSearchOutcome.MATCHES_FOUND
    ]
    assert hits  # the fuel-cost enquiry locates the overview passage


def _by_id(bundle, enquiry_id):
    return next(ex for ex in bundle.executed if ex.enquiry.enquiry_id == enquiry_id)


def test_source_references_preserved(db_session):
    plan = _plan_obj(
        _enquiry(enquiry_id="target",
                 search_query="technical training academy apprenticeship")
    )
    corpus = build_corpus_from_pdf("MFG", _mfg_pdf())
    valid_ids = {p.passage_id for p in corpus.passages}
    bundle = EvidenceEnquiryCoordinator(EvidenceSearchService(corpus)).execute(plan)
    rp = _by_id(bundle, "target").result.ranked_passages[0]
    assert rp.passage.passage_id in valid_ids
    assert rp.passage.source_ref().document_id == "MFG"


def test_no_match_recorded_as_unresolved_not_absence(db_session):
    plan = _plan_obj(
        _enquiry(enquiry_id="nm", search_query="zzzxylophonequarknonsense")
    )
    bundle = _coordinator(build_corpus_from_pdf("MFG", _mfg_pdf())).execute(plan)
    assert _by_id(bundle, "nm").result.outcome is EvidenceSearchOutcome.NO_MATCH_FOUND
    # Explicit, not treated as 'capability absent'.
    assert any(u.get("outcome") == "no_match_found" for u in bundle.unresolved_enquiries)


def test_unavailable_document_outcome(db_session):
    plan = _plan_obj(_enquiry(enquiry_id="u", search_query="technicians"))
    empty_corpus = CorpusBuilder().build([])  # no documents
    bundle = EvidenceEnquiryCoordinator(EvidenceSearchService(empty_corpus)).execute(plan)
    assert _by_id(bundle, "u").result.outcome is (
        EvidenceSearchOutcome.SOURCE_NOT_AVAILABLE
    )
    assert bundle.unresolved_enquiries


def test_coordinator_total_query_budget(db_session):
    # 3 coverage enquiries + 2 extras = 5; budget of 2 executes only 2.
    plan = _plan_obj(
        _enquiry(enquiry_id="x1"), _enquiry(enquiry_id="x2")
    )
    coordinator = EvidenceEnquiryCoordinator(
        EvidenceSearchService(build_corpus_from_pdf("MFG", _mfg_pdf())),
        budget=EnquiryBudget(max_total_queries=2),
    )
    bundle = coordinator.execute(plan)
    assert len(bundle.executed) == 2  # remainder not executed
    assert any(
        u.get("reason", "").startswith("total query budget")
        for u in bundle.unresolved_enquiries
    )


def test_deterministic_execution_same_plan_and_corpus(db_session):
    plan = _plan_obj(
        _enquiry(enquiry_id="det",
                 search_query="technical training academy apprenticeship")
    )
    data = _mfg_pdf()
    b1 = _coordinator(build_corpus_from_pdf("MFG", data)).execute(plan)
    b2 = _coordinator(build_corpus_from_pdf("MFG", data)).execute(plan)
    ids1 = [rp.passage.passage_id
            for rp in _by_id(b1, "det").result.ranked_passages]
    ids2 = [rp.passage.passage_id
            for rp in _by_id(b2, "det").result.ranked_passages]
    assert ids1 == ids2


def test_retrieved_passage_not_marked_as_supporting(db_session):
    """A passage retrieved under an intent is a candidate, not an evaluated verdict."""
    plan = _plan_obj(
        _enquiry(enquiry_id="contra", search_query="skilled machining technicians",
                 requirement_kind="contradictory",
                 search_intent="contradictory_evidence")
    )
    bundle = _coordinator(build_corpus_from_pdf("MFG", _mfg_pdf())).execute(plan)
    item = _by_id(bundle, "contra")
    # The result carries only the search intent + ranked passages; there is NO
    # field asserting the passage supports or contradicts the hypothesis.
    assert item.enquiry.search_intent is EvidenceSearchIntent.CONTRADICTORY_EVIDENCE
    assert not hasattr(item.result.ranked_passages[0], "verdict")


# ===========================================================================
# PROMPT INSTRUCTION (string only)
# ===========================================================================


def test_prompt_text_covers_four_perspectives_and_discipline():
    text = EVIDENCE_ENQUIRY_PLANNING_TEMPLATE.lower()
    for token in ("supporting", "contradictory", "offsetting", "effectiveness"):
        assert token in text
    assert "not evidence that it achieved a financial result" in text
    assert "do not invent" in text
    assert "do not change the hypothesis" in text


def test_prompt_text_is_industry_general():
    text = EVIDENCE_ENQUIRY_PLANNING_TEMPLATE.lower()
    for banned in ("hca", "galen", "hospital", "nursing"):
        assert banned not in text
