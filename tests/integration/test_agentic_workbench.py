"""Milestone 19 — read-only agentic workbench views (Req 21).

Proves: the agentic workbench surfaces all required sections scoped to an
explicit analysis_run_id; multiple runs per snapshot are listable; assembling a
view mutates nothing; the API routes are GET-only.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.api.deps import get_session
from app.models.orm import (
    AgenticAnalysisRun,
    AgentRunRow,
    CandidateFeasibilityRow,
    CandidateStructureRow,
    ChallengeFindingRow,
    EvidencePacketRow,
    ParameterResultRow,
    RiskScoreRow,
    TopicConclusionRow,
)
from app.services.workbench.agentic import (
    AGENTIC_SECTIONS,
    AgenticWorkbenchService,
    AnalysisRunNotFoundError,
)


def _seed(db_session, run_id="AR1", case_id="C1"):
    db_session.add(AgenticAnalysisRun(
        analysis_run_id=run_id, case_id=case_id, evidence_snapshot_version=1,
        status="completed", started_at=datetime.now(timezone.utc),
        router_version="router-1", agent_registry_version="reg-1",
        agent_registry_hash="rh", scoring_config_version=1, scoring_config_hash="sh",
        total_input_tokens=100, total_output_tokens=40, total_tokens=140))
    db_session.add_all([
        ParameterResultRow(
            parameter_result_id="pr1", analysis_run_id=run_id, case_id=case_id,
            snapshot_version=1, parameter_id="customer_hhi", topic="business",
            method="deterministic", value={"v": 0.3}, value_type="index", status="ok",
            formula_id="hhi", formula_version="1", evidence_quality="high",
            acceptance_state="accepted"),
        RiskScoreRow(
            score_id="s_obl", analysis_run_id=run_id, case_id=case_id,
            snapshot_version=1, kind="obligor", status="final", band=2,
            scoring_config_version=1, scoring_config_hash="sh",
            acceptance_state="accepted"),
        RiskScoreRow(
            score_id="s_fac", analysis_run_id=run_id, case_id=case_id,
            snapshot_version=1, kind="facility", status="unavailable", band=None,
            scoring_config_version=1, scoring_config_hash="sh",
            acceptance_state="accepted"),
        AgentRunRow(
            run_id="ar1", analysis_run_id=run_id, agent_id="business_model",
            agent_definition_hash="adh", topic="business", case_id=case_id,
            snapshot_version=1, prompt_id="business_model_v1.0", prompt_hash="ph",
            model_id="fake", input_hash="ih", validation_status="valid",
            execution_wave=0, latency_ms=12, usage={"total_tokens": 50}),
        EvidencePacketRow(
            packet_hash="pk1", analysis_run_id=run_id, case_id=case_id,
            snapshot_version=1, router_version="router-1", agent_id="business_model",
            payload={}, evidence_ids=["E1"]),
        ChallengeFindingRow(
            challenge_id="ch1", analysis_run_id=run_id, case_id=case_id,
            snapshot_version=1, topic="business", issue_type="omitted_risk",
            severity="material", reason="r", requires_reanalysis=True),
        CandidateStructureRow(
            candidate_id="cs1", analysis_run_id=run_id, case_id=case_id,
            snapshot_version=1, payload={"facility_amount": 100}),
        CandidateFeasibilityRow(
            feasibility_id="fe1", analysis_run_id=run_id, candidate_id="cs1",
            feasible=False, feasibility_detail={"failures": ["ltv_exceeds_policy"],
                                                "unavailable": []},
            acceptance_state="accepted"),
        TopicConclusionRow(
            id="tc1", analysis_run_id=run_id, case_id=case_id, snapshot_version=1,
            topic="business", payload={"overall_assessment": {"claim_id": "a"}},
            challenge_status="clean", acceptance_state="accepted"),
    ])
    db_session.flush()


def test_assemble_surfaces_all_sections(db_session):
    _seed(db_session)
    view = AgenticWorkbenchService(db_session).assemble("AR1")
    expected = {k for k, _ in AGENTIC_SECTIONS}
    assert set(view.sections) == expected
    assert view.sections["obligor_score"]["band"] == 2
    assert view.sections["facility_score"]["status"] == "unavailable"
    assert view.sections["parameters"][0]["parameter_id"] == "customer_hhi"
    assert view.sections["policy_failures"][0]["failures"] == ["ltv_exceeds_policy"]
    assert view.sections["token_usage"]["total_tokens"] == 140
    assert view.sections["challenges"][0]["severity"] == "material"
    assert view.section_order[0]["key"] == "analysis_runs"


def test_assembly_is_read_only(db_session):
    _seed(db_session)
    before = {m: db_session.query(m).count() for m in
              (ParameterResultRow, RiskScoreRow, AgentRunRow, ChallengeFindingRow)}
    AgenticWorkbenchService(db_session).assemble("AR1")
    after = {m: db_session.query(m).count() for m in before}
    assert before == after  # viewing mutates nothing


def test_multiple_runs_per_snapshot_listed(db_session):
    _seed(db_session, run_id="AR1")
    # A second run over the SAME snapshot (e.g. force_regenerate) — distinct run.
    db_session.add(AgenticAnalysisRun(
        analysis_run_id="AR2", case_id="C1", evidence_snapshot_version=1,
        status="completed", started_at=datetime.now(timezone.utc),
        router_version="router-1", agent_registry_version="reg-1",
        agent_registry_hash="rh", scoring_config_version=1, scoring_config_hash="sh"))
    db_session.flush()
    runs = AgenticWorkbenchService(db_session).runs_for_case("C1")
    assert {r["analysis_run_id"] for r in runs} == {"AR1", "AR2"}


def test_unknown_run_raises(db_session):
    with pytest.raises(AnalysisRunNotFoundError):
        AgenticWorkbenchService(db_session).assemble("nope")


def test_api_routes_are_get_only(db_session):
    _seed(db_session)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db_session
    client = TestClient(app)

    runs = client.get("/cases/C1/agentic/runs")
    assert runs.status_code == 200
    assert any(r["analysis_run_id"] == "AR1" for r in runs.json()["runs"])

    view = client.get("/cases/C1/agentic/runs/AR1")
    assert view.status_code == 200
    assert view.json()["analysis_run_id"] == "AR1"

    # A run that belongs to a different case 404s; mutating verbs are not routed.
    assert client.get("/cases/OTHER/agentic/runs/AR1").status_code == 404
    assert client.post("/cases/C1/agentic/runs/AR1").status_code in (404, 405)
    app.dependency_overrides.clear()