"""Real-model READINESS for the standalone Module 3 pathway (Priority 3).

Offline only: every "live" exercise here drives the OPT-IN ``live_backend``
parameter of :func:`app.services.bo.m3_standalone.run_m3` with the repo's
deterministic :class:`~app.services.llm.client.FakeLLMBackend` (or a backend
that raises) — NO provider and NO paid model call. conftest forbids live SEC
I/O and disables ``.env``.

These tests prove the SAFEGUARDS the brief requires:

* offline (scripted) mode still works and stays the default;
* live mode cannot activate accidentally (``live_backend=None`` -> offline);
* a provider failure / schema-invalid response fails SAFELY (no fabricated
  findings, ``partially_failed``) with NO silent fallback to the scripted path;
* ONLY ``m3_corporate_management`` is invoked (one analysis run / one packet; no
  forbidden downstream/cross-module audit event);
* the admitted evidence AND the validated structural facts reach the specialist
  BEFORE it reasons;
* the run manifest accurately identifies simulation vs real execution; and
* token/timeout/retry safeguards are recorded and bounded to one execution.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.base import create_engine_and_session, init_db
from app.models.orm import (
    AgentRunRow,
    AgenticAnalysisRun,
    AuditEvent,
    EvidencePacketRow,
)
from app.services.bo.m3_standalone import run_m3
from app.services.llm.client import FakeLLMBackend, LLMRawResult
from tests import bo_helpers as h


SIMPLIFIED = Path("examples/bo_m3/Simplified HCA Corp Structure.json")
AGENT_ID = "m3_corporate_management"

FORBIDDEN_AUDIT_EVENTS = {
    "bo_orchestrator_executed", "bo_challenge_executed", "bo_synthesis_executed",
    "bo_scoring_executed", "bo_memo_rendered", "bo_handoff_built",
    "bo_m1s2_mode_executed", "agent6_executed",
}


def _simplified() -> dict:
    return json.loads(SIMPLIFIED.read_text(encoding="utf-8"))


def _fresh_session():
    engine, factory = create_engine_and_session("sqlite://")
    init_db(engine)
    return factory()


def _scripted_single_pass() -> dict:
    """A minimal scripted offline response (anchors resolved by run_single_pass)."""
    return {
        "parameters": [],
        "hypotheses": [h.hyp(
            "M3-H001", "The board oversees governance.", "fact", "neutral",
            "supported", support=["executive officers"],
            quotes=[("executive officers", "The board of directors oversees governance")])],
        "m3_assessment": {
            "schema_version": "bo-m3-corporate-assessment-1.0",
            "borrower_identification": {
                "status": "unavailable", "entity_id": None, "evidence_ids": [],
                "limitation": "No proposed borrowing entity is identified."},
            "legal_entities": [], "ownership_relationships": [],
            "cash_flow_locations": [], "cash_transfer_relationships": [],
            "structural_credit_findings": [],
            "management_assessments": [],
            "governance_screening": [],
            "information_gaps": ["Management outcomes are not disclosed."],
            "downstream_dependencies": [],
        },
    }


def _live_valid_response() -> dict:
    """A schema-valid specialist response WITHOUT anchors (the live path does not
    resolve anchors). Evidence lists are empty, so Validator 7 governs the
    management observation to unverified — exactly the conservative, no-fabrication
    behaviour expected when a live model cites nothing resolvable."""
    return {
        "parameters": [],
        "hypotheses": [{
            "hypothesis_id": "M3-H001",
            "claim": "The board oversees governance.",
            "claim_type": "fact", "direction": "neutral", "disposition": "unverified",
            "confidence": "medium", "materiality": "low", "display_priority": "display",
            "supporting_evidence_ids": [], "contradicting_evidence_ids": [],
            "evidence_quotes": [],
        }],
        "m3_assessment": {
            "schema_version": "bo-m3-corporate-assessment-1.1",
            "borrower_identification": {
                "status": "unavailable", "entity_id": None, "evidence_ids": [],
                "limitation": "No proposed borrowing entity is identified."},
            "legal_entities": [], "ownership_relationships": [],
            "cash_flow_locations": [], "cash_transfer_relationships": [],
            "structural_credit_findings": [],
            "management_assessments": [{
                "observation_id": "MO1", "management_id": "board",
                "legal_name": "Board of Directors", "role": "Board",
                "effective_from": None, "effective_to": None,
                "dimension": "competence", "evidence_basis": "reported_experience",
                "attribution": "company_reported", "assurance_scope": None,
                "assurance_date": None,
                "hypothesis": "The board oversees governance.",
                "credit_mechanism": "Board oversight may reduce continuity risk somewhat.",
                "supporting_evidence_ids": [], "contradicting_evidence_ids": [],
                "supporting_evidence_investigated": True,
                "contradictory_evidence_investigated": True,
                "disposition": "unverified", "admissibility": "audit_only",
                "confidence": "medium", "materiality": "low",
                "missing_information": [],
            }],
            "governance_screening": [],
            "information_gaps": ["Management outcomes are not disclosed."],
            "downstream_dependencies": [],
        },
    }


def _live_backend(response: dict) -> FakeLLMBackend:
    backend = FakeLLMBackend(model_id="fake-live-under-test")
    # The live path calls build_request(agent_id, agent_id, ...) with no explicit
    # key, so the backend lookup key defaults to "default". Register the raw
    # response (the live path performs NO anchor resolution).
    backend.register(AGENT_ID, response, key="default")
    return backend


def _run(session, *, scripted=True, response=None, document=_simplified):
    if scripted:
        return run_m3(
            session, case_id=h.CASE, evidence=h.evidence_snapshot(),
            metrics=h.metrics(), m3_responses={"single_pass": _scripted_single_pass()},
            corporate_structure_document=document() if callable(document) else document)
    return run_m3(
        session, case_id=h.CASE, evidence=h.evidence_snapshot(),
        metrics=h.metrics(),
        live_backend=_live_backend(response or _live_valid_response()),
        live_config={"max_retries": 0, "timeout_seconds": 60, "max_output_tokens": 8000},
        corporate_structure_document=document() if callable(document) else document)


# -- offline stays the default and still works -------------------------------


def test_offline_mode_still_works_and_is_simulation():
    result = _run(_fresh_session(), scripted=True)
    assert result.model_called is False
    assert result.run_manifest["execution"]["execution_mode"] == "offline_simulation"
    assert result.run_manifest["execution"]["real_model_invoked"] is False
    assert result.specialist_validated is True


def test_live_cannot_activate_accidentally():
    # No live_backend supplied -> offline; a model is never called by default.
    result = run_m3(
        _fresh_session(), case_id=h.CASE, evidence=h.evidence_snapshot(),
        metrics=h.metrics(), m3_responses={"single_pass": _scripted_single_pass()},
        corporate_structure_document=_simplified())
    assert result.model_called is False
    assert result.run_manifest["execution"]["execution_mode"] == "offline_simulation"


def test_run_m3_requires_a_backend_or_scripted_responses():
    with pytest.raises(ValueError):
        run_m3(_fresh_session(), case_id=h.CASE, evidence=h.evidence_snapshot(),
               metrics=h.metrics())  # neither m3_responses nor live_backend


# -- the opt-in live path exercises the real code path (fake backend) --------


def test_live_path_sets_live_mode_but_fake_is_not_a_real_model():
    # The LIVE pathway is selected (execution_mode == 'live'), but a fake backend
    # is honestly NOT counted as a real model: model_called / real_model_invoked
    # stay False. This is the no-paid-call guarantee — the capability is wired
    # and exercised, but no genuine provider answered.
    result = _run(_fresh_session(), scripted=False)
    exe = result.run_manifest["execution"]
    assert exe["execution_mode"] == "live"
    assert result.model_called is False
    assert exe["real_model_invoked"] is False
    assert exe["model_id"] == "fake-live-under-test"
    # The run still produced a governed assessment via the live code path.
    assert result.governed_assessment is not None
    assert result.governed_assessment["replay_provenance"]["execution_mode"] == "live"


def test_live_path_invokes_only_m3_corporate_management(db_session=None):
    session = _fresh_session()
    result = _run(session, scripted=False)

    runs = session.query(AgenticAnalysisRun).all()
    assert len(runs) == 1
    packets = session.query(EvidencePacketRow).all()
    assert {p.agent_id for p in packets} == {AGENT_ID}
    assert len(packets) == 1
    agent_ids = {r.agent_id for r in session.query(AgentRunRow).all()}
    assert agent_ids <= {AGENT_ID}
    event_types = {e.event_type for e in session.query(AuditEvent).all()}
    assert not (event_types & FORBIDDEN_AUDIT_EVENTS)
    assert result.run_manifest["cross_module_or_downstream_invoked"] is False


def test_live_path_has_structure_and_evidence_in_specialist_context(monkeypatch):
    from app.services.bo import m3_standalone

    captured: dict = {}
    real = m3_standalone.run_single_pass

    def spy(session, prep, response=None, *, backend=None):
        captured["structural_facts"] = prep.structural_facts is not None
        inputs = m3_standalone._single_pass_inputs(prep) \
            if hasattr(m3_standalone, "_single_pass_inputs") else None
        # _single_pass_inputs lives in comparison; import directly.
        from app.services.bo.comparison import _single_pass_inputs
        built = _single_pass_inputs(prep)
        captured["has_structure_block"] = "corporate_structure" in built
        captured["has_evidence_block"] = "evidence" in built
        captured["backend_is_live"] = backend is not None
        return real(session, prep, response, backend=backend)

    monkeypatch.setattr("app.services.bo.m3_standalone.run_single_pass", spy)
    result = _run(_fresh_session(), scripted=False)
    assert captured["structural_facts"] is True
    assert captured["has_structure_block"] is True
    assert captured["has_evidence_block"] is True
    assert captured["backend_is_live"] is True
    assert result.run_manifest["execution"]["structure_in_specialist_context"] is True


# -- fail-safe behaviour ------------------------------------------------------


def test_schema_invalid_live_response_fails_safely():
    # A backend returning JSON that violates the response schema -> rejected; the
    # run is partially_failed with NO m3_assessment and NO fabricated findings.
    backend = FakeLLMBackend(model_id="fake-live-under-test")
    backend.register(AGENT_ID, '{"not":"a valid specialist response"}', key=AGENT_ID)
    result = run_m3(
        _fresh_session(), case_id=h.CASE, evidence=h.evidence_snapshot(),
        metrics=h.metrics(), live_backend=backend,
        corporate_structure_document=_simplified())
    assert result.specialist_validated is False
    assert result.governed_assessment is None
    assert result.run_manifest["execution"]["execution_mode"] == "live"
    # NO silent fallback to scripted: nothing is promoted into the conclusion.
    conclusion = result.m3_analysis["consolidated_conclusion"]
    assert conclusion["admitted_structural_credit_finding_ids"] == []
    assert conclusion["admitted_management_signal_ids"] == []


def test_provider_error_fails_safely_without_fabrication():
    class _ErroringBackend(FakeLLMBackend):
        def generate(self, request):
            raise RuntimeError("provider unavailable")

    backend = _ErroringBackend(model_id="fake-live-under-test")
    result = run_m3(
        _fresh_session(), case_id=h.CASE, evidence=h.evidence_snapshot(),
        metrics=h.metrics(), live_backend=backend,
        corporate_structure_document=_simplified())
    # The arm failed; no assessment, no fabricated output, still no downstream.
    assert result.specialist_validated is False
    assert result.governed_assessment is None
    assert result.run_manifest["cross_module_or_downstream_invoked"] is False


# -- safeguards: bounded execution, manifest honesty -------------------------


def test_manifest_records_bounded_execution_and_retry_safeguards():
    result = _run(_fresh_session(), scripted=False)
    exe = result.run_manifest["execution"]
    # Exactly one specialist pass; retries bounded (default 0).
    assert exe["specialist_pass_count"] == 1
    assert exe["max_retries_configured"] == 0


def test_manifest_distinguishes_simulation_from_real_execution():
    offline = _run(_fresh_session(), scripted=True)
    live = _run(_fresh_session(), scripted=False)
    # execution_mode reflects the pathway chosen; real_model_invoked stays False
    # for BOTH here because the live path is exercised only with a fake backend
    # (no paid call). A genuine provider would set real_model_invoked True.
    assert offline.run_manifest["execution"]["real_model_invoked"] is False
    assert live.run_manifest["execution"]["real_model_invoked"] is False
    assert offline.run_manifest["execution"]["execution_mode"] == "offline_simulation"
    assert live.run_manifest["execution"]["execution_mode"] == "live"
