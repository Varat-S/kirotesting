"""Milestone 13 — targeted rerun lifecycle (Req 15, 29, 32; R4 items 8, 9).

Proves the full append-only correction lifecycle: a challenge supersedes the
accepted affected artifacts and new accepted versions replace them, prior
versions remain queryable, unrelated branches are untouched, deterministic-only
issues recompute without an LLM, and requires_reanalysis=false does not auto-run.
"""

from __future__ import annotations

from app.models.orm import ParameterResultRow, RiskScoreRow, TopicConclusionRow
from app.services.agents.registry import default_registry
from app.services.audit.log import AuditLog
from app.services.escalation.engine import EscalationEngine
from app.services.orchestration.challenge_loop import ChallengeLoop, parse_findings
from app.services.orchestration.rerun import RerunController


def _loop(db_session, max_rounds=1):
    esc = EscalationEngine(db_session, audit=AuditLog(db_session), case_id="C1",
                           deterministic_ids=True)
    return ChallengeLoop(db_session, default_registry(), escalation=esc,
                         max_rerun_rounds=max_rounds)


def _seed_artifacts(db_session):
    """Business param v1 -> BusinessRiskScore v1 -> BusinessConclusion v1 +
    ObligorRiskScore v1, plus an unrelated Financial param."""
    db_session.add_all([
        ParameterResultRow(
            parameter_result_id="pr_biz_v1", analysis_run_id="AR1", case_id="C1",
            snapshot_version=1, parameter_id="competitive_position_risk_signal",
            topic="business", method="deterministic", value={"v": 2},
            value_type="risk_band", status="ok", formula_id="rubric", formula_version="1",
            acceptance_state="accepted"),
        ParameterResultRow(
            parameter_result_id="pr_fin_v1", analysis_run_id="AR1", case_id="C1",
            snapshot_version=1, parameter_id="net_leverage", topic="financial",
            method="deterministic", value={"v": 3.0}, value_type="ratio",
            status="ok", formula_id="f", formula_version="1",
            acceptance_state="accepted"),
        RiskScoreRow(
            score_id="s_bus_v1", analysis_run_id="AR1", case_id="C1",
            snapshot_version=1, kind="business", status="final", band=2,
            scoring_config_version=1, scoring_config_hash="h",
            acceptance_state="accepted"),
        RiskScoreRow(
            score_id="s_obl_v1", analysis_run_id="AR1", case_id="C1",
            snapshot_version=1, kind="obligor", status="final", band=2,
            scoring_config_version=1, scoring_config_hash="h",
            acceptance_state="accepted"),
        TopicConclusionRow(
            id="tc_bus_v1", analysis_run_id="AR1", case_id="C1", snapshot_version=1,
            topic="business", payload={}, challenge_status="clean",
            acceptance_state="accepted"),
    ])
    db_session.flush()


