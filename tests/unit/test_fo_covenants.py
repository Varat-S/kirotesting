"""FO covenant pre-check: the search, the keyword rules, the agent's validated findings and the scoring effect.

Synthetic 10-K text only; the agent runs on a scripted offline backend (no network, no model).
"""

from __future__ import annotations

import pytest

from app.services.fo.covenants import covenant_passages, covenant_precheck, derive_status, rule_findings
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.scoring import SCORING_DEFAULTS, composite_risk, score_financial_overview
from app.services.llm.client import FakeLLMBackend

COMPLIANT = "As of December 31, 2025, we were in compliance with all financial covenants under our credit agreement."
BREACH = ("As of December 31, 2025, we were not in compliance with the maximum leverage ratio covenant under our "
          "credit agreement, and the lenders have not waived the breach.")
WAIVED_LONG = ("In February 2026, the lenders waived the leverage covenant breach through March 31, 2027 under our "
               "credit agreement.")
WAIVED_SHORT = ("In February 2026, the lenders waived the leverage covenant breach through June 30, 2026 under our "
                "credit agreement.")
WAIVED_OPEN = "The lenders under our credit agreement granted a waiver of the leverage covenant breach."
RELIEF = ("In November 2025, we amended the credit agreement to increase the maximum net leverage ratio covenant to "
          "5.00 to 1.00 through 2026.")
ROUTINE = ("In October 2025, the credit facility was amended to extend its maturity to 2030 and replace LIBOR with "
           "SOFR as the benchmark interest rate.")
LIKELY = ("Based on our forecasts, it is reasonably likely that we will not be in compliance with the leverage ratio "
          "covenant within the next 12 months.")
BOILERPLATE = ("A breach of any of these covenants could result in an event of default under the credit agreement, "
               "which could permit the lenders to accelerate the debt.")
HEADROOM = ("The credit agreement requires a maximum leverage ratio of 4.50 to 1.00; our leverage ratio was 2.10 to "
            "1.00 as of December 31, 2025, and we were in compliance with all covenants.")
MEDICAID = ("Some states use waivers granted by CMS to implement Medicaid expansion and to fund uncompensated care "
            "programs in those states.")
DATA_BREACH = "A breach of our information systems could expose patient data and lead to significant liabilities."


def _data(*, debt=(), liquidity=(), subsequent=(), other_mdna=(), risk=(DATA_BREACH, BOILERPLATE)):
    item7 = ["Results of Operations", *other_mdna, "Liquidity and Capital Resources", *liquidity,
             "Market Risk", "Our exposure to changes in interest rates is limited and discussed in this section."]
    notes = ["NOTE 1 — ACCOUNTING POLICIES", "We prepare our statements in accordance with generally accepted principles.",
             "NOTE 8 — DEBT", *debt, "NOTE 9 — LEASES", BOILERPLATE.replace("credit agreement", "lease agreements"),
             *(["NOTE 16 — SUBSEQUENT EVENTS", *subsequent] if subsequent else [])]
    return {
        "meta": {"entity_name": "Example Co", "period_end": "2025-12-31", "fiscal_year": "2025"},
        "facts": [],
        "document": [
            {"item": "1A", "title": "Risk Factors", "text": "\n".join(risk)},
            {"item": "7", "title": "MD&A", "text": "\n".join(item7)},
            {"item": "8", "title": "Financial Statements", "text": "\n".join(notes)},
        ],
    }


def test_the_search_reads_only_hits_in_the_four_sections():
    cov = covenant_passages(_data(debt=[COMPLIANT], liquidity=[ROUTINE], other_mdna=[MEDICAID],
                                  subsequent=[WAIVED_LONG]))
    assert [(p["passage_id"], p["section"]) for p in cov["passages"]] == [
        ("C01", "mdna_liquidity"), ("C02", "debt_note"), ("C03", "subsequent_events")]
    # Not read: the risk factors, a Medicaid waiver elsewhere in MD&A, the leases note.
    assert all("Medicaid" not in p["text"] and "lease" not in p["text"] for p in cov["passages"])
    located = {s["key"]: s["located"] for s in cov["sections"]}
    assert located == {"debt_note": True, "mdna_liquidity": True, "auditor_report": False, "subsequent_events": True}


def test_no_hit_is_in_compliance_without_calling_the_agent(db_session):
    result = covenant_precheck(_data(debt=["Long-term debt consists of senior notes due between 2027 and 2035."]),
                               session=db_session, backend=FakeLLMBackend(), mode="scripted")
    assert (result["status"], result["outcome"], result["decided_by"]) == ("compliant", "d", "no_hits")
    assert result["agent"] is None and not result["review_required"]


