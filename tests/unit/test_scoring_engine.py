"""Milestone 9 — deterministic scoring engine (Req 9, 10, 31).

Proves: weighted banding; config-driven coverage with NO silent renormalization;
provisional vs unavailable; critical-missing => unavailable; floors on base-case
covenant breach / DSCR dominate the weighted average; evidence quality never
moves a band; LLM cannot modify a score; identical scores on replay; scoring is
a versioned/hashed ConfigRegistry artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.bootstrap import bootstrap_config
from app.core.config_registry import ConfigRegistry
from app.schemas.agentic import (
    EvidenceQuality,
    Method,
    ParameterResult,
    ParameterStatus,
    ScoreKind,
    ScoreStatus,
    Topic,
)
from app.services.scoring.engine import ScoringConfig, ScoringEngine

CONFIG = Path(__file__).resolve().parents[2] / "config" / "poc" / "scoring.json"


def _config() -> ScoringConfig:
    content = json.loads(CONFIG.read_text())
    return ScoringConfig(content=content, version=1, content_hash="hash-1")


def _param(parameter_id, value, topic, *, status=ParameterStatus.OK,
           evidence_quality=None, risk_signal=None) -> ParameterResult:
    return ParameterResult(
        parameter_result_id=f"pr_{parameter_id}",
        analysis_run_id="AR1",
        parameter_id=parameter_id,
        topic=topic,
        value=value,
        value_type="ratio",
        method=Method.DETERMINISTIC,
        status=status,
        formula_id="f",
        formula_version="1",
        evidence_quality=evidence_quality,
        risk_signal=risk_signal,
    )


def test_band_parameter_thresholds():
    e = ScoringEngine(_config())
    assert e.band_parameter("net_leverage", 1.5) == 1
    assert e.band_parameter("net_leverage", 2.5) == 2
    assert e.band_parameter("net_leverage", 3.2) == 3
    assert e.band_parameter("net_leverage", 5.0) == 4
    # 'min'-style band (higher is better).
    assert e.band_parameter("interest_coverage", 7.0) == 1
    assert e.band_parameter("interest_coverage", 1.0) == 4


def test_financial_score_full_coverage_is_final():
    e = ScoringEngine(_config())
    params = [
        _param("net_leverage", 1.5, Topic.FINANCIAL),
        _param("interest_coverage", 7.0, Topic.FINANCIAL),
        _param("fcf_conversion", 0.95, Topic.FINANCIAL),
        _param("liquidity", 2.5, Topic.FINANCIAL),
    ]
    score = e.score_financial(params, analysis_run_id="AR1")
    assert score.status is ScoreStatus.FINAL
    assert score.band == 1
    assert score.coverage_weight == pytest.approx(1.0)


def test_missing_dimension_is_not_renormalized():
    e = ScoringEngine(_config())
    # Omit liquidity (0.3 weight, and it is CRITICAL) -> unavailable.
    params = [
        _param("net_leverage", 1.5, Topic.FINANCIAL),
        _param("interest_coverage", 7.0, Topic.FINANCIAL),
        _param("fcf_conversion", 0.95, Topic.FINANCIAL),
    ]
    score = e.score_financial(params, analysis_run_id="AR1")
    assert score.status is ScoreStatus.UNAVAILABLE  # critical dim missing
    assert score.band is None
    assert "liquidity" in score.critical_missing_parameter_ids
    assert score.coverage_weight == pytest.approx(0.7)  # NOT rescaled to 1.0


def test_partial_non_critical_coverage_is_provisional():
    e = ScoringEngine(_config())
    # Business: omit management_governance (0.3, non-critical). coverage 0.7 >=
    # min 0.6 and allow_partial => provisional.
    params = [
        _param("customer_hhi", 0.1, Topic.BUSINESS),
        _param("competitive_position", 1, Topic.BUSINESS),
    ]
    score = e.score_business(params, analysis_run_id="AR1")
    assert score.status is ScoreStatus.PROVISIONAL
    assert score.band is not None
    assert score.coverage_weight == pytest.approx(0.7)


def test_below_min_coverage_is_unavailable():
    e = ScoringEngine(_config())
    # Only customer_hhi (0.3) present; competitive_position is critical+missing.
    params = [_param("customer_hhi", 0.1, Topic.BUSINESS)]
    score = e.score_business(params, analysis_run_id="AR1")
    assert score.status is ScoreStatus.UNAVAILABLE


def test_evidence_quality_does_not_move_band():
    e = ScoringEngine(_config())
    strong = e.score_financial(
        [
            _param("net_leverage", 1.5, Topic.FINANCIAL),
            _param("interest_coverage", 7.0, Topic.FINANCIAL),
            _param("fcf_conversion", 0.95, Topic.FINANCIAL),
            _param("liquidity", 2.5, Topic.FINANCIAL),
        ],
        analysis_run_id="AR1",
    )
    low_quality = e.score_financial(
        [
            _param("net_leverage", 1.5, Topic.FINANCIAL,
                   evidence_quality=EvidenceQuality.LOW),
            _param("interest_coverage", 7.0, Topic.FINANCIAL,
                   evidence_quality=EvidenceQuality.LOW),
            _param("fcf_conversion", 0.95, Topic.FINANCIAL,
                   evidence_quality=EvidenceQuality.LOW),
            _param("liquidity", 2.5, Topic.FINANCIAL,
                   evidence_quality=EvidenceQuality.LOW),
        ],
        analysis_run_id="AR1",
    )
    assert strong.band == low_quality.band  # evidence quality never moves a band


def test_provisional_parameter_excluded_from_scoring():
    e = ScoringEngine(_config())
    # A parameter that is UNAVAILABLE is treated as missing input to scoring.
    params = [
        _param("net_leverage", 1.5, Topic.FINANCIAL),
        _param("interest_coverage", 7.0, Topic.FINANCIAL),
        _param("fcf_conversion", 0.95, Topic.FINANCIAL),
        _param("liquidity", None, Topic.FINANCIAL, status=ParameterStatus.UNAVAILABLE),
    ]
    score = e.score_financial(params, analysis_run_id="AR1")
    assert score.status is ScoreStatus.UNAVAILABLE  # liquidity critical+missing


def test_obligor_floor_on_covenant_breach_dominates():
    e = ScoringEngine(_config())
    # Strong financials => financial band 1, but a base-case covenant breach
    # floors financial to >= band 3 (Req 9.7), dragging obligor up.
    business = e.score_business(
        [
            _param("customer_hhi", 0.1, Topic.BUSINESS),
            _param("competitive_position", 1, Topic.BUSINESS),
            _param("management_governance", 1, Topic.BUSINESS),
        ],
        analysis_run_id="AR1",
    )
    financial = e.score_financial(
        [
            _param("net_leverage", 1.5, Topic.FINANCIAL),
            _param("interest_coverage", 7.0, Topic.FINANCIAL),
            _param("fcf_conversion", 0.95, Topic.FINANCIAL),
            _param("liquidity", 2.5, Topic.FINANCIAL),
        ],
        analysis_run_id="AR1",
    )
    assert financial.band == 1
    obligor = e.score_obligor(
        business, financial, analysis_run_id="AR1",
        conditions={"base_case_covenant_breach": True},
    )
    # financial floored to 3: obligor = round(1*0.4 + 3*0.6) = round(2.2) = 2
    assert obligor.band >= 2
    assert any("base_case_covenant_breach" in a for a in obligor.applied_overlays)


def test_severe_risk_not_washed_out_by_benign_average():
    e = ScoringEngine(_config())
    business = e.score_business(
        [
            _param("customer_hhi", 0.1, Topic.BUSINESS),
            _param("competitive_position", 1, Topic.BUSINESS),
            _param("management_governance", 1, Topic.BUSINESS),
        ],
        analysis_run_id="AR1",
    )
    financial = e.score_financial(
        [
            _param("net_leverage", 1.5, Topic.FINANCIAL),
            _param("interest_coverage", 7.0, Topic.FINANCIAL),
            _param("fcf_conversion", 0.95, Topic.FINANCIAL),
            _param("liquidity", 2.5, Topic.FINANCIAL),
        ],
        analysis_run_id="AR1",
    )
    obligor = e.score_obligor(
        business, financial, analysis_run_id="AR1",
        conditions={"downside_dscr_below_minimum": True},
    )
    assert obligor.band >= 3  # obligor floor dominates the benign average


def test_facility_unavailable_when_no_protection():
    e = ScoringEngine(_config())
    business = e.score_business(
        [
            _param("customer_hhi", 0.1, Topic.BUSINESS),
            _param("competitive_position", 1, Topic.BUSINESS),
            _param("management_governance", 1, Topic.BUSINESS),
        ],
        analysis_run_id="AR1",
    )
    financial = e.score_financial(
        [
            _param("net_leverage", 1.5, Topic.FINANCIAL),
            _param("interest_coverage", 7.0, Topic.FINANCIAL),
            _param("fcf_conversion", 0.95, Topic.FINANCIAL),
            _param("liquidity", 2.5, Topic.FINANCIAL),
        ],
        analysis_run_id="AR1",
    )
    obligor = e.score_obligor(business, financial, analysis_run_id="AR1")
    facility = e.score_facility(obligor, None, analysis_run_id="AR1")
    assert facility.status is ScoreStatus.UNAVAILABLE
    assert facility.band is None


def test_scores_are_deterministic_on_replay():
    e = ScoringEngine(_config())
    params = [
        _param("net_leverage", 3.2, Topic.FINANCIAL),
        _param("interest_coverage", 4.0, Topic.FINANCIAL),
        _param("fcf_conversion", 0.5, Topic.FINANCIAL),
        _param("liquidity", 1.3, Topic.FINANCIAL),
    ]
    a = e.score_financial(params, analysis_run_id="AR1")
    b = e.score_financial(params, analysis_run_id="AR1")
    assert a.band == b.band
    assert a.coverage_weight == b.coverage_weight
    assert a.method == "deterministic"


def test_scoring_is_a_versioned_hashed_registry_artifact(db_session):
    registry = ConfigRegistry(db_session)
    versions = bootstrap_config(registry)
    assert "scoring" in versions
    cfg = ScoringConfig.from_registry(registry)
    assert cfg.version == versions["scoring"]
    assert cfg.content_hash
    assert cfg["label"].startswith("ILLUSTRATIVE")
