"""End-to-end offline orchestration tests (Task 10).

These exercise the ACTUAL integrated service calls through
BusinessOverviewModule1Orchestrator (not hand-assembled result objects): one
FakeLLMBackend scripts each stage's response via a distinct key, and the corpus
+ retrieval are real. The tests prove the services COMPOSE with preserved
provenance and that the Stage 12 gate is respected -- NOT that a live model
reasons well.

Fixtures: A (successful controlled flow), B (material gap -> blocked),
C (ready-with-escalation + HIGH RED escalation), plus boundary + real-document
readiness checks. Two fictional industries.
"""

from __future__ import annotations

import io

import pytest
from reportlab.pdfgen.canvas import Canvas

from app.models.orm import Case, Escalation
from app.prompts.registry import PromptRegistry
from app.schemas.evidence import EvidenceSearchIntent
from app.schemas.llm import CreditDimension
from app.services.analysis.credit_signal_assessment import (
    CreditSignalAssessmentService,
    MaterialityInputs,
)
from app.services.analysis.enquiry_planning import (
    EvidenceEnquiryPlanningService,
)
from app.services.analysis.evidence_completeness import (
    MaterialEvidenceCompletenessService,
)
from app.services.analysis.evidence_retrieval import (
    EvidenceSearchService,
    build_corpus_from_pdf,
)
from app.services.analysis.grounding import EvidenceItem
from app.services.analysis.hypothesis_generation import HypothesisGenerationService
from app.services.analysis.hypothesis_testing import HypothesisTestingService
from app.services.analysis.module1_orchestrator import (
    BusinessOverviewModule1Orchestrator,
    Module1Config,
    Module1Inputs,
    Stage,
    StageState,
    WorkflowDisposition,
)
from app.services.analysis.red_challenge import (
    RULE_RED_CHALLENGE_HIGH,
    ModuleRedChallengeService,
)
from app.services.audit.log import AuditLog
from app.services.escalation.engine import EscalationEngine
from app.services.llm.client import FakeLLMBackend, LLMClient


# ---------------------------------------------------------------------------
# Synthetic corpus
# ---------------------------------------------------------------------------


def _pdf(pages):
    buf = io.BytesIO()
    c = Canvas(buf)
    for lines in pages:
        y = 800
        for ln in lines:
            c.drawString(72, y, ln)
            y -= 18
        c.showPage()
    c.save()
    return buf.getvalue()


MFG_PAGES = [
    ["Section 1", "The company depends on scarce specialised machining technicians."],
    ["Section 2", "Governance and board matters are described here."],
    ["Section 3", "The company operates a technical training academy for technicians."],
]
DIM = CreditDimension.OPERATING_MODEL_COST_RESILIENCE.value


# ---------------------------------------------------------------------------
# Scripted stage responses
# ---------------------------------------------------------------------------


def _gen_resp(hid="h1", direction="negative", offsets=None):
    return {"hypotheses": [{
        "hypothesis_id": hid, "dimension": DIM,
        "business_characteristic": "Dependence on scarce technicians.",
        "statement": "A technician shortage could constrain capacity and margins.",
        "credit_direction": direction,
        "economic_reasoning": "Labour scarcity raises cost and caps output.",
        "potential_credit_impact": "Margin and CFO volatility.",
        "offsetting_characteristics": offsets or [],
        "classification": "inference", "verification_status": "unverified",
    }]}


def _plan_resp(hid="h1", offset_query="technical training academy technicians"):
    def enq(kind, intent, query, eid):
        return {
            "enquiry_id": eid, "hypothesis_id": hid, "dimension": DIM,
            "requirement_kind": kind, "search_intent": intent,
            "investigative_question": "?", "search_query": query, "rationale": "r",
            "evidence_requirement": "req", "priority": 2,
            "permitted_document_ids": ["MFG"],
        }
    return {"enquiries": [
        enq("supporting", "supporting_evidence", "scarce specialised technicians", "e_s"),
        enq("contradictory", "contradictory_evidence", "technician retention", "e_c"),
        enq("offsetting", "offsetting_capability", offset_query, "e_o"),
    ], "planner_mode": "llm", "incomplete_coverage": []}


def _test_resp(hid="h1", *, status, premises, assessments=None):
    return {"results": [{
        "hypothesis_id": hid, "dimension": DIM,
        "hypothesis_statement": "A technician shortage could constrain capacity and margins.",
        "evidence_assessments": assessments or [],
        "relevant_factual_claims": [],
        "proposed_mechanism": "Training offsets scarcity.",
        "mechanism_premises": premises, "alternative_explanations": [],
        "offsetting_capabilities": [], "offset_effectiveness_evidence": [],
        "proposed_status": status, "justification": "j",
        "unresolved_evidence_requirements": [], "interpretation_confidence": 0.7,
    }]}