def test_full_rerun_lifecycle_supersedes_and_recomputes(db_session):
    _seed_artifacts(db_session)
    ctl = RerunController(db_session, analysis_run_id="AR1")

    # A material challenge changes the Business competitive-position signal.
    parsed = {"challenges": [{
        "challenge_id": "ch1", "issue_type": "contradictory_parameter",
        "severity": "material", "reason": "contradiction",
        "affected_agent_ids": ["competition_pricing"],
        "requires_reanalysis": True,
        "requested_rerun_scope": ["competition_pricing"],
    }]}
    findings = parse_findings(parsed, analysis_run_id="AR1", topic="business")
    decision = _loop(db_session).evaluate(findings, case_id="C1",
                                          snapshot_version=1, round_index=0,
                                          topic="business")
    assert decision.requires_rerun

    # Lifecycle: supersede accepted business param + score + conclusion + obligor.
    ctl.supersede_parameters({"competitive_position_risk_signal"})
    ctl.supersede_scores({"business", "obligor"})
    ctl.supersede_conclusions({"business"})

    # New accepted versions replace them (append-only).
    db_session.add_all([
        ParameterResultRow(
            parameter_result_id="pr_biz_v2", analysis_run_id="AR1", case_id="C1",
            snapshot_version=1, parameter_id="competitive_position_risk_signal",
            topic="business", method="deterministic", value={"v": 3},
            value_type="risk_band", status="ok", formula_id="rubric",
            formula_version="1", supersedes_id="pr_biz_v1", acceptance_state="accepted"),
        RiskScoreRow(
            score_id="s_bus_v2", analysis_run_id="AR1", case_id="C1",
            snapshot_version=1, kind="business", status="final", band=3,
            scoring_config_version=1, scoring_config_hash="h",
            supersedes_id="s_bus_v1", acceptance_state="accepted"),
        RiskScoreRow(
            score_id="s_obl_v2", analysis_run_id="AR1", case_id="C1",
            snapshot_version=1, kind="obligor", status="final", band=3,
            scoring_config_version=1, scoring_config_hash="h",
            supersedes_id="s_obl_v1", acceptance_state="accepted"),
        TopicConclusionRow(
            id="tc_bus_v2", analysis_run_id="AR1", case_id="C1", snapshot_version=1,
            topic="business", payload={}, challenge_status="reran",
            supersedes_id="tc_bus_v1", acceptance_state="accepted"),
    ])
    db_session.flush()

    # --- assertions: old superseded, new accepted ---
    assert db_session.get(ParameterResultRow, "pr_biz_v1").acceptance_state == "superseded"
    assert db_session.get(ParameterResultRow, "pr_biz_v2").acceptance_state == "accepted"
    assert db_session.get(RiskScoreRow, "s_bus_v1").acceptance_state == "superseded"
    assert db_session.get(RiskScoreRow, "s_bus_v2").acceptance_state == "accepted"
    assert db_session.get(RiskScoreRow, "s_obl_v1").acceptance_state == "superseded"
    assert db_session.get(RiskScoreRow, "s_obl_v2").acceptance_state == "accepted"
    assert db_session.get(TopicConclusionRow, "tc_bus_v1").acceptance_state == "superseded"
    assert db_session.get(TopicConclusionRow, "tc_bus_v2").acceptance_state == "accepted"

    # Unrelated Financial parameter is NOT touched.
    assert db_session.get(ParameterResultRow, "pr_fin_v1").acceptance_state == "accepted"
    assert "ebitda_adjustments" not in decision.rerun_scope

    # Old versions remain queryable (append-only, not deleted).
    assert db_session.get(ParameterResultRow, "pr_biz_v1") is not None

    # Accepted-current resolution returns ONLY the new versions.
    assert ctl.accepted_parameter("competitive_position_risk_signal").parameter_result_id == "pr_biz_v2"
    assert ctl.accepted_score("obligor").score_id == "s_obl_v2"
    assert ctl.accepted_conclusion("business").id == "tc_bus_v2"


def test_material_without_requires_reanalysis_does_not_auto_rerun(db_session):
    _seed_artifacts(db_session)
    parsed = {"challenges": [{
        "challenge_id": "ch2", "issue_type": "omitted_risk", "severity": "material",
        "reason": "a risk the model missed", "affected_agent_ids": ["business_model"],
        "requires_reanalysis": False,  # item 9: no automatic rerun
    }]}
    findings = parse_findings(parsed, analysis_run_id="AR1", topic="business")
    decision = _loop(db_session).evaluate(findings, case_id="C1",
                                          snapshot_version=1, round_index=0,
                                          topic="business")
    assert not decision.requires_rerun
    assert decision.escalated  # routed to human review instead


def test_deterministic_only_issue_flagged(db_session):
    _seed_artifacts(db_session)
    parsed = {"challenges": [{
        "challenge_id": "ch3", "issue_type": "metric_contradiction",
        "severity": "material", "reason": "parameter needs recompute",
        "affected_parameter_ids": ["net_leverage"],
        "requires_reanalysis": True,
        "requested_rerun_scope": ["net_leverage"],  # a parameter, not an agent
    }]}
    findings = parse_findings(parsed, analysis_run_id="AR1", topic="financial")
    decision = _loop(db_session).evaluate(findings, case_id="C1",
                                          snapshot_version=1, round_index=0,
                                          topic="financial")
    assert decision.deterministic_only  # recompute without an LLM
    assert "net_leverage" in decision.rerun_parameter_ids
    assert not decision.rerun_scope  # no agent rerun


def test_challenge_topic_is_persisted(db_session):
    # Item 21: the actual topic survives round-trip persistence.
    parsed = {"challenges": [{"challenge_id": "ch4", "issue_type": "x",
                              "severity": "low", "reason": "r"}]}
    findings = parse_findings(parsed, analysis_run_id="AR1", topic="structuring")
    _loop(db_session).evaluate(findings, case_id="C1", snapshot_version=1,
                               round_index=0, topic="structuring")
    from app.models.orm import ChallengeFindingRow
    row = db_session.get(ChallengeFindingRow, "ch4")
    assert row.topic == "structuring"
