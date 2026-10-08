"""Milestone 16 — deterministic structuring engine (Req 13.3-13.5, 7.4, 36; R6).

Proves: candidate TERMS drive economics (two structures => different stress);
DSCR = CFADS / debt service (NOT EBITDA); missing inputs never become zero;
policy thresholds come from versioned config; feasibility verdicts are
append-only; infeasible candidates cannot be selected.
"""

from __future__ import annotations

import pytest

from app.core.bootstrap import bootstrap_config
from app.core.config_registry import ConfigRegistry
from app.schemas.agentic import CandidateStructure
from app.services.structuring import (
    BorrowerForecast,
    FeasibilityPolicy,
    StructuringEngine,
    assemble_candidate,
    derive_economics,
    stress_candidate,
)


def _forecast(**over) -> BorrowerForecast:
    base = dict(
        ebitda=100.0, cfads=60.0, existing_net_debt=100.0,
        available_liquidity=150.0, liquidity_need=100.0, collateral_value=1000.0,
    )
    base.update(over)
    return BorrowerForecast(**base)


def _candidate(**over) -> CandidateStructure:
    kw = dict(
        analysis_run_id="AR1", facility_amount=300.0, tenor_months=60,
        amortization={"type": "straight_line", "interest_rate": 0.05},
        covenant_package=[{"type": "max_net_leverage", "threshold": 3.5}],
        collateral=[{"type": "fleet"}],
    )
    kw.update(over)
    return assemble_candidate(**kw)


# --- item 12: DSCR = CFADS / debt service -----------------------------------


def test_dscr_uses_cfads_not_ebitda():
    # EBITDA 100, CFADS 50, debt service 40 => DSCR must be 1.25x, not 2.50x.
    result = stress_candidate(
        ebitda=100.0, cfads=50.0, debt_service=40.0, net_debt=200.0,
        available_liquidity=150.0, liquidity_need=100.0,
    )
    assert result["base"].dscr == pytest.approx(1.25)
    assert result["base"].dscr != pytest.approx(2.5)
    assert result["base"].leverage == pytest.approx(2.0)  # net debt / EBITDA


# --- item 13: candidate terms drive economics -------------------------------


def test_two_structures_produce_different_economics():
    forecast = _forecast()
    straight = derive_economics(_candidate(amortization={"type": "straight_line",
                                                         "interest_rate": 0.05}),
                                forecast)
    bullet = derive_economics(_candidate(amortization={"type": "bullet",
                                                       "interest_rate": 0.05}),
                              forecast)
    # Straight-line amortizes principal; bullet defers it -> different service.
    assert straight.annual_principal == pytest.approx(300.0 / 5)
    assert bullet.annual_principal == 0.0
    assert straight.annual_debt_service != bullet.annual_debt_service
    assert bullet.bullet_amount == pytest.approx(300.0)
    assert straight.bullet_amount == 0.0


def test_different_facility_amounts_change_debt_service():
    small = derive_economics(_candidate(facility_amount=100.0), _forecast())
    large = derive_economics(_candidate(facility_amount=500.0), _forecast())
    assert large.annual_debt_service > small.annual_debt_service
    assert large.ending_net_debt > small.ending_net_debt


# --- item 14: missing inputs never become zero ------------------------------


def test_missing_facility_amount_makes_ltv_unavailable_not_zero():
    engine = StructuringEngine()
    verdict = engine.evaluate(_candidate(facility_amount=None), _forecast())
    assert verdict.feasible is False  # untestable, not falsely 0% LTV
    assert "ltv" in verdict.feasibility_detail["unavailable"]
    assert verdict.feasibility_detail["ltv"] is None


def test_missing_collateral_value_makes_ltv_unavailable():
    engine = StructuringEngine()
    verdict = engine.evaluate(_candidate(), _forecast(collateral_value=None))
    assert "ltv" in verdict.feasibility_detail["unavailable"]


def test_missing_cfads_makes_dscr_unavailable():
    engine = StructuringEngine()
    verdict = engine.evaluate(_candidate(), _forecast(cfads=None))
    detail = verdict.feasibility_detail
    assert verdict.feasible is False
    assert "base_dscr" in detail["unavailable"]
    assert detail["stress"]["base"]["dscr"] is None  # not 0


# --- feasibility + rejection ------------------------------------------------


def test_feasible_candidate_passes():
    engine = StructuringEngine()
    # Strong borrower: low existing debt + ample CFADS so base AND downside pass.
    verdict = engine.evaluate(
        _candidate(facility_amount=100.0),
        _forecast(ebitda=200.0, cfads=200.0, existing_net_debt=0.0),
    )
    assert verdict.feasible is True
    assert verdict.feasibility_detail["failures"] == []
    assert verdict.feasibility_detail["unavailable"] == []


def test_candidate_structure_is_immutable_no_selected_flag():
    assert "selected" not in CandidateStructure.model_fields


def test_rejected_on_leverage_breach():
    engine = StructuringEngine(FeasibilityPolicy(max_leverage=1.0))
    verdict = engine.evaluate(_candidate(), _forecast(cfads=200.0,
                                                      existing_net_debt=400.0))
    assert verdict.feasible is False
    assert any("leverage" in f for f in verdict.feasibility_detail["failures"])


def test_rejected_on_downside_dscr():
    engine = StructuringEngine(FeasibilityPolicy(min_downside_dscr=5.0))
    verdict = engine.evaluate(_candidate(), _forecast(cfads=100.0))
    assert verdict.feasible is False
    assert "downside_dscr_below_minimum" in verdict.feasibility_detail["failures"]


def test_rejected_on_missing_covenant_package():
    engine = StructuringEngine()
    verdict = engine.evaluate(_candidate(covenant_package=[]),
                              _forecast(cfads=200.0))
    assert "missing_required_covenant_package" in verdict.feasibility_detail["failures"]


# --- item 15: versioned policy ----------------------------------------------


def test_policy_loaded_from_versioned_config(db_session):
    registry = ConfigRegistry(db_session)
    bootstrap_config(registry)
    policy = FeasibilityPolicy.from_registry(registry)
    assert policy.config_version is not None
    assert policy.config_hash
    engine = StructuringEngine(policy)
    verdict = engine.evaluate(_candidate(), _forecast(cfads=200.0))
    assert verdict.feasibility_detail["policy_version"] == policy.config_version
    assert verdict.feasibility_detail["policy_hash"] == policy.config_hash


def test_same_inputs_and_policy_reproduce_verdict():
    engine = StructuringEngine(FeasibilityPolicy(config_version=1, config_hash="h"))
    v1 = engine.evaluate(_candidate(), _forecast(cfads=200.0))
    v2 = engine.evaluate(_candidate(), _forecast(cfads=200.0))
    assert v1.feasible == v2.feasible
    assert v1.feasibility_detail["failures"] == v2.feasibility_detail["failures"]
    assert v1.feasibility_id != v2.feasibility_id  # append-only identities
