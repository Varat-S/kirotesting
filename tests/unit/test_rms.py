"""Risk Mitigation & Structuring: inputs, debt capacity, grade, gates, verdict matrix, terms and mitigants.

Built on a recorded FO run on HCA's FY2025 10-K (tests/fixtures/rms/hca_fy2025_fo.json, US$) and the committed
Module 4 sample (examples/bo_m4/m4_analysis.sample.json, US$ millions). Hand-calculated HCA figures (US$M):
EBITDAR 16,119; lease-adjusted debt 48,697, net 47,657; CFADS 8,566; interest 2,248; FO composite 3.3 (low).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.services.fo.runner import composite_for
from app.services.rms import RmsRequest, assess, load_bo, load_fo
from app.services.rms.verdict import adjusted_grade

ROOT = Path(__file__).resolve().parents[2]
FO = json.loads((ROOT / "tests/fixtures/rms/hca_fy2025_fo.json").read_text(encoding="utf-8"))
M4 = json.loads((ROOT / "examples/bo_m4/m4_analysis.sample.json").read_text(encoding="utf-8"))

# Downside leverage binds: 4.5 x 16,119 x 0.8 - 47,657.
HCA_CAPACITY = 10371.4


def fo_with(scores=None, **precheck):
    """The HCA FO payload with some measures' headline scores and pre-check statuses replaced."""
    fo = copy.deepcopy(FO)
    for key, score in (scores or {}).items():
        col = next(m for m in fo["metrics"] if m["key"] == key)["columns"][0]
        target = {"leverage": col.get("composite"), "rcf": col.get("rcf_to_debt")}.get(key, col)
        target["score"] = score
    for check, status in precheck.items():
        fo["precheck"][check]["status"] = status
        if check == "going_concern":
            fo["gate"]["going_concern"] = status  # the verified result RMS reads
    return fo


def m4_with(**box):
    m4 = copy.deepcopy(M4)
    m4["deliverable_4"]["executive_credit_summary_box"].update(box)
    return m4


def handoff(m4=M4, *, band=None, m3=(), vulnerabilities=(), escalations=(), unproven=()):
    return {
        "business_risk_band": band,
        "module_conclusions": {"M3": {"m3_assessment": {"structural_credit_findings": list(m3)}},
                               "M4": {"m4_analysis": m4}},
        "operational_vulnerabilities": list(vulnerabilities),
        "escalations": list(escalations),
        "unproven_mitigants": [{"hypothesis_id": h} for h in unproven],
    }


def m3_finding(finding_id, dimension):
    return {"finding_id": finding_id, "credit_direction": "negative", "materiality": "high",
            "admissibility": "established", "analytical_dimension": dimension}


def test_fo_inputs_come_from_the_headline_columns_in_millions():
    fo = load_fo(FO)
    assert (fo.composite_score, fo.credit_rating, fo.gc_status) == (3.3, "A+", "pass")
    assert fo.ebitdar_m == pytest.approx(16119) and fo.adj_debt_m == pytest.approx(48697)
    assert fo.adj_net_debt_m == pytest.approx(47657) and fo.debt_m == pytest.approx(46492)
    assert fo.cfads_m == pytest.approx(8566) and fo.interest_m == pytest.approx(2248)
    assert fo.debt_service_m == pytest.approx(7611)
    assert fo.scores == {"leverage": 4.4, "liquidity": 3, "rcf": 1.8, "dscr": 5, "icr": 3.5, "margin": 2.3,
                         "dso": 2.4}
    assert fo.refinancing_score == 3 and fo.triggers_fired == {"rcf_t4"}


def test_bo_inputs_read_the_m4_sample_directly_or_through_the_handoff():
    direct, via = load_bo(m4_analysis=M4), load_bo(handoff())
    for bo in (direct, via):
        assert (bo.m4_status, bo.m4_debt_validated, bo.m4_verdict) == ("complete", True, "satisfactory")
        assert bo.m4_placement == "senior_unsecured_tier_2" and bo.m4_total_debt_m == 46492.0
        assert bo.distress_events == 0 and bo.adverse_strategy_findings == ()
    assert not direct.handoff_present and via.handoff_present


