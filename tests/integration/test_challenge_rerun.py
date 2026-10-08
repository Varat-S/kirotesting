"""Milestone 13 — challenge loop + bounded targeted rerun (Req 11.4-11.5, 12.5, 15, 29, 32).

Proves: challenges mutate nothing; a material finding's rerun scope is the named
nodes PLUS their DAG descendants (and ONLY those — unrelated Financial narrow
agents are untouched); the loop is bounded to one automatic round; an unresolved
material finding after the bound raises a MANDATORY escalation.
"""

from __future__ import annotations

from app.models.orm import ChallengeFindingRow, Escalation
from app.services.agents.registry import default_registry
from app.services.audit.log import AuditLog
from app.services.escalation.engine import EscalationEngine
from app.services.orchestration.challenge_loop import (
    RULE_UNRESOLVED_CHALLENGE,
    ChallengeLoop,
    parse_findings,
)


def _loop(db_session, max_rounds=1):
    escalation = EscalationEngine(db_session, audit=AuditLog(db_session), case_id="C1",
                                  deterministic_ids=True)
    return ChallengeLoop(db_session, default_registry(), escalation=escalation,
                         max_rerun_rounds=max_rounds)


def _material_finding(agent_id):
    return {
        "challenges": [
            {
                "challenge_id": "ch1",
                "target": "cl1",
                "issue_type": "contradictory_parameter",
                "severity": "material",
                "reason": "Dependency finding contradicts the parameter.",
                "affected_agent_ids": [agent_id],
                "requires_reanalysis": True,
                "requested_rerun_scope": [agent_id],
            }
        ]
    }


def test_clean_challenge_requires_no_rerun(db_session):
    loop = _loop(db_session)
    findings = parse_findings({"challenges": []}, analysis_run_id="AR1",
                              topic="business")
    decision = loop.evaluate(findings, case_id="C1", snapshot_version=1,
                             round_index=0)
    assert not decision.requires_rerun
    assert not decision.escalated


def test_low_severity_challenge_is_not_material(db_session):
    loop = _loop(db_session)
    parsed = {"challenges": [{"challenge_id": "c", "issue_type": "nit",
                              "severity": "low", "reason": "minor"}]}
    findings = parse_findings(parsed, analysis_run_id="AR1", topic="business")
    decision = loop.evaluate(findings, case_id="C1", snapshot_version=1,
                             round_index=0)
    assert not decision.requires_rerun


def test_business_rerun_scope_includes_descendants_not_unrelated_financial(db_session):
    loop = _loop(db_session)
    findings = parse_findings(
        _material_finding("customer_supplier_contract"),
        analysis_run_id="AR1", topic="business",
    )
    decision = loop.evaluate(findings, case_id="C1", snapshot_version=1,
                             round_index=0)
    assert decision.requires_rerun
    scope = decision.rerun_scope
    # Named node + its descendants.
    assert "customer_supplier_contract" in scope
    assert "business_orchestrator" in scope
    assert "business_challenge" in scope
    assert "credit_orchestrator" in scope
    # Downstream Structuring depends on business_challenge => included.
    assert "structuring_orchestrator" in scope
    assert "business_concentration_mitigant" in scope
    # Unrelated Financial narrow agents are NOT rerun (Remediation 2).
    assert "ebitda_adjustments" not in scope
    assert "covenant_extraction" not in scope


def test_findings_are_persisted_and_challenge_mutates_nothing(db_session):
    loop = _loop(db_session)
    findings = parse_findings(_material_finding("business_model"),
                              analysis_run_id="AR1", topic="business")
    loop.evaluate(findings, case_id="C1", snapshot_version=1, round_index=0)
    rows = db_session.query(ChallengeFindingRow).all()
    assert len(rows) == 1
    assert rows[0].severity == "material"
    # A challenge is a finding only — it writes no parameter/score rows.
    from app.models.orm import ParameterResultRow, RiskScoreRow
    assert db_session.query(ParameterResultRow).count() == 0
    assert db_session.query(RiskScoreRow).count() == 0


def test_bounded_rerun_then_escalation(db_session):
    loop = _loop(db_session, max_rounds=1)
    findings = parse_findings(_material_finding("business_model"),
                              analysis_run_id="AR1", topic="business")
    # Round 0: a rerun is requested (within bound).
    d0 = loop.evaluate(findings, case_id="C1", snapshot_version=1, round_index=0)
    assert d0.requires_rerun and not d0.escalated

    # Round 1: still material but bound reached -> MANDATORY escalation, no rerun.
    d1 = loop.evaluate(findings, case_id="C1", snapshot_version=1, round_index=1)
    assert d1.escalated
    assert not d1.requires_rerun
    esc = db_session.query(Escalation).all()
    assert any(e.rule_id == RULE_UNRESOLVED_CHALLENGE and e.mandatory for e in esc)


def test_requested_scope_may_name_parameters(db_session):
    loop = _loop(db_session)
    parsed = {
        "challenges": [
            {"challenge_id": "c", "issue_type": "x", "severity": "high",
             "reason": "r", "affected_parameter_ids": ["net_leverage"],
             "requires_reanalysis": True,
             "requested_rerun_scope": ["financial_orchestrator", "net_leverage"]}
        ]
    }
    findings = parse_findings(parsed, analysis_run_id="AR1", topic="financial")
    decision = loop.evaluate(findings, case_id="C1", snapshot_version=1,
                             round_index=0)
    assert "financial_orchestrator" in decision.rerun_scope  # an agent
    assert "net_leverage" in decision.rerun_parameter_ids   # a parameter
