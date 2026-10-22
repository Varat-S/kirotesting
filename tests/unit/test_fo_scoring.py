"""Financial Overview scoring: the benchmark curve, fixed bands, rating notches and the composite."""

from __future__ import annotations

import math

import pytest

from app.services.fo.measures import DSCR_BANDS, ICR_BANDS, credit_rating, risk_level, risk_score
from app.services.fo.scoring import (
    SCORING_DEFAULTS,
    composite_leverage,
    composite_risk,
    gap_score,
    points_gap,
    ratio_gap,
)


@pytest.mark.parametrize("p", [4, 5, 6])
def test_gap_score_is_p_at_the_benchmark_and_moves_by_delta_per_unit_gap(p):
    assert gap_score(0, p, 2.5) == pytest.approx(p)
    assert gap_score(1, p, 2.5) == pytest.approx(p + 2.5)
    assert gap_score(-1, p, 2.5) == pytest.approx(p - 2.5)
    assert 1 < gap_score(-3, p, 2.5) < gap_score(3, p, 2.5) < 10


def test_ratio_gap_is_log_scaled_capped_and_treats_net_cash_as_best():
    assert ratio_gap(7.22, 3.61, 1, 2.0, 3.0) == pytest.approx(1.0)  # twice the benchmark = one step worse
    assert ratio_gap(100, 3.61, 1, 2.0, 3.0) == 3.0
    assert ratio_gap(-0.5, 3.42, 1, 2.0, 3.0) == -3.0
    assert points_gap(10.0, 15.0, -1, 5.0, 3.0) == pytest.approx(1.0)  # 5 points below a margin benchmark


def test_fixed_bands_take_the_first_band_reached():
    assert risk_score(2.0, DSCR_BANDS) == 1
    assert risk_score(1.99, DSCR_BANDS) == 2
    assert [risk_score(r, DSCR_BANDS) for r in (1.5, 1.25, 1.0, 0.5, 0.49, 0.01)] == [2, 3, 5, 7, 9, 9]
    assert risk_score(0, DSCR_BANDS) == 10 and risk_score(-1, DSCR_BANDS) == 10
    assert risk_score(None, DSCR_BANDS) is None
    assert risk_score(12, ICR_BANDS) == 1
    assert [risk_score(r, ICR_BANDS) for r in (11.9, 8, 6, 4, 3, 2, 1.5, 1.0)] == [1.5, 1.5, 2.5, 3.5, 5, 6, 7.5, 8.5]
    assert risk_score(0.99, ICR_BANDS) == 10 and risk_score(-1, ICR_BANDS) == 10


@pytest.mark.parametrize("score,rating", [
    (1.0, "AAA"), (1.4, "AAA"), (2.0, "AA+"), (4.0, "BBB+"), (4.5, "BBB"), (4.6, "BBB-"), (9.9, "C-"), (10.0, "D+"),
])
def test_credit_rating_notches_like_the_fo_project(score, rating):
    assert credit_rating(score) == rating


def test_risk_levels():
    assert risk_level(1.5) == ("Very low", "very-low")
    assert risk_level(5) == ("Moderate", "moderate")
    assert risk_level(10) == ("Very high", "very-high")


def test_composite_leverage_uplift():
    assert composite_leverage(6.0, 3.0) == {"score": 4.0, "uplift": True, "reason": None}
    assert composite_leverage(4.0, 3.0)["score"] == 3.0
    assert composite_leverage(None, 3.0)["score"] is None


def _fo(leverage=None, liquidity=None, dscr=None, dso=None, dso_days=40.0):
    def m(key, score, **extra):
        return {"key": key, "columns": [{"heading": "FY2025", "period": "2025-01-01--2025-12-31", "score": score,
                                         **extra}]}
    fo = [{"key": "leverage", "columns": [{"heading": "FY2025", "period": "2025-01-01--2025-12-31",
                                           "composite": {"score": leverage}}]},
          m("liquidity", liquidity), m("dscr", dscr)]
    if dso is not None:
        fo.append(m("dso", dso, dso=dso_days))
    return fo


SETTINGS = {"values": dict(SCORING_DEFAULTS)}


def test_composite_renormalises_weights_over_available_measures():
    out = composite_risk(_fo(leverage=4.0, liquidity=6.0), SETTINGS)
    assert out["used"] == 2
    assert out["columns"][0]["score"] == round((25 * 4 + 20 * 6) / 45, 1)
    shares = {r["key"]: r["share"] for r in out["rows"]}
    assert math.isclose(shares["leverage"] + shares["liquidity"], 1.0)
    assert out["gc_status"] == "pass"  # no pre-check status given: the gate reads pass


def test_composite_severity_floor_and_required_measures():
    floored = composite_risk(_fo(leverage=2.0, liquidity=9.0), SETTINGS)
    assert floored["columns"][0]["score"] == 7
    assert floored["adjustments"] and "Floored at 7" in floored["adjustments"][0]
    missing = composite_risk(_fo(leverage=2.0), SETTINGS)
    assert missing["columns"][0]["score"] is None
    assert missing["columns"][0]["reason"].startswith("Incomplete")


def test_going_concern_watch_floors_liquidity_at_seven():
    out = composite_risk(_fo(leverage=4.0, liquidity=3.0), SETTINGS, "watch")
    liquidity = next(r for r in out["rows"] if r["key"] == "liquidity")
    assert liquidity["score"] == 7 and "going-concern watch" in liquidity["notes"][0]
    assert out["columns"][0]["score"] == round((25 * 4 + 20 * 7) / 45, 1)
    high = composite_risk(_fo(leverage=4.0, liquidity=8.0), SETTINGS, "watch")
    assert next(r for r in high["rows"] if r["key"] == "liquidity")["score"] == 8  # a floor, not a cap


def test_going_concern_fail_sets_ten_and_supersedes_every_measure():
    out = composite_risk(_fo(leverage=2.0, liquidity=2.0, dscr=1.0), SETTINGS, "fail")
    assert out["columns"][0]["score"] == 10 and out["gc_status"] == "fail"
    assert all(r["notes"] == ["superseded by going-concern fail: not counted"] for r in out["rows"])
    assert next(r for r in out["rows"] if r["key"] == "liquidity")["score"] == 2.0  # still visible, unchanged
    assert out["used"] == 0 and out["average"] is None  # and none of them counted
    assert all(r["contribution"] is None and r["share"] == 0 for r in out["rows"])
    incomplete = composite_risk(_fo(leverage=2.0), SETTINGS, "fail")
    assert incomplete["columns"][0]["score"] == 10  # a fail decides even without liquidity


def test_low_dso_gets_half_weight():
    out = composite_risk(_fo(leverage=4.0, liquidity=4.0, dso=8.0, dso_days=10.0), SETTINGS)
    dso = next(r for r in out["rows"] if r["key"] == "dso")
    assert dso["weight"] == SCORING_DEFAULTS["w_dso"] / 2
    assert out["columns"][0]["score"] == round((25 * 4 + 20 * 4 + 3.75 * 8) / 48.75, 1)