def test_hca_capacity_matches_the_hand_calculation():
    r = assess(RmsRequest(2000), FO, m4_analysis=M4)
    c = r.capacity
    assert c["level"] == "low" and c["assumptions"]["rollover"] is True
    assert c["assumptions"]["existing_debt_service_m"] == pytest.approx(2248)  # interest only
    assert c["tests_m"] == {
        "base_leverage": pytest.approx(23838.5),  # 4.5 x 16,119 - 48,697
        "downside_leverage": pytest.approx(HCA_CAPACITY),
        "base_dscr": pytest.approx(17376.6),  # (8,566 / 1.25 - 2,248) / (1/5 + 6.5%)
        "downside_dscr": pytest.approx(17376.6),  # (8,566 x 0.8 / 1.0 - 2,248) / (1/5 + 6.5%)
    }
    assert c["binding_test"] == "downside_leverage" and c["haircut"] == 0
    assert c["derived_capacity_m"] == pytest.approx(HCA_CAPACITY)


def test_the_capacity_breakdown_labels_each_test_and_marks_the_binding_one():
    from app.services.rms.runner import capacity_breakdown

    c = capacity_breakdown(assess(RmsRequest(2000), FO, m4_analysis=M4).capacity)
    assert c["inputs_m"] == {"ebitdar": pytest.approx(16119), "adj_debt": pytest.approx(48697),
                             "adj_net_debt": pytest.approx(47657), "cfads": pytest.approx(8566),
                             "existing_debt_service": pytest.approx(2248)}
    assert [(t["test"], t["binding"]) for t in c["tests"]] == [
        ("base_leverage", False), ("downside_leverage", True), ("base_dscr", False), ("downside_dscr", False)]
    assert c["tests"][0]["label"] == "Leverage, base: 4.5x EBITDAR - lease-adjusted debt"
    assert c["tests"][2]["label"] == "DSCR, base: (CFADS / 1.25x - existing debt service) / (1/5 + 6.50%)"
    assert c["base_capacity_m"] == c["derived_capacity_m"] == pytest.approx(HCA_CAPACITY) and c["haircuts"] == []


def test_without_rollover_the_bond_maturities_leave_no_dscr_capacity():
    fo = copy.deepcopy(FO)
    next(m for m in fo["metrics"] if m["key"] == "liquidity")["columns"][0]["refinancing_score"] = 7
    r = assess(RmsRequest(2000), fo, m4_analysis=M4)
    assert r.capacity["assumptions"]["rollover"] is False
    assert r.capacity["assumptions"]["existing_debt_service_m"] == pytest.approx(7611)
    assert r.capacity["tests_m"]["base_dscr"] == 0 and r.capacity["derived_capacity_m"] == 0
    assert r.utilisation is None and r.verdict == "decline" and r.approved_amount_m is None


@pytest.mark.parametrize("requested, verdict, approved", [
    (2000, "approve", 2000), (9000, "approve", 9000),
    (12000, "counter_offer", HCA_CAPACITY), (20000, "counter_offer", HCA_CAPACITY),
])
def test_hca_verdict_follows_utilisation_at_a_low_grade(requested, verdict, approved):
    r = assess(RmsRequest(requested), FO, m4_analysis=M4)
    assert r.grade["score"] == 3.3 and r.grade["rating"] == "A+" and r.grade["level"] == "low"
    assert r.verdict == verdict and r.approved_amount_m == pytest.approx(approved)
    assert r.gates == [] and r.requires_human_review
    # The approved structure passes the structuring engine, even at exactly the capacity.
    assert r.feasibility["feasible"] is True