def test_keyword_rules_cross_check_first_match():
    def status(**kw):
        data = _data(**kw)
        return derive_status(rule_findings(covenant_passages(data)["passages"], data["meta"]))

    assert status(debt=[COMPLIANT]) == "compliant"
    assert status(debt=[COMPLIANT, BREACH]) == "breach"  # (a) before (d)
    assert status(liquidity=[LIKELY]) == "breach"
    assert status(debt=[RELIEF, ROUTINE]) == "relief"  # (b) before (c)
    assert status(debt=[ROUTINE, COMPLIANT]) == "routine"
    assert status(subsequent=[WAIVED_LONG]) == "relief"
    assert status(subsequent=[WAIVED_SHORT]) == status(debt=[WAIVED_OPEN]) == "unclear"
    assert status(debt=[BOILERPLATE]) == "compliant"  # what could happen is not a breach


def _cov(status, *findings, headroom=()):
    return {"covenants": {"status": status, "rationale": "Scripted.", "missing_information": [],
                          "findings": list(findings), "headroom": list(headroom)}}


def test_a_search_hit_always_needs_the_agent(db_session):
    with pytest.raises(PrecheckAgentError, match="required to interpret the covenant passages"):
        covenant_precheck(_data(debt=[COMPLIANT]))  # the rules alone never decide
    only_gc = scripted_backend({"going_concern": {"status": "pass"}})
    with pytest.raises(PrecheckAgentError, match="no 'covenants' response"):
        covenant_precheck(_data(debt=[COMPLIANT]), backend=only_gc, mode="scripted")
    rejected = FakeLLMBackend()
    rejected.register("analyze", '{"status": "breach"}', key="fo_precheck_covenants")  # schema-invalid
    with pytest.raises(PrecheckAgentError, match="covenant output was rejected"):
        covenant_precheck(_data(debt=[COMPLIANT]), session=db_session, backend=rejected, mode="scripted")


def test_the_agent_interprets_and_the_rules_only_cross_check(db_session):
    result = covenant_precheck(_data(debt=[COMPLIANT, BREACH]), session=db_session, mode="scripted",
                               backend=scripted_backend(_cov("compliant", {
                                   "passage_id": "C01", "classification": "in_compliance", "quote": COMPLIANT})))
    assert (result["status"], result["decided_by"], result["rules"]["status"]) == ("compliant", "agent", "breach")
    assert any("keyword rules read this filing as BREACH" in n for n in result["notes"])


def test_unclear_or_short_waivers_are_an_information_flag_not_an_escalation(db_session):
    short = covenant_precheck(_data(subsequent=[WAIVED_SHORT]), session=db_session, mode="scripted",
                              backend=scripted_backend(_cov("unclear", {
                                  "passage_id": "C01", "classification": "short_waiver", "quote": WAIVED_SHORT,
                                  "waiver_until": "2026-06-30"})))
    assert (short["status"], short["review_required"]) == ("unclear", False)
    assert short["waivers"][0]["months"] < 12 and "12 months or less" in short["info_flags"][0]
    unclear = covenant_precheck(_data(debt=[WAIVED_OPEN]), session=db_session, mode="scripted",
                                backend=scripted_backend(_cov("unclear", {
                                    "passage_id": "C01", "classification": "unclear_status", "quote": WAIVED_OPEN})))
    assert unclear["status"] == "unclear" and "status or duration isn't clear" in unclear["info_flags"][0]
    assert "no escalation" in unclear["effect"]


def test_agent_waiver_length_is_checked_against_its_quote(db_session):
    data = _data(subsequent=[WAIVED_LONG])
    good = covenant_precheck(data, session=db_session, mode="scripted", backend=scripted_backend(_cov("relief", {
        "passage_id": "C01", "classification": "waiver_over_12_months", "quote": WAIVED_LONG,
        "waiver_until": "2027-03-31"})))
    assert (good["status"], good["decided_by"]) == ("relief", "agent")
    assert good["agent"]["accepted"][0]["waiver_months"] > 12
    wrong_date = covenant_precheck(data, session=db_session, mode="scripted", backend=scripted_backend(_cov(
        "relief", {"passage_id": "C01", "classification": "waiver_over_12_months", "quote": WAIVED_LONG,
                   "waiver_until": "2028-12-31"})))
    assert wrong_date["status"] == "unclear"  # the date is not in the quote: duration unclear (e)
    assert any("not in the quote" in n for n in wrong_date["notes"])