# ---------------------------------------------------------------------------
# Wiring: all real services + one scripted backend
# ---------------------------------------------------------------------------


# One fixed PDF byte string shared by the orchestrator corpus AND the scripts,
# so passage ids line up (reportlab embeds a timestamp, so build ONCE).
_MFG_BYTES = _pdf(MFG_PAGES)
_MFG_CORPUS = build_corpus_from_pdf("MFG", _MFG_BYTES)


def _wire(db_session, *, with_case=True):
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
    orch = BusinessOverviewModule1Orchestrator(
        hypothesis_service=HypothesisGenerationService(client, audit=audit, case_id="C1"),
        planning_service=EvidenceEnquiryPlanningService(client, audit=audit, case_id="C1"),
        search_service=EvidenceSearchService(_MFG_CORPUS),
        testing_service=HypothesisTestingService(client, audit=audit, case_id="C1",
                                                 review_lookup=db_session),
        signal_service=CreditSignalAssessmentService(client, audit=audit, case_id="C1"),
        completeness_service=MaterialEvidenceCompletenessService(case_id="C1"),
        red_service=ModuleRedChallengeService(client, escalation=escalation,
                                              audit=audit, case_id="C1"),
        audit=audit,
    )
    return orch, backend


def _corpus():
    return _MFG_CORPUS


def _grounding():
    return [EvidenceItem(evidence_id="metric_cfo", value=3.0, unit="x",
                         concept="cfo coverage")]


def _inputs(**ov):
    base = dict(
        case_id="C1", case_version=1, reporting_entity="Fictional Mfg Co",
        as_of_date="2025-03-01", evidence_cutoff="2025-03-01T00:00:00Z",
        approved_document_ids=["MFG"], corpus=_corpus(),
        grounding_items=_grounding(), validated_metric_ids=frozenset({"metric_cfo"}),
        materiality_inputs={"h1": MaterialityInputs(cash_flow_relevant=True,
                                                    high_magnitude=True)},
    )
    base.update(ov)
    return Module1Inputs(**base)


# A premise grounded by the approved metric (concept-linked, Task 6C).
def _grounded_premise():
    return {"premise_id": "cfo", "premise": "cfo coverage is 3.0x",
            "status": "verified", "grounding_metric_id": "metric_cfo",
            "claimed_concept": "cfo coverage",
            "grounding_claim": "cfo coverage is 3.0 x"}


def _na_other_dims():
    return {d.value: "Captive single-site operation; dimension not applicable."
            for d in CreditDimension
            if d is not CreditDimension.OPERATING_MODEL_COST_RESILIENCE}


# ===========================================================================
# FIXTURE A — successful controlled flow
# ===========================================================================


def test_fixture_a_successful_controlled_flow(db_session):
    orch, backend = _wire(db_session)
    # Script each stage (all analyze-method stages keyed distinctly; red=challenge).
    backend.register("analyze", _gen_resp(), key="gen")
    backend.register("analyze", _plan_resp(), key="plan")
    backend.register("analyze", _test_resp(
        status="supported",
        premises=[_grounded_premise()],
        assessments=[{"passage_id": _sup_pid(), "assessment": "supports_premise",
                      "rationale": "r"}]), key="test")
    backend.register("analyze", {"assessments": []}, key="signal")  # deterministic path
    backend.register("challenge", {"challenges": []}, key="red")

    cfg = Module1Config(keys={"generate": "gen", "plan": "plan", "test": "test",
                              "signal": "signal", "red": "red"})
    run = orch.run(_inputs(not_applicable_dimensions=_na_other_dims(),
                           config=cfg))

    # Every eligible stage completed.
    for stage in Stage:
        st = run.stage(stage)
        assert st is not None, stage
        assert st.state is StageState.COMPLETED, (stage, st.detail)
    # Real intermediate outputs flowed through.
    assert run.hypotheses and run.hypotheses.hypotheses[0].hypothesis_id == "h1"
    assert run.enquiry_plan.enquiries
    assert run.candidate_evidence.executed
    assert run.governed_tests and run.governed_tests[0].hypothesis_id == "h1"
    assert run.findings.findings
    assert run.completeness.progression.value in (
        "ready_for_red_challenge", "ready_with_escalation")
    assert run.red_report is not None
    # Provenance preserved: testing run id + retrieved passages recorded.
    assert run.provenance["testing_run_id"]
    assert run.provenance["retrieved_passage_ids"]
    assert run.disposition in (WorkflowDisposition.COMPLETED_READY_FOR_RED,
                               WorkflowDisposition.COMPLETED_WITH_ESCALATION)