def test_hca_terms_and_mitigants_at_a_low_grade():
    r = assess(RmsRequest(2000, tenor_years=10), FO, m4_analysis=M4)
    t = r.terms
    assert t["rank"] == "senior_unsecured_tier_2" and not t["secured"] and not t["provisional"]
    assert t["tenor_years"] == 7  # capped for a low grade
    assert t["amortisation"]["type"] == "bullet"
    assert t["pro_forma_leverage"] == pytest.approx((48697 + 2000) / 16119, abs=0.005)
    # M4 D3: $21,950M of buybacks and dividends over 3 years, partly debt-funded (47% of total debt), so the low
    # grade's incurrence test becomes a maintenance leverage covenant.
    assert t["financial_policy"]["signal"] == "aggressive" and t["financial_policy"]["distributions_3y_m"] == 21950
    assert t["financial_covenants"] == [{"covenant": "max_leverage", "test": "Debt / EBITDAR (lease-adjusted), quarterly",
                                         "max": pytest.approx((48697 + 2000) / 16119 * 1.25, abs=0.01)}]
    # Debt-funded buybacks; FO RCF T4 found NCI distributions; the revolver backstops $2,207M of CP.
    assert [m["rule_id"] for m in r.mitigants] == ["RMS-M05", "RMS-M12", "RMS-M18"]


def test_a_weak_m4_verdict_reaches_capacity_through_the_grade_only():
    r = assess(RmsRequest(4000), FO, m4_analysis=m4_with(overall_credit_rating_verdict="weak"))
    assert r.grade["score"] == 4.8 and r.grade["level"] == "moderate"  # 3.3 + 1.5
    # Sized at the moderate multiple (4.0x), with no separate M4 haircut: 4.0 x 16,119 x 0.8 - 47,657.
    assert r.capacity["level"] == "moderate" and r.capacity["assumptions"]["leverage_multiple"] == 4.0
    assert r.capacity["haircut"] == 0 and r.capacity["derived_capacity_m"] == pytest.approx(3923.8)
    assert r.verdict == "counter_offer" and r.approved_amount_m == pytest.approx(3923.8)
    assert r.terms["amortisation"]["type"] == "partial"
    lev = r.terms["financial_covenants"][0]
    assert lev["covenant"] == "max_leverage"
    assert lev["max"] == pytest.approx((48697 + 3923.8) / 16119 * 1.25, abs=0.01)


def test_the_business_band_notches_and_an_m3_finding_only_cuts_capacity():
    h = handoff(band=4, m3=[m3_finding("M3-F1", "guarantor_coverage")],
                vulnerabilities=[{"claim_id": "CLM-RSK-01", "title": "Regulatory and Reimbursement Dependency",
                                  "text": "Medicare and Medicaid make up a large share of revenue."}])
    r = assess(RmsRequest(2000), FO, handoff=h)
    assert r.grade["score"] == 4.8 and [n["source"] for n in r.grade["notches"]] == ["m4_verdict", "business_band"]
    assert [h["source"] for h in r.capacity["haircuts"]] == ["m3_structural"] and r.capacity["haircut"] == 0.05
    assert r.capacity["derived_capacity_m"] == pytest.approx(3727.6)  # 3,923.8 x 0.95
    assert r.verdict == "approve" and r.approved_amount_m == 2000
    t = r.terms
    assert not t["secured"] and t["rank"] == "senior_unsecured_tier_2" and t["tenor_years"] == 5
    assert {"RMS-M13", "RMS-M14"} <= {m["rule_id"] for m in r.mitigants}


def test_one_grade_sizes_everything_so_an_elevated_hca_has_no_capacity():
    # 3.3 + 0.5 (adequate) + 1.5 (band 4) = 5.3: at 3.6x, stressed EBITDAR already carries more than the net debt.
    r = assess(RmsRequest(2000), FO, handoff=handoff(m4_with(overall_credit_rating_verdict="adequate"), band=4))
    assert r.grade["level"] == "elevated" and r.capacity["tests_m"]["downside_leverage"] == 0
    assert r.capacity["derived_capacity_m"] == 0 and r.verdict == "decline"


def test_refinancing_distress_acts_through_the_rollover_only():
    m4 = copy.deepcopy(M4)
    m4["deliverable_2"]["metrics"]["historical_refinancing_distress_events"] = 1
    r = assess(RmsRequest(2000), FO, m4_analysis=m4)
    assert r.capacity["assumptions"]["rollover"] is False and r.capacity["haircuts"] == []
    assert r.capacity["tests_m"]["base_dscr"] == 0 and r.verdict == "decline"


