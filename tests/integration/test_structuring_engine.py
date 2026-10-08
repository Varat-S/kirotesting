"""Milestone 16 — deterministic structuring engine (Req 13.3-13.5, 7.4, 36).

Proves: candidates are immutable (no 'selected' flag); feasibility + policy
tests are deterministic; a policy violation (leverage, DSCR, LTV, missing
covenant) rejects the candidate; downside stress is computed; an infeasible
candidate's verdict is append-only and feasible is False.
"""

from __future__ import annotations

from app.schemas.agentic import CandidateStructure
from app.services.structuring import assemble_candidate, stress_candidate
from app.services.structuring.engine import (
    CandidateEconomics,
    FeasibilityPolicy,
    StructuringEngine,
)


def _strong_economics(**over) -> CandidateEconomics:
    base = dict(
        ebitda=100.0, debt_service=40.0, net_debt=200.0,
        available_liquidity=150.0, liquidity_need=100.0,
        collateral_value=1000.0, max_feasible_facility=1000.0,
    )
    base.update(over)
    return CandidateEconomics(**base)


def _candidate(**over) -> CandidateStructure:
    kw = dict(
        analysis_run_id="AR1", facility_amount=500.0, tenor_months=60,
        covenant_package=[{"type": "max_net_leverage", "threshold": 3.5}],
        collateral=[{"type": "fleet"}],
    )
    kw.update(over)
    return assemble_candidate(**kw)


def test_feasible_candidate_passes():
    engine = StructuringEngine()
    verdict = engine.evaluate(_candidate(), _strong_economics())
    assert verdict.feasible is True
    assert verdict.feasibility_detail["failures"] == []
    assert "stress" in verdict.feasibility_detail


def test_candidate_structure_is_immutable_no_selected_flag():
    c = _candidate()
    assert not hasattr(c, "selected")
    assert "selected" not in CandidateStructure.model_fields


def test_rejected_on_leverage_breach():
    engine = StructuringEngine(FeasibilityPolicy(max_leverage=1.5))
    verdict = engine.evaluate(_candidate(), _strong_economics(net_debt=300.0))
    assert verdict.feasible is False
    assert any("leverage" in f for f in verdict.feasibility_detail["failures"])


def test_rejected_on_downside_dscr():
    # Downside DSCR = (100 * 0.8) / 40 = 2.0; a 2.5 minimum rejects it.
    engine = StructuringEngine(FeasibilityPolicy(min_downside_dscr=2.5))
    verdict = engine.evaluate(_candidate(), _strong_economics())
    assert verdict.feasible is False
    assert "downside_dscr_below_minimum" in verdict.feasibility_detail["failures"]


def test_rejected_on_ltv():
    engine = StructuringEngine(FeasibilityPolicy(max_ltv=0.1))
    verdict = engine.evaluate(_candidate(facility_amount=900.0),
                              _strong_economics(collateral_value=1000.0))
    assert verdict.feasible is False
    assert "ltv_exceeds_policy" in verdict.feasibility_detail["failures"]


def test_rejected_on_missing_covenant_package():
    engine = StructuringEngine()
    verdict = engine.evaluate(_candidate(covenant_package=[]), _strong_economics())
    assert verdict.feasible is False
    assert "missing_required_covenant_package" in verdict.feasibility_detail["failures"]


def test_rejected_on_facility_exceeding_max_feasible():
    engine = StructuringEngine()
    verdict = engine.evaluate(_candidate(facility_amount=2000.0),
                              _strong_economics(max_feasible_facility=1000.0))
    assert verdict.feasible is False
    assert "facility_amount_exceeds_max_feasible" in verdict.feasibility_detail["failures"]


def test_downside_stress_is_computed():
    result = stress_candidate(
        ebitda=100.0, debt_service=40.0, net_debt=200.0,
        available_liquidity=150.0, liquidity_need=100.0, downside_shock_pct=-0.25,
    )
    assert result["base"].dscr == 2.5  # 100/40
    assert result["downside"].dscr == 1.875  # 75/40
    assert result["base"].leverage == 2.0  # 200/100


def test_feasibility_verdict_is_append_only_identity():
    engine = StructuringEngine()
    v1 = engine.evaluate(_candidate(), _strong_economics())
    v2 = engine.evaluate(_candidate(), _strong_economics())
    assert v1.feasibility_id != v2.feasibility_id  # distinct append-only versions
    assert v1.acceptance_state.value == "accepted"
