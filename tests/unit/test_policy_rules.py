"""Versioned policy/rule layer tests (Req 11.1-11.6; task 5.5)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.orm import RuleVersion
from app.services.escalation.rules import (
    DEFAULT_POLICY,
    EscalationCategory,
    PolicyRuleEvaluator,
    RuleConcept,
    RuleRegistry,
    Severity,
)


def _evaluator() -> PolicyRuleEvaluator:
    return PolicyRuleEvaluator(DEFAULT_POLICY, policy_version=1)


def test_three_concepts_are_distinct_rules() -> None:
    """Req 11.1: policy / peer / historical are three separate evaluations."""
    ev = _evaluator()
    policy = ev.evaluate_policy_threshold("net_debt_to_ebitda", 3.0)
    peer = ev.evaluate_peer_benchmark("net_debt_to_ebitda", 3.0, 3.10)
    trend = ev.evaluate_historical_deterioration(
        "net_debt_to_ebitda", 0.73, direction_is_adverse=True
    )
    concepts = {policy.concept, peer.concept, trend.concept}
    assert concepts == {
        RuleConcept.POLICY_THRESHOLD,
        RuleConcept.PEER_BENCHMARK,
        RuleConcept.HISTORICAL_DETERIORATION,
    }
    # Distinct rule ids, never collapsed into one score.
    assert len({policy.rule_id, peer.rule_id, trend.rule_id}) == 3


def test_policy_breach_is_mandatory() -> None:
    ev = _evaluator()
    breach = ev.evaluate_policy_threshold("net_debt_to_ebitda", 3.6)  # > 3.5
    assert breach.triggered is True
    assert breach.severity is Severity.MANDATORY
    assert breach.category is EscalationCategory.FINANCIAL_RULES


def test_policy_within_limit_does_not_trigger() -> None:
    ev = _evaluator()
    ok = ev.evaluate_policy_threshold("net_debt_to_ebitda", 3.0)  # < 3.5
    assert ok.triggered is False


def test_peer_anomaly_is_review_not_mandatory() -> None:
    """Req 10.7: peer benchmark is an anomaly signal -> review, never mandatory."""
    ev = _evaluator()
    anomaly = ev.evaluate_peer_benchmark("net_debt_to_ebitda", 3.0, 3.10)
    # 3.0 < P90 3.10 -> not yet anomalous.
    assert anomaly.triggered is False
    elevated = ev.evaluate_peer_benchmark("net_debt_to_ebitda", 3.2, 3.10)
    assert elevated.triggered is True
    assert elevated.severity is Severity.REVIEW


def test_suppressed_percentile_does_not_fire_peer_rule() -> None:
    """A suppressed (None) percentile means the peer rule cannot fire."""
    ev = _evaluator()
    outcome = ev.evaluate_peer_benchmark("net_debt_to_ebitda", 3.2, None)
    assert outcome.triggered is False


def test_historical_deterioration_fires_above_threshold() -> None:
    ev = _evaluator()
    # 2y change of +73% is adverse and exceeds the 50% review threshold.
    trend = ev.evaluate_historical_deterioration(
        "net_debt_to_ebitda", 0.73, direction_is_adverse=True, window="2y"
    )
    assert trend.triggered is True
    assert trend.severity is Severity.REVIEW


def test_historical_non_adverse_move_does_not_fire() -> None:
    ev = _evaluator()
    # A big improvement (not adverse) must not trigger a deterioration review.
    trend = ev.evaluate_historical_deterioration(
        "net_debt_to_ebitda", -0.73, direction_is_adverse=False
    )
    assert trend.triggered is False


def test_rule_registry_versions_and_preserves_history(db_session: Session) -> None:
    """Req 11.5/11.6: a rule change is a new version; history is preserved."""
    reg = RuleRegistry(db_session)
    v1 = reg.register(
        "R-POLICY-LEV-01",
        concept=RuleConcept.POLICY_THRESHOLD,
        category=EscalationCategory.FINANCIAL_RULES,
        definition={"policy_limit": 3.5},
        config_kind="policy",
        config_version=1,
    )
    assert v1.version == 1
    # Idempotent re-register.
    again = reg.register(
        "R-POLICY-LEV-01",
        concept=RuleConcept.POLICY_THRESHOLD,
        category=EscalationCategory.FINANCIAL_RULES,
        definition={"policy_limit": 3.5},
    )
    assert again.version == 1
    # Changed definition -> new version, old preserved.
    v2 = reg.register(
        "R-POLICY-LEV-01",
        concept=RuleConcept.POLICY_THRESHOLD,
        category=EscalationCategory.FINANCIAL_RULES,
        definition={"policy_limit": 4.0},
    )
    assert v2.version == 2
    rows = (
        db_session.query(RuleVersion)
        .filter_by(rule_id="R-POLICY-LEV-01")
        .order_by(RuleVersion.version)
        .all()
    )
    assert [r.definition["policy_limit"] for r in rows] == [3.5, 4.0]