def test_a_secured_placement_adds_collateral_and_leaves_ltv_to_a_valuation():
    r = assess(RmsRequest(2000), FO, m4_analysis=m4_with(recommended_loan_placement="secured_tier_1"))
    assert r.verdict == "approve" and r.terms["secured"] and r.terms["rank"] == "secured_tier_1"
    ids = [m["rule_id"] for m in r.mitigants]
    assert {"RMS-M08", "RMS-M09"} <= set(ids)
    assert "anti-assignment" in next(m for m in r.mitigants if m["rule_id"] == "RMS-M08")["mitigant"]
    # No collateral value: LTV is untested (a condition precedent), not a reason to refer.
    assert r.feasibility["unavailable"] == ["ltv"] and r.feasibility["failures"] == []


def test_fo_severity_floor_carries_into_the_adjusted_grade():
    grade = adjusted_grade(load_fo(fo_with({"leverage": 9.2})), load_bo(m4_analysis=M4))
    assert grade["score"] == 7.0 and grade["severity_floor"] == {"applied": True, "measures": ["leverage"]}


def test_high_fo_measure_scores_add_their_mitigants():
    fo = fo_with({"liquidity": 7.5, "dscr": 7, "icr": 6, "margin": 8, "dso": 7})
    fo["triggers"]["liquidity_t3"]["fired"] = True
    r = assess(RmsRequest(500), fo, m4_analysis=M4)
    assert {"RMS-M01", "RMS-M02", "RMS-M03", "RMS-M04", "RMS-M06", "RMS-M07"} <= {m["rule_id"] for m in r.mitigants}


def _mismatched_m4():
    m4 = copy.deepcopy(M4)
    m4["deliverable_1"]["creditor_priority_framework"]["consolidated_total_indebtedness_m"] = 30000.0
    return m4


def _incomplete_m4():
    m4 = copy.deepcopy(M4)
    m4["status"] = "incomplete"
    return m4


@pytest.mark.parametrize("fo, bo, gate, verdict", [
    (fo_with(going_concern="fail"), {"m4_analysis": M4}, "RMS-G01", "decline"),
    ({**FO, "escalations": [{"id": "e1", "type": "measure_override"}]}, {"m4_analysis": M4}, "RMS-G02", "refer"),
    (fo_with(covenants="breach"), {"m4_analysis": M4}, "RMS-G03", "refer"),
    (FO, {}, "RMS-G05", "refer"),
    (FO, {"m4_analysis": _incomplete_m4()}, "RMS-G05", "refer"),
    (FO, {"handoff": handoff(escalations=[{"challenge_id": "c1", "severity": "high"}])}, "RMS-G06", "refer"),
    (FO, {"m4_analysis": _mismatched_m4()}, "RMS-G07", "refer"),
])
def test_hard_gates_decline_or_refer(fo, bo, gate, verdict):
    r = assess(RmsRequest(2000), fo, **bo)
    assert gate in [g["gate_id"] for g in r.gates]
    assert r.verdict == verdict and r.approved_amount_m is None and r.feasibility is None


def test_a_referral_still_drafts_provisional_terms():
    r = assess(RmsRequest(2000), FO)  # no Module 4 output
    assert r.verdict == "refer" and r.terms["provisional"] is True and r.terms["amount_m"] == 2000


LIQUIDITY_ESCALATION = {"id": "covenants:liquidity", "type": "measure_override", "check": "covenants",
                        "measure": "liquidity", "engine_score": 3}
DSCR_ESCALATION = {"id": "dscr_t1:dscr", "type": "measure_override", "check": "dscr_t1", "measure": "dscr",
                   "engine_score": 5}


def fo_override(measure, decision, engine, analyst):
    return {"override_id": f"foo-{measure}", "measure": measure, "decision": decision, "engine_score": engine,
            "analyst_score": analyst, "reviewer": "A. Analyst"}


def _gate_ids(r):
    return [g["gate_id"] for g in r.gates]


