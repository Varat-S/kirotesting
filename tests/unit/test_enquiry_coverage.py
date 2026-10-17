"""Enquiry coverage validation & proactive offset discovery (Task 5A).

Executable governance tests for:
* proactive offset investigation when a negative hypothesis names no offset
  (both the deterministic fallback and the LLM-plan coverage check);
* minimum-coverage detection (fail-closed when budget permits; partial +
  explicit incomplete_coverage when the budget is too small);
* applicability rules (effectiveness not forced without a found offset; four
  categories not forced on a positive hypothesis);
* requirement_kind / search_intent consistency;
* preservation of hypothesis verification status and source/scope safeguards.

A separate group asserts prompt wording only. Scripted FakeLLMBackend outputs
prove validation/plumbing, NOT live-model discovery. Fictional companies from
different industries are used.
"""

from __future__ import annotations

import pytest

from app.prompts.templates import EVIDENCE_ENQUIRY_PLANNING_TEMPLATE
from app.prompts.registry import PromptRegistry
from app.schemas.llm import (
    CreditDimension,
    CreditHypothesis,
    CreditHypothesisSet,
    EvidenceRequirementKind,
    HypothesisVerificationStatus,
)
from app.services.analysis.enquiry_planning import (
    ENQUIRY_PLANNING_REJECTED_EVENT,
    EnquiryBudget,
    EnquiryPlanningInputs,
    EvidenceEnquiryPlanningRejectedError,
    EvidenceEnquiryPlanningService,
    applicable_perspectives,
    evaluate_coverage,
)
from app.services.audit.log import AuditLog
from app.services.llm.client import FakeLLMBackend, LLMClient


# ---------------------------------------------------------------------------
# Harness
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


def _neg_hypothesis(hid="h1", **overrides) -> CreditHypothesis:
    """Negative operating-risk hypothesis with NO named offset."""
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
    return CreditHypothesisSet(hypotheses=list(h) or [_neg_hypothesis()])