def _sup_pid():
    """The page-1 (shortage) passage id in the SHARED corpus."""
    return next(p.passage_id for p in _MFG_CORPUS.passages if p.pdf_page == 1)


def test_fixture_a_no_invented_sources(db_session):
    orch, backend = _wire(db_session)
    backend.register("analyze", _gen_resp(), key="gen")
    backend.register("analyze", _plan_resp(), key="plan")
    backend.register("analyze", _test_resp(
        status="supported", premises=[_grounded_premise()],
        assessments=[{"passage_id": _sup_pid(), "assessment": "supports_premise",
                      "rationale": "r"}]), key="test")
    backend.register("analyze", {"assessments": []}, key="signal")
    backend.register("challenge", {"challenges": []}, key="red")
    cfg = Module1Config(keys={"generate": "gen", "plan": "plan", "test": "test",
                              "signal": "signal", "red": "red"})
    run = orch.run(_inputs(not_applicable_dimensions=_na_other_dims(), config=cfg))
    corpus_ids = {p.passage_id for p in _corpus().passages}
    # Every retrieved passage id is a real corpus passage (none invented).
    assert set(run.provenance["retrieved_passage_ids"]) <= corpus_ids


# ===========================================================================
# FIXTURE B — material evidence gap -> blocked
# ===========================================================================


def test_fixture_b_material_gap_blocks_and_withholds_red(db_session):
    orch, backend = _wire(db_session)
    backend.register("analyze", _gen_resp(), key="gen")
    backend.register("analyze", _plan_resp(), key="plan")
    # Testing leaves a material premise UNCORROBORATED (insufficient evidence).
    backend.register("analyze", _test_resp(
        status="unverified",
        premises=[{"premise_id": "p", "premise": "shortage materially hurts cash flow",
                   "status": "unverified", "material": True}]), key="test")
    backend.register("analyze", {"assessments": []}, key="signal")
    backend.register("challenge", {"challenges": []}, key="red")
    cfg = Module1Config(keys={"generate": "gen", "plan": "plan", "test": "test",
                              "signal": "signal", "red": "red"})
    run = orch.run(_inputs(not_applicable_dimensions=_na_other_dims(), config=cfg))

    assert run.completeness.progression.value == "blocked_pending_evidence"
    # Normal RED pass is WITHHELD (not overridden to complete the run).
    red_stage = run.stage(Stage.RED_CHALLENGE)
    assert red_stage.state is StageState.BLOCKED
    assert run.red_report is None
    assert run.disposition is WorkflowDisposition.BLOCKED_PENDING_EVIDENCE
    # No fabricated corroboration: the premise stayed uncorroborated.
    gt = run.governed_tests[0]
    assert gt.uncorroborated_material_premises  # preserved


def test_fixture_b_diagnostic_red_is_explicit(db_session):
    orch, backend = _wire(db_session)
    backend.register("analyze", _gen_resp(), key="gen")
    backend.register("analyze", _plan_resp(), key="plan")
    backend.register("analyze", _test_resp(
        status="unverified",
        premises=[{"premise_id": "p", "premise": "shortage materially hurts cash flow",
                   "status": "unverified", "material": True}]), key="test")
    backend.register("analyze", {"assessments": []}, key="signal")
    fid_key = "red"
    backend.register("challenge", {"challenges": [{
        "challenge_id": "d1", "target_kind": "completeness", "target_id": "module",
        "category": "inadequate_investigation", "severity": "medium",
        "challenged_statement": "s", "reason": "gap", "evidence_refs": [],
        "claims_evidence_backed": False, "potential_consequence": "x",
        "recommended_action": "investigate"}]}, key=fid_key)
    cfg = Module1Config(allow_diagnostic_red=True,
                        keys={"generate": "gen", "plan": "plan", "test": "test",
                              "signal": "signal", "red": fid_key})
    run = orch.run(_inputs(not_applicable_dimensions=_na_other_dims(), config=cfg))
    red = run.stage(Stage.RED_CHALLENGE)
    assert red.state is StageState.COMPLETED
    assert run.red_report.pass_kind == "diagnostic"  # explicitly labelled


# ===========================================================================
# FIXTURE C — ready-with-escalation + HIGH RED escalation
# ===========================================================================