def test_an_analysts_override_replaces_the_score_and_clears_the_gate():
    fo = {**FO, "escalations": [LIQUIDITY_ESCALATION]}
    before = assess(RmsRequest(2000), fo, m4_analysis=M4)
    assert before.verdict == "refer" and "RMS-G02" in _gate_ids(before)
    overrides = [fo_override("liquidity", "override", 3, 8.0)]
    after = assess(RmsRequest(2000), fo, m4_analysis=M4, fo_overrides=overrides)
    # The composite is re-weighted on the analyst's score, as FO does it.
    expected = composite_for(fo, {"liquidity": 8.0}, {"liquidity": "override"})["columns"][0]["score"]
    assert after.grade["fo_composite_score"] == expected and expected > 3.3
    assert load_fo(fo, overrides=overrides).scores["liquidity"] == 8.0
    assert "RMS-G02" not in _gate_ids(after) and after.verdict == "approve"
    assert after.fo_decisions == ["liquidity: override 3 -> 8 (A. Analyst)"]
    assert "RMS-M01" in {m["rule_id"] for m in after.mitigants}  # liquidity now scores 8


def test_accepting_the_engine_score_keeps_the_composite():
    fo = {**FO, "escalations": [LIQUIDITY_ESCALATION]}
    r = assess(RmsRequest(2000), fo, m4_analysis=M4, fo_overrides=[fo_override("liquidity", "accept", 3, 3)])
    assert r.grade["fo_composite_score"] == 3.3 and r.verdict == "approve"


def test_one_decision_of_two_keeps_the_run_referred():
    fo = {**FO, "escalations": [LIQUIDITY_ESCALATION, DSCR_ESCALATION]}
    r = assess(RmsRequest(2000), fo, m4_analysis=M4, fo_overrides=[fo_override("liquidity", "accept", 3, 3)])
    assert r.verdict == "refer"
    assert next(g for g in r.gates if g["gate_id"] == "RMS-G02")["reason"].startswith(
        "FO run awaiting an analyst: 1 escalation(s) undecided")


@pytest.mark.parametrize("decision, verdict, composite", [
    ("continue", "decline", 10.0),  # the going-concern fail stands: composite 10
    ("stop", "decline", None),  # not scored
    ("use_verified", "approve", 3.3),  # the verified pass stands
])
def test_a_going_concern_decision_is_applied(decision, verdict, composite):
    fo = {**FO, "escalations": [{"id": "going_concern", "type": "run_decision"}]}
    review = {"review_id": "for-1", "decision": decision, "reviewer": "A. Analyst"}
    r = assess(RmsRequest(2000), fo, m4_analysis=M4, fo_review=review)
    assert "RMS-G02" not in _gate_ids(r) and r.verdict == verdict
    assert (r.grade or {}).get("fo_composite_score") == composite
    assert ("RMS-G01" in _gate_ids(r)) is (decision != "use_verified")


def test_a_medium_bo_challenge_does_not_block():
    h = handoff(escalations=[{"challenge_id": "c1", "severity": "medium"}])
    assert assess(RmsRequest(2000), FO, handoff=h).verdict == "approve"


@pytest.mark.parametrize("bad", [0, -5])
def test_a_requested_amount_must_be_positive(bad):
    with pytest.raises(ValueError):
        RmsRequest(bad)


@pytest.mark.parametrize("request_", [None, RmsRequest(), RmsRequest(tenor_years=10)])
def test_without_a_requested_amount_rms_reports_the_debt_capacity(request_):
    r = assess(request_, FO, m4_analysis=M4)
    assert r.verdict == "capacity_only" and r.approved_amount_m is None and r.utilisation is None
    assert r.request["requested_amount_m"] is None and r.gates == []
    assert r.capacity["derived_capacity_m"] == pytest.approx(HCA_CAPACITY)
    # Indicative terms at the capacity (rounded down), checked by the structuring engine.
    t = r.terms
    assert t["amount_m"] == 10371.4 and t["provisional"] and t["basis"] == "derived capacity"
    assert t["tenor_years"] == (7 if request_ and request_.tenor_years == 10 else 5)
    assert r.mitigants and r.feasibility["feasible"] is True


