"""FO pre-checks: when the agent's own answer is more severe than its verified evidence, the analyst decides.

Pure functions over pre-check results shaped as the pre-checks record them; no filing, no model.
"""

from __future__ import annotations

from app.services.fo.runner import disagreements, escalations_for


def _check(status, stated, **extra):
    return {"status": status, "reason": f"{status} reason", "agent": {"stated_status": stated, **extra}}


PASS = _check("pass", "pass")
COMPLIANT = _check("compliant", "compliant")
METRICS = [{"key": k, "columns": [{"heading": "FY", "score": s}]} for k, s in (("liquidity", 4), ("dso", 6))]


def test_agreement_or_an_agent_less_severe_than_the_evidence_is_not_a_disagreement():
    assert disagreements(PASS, COMPLIANT, None, None) == []
    assert disagreements(_check("fail", "pass"), _check("breach", "compliant"), None, None) == []  # engine escalates
    assert disagreements(PASS, _check("relief", "routine"), None, _check("unresolved", "billed_revenue")) == []


def test_an_agent_fail_against_the_evidence_is_a_run_decision():
    [e, *rest] = escalations_for(_check("pass", "fail"), COMPLIANT, METRICS)
    assert rest == [] and (e["type"], e["id"], e["disagreement"]) == ("run_decision", "going_concern:disagreement",
                                                                      True)
    assert e["reason"] == "Going concern: the agent read FAIL; the verified evidence gives PASS"
    [watch] = escalations_for(_check("pass", "watch"), COMPLIANT, METRICS)
    assert (watch["type"], watch["measure"], watch["engine_score"]) == ("measure_override", "liquidity", 4)


def test_covenant_and_prepaid_disagreements_escalate_their_measure():
    [cov] = escalations_for(PASS, _check("compliant", "breach"), METRICS)
    assert (cov["measure"], cov["check"], cov["title"]) == ("liquidity", "covenants",
                                                            "agent and verified evidence disagree")
    assert "the agent read breach; the verified evidence gives compliant" in cov["reason"]
    [pp] = escalations_for(PASS, COMPLIANT, METRICS, None, _check("not_applicable", "unresolved"))
    assert (pp["id"], pp["measure"], pp["engine_score"]) == ("prepaid:dso", "dso", 6)


def test_a_deal_the_agent_left_unresolved_escalates_the_measures_it_affects():
    ad = {"status": "no_adjustment", "reason": "x", "unresolved_measures": [], "agent": {"held_back_deals": [
        {"deal_id": "D2", "kind": "divestiture", "timing": "after_year_end", "presentation": "continuing",
         "outcome": "unresolved"}]},
        "deals": [{"deal_id": "D1", "kind": "acquisition", "timing": "in_year", "presentation": "not_applicable",
                   "outcome": "not_material", "stated": {"outcome": "unresolved"}}]}
    found = disagreements(PASS, COMPLIANT, ad, None)
    assert [(d["measure"], d["verified"]) for d in found] == [("dso", "not_material"), ("leverage", "held back")]
    assert found[0]["reason"] == "Acquisitions: the agent called D1 unresolved; the verified evidence gives not material"


def test_one_decision_per_measure_when_several_checks_escalate_it():
    ad = {"status": "unresolved", "reason": "A deal", "unresolved_measures": ["dso"],
          "deals": [{"deal_id": "D1", "effects": {"unresolved": ["dso"]}, "stated": {"outcome": "unresolved"},
                     "outcome": "unresolved", "kind": "acquisition", "timing": "in_year"}], "agent": {}}
    [e] = escalations_for(PASS, COMPLIANT, METRICS, ad, _check("not_applicable", "unresolved"))
    assert (e["id"], e["checks"], e["disagreement"]) == ("acquisitions:dso", ["acquisitions", "prepaid"], True)
    assert e["reason"].startswith("A deal (D1); and: Prepaid model: the agent read unresolved")