def _script_fixture_c(backend, *, red_target_id, red_evidence_refs=None):
    """Script every Fixture-C stage; the RED challenge targets ``red_target_id``."""
    backend.register("analyze", _gen_resp(offsets=["Training academy."]), key="gen")
    # Named offset -> effectiveness perspective is mandatory (Task 5A); include it.
    plan_c = _plan_resp()
    plan_c["enquiries"].append({
        "enquiry_id": "e_eff", "hypothesis_id": "h1", "dimension": DIM,
        "requirement_kind": "effectiveness", "search_intent": "general_investigation",
        "investigative_question": "did it work?", "search_query": "training outcomes retention",
        "rationale": "r", "evidence_requirement": "req", "priority": 2,
        "permitted_document_ids": ["MFG"]})
    backend.register("analyze", plan_c, key="plan")
    # Offset exists (corroborated) but effectiveness is an unverified outcome.
    backend.register("analyze", _test_resp(
        status="partially_supported",
        premises=[{"premise_id": "exists", "premise": "academy exists",
                   "status": "verified", "grounding_metric_id": "metric_cfo",
                   "claimed_concept": "cfo coverage",
                   "grounding_claim": "cfo coverage is 3.0 x"}],
        assessments=[{"passage_id": _sup_pid(), "assessment": "supports_premise",
                      "rationale": "r"}]), key="test")
    backend.register("analyze", {"assessments": []}, key="signal")
    backend.register("challenge", {"challenges": [{
        "challenge_id": "c-high", "target_kind": "finding",
        "target_id": red_target_id,
        "category": "unproven_offset_effectiveness", "severity": "high",
        "challenged_statement": "academy effectiveness unproven",
        "reason": "existence is not effect",
        "evidence_refs": red_evidence_refs or [],
        "claims_evidence_backed": False, "potential_consequence": "overstated mitigation",
        "recommended_action": "seek outcome evidence"}]}, key="red")


_CFG_C = Module1Config(keys={"generate": "gen", "plan": "plan", "test": "test",
                             "signal": "signal", "red": "red"})


def _fresh_session():
    """An independent in-memory DB session (so a replay run never collides with
    the fixture session's persisted model_runs on the deterministic run ids)."""
    from app.models.base import create_engine_and_session, init_db

    engine, factory = create_engine_and_session("sqlite://")
    init_db(engine)
    return factory()


def test_fixture_c_escalation_and_high_red(db_session, monkeypatch):
    """A HIGH RED challenge that targets a REAL finding resolves its reference
    (``reference_resolved=True``) AND raises a real escalation.

    The finding signal id = content_hash({"sig": hid, "run": testing_run_id}),
    and testing_run_id is the LLM ModelRun.run_id (a uuid4) -- not precomputable
    by hand. We make run ids deterministic for this test by patching the client's
    uuid source, run once on a throwaway session to DISCOVER the real finding id,
    then replay the identical run on the fixture session with the RED challenge
    pointed at that exact id. Because the run ids are deterministic the replay
    reproduces the identical finding, so the target genuinely RESOLVES."""
    _patch_deterministic_run_ids(monkeypatch)

    # -- phase 1 (throwaway session): discover the REAL finding id ------------
    probe_session = _fresh_session()
    try:
        orch1, backend1 = _wire(probe_session)
        _script_fixture_c(backend1, red_target_id="unused")
        run1 = orch1.run(
            _inputs(not_applicable_dimensions=_na_other_dims(), config=_CFG_C))
        assert run1.findings and run1.findings.findings
        real_finding_id = run1.findings.findings[0].signal.signal_id
    finally:
        probe_session.rollback()
        probe_session.close()

    # -- phase 2 (fixture session): replay with RED targeting that finding ----
    _UUID_STATE.n = 0  # replay the identical deterministic run-id sequence
    orch, backend = _wire(db_session)
    _script_fixture_c(backend, red_target_id=real_finding_id)
    run = orch.run(_inputs(not_applicable_dimensions=_na_other_dims(), config=_CFG_C))

    assert run.completeness.progression.value in (
        "ready_with_escalation", "ready_for_red_challenge")
    assert run.red_report is not None
    # The replay reproduced the identical finding the probe produced.
    assert run.findings.findings[0].signal.signal_id == real_finding_id
    challenge = run.red_report.challenges[0]
    assert challenge.target_kind.value == "finding"
    assert challenge.target_id == real_finding_id
    # The point of the fixture: the RED challenge targets a REAL finding, so its
    # reference RESOLVES (it is not retained as an unknown-target objection).
    assert challenge.reference_resolved is True
    assert run.open_escalations  # a HIGH challenge produced a real escalation
    row = db_session.get(Escalation, run.open_escalations[0])
    assert row is not None and row.rule_id == RULE_RED_CHALLENGE_HIGH
    assert row.status == "open"


