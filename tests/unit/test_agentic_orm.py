"""Milestone 1.2 — agentic ORM tables + additive migration (Req 19, 24.5, 27, 32).

Covers: tables are created by ``init_db``; the agentic rows round-trip; the
additive migration adds the tables to a legacy database without rewriting any
existing evidence/snapshot/audit/review payload; migration is idempotent.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import create_engine, inspect, text

from app.migrate import migrate
from app.models.base import init_db
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

AGENTIC_TABLES = {
    "agentic_analysis_runs",
    "parameter_results",
    "agent_runs",
    "evidence_packets",
    "topic_conclusions",
    "challenge_findings",
    "candidate_structures",
    "candidate_feasibility",
    "risk_scores",
}


def test_init_db_creates_agentic_tables(db_session):
    inspector = inspect(db_session.get_bind())
    tables = set(inspector.get_table_names())
    assert AGENTIC_TABLES <= tables


def test_candidate_structures_has_no_selected_column(db_session):
    inspector = inspect(db_session.get_bind())
    cols = {c["name"] for c in inspector.get_columns("candidate_structures")}
    assert "selected" not in cols  # Remediation 11: selection is not a mutable flag


def test_agent_runs_carries_cache_and_definition_columns(db_session):
    inspector = inspect(db_session.get_bind())
    cols = {c["name"] for c in inspector.get_columns("agent_runs")}
    assert {"agent_definition_hash", "response_schema_hash", "cache_key"} <= cols


def test_risk_scores_has_status_and_coverage_columns(db_session):
    inspector = inspect(db_session.get_bind())
    cols = {c["name"] for c in inspector.get_columns("risk_scores")}
    assert {
        "status",
        "band",
        "coverage_weight",
        "missing_required_parameter_ids",
        "critical_missing_parameter_ids",
    } <= cols


def test_analytical_rows_round_trip(db_session):
    run = AgenticAnalysisRun(
        analysis_run_id="AR1",
        case_id="C1",
        evidence_snapshot_version=1,
        started_at=datetime.now(timezone.utc),
        router_version="router-1",
        agent_registry_version="reg-1",
        agent_registry_hash="rh",
        scoring_config_version=1,
        scoring_config_hash="sh",
    )
    db_session.add(run)
    db_session.add(
        ParameterResultRow(
            parameter_result_id="PR1",
            analysis_run_id="AR1",
            case_id="C1",
            snapshot_version=1,
            parameter_id="net_leverage",
            topic="financial",
            method="deterministic",
            value={"n": 3.07},
            value_type="ratio",
            status="ok",
            formula_id="net_debt_to_ebitda",
            formula_version="1",
        )
    )
    db_session.add(
        AgentRunRow(
            run_id="run-1",
            analysis_run_id="AR1",
            agent_id="business_model",
            agent_definition_hash="adh",
            topic="business",
            case_id="C1",
            snapshot_version=1,
            prompt_id="business_model_v1.0",
            prompt_hash="ph",
            model_id="fake-deterministic-v1",
            input_hash="ih",
            cache_key="ck",
            validation_status="valid",
            execution_wave=0,
        )
    )
    db_session.add(
        CandidateStructureRow(
            candidate_id="CS1",
            analysis_run_id="AR1",
            case_id="C1",
            snapshot_version=1,
            payload={"facility_amount": 1000.0},
        )
    )
    db_session.add(
        CandidateFeasibilityRow(
            feasibility_id="FE1",
            analysis_run_id="AR1",
            candidate_id="CS1",
            feasible=False,
            feasibility_detail={"reason": "policy breach"},
        )
    )
    db_session.add(
        RiskScoreRow(
            score_id="S1",
            analysis_run_id="AR1",
            case_id="C1",
            snapshot_version=1,
            kind="facility",
            status="unavailable",
            band=None,
            scoring_config_version=1,
            scoring_config_hash="sh",
            missing_required_parameter_ids=["facility_terms"],
        )
    )
    db_session.add(
        TopicConclusionRow(
            id="TC1",
            analysis_run_id="AR1",
            case_id="C1",
            snapshot_version=1,
            topic="business",
            payload={"overall_assessment": {"claim_id": "CL0"}},
        )
    )
    db_session.add(
        ChallengeFindingRow(
            challenge_id="CH1",
            analysis_run_id="AR1",
            case_id="C1",
            snapshot_version=1,
            topic="business",
            target="CL0",
            issue_type="unsupported_claim",
            severity="material",
            reason="under review",
        )
    )
    db_session.add(
        EvidencePacketRow(
            packet_hash="PH1",
            analysis_run_id="AR1",
            case_id="C1",
            snapshot_version=1,
            router_version="router-1",
            agent_id="business_model",
            payload={"facts": []},
            evidence_ids=["E1"],
        )
    )
    db_session.flush()

    assert db_session.get(AgenticAnalysisRun, "AR1").status == "created"
    assert db_session.get(ParameterResultRow, "PR1").acceptance_state == "accepted"
    assert db_session.get(RiskScoreRow, "S1").band is None
    assert db_session.get(CandidateFeasibilityRow, "FE1").feasible is False


def test_migration_adds_agentic_tables_to_legacy_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    init_db(engine)
    # Simulate a pre-agentic database: drop the new tables and seed a snapshot.
    with engine.begin() as connection:
        for table in AGENTIC_TABLES:
            connection.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
        connection.execute(
            text(
                "INSERT INTO snapshots "
                "(case_id,snapshot_type,snapshot_version,schema_version,finalized,"
                "config_versions,payload,created_at) "
                "VALUES ('C','final_case',1,'1.1',1,'{}',:payload,'2024-01-01')"
            ),
            {"payload": '{"preserve":true}'},
        )
    with engine.connect() as connection:
        before = set(inspect(connection).get_table_names())
    assert not (AGENTIC_TABLES & before)  # legacy really lacks them

    changes = migrate(engine)
    # The migration explicitly REPORTS each new agentic table (observable
    # upgrade-in-place, not an implicit init_db side effect).
    for table in AGENTIC_TABLES:
        assert f"table:{table}" in changes
    assert migrate(engine) == []  # idempotent: nothing new on a second run

    with engine.connect() as connection:
        after = set(inspect(connection).get_table_names())
        assert AGENTIC_TABLES <= after
        # Existing payload is untouched.
        assert (
            connection.execute(text("SELECT payload FROM snapshots")).scalar()
            == '{"preserve":true}'
        )
    engine.dispose()
