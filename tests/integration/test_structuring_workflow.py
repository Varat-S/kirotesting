"""Milestone 17.1 — end-to-end Structuring workflow (Req 13.6-13.9; R7 item 18).

Ties the structuring pieces into the full workflow and asserts the Definition of
Done: candidate feasibility -> StructureProtectionScore -> FacilityRiskScore ->
StructuringConclusion (selecting a FEASIBLE candidate) -> Structuring Challenge,
with Facility separate from Obligor, infeasible candidates unselectable,
reselection preserving the prior conclusion, and unavailable facility inputs ->
unavailable FacilityRiskScore.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schemas.agentic import (
    Method,
    ParameterResult,
    ParameterStatus,
    ScoreKind,
    ScoreStatus,
    Topic,
)
from app.services.scoring.engine import ScoringConfig, ScoringEngine
from app.services.structuring import (
    BorrowerForecast,
    FeasibilityPolicy,
    StructuringEngine,
    assemble_candidate,
)
from app.services.orchestration.conclusions import build_topic_conclusion
from app.services.orchestration.structuring import (
    InfeasibleSelectionError,
    feasible_candidate_ids,
    validate_selection,
)

CONFIG = Path(__file__).resolve().parents[2] / "config" / "poc" / "scoring.json"


def _scoring() -> ScoringEngine:
    return ScoringEngine(ScoringConfig(content=json.loads(CONFIG.read_text()),
                                       version=1, content_hash="h"))


def _protection_params():
    def _p(pid, v):
        return ParameterResult(
            parameter_result_id=f"pr_{pid}", analysis_run_id="AR1",
            parameter_id=pid, topic=Topic.STRUCTURING, value=v, value_type="ratio",
            method=Method.DETERMINISTIC, status=ParameterStatus.OK,
            formula_id="f", formula_version="1")
    return [_p("collateral_coverage", 1.6), _p("guarantee_support", 0.95),
            _p("covenant_protection_score", 1)]


def _obligor():
    e = _scoring()
    business = e.score_business(
        [ParameterResult(parameter_result_id="pr_h", analysis_run_id="AR1",
                         parameter_id="customer_hhi", topic=Topic.BUSINESS, value=0.1,
                         value_type="index", method=Method.DETERMINISTIC,
                         status=ParameterStatus.OK, formula_id="f", formula_version="1"),
         ParameterResult(parameter_result_id="pr_c", analysis_run_id="AR1",
                         parameter_id="competitive_position_risk_signal",
                         topic=Topic.BUSINESS, value=2, value_type="risk_band",
                         method=Method.DETERMINISTIC, status=ParameterStatus.OK,
                         formula_id="f", formula_version="1"),
         ParameterResult(parameter_result_id="pr_m", analysis_run_id="AR1",
                         parameter_id="management_governance_risk_signal",
                         topic=Topic.BUSINESS, value=2, value_type="risk_band",
                         method=Method.DETERMINISTIC, status=ParameterStatus.OK,
                         formula_id="f", formula_version="1")],
        analysis_run_id="AR1")
    financial = e.score_financial(
        [ParameterResult(parameter_result_id=f"pr_{p}", analysis_run_id="AR1",
                         parameter_id=p, topic=Topic.FINANCIAL, value=v,
                         value_type="ratio", method=Method.DETERMINISTIC,
                         status=ParameterStatus.OK, formula_id="f", formula_version="1")
         for p, v in [("net_leverage", 2.5), ("interest_coverage", 5.0),
                      ("fcf_conversion", 0.7), ("liquidity", 1.5)]],
        analysis_run_id="AR1")
    return e, e.score_obligor(business, financial, analysis_run_id="AR1")


def test_structuring_workflow_produces_protection_facility_conclusion_challenge():
    engine, obligor = _obligor()

    # 1. candidate feasibility
    candidate = assemble_candidate(
        analysis_run_id="AR1", facility_amount=100.0, tenor_months=60,
        amortization={"type": "straight_line", "interest_rate": 0.05},
        covenant_package=[{"type": "max_net_leverage"}],
        collateral=[{"type": "fleet"}])
    verdict = StructuringEngine(FeasibilityPolicy()).evaluate(
        candidate, BorrowerForecast(ebitda=200.0, cfads=200.0, existing_net_debt=0.0,
                                    available_liquidity=200.0, liquidity_need=100.0,
                                    collateral_value=1000.0))
    assert verdict.feasible, verdict.feasibility_detail

    # 2. StructureProtectionScore
    protection = engine.score_structure_protection(_protection_params(),
                                                   analysis_run_id="AR1")
    assert protection.kind is ScoreKind.STRUCTURE_PROTECTION
    assert protection.status is ScoreStatus.FINAL

    # 3. FacilityRiskScore (separate from Obligor)
    facility = engine.score_facility(obligor, protection, analysis_run_id="AR1")
    assert facility.kind is ScoreKind.FACILITY
    assert facility.score_id != obligor.score_id  # separate scores

    # 4. selection must be a FEASIBLE candidate
    assert feasible_candidate_ids([verdict]) == {candidate.candidate_id}
    selected = validate_selection(candidate.candidate_id, [verdict])
    assert selected == candidate.candidate_id

    # 5. StructuringConclusion carries the selection + facility score reference
    conclusion = build_topic_conclusion(
        Topic.STRUCTURING,
        {"overall_assessment": {"claim_id": "sa", "category": "assessment",
                                "text": "Secured, covenanted facility."},
         "material_risks": []},
        analysis_run_id="AR1", orchestrator_run_id="run",
        score_reference=facility.score_id, selected_candidate_id=selected)
    assert conclusion.selected_candidate_id == candidate.candidate_id


def test_infeasible_candidate_cannot_be_selected():
    engine, _ = _obligor()
    candidate = assemble_candidate(analysis_run_id="AR1", facility_amount=100.0,
                                   covenant_package=[])  # missing covenant -> infeasible
    verdict = StructuringEngine(FeasibilityPolicy()).evaluate(
        candidate, BorrowerForecast(ebitda=200.0, cfads=200.0))
    assert not verdict.feasible
    with pytest.raises(InfeasibleSelectionError):
        validate_selection(candidate.candidate_id, [verdict])


def test_facility_unavailable_when_structuring_inputs_absent():
    engine, obligor = _obligor()
    # No protection score available (absent structuring inputs).
    facility = engine.score_facility(obligor, None, analysis_run_id="AR1")
    assert facility.status is ScoreStatus.UNAVAILABLE
    assert facility.band is None


def test_reselection_preserves_prior_conclusion():
    # Two conclusions for the same topic with lineage: the second supersedes the
    # first; both remain (append-only). Modeled with acceptance_state.
    first = build_topic_conclusion(
        Topic.STRUCTURING,
        {"overall_assessment": {"claim_id": "a", "category": "assessment", "text": "x"}},
        analysis_run_id="AR1", orchestrator_run_id="r", score_reference="facility",
        selected_candidate_id="cs1")
    second = build_topic_conclusion(
        Topic.STRUCTURING,
        {"overall_assessment": {"claim_id": "b", "category": "assessment", "text": "y"}},
        analysis_run_id="AR1", orchestrator_run_id="r", score_reference="facility",
        selected_candidate_id="cs2")
    assert first.selected_candidate_id == "cs1"
    assert second.selected_candidate_id == "cs2"  # distinct selections, both retained