def test_a_capacity_report_records_the_gates_but_decides_nothing():
    r = assess(RmsRequest(), fo_with(going_concern="fail"), m4_analysis=M4)
    assert r.verdict == "capacity_only" and "RMS-G01" in _gate_ids(r)
    no_capacity = assess(RmsRequest(), FO, handoff=handoff(m4_with(overall_credit_rating_verdict="adequate"), band=4))
    assert no_capacity.verdict == "capacity_only" and no_capacity.capacity["derived_capacity_m"] == 0
    assert no_capacity.terms is None and no_capacity.feasibility is None


def test_the_result_serialises_with_its_versions():
    out = json.loads(json.dumps(assess(RmsRequest(2000), FO, m4_analysis=M4).to_dict()))
    assert out["schema_version"] == "rms-1.2" and out["policy_version"] == "rms-policy-1.2"
    assert out["verdict"] == "approve" and out["request"]["requested_amount_m"] == 2000


# --- The M3 rebuild, structured M4 signals, cross-checks and limitations ----------------------------------------

def m3_rebuild(*, simulated=True, borrower="HCA Inc."):
    """An M3 rebuild conclusion: a borrower with two guarantor OpCos, an 80% non-guarantor OpCo and a 35% JV
    beneath a 90% shared-services company; and a partial management assessment."""
    nodes = [("top", "HCA Healthcare, Inc.", "Parent Co", False), ("b", borrower, "Borrower", False),
             ("g1", "Hospital East LLC", "OpCo", True), ("g2", "Hospital West LLC", "OpCo", True),
             ("asc", "Surgery Network LLC", "OpCo", False), ("ss", "Shared Services LLC", "OpCo", False),
             ("jv", "JV Investments", "JV", False)]
    links = [("top", "b", 100), ("b", "g1", 100), ("b", "g2", 100), ("g1", "asc", 80), ("b", "ss", 90),
             ("ss", "jv", 35)]
    return {"m3_rebuild": {
        "structure": {"status": "complete", "validation_status": "passed", "artifact": {
            "provenance": {"assumption_status": "simulated" if simulated else "verified",
                           "data_status": "simulated_educational_example" if simulated else "filed"},
            "companies": {"HCA": {
                "nodes": [{"id": i, "display_name": n, "role": r, "is_guarantor": g} for i, n, r, g in nodes],
                "ownership_links": [{"parent_id": p, "child_id": c, "ownership_percent": pct}
                                    for p, c, pct in links]}}}},
        "management": {"status": "partial", "artifact": {
            "coverage": {"admitted": 0, "expected": 7}, "overall_assessment": {"outcome": "unavailable"},
            "model_assessment": {"signal": "credit_positive"}}}}}


def rebuild_handoff(m4=M4, **kwargs):
    h = handoff(m4)
    h["module_conclusions"]["M3"] = m3_rebuild(**kwargs)
    return h


def test_the_m3_rebuild_is_read_from_the_borrower_down():
    bo = load_bo(rebuild_handoff())
    s = bo.m3_structure
    assert bo.m3_source == "rebuild" and bo.m3_findings == () and s.simulated and s.borrower == "HCA Inc."
    assert s.guarantors == ("Hospital East LLC", "Hospital West LLC")
    assert set(s.non_guarantor_opcos) == {"Surgery Network LLC", "Shared Services LLC"}
    # Ownership is effective: the JV is 35% of a 90% subsidiary.
    assert set(s.partly_owned) == {("Surgery Network LLC", 80.0), ("Shared Services LLC", 90.0),
                                   ("JV Investments", 31.5)}
    assert bo.m3_management == {"status": "partial", "admitted": 0, "expected": 7, "outcome": "unavailable",
                                "signal": "credit_positive"}


