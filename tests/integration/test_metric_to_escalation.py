"""Metric-to-escalation flow + escalation precision/recall (Req 8.5, 8.6, 23.1; task 5.7).

Covers the design worked example ``R-TREND-LEV-01``:

    ND/EBITDA trending 1.73x -> 2.17x -> 3.00x against illustrative policy 3.50
    and peer P90 3.10 => NO policy breach, elevated peer anomaly, >50% 2-year
    deterioration => analyst review fires via R-TREND-LEV-01.

Also measures escalation precision/recall on a small seeded set of exceptions.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.orm import Case
from app.schemas.enums import FactStatus
from app.services.benchmarking.peers import PeerBenchmarker, PeerValue
from app.services.escalation.engine import CASE_STATUS_OPEN, EscalationEngine
from app.services.escalation.rules import (
    DEFAULT_POLICY,
    PolicyRuleEvaluator,
    Severity,
)
from app.services.metrics.definitions import MetricDefinitionRegistry
from app.services.metrics.engine import MetricEngine, MetricInput, MetricState
from app.services.metrics.trends import Direction, TrendAnalyzer, TrendPoint

METRIC = "net_debt_to_ebitda"


def _compute_leverage(engine: MetricEngine, definition, debt, cash, ebitda) -> float:
    result = engine.compute(
        definition,
        {
            "total_debt": MetricInput("total_debt", debt, FactStatus.VERIFIED, "d"),
            "unrestricted_cash": MetricInput(
                "unrestricted_cash", cash, FactStatus.VERIFIED, "c"
            ),
            "ebitda": MetricInput("ebitda", ebitda, FactStatus.VERIFIED, "e"),
        },
        metric_name=METRIC,
    )
    assert result.state is MetricState.OK
    return result.result


def test_r_trend_lev_01_worked_example(db_session: Session) -> None:
    db_session.add(Case(case_id="DAL", status=CASE_STATUS_OPEN))
    db_session.flush()

    defs = MetricDefinitionRegistry(db_session)
    defs.register_defaults()
    definition = defs.latest(METRIC)
    engine = MetricEngine(near_zero_floor=1.0, session=db_session, case_id="DAL")

    # Deterministic metric values that yield 1.73 / 2.17 / 3.00.
    # 1.73 = (1730-0)/1000 ; 2.17 = (2170)/1000 ; 3.00 = (3000)/1000
    lev_2021 = _compute_leverage(engine, definition, 1730.0, 0.0, 1000.0)
    lev_2022 = _compute_leverage(engine, definition, 2170.0, 0.0, 1000.0)
    lev_2023 = _compute_leverage(engine, definition, 3000.0, 0.0, 1000.0)
    assert (lev_2021, lev_2022, lev_2023) == pytest.approx((1.73, 2.17, 3.00))

    # Trend layer: rising leverage is deteriorating; 2-year change > 50%.
    trends = TrendAnalyzer()
    trend = trends.analyze(
        METRIC,
        [
            TrendPoint(2021, lev_2021),
            TrendPoint(2022, lev_2022),
            TrendPoint(2023, lev_2023),
        ],
    )
    assert trend.direction is Direction.DETERIORATING
    assert trend.pct_change_2y > 0.50  # (3.00-1.73)/1.73 = 0.734

    # Peer benchmark: small cohort, P90 provided illustratively as 3.10.
    bench = PeerBenchmarker()
    benchmark = bench.benchmark(
        METRIC,
        "DAL",
        borrower_value=lev_2023,
        peers=[
            PeerValue("UAL", 3.10),
            PeerValue("AAL", 2.90),
            PeerValue("LUV", 1.20),
            PeerValue("ALK", 1.80),
        ],
    )
    # Small cohort suppresses an unstable P90; the rule uses the configured
    # percentile value supplied by the benchmark layer (here illustrative 3.10).
    assert benchmark.percentiles_reliable is False
    peer_p90_value = 3.10

    # Three SEPARATE rule evaluations (no single score).
    ev = PolicyRuleEvaluator(DEFAULT_POLICY, policy_version=1)
    policy = ev.evaluate_policy_threshold(METRIC, lev_2023)
    peer = ev.evaluate_peer_benchmark(METRIC, lev_2023, peer_p90_value)
    hist = ev.evaluate_historical_deterioration(
        METRIC,
        trend.pct_change_2y,
        direction_is_adverse=(trend.direction is Direction.DETERIORATING),
        window="2y",
    )

    # The three concepts stay separate and are evaluated independently:
    #  * NO policy breach: 3.00 < 3.50.
    #  * Peer: 3.00 is elevated (just below P90 3.10) but does not strictly
    #    exceed it, so the strict peer-exceedance rule does not fire -- the peer
    #    layer is an anomaly SIGNAL only and never a pass/fail threshold.
    #  * Adverse 2-year deterioration > 50% -> R-TREND-LEV-01 fires analyst
    #    review (the design's explicitly named rule).
    assert policy.triggered is False
    assert peer.triggered is False  # 3.00 < P90 3.10 (not a credit threshold)
    assert hist.triggered is True and hist.rule_id == "R-TREND-LEV-01"
    assert hist.severity is Severity.REVIEW

    # Route to the escalation engine (event-based).
    esc_engine = EscalationEngine(db_session, case_id="DAL")
    created = [esc_engine.raise_from_outcome(o) for o in (policy, peer, hist)]
    raised = [c for c in created if c is not None]
    # Only the trend rule fired -> exactly one analyst-review escalation named
    # R-TREND-LEV-01 (the worked example's deterministic trigger).
    assert len(raised) == 1
    assert raised[0].rule_id == "R-TREND-LEV-01"
    assert raised[0].severity == Severity.REVIEW.value
    # Not mandatory, so the case stays open (analyst review, not a block).
    case = db_session.get(Case, "DAL")
    assert case.status == CASE_STATUS_OPEN
    assert esc_engine.has_unresolved_mandatory("DAL") is False
    # The analyst-review escalation cannot vanish: it stays open until resolved.
    assert len(esc_engine.open_escalations("DAL")) == 1


def test_escalation_precision_recall_on_seeded_exceptions(db_session: Session) -> None:
    """Seeded exceptions: measure escalation precision/recall == 1.0 (task 5.7).

    Each seeded borrower has a known expected trigger for the policy rule; the
    deterministic engine must flag exactly the true exceptions and nothing else.
    """
    defs = MetricDefinitionRegistry(db_session)
    defs.register_defaults()
    definition = defs.latest(METRIC)
    engine = MetricEngine(near_zero_floor=1.0)
    ev = PolicyRuleEvaluator(DEFAULT_POLICY, policy_version=1)

    # (debt, cash, ebitda, expected_policy_breach)
    seeded = {
        "within_1": (2000.0, 0.0, 1000.0, False),  # 2.0x
        "within_2": (3400.0, 0.0, 1000.0, False),  # 3.4x
        "breach_1": (3600.0, 0.0, 1000.0, True),   # 3.6x
        "breach_2": (4000.0, 0.0, 1000.0, True),   # 4.0x
        "edge_at_limit": (3500.0, 0.0, 1000.0, False),  # 3.5x == limit, not > limit
    }

    tp = fp = fn = tn = 0
    for debt, cash, ebitda, expected in seeded.values():
        value = _compute_leverage(engine, definition, debt, cash, ebitda)
        outcome = ev.evaluate_policy_threshold(METRIC, value)
        predicted = outcome.triggered
        if predicted and expected:
            tp += 1
        elif predicted and not expected:
            fp += 1
        elif not predicted and expected:
            fn += 1
        else:
            tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    assert precision == 1.0
    assert recall == 1.0
    assert (tp, fp, fn, tn) == (2, 0, 0, 3)