def test_agent_findings_that_do_not_hold_up_are_held_back(db_session):
    data = _data(debt=[BOILERPLATE, HEADROOM])
    result = covenant_precheck(data, session=db_session, mode="scripted", backend=scripted_backend(_cov(
        "breach",
        {"passage_id": "C01", "classification": "breach_not_waived", "quote": BOILERPLATE},
        {"passage_id": "C02", "classification": "in_compliance", "quote": "we were in compliance with all covenants"},
        headroom=[{"passage_id": "C02", "covenant": "Maximum leverage ratio", "required": "4.50 to 1.00",
                   "actual": "2.10 to 1.00", "quote": HEADROOM.split(";")[0] + ";" + HEADROOM.split(";")[1]},
                  {"passage_id": "C02", "covenant": "Interest coverage", "required": "3.00 to 1.00", "actual": None,
                   "quote": "The credit agreement requires a maximum leverage ratio of 4.50 to 1.00"}])))
    assert result["status"] == "compliant" and not result["agent"]["status_consistent"]
    assert "hypothetical" in result["agent"]["held_back"][0]["reason"]
    assert [h["covenant"] for h in result["headroom"]] == ["Maximum leverage ratio"]
    assert result["agent"]["headroom_held_back"][0]["reason"] == "the levels given are not in the quote"


def _liquidity(score_inputs):
    return [{"key": "liquidity", "title": "Liquidity", "short": "Liquidity", "columns": [{
        "heading": "Next 12M", "as_at": "2025-12-31", "cash": 100.0, "cfo": 50.0, "y1": 60.0, "capex": 40.0,
        "dividends": 0.0, "nci_distributions": 0.0, "revolver_found": 200.0, "revolver_found_source": "tagged",
        "weighted_maturities": 300.0, "concentration": 0.5, **score_inputs}]}]


def test_breach_sets_the_revolver_to_zero_and_liquidity_to_ten_relief_adds_one():
    settings = {"values": dict(SCORING_DEFAULTS), "benchmarks": {}}
    normal = score_financial_overview(_liquidity({}), settings)[1]["columns"][0]
    breach_m = score_financial_overview(_liquidity({}), settings, covenant_status="breach")[1]
    breach = breach_m["columns"][0]
    assert breach["revolver"] == 0 and breach["revolver_source"].startswith("Set to 0: covenant breach")
    assert breach["coverage"] < normal["coverage"] and breach["refinancing"] < normal["refinancing"]
    assert breach["score"] == 10 and breach["covenant_adjustment"]["calculated"] >= normal["score"]
    assert "Escalated to an analyst" in breach_m["covenant_note"]
    relief = score_financial_overview(_liquidity({}), settings, covenant_status="relief")[1]["columns"][0]
    assert relief["score"] == min(10, normal["score"] + 1)
    unclear = score_financial_overview(_liquidity({}), settings, covenant_status="unclear")[1]["columns"][0]
    assert unclear["score"] == normal["score"] and "covenant_adjustment" not in unclear


def test_an_analyst_override_replaces_the_measure_score_in_the_composite():
    fo = [{"key": "leverage", "columns": [{"heading": "FY", "period": "2025", "composite": {"score": 4.0}}]},
          {"key": "liquidity", "columns": [{"heading": "Next 12M", "period": "2025", "score": 10}]}]
    settings = {"values": dict(SCORING_DEFAULTS)}
    engine = composite_risk(fo, settings)
    overridden = composite_risk(fo, settings, overrides={"liquidity": 6})
    assert engine["columns"][0]["score"] > overridden["columns"][0]["score"]
    row = next(r for r in overridden["rows"] if r["key"] == "liquidity")
    assert row["score"] == 6 and "overridden by analyst: 10 → 6" in row["notes"]
    assert overridden["adjustments"][0].startswith("Liquidity and debt maturity: overridden by analyst to 6")
    accepted = composite_risk(fo, settings, overrides={"liquidity": 10}, decisions={"liquidity": "accept"})
    row = next(r for r in accepted["rows"] if r["key"] == "liquidity")
    assert row["notes"][-1] == "engine score (accepted by the analyst)"
    assert accepted["adjustments"][0] == "Liquidity and debt maturity: engine score 10 accepted by the analyst"