def test_the_m3_rebuild_shapes_the_terms_but_not_the_capacity():
    r = assess(RmsRequest(2000), FO, handoff=rebuild_handoff())
    assert r.capacity["haircuts"] == [] and r.grade["score"] == 3.3  # management is recorded, not scored
    by_id = {m["rule_id"]: m for m in r.mitigants}
    assert "JV Investments (31.5%)" in by_id["RMS-M12"]["mitigant"]
    assert "non-guarantors: " in by_id["RMS-M13"]["mitigant"] and "simulated" in by_id["RMS-M13"]["trigger"]
    assert r.terms["guarantees"].startswith("upstream guarantees from Hospital East LLC, Hospital West LLC")
    assert any("0 of 7 executive profiles admitted" in x for x in r.limitations)
    assert any("not scored structural findings" in x for x in r.limitations)


def test_a_borrower_mismatch_refers_only_on_a_verified_structure():
    simulated = assess(RmsRequest(2000), FO, handoff=rebuild_handoff(borrower="HCA Healthcare Holdings, Inc."))
    assert simulated.verdict == "approve" and "RMS-G11" not in _gate_ids(simulated)
    assert any(x.startswith("Borrower differs between M3 and M4") for x in simulated.limitations)
    verified = assess(RmsRequest(2000), FO, handoff=rebuild_handoff(simulated=False, borrower="HCA Holdings LLC"))
    assert verified.verdict == "refer" and "RMS-G11" in _gate_ids(verified)
    # Case, punctuation and a legal suffix don't make a mismatch.
    same = assess(RmsRequest(2000), FO, handoff=rebuild_handoff(simulated=False, borrower="HCA, Inc"))
    assert next(c for c in same.cross_checks if c["check"] == "borrower")["outcome"] == "match"


def _d3_distributions(funding, impact, basis=None):
    m4 = copy.deepcopy(M4)
    m4["deliverable_3"]["debt_utilisation_matrix"]["shareholder_distributions_vs_leverage_balance"] = "Buybacks."
    for row in m4["deliverable_3"]["capital_deployment_reconciliation"]:
        if row["capital_allocation_stream"] == "Share repurchases":
            row.update(primary_funding_source=funding, leverage_impact=impact)
            if basis:
                row["funding_basis"] = basis
    return m4


def test_debt_funded_distributions_are_read_from_the_reconciliation_rows():
    # The live wording, with no "debt-funded" anywhere.
    live = _d3_distributions("Operating cash flow and debt issuance proceeds",
                             "Aggressively debt-supported shareholder returns")
    assert load_bo(m4_analysis=live).distributions_debt_funded
    assert "RMS-M05" in {m["rule_id"] for m in assess(RmsRequest(2000), FO, m4_analysis=live).mitigants}
    # A declared funding basis wins over the text.
    internal = _d3_distributions("Operating cash flow and debt issuance proceeds", "", basis="internal")
    r = assess(RmsRequest(2000), FO, m4_analysis=internal)
    assert not load_bo(m4_analysis=internal).distributions_debt_funded
    assert "RMS-M05" not in {m["rule_id"] for m in r.mitigants} and r.terms["financial_policy"] is None
    assert r.terms["financial_covenants"][0]["covenant"] == "debt_incurrence"


def test_small_debt_funded_distributions_keep_the_incurrence_test():
    m4 = copy.deepcopy(M4)
    for row in m4["deliverable_3"]["capital_deployment_reconciliation"]:
        if row["capital_allocation_stream"] in ("Share repurchases", "Dividends"):
            row["cumulative_amount_3y_m"] = 2000.0  # 4,000 of 46,492: under a quarter
    r = assess(RmsRequest(2000), FO, m4_analysis=m4)
    assert "RMS-M05" in {m["rule_id"] for m in r.mitigants} and r.terms["financial_policy"] is None
    assert r.terms["financial_covenants"][0]["covenant"] == "debt_incurrence"