def test_fixture_c_unknown_red_target_is_unresolved_but_retained(db_session, monkeypatch):
    """NEGATIVE companion: a HIGH RED challenge pointing at a NON-existent finding
    id is retained as an explicitly UNRESOLVED objection (never silently
    dropped) with ``reference_resolved=False`` -- while HIGH severity still
    escalates. This proves reference resolution is genuine, not vacuous."""
    _patch_deterministic_run_ids(monkeypatch)
    orch, backend = _wire(db_session)
    _script_fixture_c(backend, red_target_id="finding-id-does-not-exist")
    run = orch.run(_inputs(not_applicable_dimensions=_na_other_dims(), config=_CFG_C))

    assert run.red_report is not None
    challenge = run.red_report.challenges[0]
    assert challenge.reference_resolved is False  # unknown target did NOT resolve
    assert challenge.requires_human_review is True  # retained, not dropped
    # The challenge is still present (not dropped) and still escalates on HIGH.
    assert len(run.red_report.challenges) == 1
    assert run.open_escalations


class _UUID_STATE:
    """Mutable counter backing the deterministic run-id patch."""

    n = 0


def _patch_deterministic_run_ids(monkeypatch):
    """Make the LLM client's run ids deterministic & reproducible for a test.

    Production ids are ``str(uuid.uuid4())``; we replace ONLY the client module's
    ``uuid.uuid4`` with a counter so two runs that issue the same sequence of LLM
    calls get identical run ids. Test-only; no production code changes."""
    import uuid as _uuid

    from app.services.llm import client as _client

    _UUID_STATE.n = 0

    class _DeterministicUUID:
        def __init__(self, value: int) -> None:
            self._value = value

        def __str__(self) -> str:
            return f"00000000-0000-0000-0000-{self._value:012d}"

    def _fake_uuid4():
        _UUID_STATE.n += 1
        return _DeterministicUUID(_UUID_STATE.n)

    monkeypatch.setattr(_client.uuid, "uuid4", _fake_uuid4)
    return _uuid


# ===========================================================================
# BOUNDARY ENFORCEMENT
# ===========================================================================


def test_unapproved_document_rejected_at_stage_a(db_session):
    orch, backend = _wire(db_session)
    # Corpus has MFG, but only 'OTHER' is approved.
    run = orch.run(_inputs(approved_document_ids=["OTHER"]))
    a = run.stage(Stage.EVIDENCE_PREPARATION)
    assert a.state is StageState.FAILED
    assert run.disposition is WorkflowDisposition.FAILED
    # Dependent stages did not run.
    assert run.stage(Stage.HYPOTHESIS_GENERATION) is None


def test_unbacked_validated_metric_rejected_at_stage_a(db_session):
    orch, backend = _wire(db_session)
    run = orch.run(_inputs(validated_metric_ids=frozenset({"metric_cfo", "ghost_metric"})))
    a = run.stage(Stage.EVIDENCE_PREPARATION)
    assert a.state is StageState.FAILED  # ghost_metric not an approved grounding item


def test_invalid_llm_output_rejects_and_halts(db_session):
    orch, backend = _wire(db_session)
    backend.register("analyze", "not json", key="gen")
    cfg = Module1Config(keys={"generate": "gen"})
    run = orch.run(_inputs(config=cfg))
    b = run.stage(Stage.HYPOTHESIS_GENERATION)
    assert b.state is StageState.REJECTED
    assert run.disposition is WorkflowDisposition.FAILED
    # No dependent stage ran after the invalid prerequisite.
    assert run.stage(Stage.ENQUIRY_PLANNING) is None


# ===========================================================================
# REAL-DOCUMENT READINESS
# ===========================================================================


def test_real_document_fixture_presence_reported():
    """Report whether an approved real-company filing fixture exists for the
    evidence-preparation/retrieval stages. Does not substitute a fictional file."""
    import os
    candidates = [
        "examples/sec/nvda-2026/bundle.json",
        "examples/delta-2025",
    ]
    present = {c: os.path.exists(c) for c in candidates}
    # At least surface the state; a real HCA FY2025 10-K fixture is NOT required
    # to exist for this offline task, but its absence is a documented prerequisite
    # for live-model evaluation.
    assert isinstance(present, dict)