def _enquiry(hid="h1", kind="supporting", intent=None, eid=None, **overrides) -> dict:
    canonical = {
        "supporting": "supporting_evidence",
        "contradictory": "contradictory_evidence",
        "offsetting": "offsetting_capability",
        "effectiveness": "general_investigation",
    }
    base = {
        "enquiry_id": eid or f"q_{hid}_{kind}",
        "hypothesis_id": hid,
        "dimension": CreditDimension.OPERATING_MODEL_COST_RESILIENCE.value,
        "requirement_kind": kind,
        "search_intent": intent or canonical[kind],
        "investigative_question": f"Investigate {kind}?",
        "search_query": f"{kind} query terms",
        "rationale": f"{kind} relevance to the hypothesis mechanism.",
        "evidence_requirement": f"{kind} requirement",
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


def _full_neg_plan(hid="h1") -> dict:
    """Supporting + contradictory + offsetting (the mandatory set for a neg)."""
    return _plan(
        _enquiry(hid, "supporting"),
        _enquiry(hid, "contradictory"),
        _enquiry(hid, "offsetting"),
    )


# ---------------------------------------------------------------------------
# Applicability rules (pure, documented policy)
# ---------------------------------------------------------------------------


def test_applicability_negative_requires_offset_without_named_offset():
    required = applicable_perspectives(_neg_hypothesis())
    assert EvidenceRequirementKind.OFFSETTING in required
    assert EvidenceRequirementKind.EFFECTIVENESS not in required  # none found yet


def test_applicability_positive_does_not_force_offset_or_effectiveness():
    pos = _neg_hypothesis(hid="p", credit_direction="positive")
    required = applicable_perspectives(pos)
    assert required == {
        EvidenceRequirementKind.SUPPORTING,
        EvidenceRequirementKind.CONTRADICTORY,
    }


def test_applicability_named_offset_requires_effectiveness():
    named = _neg_hypothesis(
        hid="o", offsetting_characteristics=["Apprenticeship programme."]
    )
    required = applicable_perspectives(named)
    assert EvidenceRequirementKind.OFFSETTING in required
    assert EvidenceRequirementKind.EFFECTIVENESS in required


# ---------------------------------------------------------------------------
# 1. Proactive offset discovery in the deterministic fallback
# ---------------------------------------------------------------------------


def test_fallback_investigates_offset_without_named_offset(db_session):
    service, _, _ = _service(db_session)
    plan = service.plan_deterministic(
        EnquiryPlanningInputs(hypotheses=_hset(), permitted_document_ids=["MFG"])
    ).plan
    kinds = {e.requirement_kind for e in plan.enquiries}
    assert EvidenceRequirementKind.OFFSETTING in kinds  # proactive (Gap 1 fixed)
    assert EvidenceRequirementKind.EFFECTIVENESS not in kinds  # none found yet
    # The offset enquiry uses generic concepts, not an assumed company fact.
    offset = next(
        e for e in plan.enquiries
        if e.requirement_kind is EvidenceRequirementKind.OFFSETTING
    )
    assert "workforce development" in offset.search_query


def test_fallback_named_offset_separates_existence_and_effectiveness(db_session):
    service, _, _ = _service(db_session)
    named = _neg_hypothesis(
        offsetting_characteristics=["Technical training academy."]
    )
    plan = service.plan_deterministic(
        EnquiryPlanningInputs(hypotheses=_hset(named), permitted_document_ids=["MFG"])
    ).plan
    kinds = {e.requirement_kind for e in plan.enquiries}
    assert EvidenceRequirementKind.OFFSETTING in kinds
    assert EvidenceRequirementKind.EFFECTIVENESS in kinds
    effect = next(
        e for e in plan.enquiries
        if e.requirement_kind is EvidenceRequirementKind.EFFECTIVENESS
    )
    assert "outcomes" in effect.search_query  # outcome, not mere existence


# ---------------------------------------------------------------------------
# 2-4. LLM-plan coverage detection
# ---------------------------------------------------------------------------


def test_llm_plan_missing_offset_for_negative_is_rejected(db_session):
    """Negative hypothesis, LLM plan with only supporting+contradictory -> fail closed."""
    service, backend, audit = _service(db_session)
    backend.register(
        "analyze",
        _plan(_enquiry("h1", "supporting"), _enquiry("h1", "contradictory")),
        key="miss",
    )
    with pytest.raises(EvidenceEnquiryPlanningRejectedError) as e:
        service.plan(
            EnquiryPlanningInputs(hypotheses=_hset(), permitted_document_ids=["MFG"]),
            key="miss",
        )
    assert e.value.stage == "coverage"
    types = [ev.event_type for ev in audit.events_for_case("C1")]
    assert ENQUIRY_PLANNING_REJECTED_EVENT in types


def test_llm_plan_missing_contradictory_is_rejected(db_session):
    service, backend, _ = _service(db_session)
    # Supporting + offsetting but NO contradictory.
    backend.register(
        "analyze",
        _plan(_enquiry("h1", "supporting"), _enquiry("h1", "offsetting")),
        key="nocontra",
    )
    with pytest.raises(EvidenceEnquiryPlanningRejectedError) as e:
        service.plan(
            EnquiryPlanningInputs(hypotheses=_hset(), permitted_document_ids=["MFG"]),
            key="nocontra",
        )
    assert e.value.stage == "coverage"


def test_balanced_plan_passes_coverage(db_session):
    service, backend, _ = _service(db_session)
    backend.register("analyze", _full_neg_plan(), key="ok")
    result = service.plan(
        EnquiryPlanningInputs(hypotheses=_hset(), permitted_document_ids=["MFG"]),
        key="ok",
    )
    assert not result.plan.incomplete_coverage  # fully covered
    assert len(result.plan.enquiries) == 3


def test_positive_hypothesis_without_offset_passes(db_session):
    """A positive hypothesis needs only supporting+contradictory; no offset req."""
    service, backend, _ = _service(db_session)
    pos = _neg_hypothesis(hid="pos", credit_direction="positive")
    backend.register(
        "analyze",
        _plan(_enquiry("pos", "supporting"), _enquiry("pos", "contradictory")),
        key="pos",
    )
    result = service.plan(
        EnquiryPlanningInputs(hypotheses=_hset(pos), permitted_document_ids=["MFG"]),
        key="pos",
    )
    assert not result.plan.incomplete_coverage


def test_effectiveness_not_mandatory_without_found_offset(db_session):
    """A negative hypothesis with offsetting present but no effectiveness passes."""
    service, backend, _ = _service(db_session)
    # No named offset on the hypothesis -> effectiveness NOT required.
    backend.register("analyze", _full_neg_plan(), key="ok")
    result = service.plan(
        EnquiryPlanningInputs(hypotheses=_hset(), permitted_document_ids=["MFG"]),
        key="ok",
    )
    kinds = {e.requirement_kind for e in result.plan.enquiries}
    assert EvidenceRequirementKind.EFFECTIVENESS not in kinds
    assert not result.plan.incomplete_coverage


def test_named_offset_requires_effectiveness_enquiry(db_session):
    """When the hypothesis names an offset, a plan lacking effectiveness fails."""
    service, backend, _ = _service(db_session)
    named = _neg_hypothesis(offsetting_characteristics=["Training academy."])
    backend.register("analyze", _full_neg_plan(), key="named")  # no effectiveness
    with pytest.raises(EvidenceEnquiryPlanningRejectedError) as e:
        service.plan(
            EnquiryPlanningInputs(hypotheses=_hset(named), permitted_document_ids=["MFG"]),
            key="named",
        )
    assert e.value.stage == "coverage"


# ---------------------------------------------------------------------------
# 7. Budget-constrained omissions are recorded, not silently dropped
# ---------------------------------------------------------------------------


def test_budget_too_small_keeps_partial_plan_and_marks_incomplete(db_session):
    """Budget of 2 cannot fit all 3 mandatory perspectives -> partial + flagged."""
    service, backend, _ = _service(
        db_session, budget=EnquiryBudget(max_enquiries_per_hypothesis=2)
    )
    # Plan provides supporting + contradictory (fits budget); offset omitted.
    backend.register(
        "analyze",
        _plan(_enquiry("h1", "supporting"), _enquiry("h1", "contradictory")),
        key="tight",
    )
    result = service.plan(
        EnquiryPlanningInputs(hypotheses=_hset(), permitted_document_ids=["MFG"]),
        key="tight",
    )
    # Not rejected (budget could not fit it), but coverage marked incomplete.
    assert result.plan.incomplete_coverage
    gap = result.plan.incomplete_coverage[0]
    assert gap["hypothesis_id"] == "h1"
    assert gap["missing_perspective"] == "offsetting"


def test_evaluate_coverage_marks_budget_permitted_flag():
    hset = _hset()  # negative, needs 3 perspectives
    plan_missing = type(
        "P", (), {"enquiries": []}
    )()  # no enquiries at all
    # Budget large enough -> omission is NOT excused (budget_permitted True).
    gaps = evaluate_coverage(plan_missing, hset, EnquiryBudget(max_enquiries_per_hypothesis=3))
    assert gaps and all(g.budget_permitted for g in gaps)
    # Budget too small -> omission excused as budget-constrained.
    gaps2 = evaluate_coverage(plan_missing, hset, EnquiryBudget(max_enquiries_per_hypothesis=1))
    assert gaps2 and not any(g.budget_permitted for g in gaps2)


# ---------------------------------------------------------------------------
# 6. Intent / requirement-kind consistency
# ---------------------------------------------------------------------------


def test_inconsistent_intent_is_rejected(db_session):
    """An offsetting enquiry mislabelled with supporting intent is rejected."""
    service, backend, _ = _service(db_session)
    backend.register(
        "analyze",
        _plan(
            _enquiry("h1", "supporting"),
            _enquiry("h1", "contradictory"),
            _enquiry("h1", "offsetting", intent="supporting_evidence"),  # mismatch
        ),
        key="badintent",
    )
    with pytest.raises(EvidenceEnquiryPlanningRejectedError) as e:
        service.plan(
            EnquiryPlanningInputs(hypotheses=_hset(), permitted_document_ids=["MFG"]),
            key="badintent",
        )
    assert e.value.stage == "service_governance"


# ---------------------------------------------------------------------------
# 9-10. Status preservation + scope safeguards still hold
# ---------------------------------------------------------------------------


def test_hypothesis_status_not_modified(db_session):
    service, backend, _ = _service(db_session)
    hset = _hset()
    backend.register("analyze", _full_neg_plan(), key="ok")
    service.plan(
        EnquiryPlanningInputs(hypotheses=hset, permitted_document_ids=["MFG"]),
        key="ok",
    )
    assert all(
        h.verification_status is HypothesisVerificationStatus.UNVERIFIED
        for h in hset.hypotheses
    )


def test_scope_safeguard_still_enforced(db_session):
    service, backend, _ = _service(db_session)
    plan = _full_neg_plan()
    plan["enquiries"][0]["permitted_document_ids"] = ["SECRET"]
    backend.register("analyze", plan, key="scope")
    with pytest.raises(EvidenceEnquiryPlanningRejectedError) as e:
        service.plan(
            EnquiryPlanningInputs(hypotheses=_hset(), permitted_document_ids=["MFG"]),
            key="scope",
        )
    assert e.value.stage == "service_governance"


# ---------------------------------------------------------------------------
# PROMPT WORDING (string only)
# ---------------------------------------------------------------------------


def test_prompt_instructs_proactive_offset_discovery():
    text = EVIDENCE_ENQUIRY_PLANNING_TEMPLATE.lower()
    assert "proactively" in text
    assert "even when the hypothesis names no offset" in text
    assert "not claimed company facts" in text