def test_each_material_risk_gets_one_category_title_first():
    h = handoff(vulnerabilities=[
        {"claim_id": "risk-01", "title": "Government Healthcare Program Dependency",
         "text": "Revenues from Medicare and Medicaid."},
        {"claim_id": "risk-02", "title": "Geographic Concentration",
         "text": "Florida and Texas facilities, exposed to regulatory changes and weather events."},
        {"claim_id": "risk-03", "title": "Intensifying Payer Power and Narrow Networks",
         "text": "Private payer consolidation and price transparency requirements."},
        {"claim_id": "risk-04", "title": "Staffing", "text": "Nurse shortages.", "risk_category": "litigation"},
    ])
    by_id = {m["rule_id"]: m for m in assess(RmsRequest(2000), FO, handoff=h).mitigants}
    assert by_id["RMS-M14"]["trigger"].startswith("BO material risk(s) risk-01:")
    assert by_id["RMS-M16"]["trigger"].startswith("BO material risk(s) risk-02:")
    assert by_id["RMS-M17"]["trigger"].startswith("BO material risk(s) risk-03:")
    assert by_id["RMS-M14"]["heuristic"] is True
    assert by_id["RMS-M15"]["trigger"].startswith("BO material risk(s) risk-04:")
    assert by_id["RMS-M15"]["heuristic"] is False  # declared by the BO


def _refinancing(fo_score=None, **metrics):
    fo = copy.deepcopy(FO)
    if fo_score is not None:
        next(m for m in fo["metrics"] if m["key"] == "liquidity")["columns"][0]["refinancing_score"] = fo_score
    m4 = copy.deepcopy(M4)
    m4["deliverable_2"]["metrics"].update(metrics)
    return fo, m4


@pytest.mark.parametrize("fo_score, metrics, rolls", [
    (None, {}, True),  # FO 3; M4 11 months, +25bp, 7.5 years
    (None, {"weighted_avg_refinancing_lead_time_months": 3.0}, False),  # refinanced too late: vetoed
    (None, {"refinancing_cost_spread_delta_bps": 200.0}, False),  # at a widening spread: vetoed
    (4, {}, True),  # one FO score worse, confirmed by a strong M4 record
    (4, {"refinancing_cost_spread_delta_bps": 75.0}, False),  # M4 not strong enough to confirm
    (5, {}, False),
])
def test_m4_refinancing_evidence_vetoes_or_confirms_the_rollover(fo_score, metrics, rolls):
    fo, m4 = _refinancing(fo_score, **metrics)
    a = assess(RmsRequest(2000), fo, m4_analysis=m4).capacity["assumptions"]
    assert a["rollover"] is rolls and a["rollover_basis"]


def test_the_revolver_is_cross_checked_gross_or_net_of_commercial_paper():
    # The sample's $8,000M revolver less $2,207M CP is FO's $5,779M (net basis).
    r = assess(RmsRequest(2000), FO, m4_analysis=M4)
    check = next(c for c in r.cross_checks if c["check"] == "revolver")
    assert check["outcome"] == "match" and "net of commercial paper" in check["detail"]
    m4 = copy.deepcopy(M4)
    next(o for o in m4["deliverable_1"]["non_note_obligations_schedule"]
         if "revolving" in o["obligation_facility"])["available_facility_limit_m"] = 15000.0
    r = assess(RmsRequest(2000), FO, m4_analysis=m4)
    assert r.verdict == "refer" and "RMS-G12" in _gate_ids(r)


def test_the_bo_questions_and_an_aggregated_ladder_become_conditions_and_limitations():
    m4 = copy.deepcopy(M4)
    m4["deliverable_1"]["senior_notes_schedule"] = m4["deliverable_1"]["senior_notes_schedule"][:1]
    h = handoff(m4)
    h.update(fo_handoffs=[{"hypothesis_id": "M4-H003", "variable": "free_cash_flow",
                           "question": "Does FCF leave headroom?"}],
             open_questions=["What are the supplier concentrations?"], evidence_limitations=["Supplier breakdown"])
    r = assess(RmsRequest(2000), FO, handoff=h)
    m19 = next(m for m in r.mitigants if m["rule_id"] == "RMS-M19")
    assert m19["category"] == "condition_precedent" and "Does FCF leave headroom?" in m19["mitigant"]
    assert any("one aggregate line" in x for x in r.limitations)
    assert "BO open question: What are the supplier concentrations?" in r.limitations
    assert "BO evidence gaps: Supplier breakdown" in r.limitations
    assert any("business risk band unavailable" in x for x in r.limitations)
