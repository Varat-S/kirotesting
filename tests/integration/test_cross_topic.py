"""Milestone 17 — Structuring orchestration + final cross-topic (Req 13.6-13.9, 16).

17.1: the Structuring Orchestrator may select only a FEASIBLE candidate (cannot
override a deterministic failure); facility vs obligor risk stay separate.
17.2: the Credit Orchestrator consumes only accepted, same-run conclusions and
scores; the Cross-Topic Challenge detects contradictions BETWEEN topics and
drives a bounded rerun/escalation.
"""

from __future__ import annotations

import uuid

import pytest

from app.schemas.agentic import (
    AcceptanceState,
    CandidateFeasibility,
    ClaimCategory,
    ConclusionClaim,
    RiskScore,
    ScoreKind,
    ScoreStatus,
    Topic,
    TopicConclusion,
)
from app.services.agents.registry import default_registry
from app.services.audit.log import AuditLog
from app.services.escalation.engine import EscalationEngine
from app.services.orchestration.challenge_loop import ChallengeLoop, parse_findings
from app.services.orchestration.credit import (
    CrossTopicInputError,
    verify_cross_topic_inputs,
)
from app.services.orchestration.structuring import (
    InfeasibleSelectionError,
    feasible_candidate_ids,
    validate_selection,
)


def _verdict(candidate_id, feasible):
    return CandidateFeasibility(
        feasibility_id=f"fe_{uuid.uuid4().hex[:8]}",
        analysis_run_id="AR1",
        candidate_id=candidate_id,
        feasible=feasible,
        feasibility_detail={},
    )


# --- 17.1 structuring selection ---------------------------------------------


def test_only_feasible_candidate_is_selectable():
    verdicts = [_verdict("cs1", True), _verdict("cs2", False)]
    assert feasible_candidate_ids(verdicts) == {"cs1"}
    assert validate_selection("cs1", verdicts) == "cs1"


def test_selecting_infeasible_candidate_is_rejected():
    verdicts = [_verdict("cs1", True), _verdict("cs2", False)]
    with pytest.raises(InfeasibleSelectionError):
        validate_selection("cs2", verdicts)  # cannot override deterministic failure


def test_selecting_unknown_candidate_is_rejected():
    with pytest.raises(InfeasibleSelectionError):
        validate_selection("ghost", [_verdict("cs1", True)])


def test_no_selection_allowed_when_nothing_feasible():
    verdicts = [_verdict("cs1", False)]
    assert validate_selection(None, verdicts) is None


def test_facility_and_obligor_scores_are_separate_kinds():
    # (sanity) the two scores are distinct kinds, never conflated.
    assert ScoreKind.OBLIGOR != ScoreKind.FACILITY


# --- 17.2 cross-topic input guards ------------------------------------------


def _claim():
    return ConclusionClaim(claim_id="c", category=ClaimCategory.ASSESSMENT,
                           text="x", parameter_result_ids=[], evidence_ids=[])


def _conclusion(topic, run="AR1", accepted=True):
    return TopicConclusion(
        topic=topic, analysis_run_id=run, overall_assessment=_claim(),
        score_reference="S:1", orchestrator_run_id="run",
        acceptance_state=(AcceptanceState.ACCEPTED if accepted
                          else AcceptanceState.SUPERSEDED),
    )


def _score(kind, run="AR1", accepted=True):
    return RiskScore(
        score_id=f"s_{kind.value}", analysis_run_id=run, kind=kind,
        status=ScoreStatus.FINAL, band=2, scoring_config_version=1,
        scoring_config_hash="h",
        acceptance_state=(AcceptanceState.ACCEPTED if accepted
                          else AcceptanceState.SUPERSEDED),
    )


def test_cross_topic_accepts_same_run_accepted_inputs():
    verify_cross_topic_inputs(
        analysis_run_id="AR1",
        conclusions=[_conclusion(Topic.BUSINESS), _conclusion(Topic.FINANCIAL)],
        scores=[_score(ScoreKind.OBLIGOR), _score(ScoreKind.FACILITY)],
    )  # no raise


def test_cross_topic_rejects_stale_conclusion():
    with pytest.raises(CrossTopicInputError, match="stale|accepted"):
        verify_cross_topic_inputs(
            analysis_run_id="AR1",
            conclusions=[_conclusion(Topic.BUSINESS, accepted=False)],
            scores=[],
        )


def test_cross_topic_rejects_cross_run_score():
    with pytest.raises(CrossTopicInputError, match="run"):
        verify_cross_topic_inputs(
            analysis_run_id="AR1",
            conclusions=[],
            scores=[_score(ScoreKind.OBLIGOR, run="AR2")],
        )


# --- 17.2 cross-topic challenge drives targeted rerun -----------------------


def test_cross_topic_contradiction_triggers_targeted_rerun(db_session):
    escalation = EscalationEngine(db_session, audit=AuditLog(db_session),
                                  case_id="C1", deterministic_ids=True)
    loop = ChallengeLoop(db_session, default_registry(), escalation=escalation,
                         max_rerun_rounds=1)
    # "revenues highly recurring" (business) vs "revenue volatility unusually
    # high" (financial) -> material cross-topic contradiction naming business.
    parsed = {
        "challenges": [
            {
                "challenge_id": "xt1",
                "issue_type": "cross_topic_contradiction",
                "severity": "material",
                "reason": "Business claims recurring revenue; Financial shows high "
                          "revenue volatility.",
                "affected_agent_ids": ["business_model"],
                "requires_reanalysis": True,
                "requested_rerun_scope": ["business_model"],
            }
        ]
    }
    findings = parse_findings(parsed, analysis_run_id="AR1", topic="cross_topic")
    decision = loop.evaluate(findings, case_id="C1", snapshot_version=1,
                             round_index=0)
    assert decision.requires_rerun
    assert "business_model" in decision.rerun_scope
    assert "credit_orchestrator" in decision.rerun_scope  # descendant recompute
