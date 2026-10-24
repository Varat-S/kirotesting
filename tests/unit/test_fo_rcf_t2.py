"""FO Trigger Agent, RCF/Debt T2: when it fires (at any score: tagged one-off inflows above 5% of FFO, or FFO up 25% or
more; agent triggers enabled), the passages it reads, the verified evidence, code's placement of each inflow against
the working-capital lines and its recurrence test, the outcome (first match), the netting with RCF/Debt T1 and the
escalation of RCF / debt.

Synthetic 10-K text and a scored RCF measure only; the agent runs on a scripted offline backend (no network, no model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.rcf_t2 import apply_rcf_t2, rcf_t2_passages, rcf_t2_trigger
from app.services.fo.rcf_triggers import apply_rcf_t1, rcf_t1_passages, rcf_t1_trigger
from app.services.fo.reporting import measure_card, rcf_t2_card
from app.services.fo.runner import escalations_for
from app.services.fo.scoring import score_cash_flow

FY25, FY24, FY23 = "2025-01-01--2025-12-31", "2024-01-01--2024-12-31", "2023-01-01--2023-12-31"
ENABLED, SKIPPED = {"agent_triggers": "enabled"}, {"agent_triggers": "skipped"}
PASS, COMPLIANT = {"status": "pass"}, {"status": "compliant"}
WC = {"us-gaap:IncreaseDecreaseInAccountsReceivable": -1.0, "us-gaap:IncreaseDecreaseInAccountsPayable": 1.0}
INSURANCE = "In 2025, we received insurance proceeds of $90 million for hurricane damage, included in operating activities."
REFUND = "We received an income tax refund of $60 million in 2025."
REFUND_ROW = "Income taxes refunded were $55 million in 2024 and $50 million in 2023."
CATCHUP = ("Operating cash flow also benefited from the collection of $70 million of prior-year supplemental payment "
           "receivables, a timing catch-up.")
RECEIVABLES = "Collections of accounts receivable increased by $40 million in 2025."
SALE = "We received proceeds of $200 million from the sale of a hospital, included in investing activities."
SETTLED = "We received a litigation settlement in 2025 from a former vendor."
OPERATING = "Cash provided by operating activities increased to $600 million in 2025."
SETTINGS = {"values": {"p": 5.0, "delta": 2.5, "cap": 3.0, "D": 5.0},
            "benchmarks": {"rcf_to_debt": 0.25}, "sector": "Hospitals/Healthcare Facilities"}


def _data(wc=WC):
    mdna = ["Liquidity and Capital Resources", INSURANCE, REFUND, REFUND_ROW, CATCHUP, RECEIVABLES, SALE, SETTLED,
            OPERATING]
    return {"meta": {"entity_name": "Example Co", "ticker": "EXCO", "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": [], "labels": {}, "sections": [], "working_capital": wc,
            "document": [{"item": "7", "title": "MD&A", "text": "\n".join(mdna)}]}


def _col(heading, period, ffo, inflows=(), dividends=50e6, adj_debt=2000e6, revenue=None):
    rcf = ffo - dividends
    col = {"heading": heading, "period": period, "basis": "historical", "cfo": ffo + 30e6, "wc_change": 30e6,
           "ffo": ffo, "ffo_basis": "FFO", "dividends": dividends, "nci_distributions": 0.0, "rcf": rcf,
           "rcf_cfo": rcf + 30e6, "rcf_to_debt_cfo": (rcf + 30e6) / adj_debt, "adj_debt": adj_debt,
           "tagged_inflows": [{"concept": c, "category": cat, "value": v} for c, cat, v in inflows], "revenue": revenue,
           "rcf_to_debt": {"ratio": rcf / adj_debt, "reason": None, "no_debt": False}}
    score_cash_flow(col, SETTINGS)
    return col


REFUND_TAG = ("us-gaap:ProceedsFromIncomeTaxRefunds", "tax_refund")


def _rcf(ffo=600e6, prior=500e6, inflows=(), prior_inflows=(), older_inflows=(), revenue=None, prior_revenue=None):
    """FFO 600 (prior 500: +20%), no tagged inflows, no revenue: not set off unless a leg is changed."""
    cols = [_col("FY2025", FY25, ffo, inflows, revenue=revenue), _col("FY2024", FY24, prior, prior_inflows,
                                                                       revenue=prior_revenue),
            _col("FY2023", FY23, 450e6, older_inflows)]
    return [{"key": "overall", "columns": [{"score": 5}]},
            {"key": "rcf", "title": "RCF to debt", "short": "RCF / Debt", "rows": [], "columns": cols,
             "scoring": {"sector": SETTINGS["sector"], "benchmarks": SETTINGS["benchmarks"], **SETTINGS["values"],
                         "m": 2.0, "D_dso": 3.0}}]


GROWN = dict(ffo=700e6, prior=500e6)  # +40%: fires on growth


def _q(pid, quote, **kw):
    return {"passage_id": pid, "quote": quote, **kw}


def _inflow(item_id, kind, category, pid, quote, amount="", line=None, one_time=None, other_years=()):
    return {"item_id": item_id, "kind": kind, "category": category, "description": category,
            "working_capital_line": line, "evidence": _q(pid, quote, amount=amount), "one_time_evidence": one_time,
            "other_years": list(other_years)}


def _says(status, inflows=(), unquantified=()):
    return {"rcf_t2": {"status": status, "inflows": list(inflows), "unquantified": list(unquantified),
                       "rationale": "Scripted.", "missing_information": []}}


def _run(response, db_session, metrics=None, data=None, t1=None):
    return rcf_t2_trigger(data or _data(), metrics or _rcf(**GROWN), ENABLED, session=db_session,
                          backend=scripted_backend(response), mode="scripted", case_id="EX_FY2025", t1=t1)


PROCEEDS = _inflow("I1", "operating_inflow", "insurance", "G01", INSURANCE, "$90 million")


def test_the_agent_reads_cash_receipt_passages():
    t = rcf_t2_passages(_data())
    assert [(p["passage_id"], p["text"]) for p in t["passages"]] == [
        ("G01", INSURANCE), ("G02", REFUND), ("G03", REFUND_ROW), ("G04", CATCHUP), ("G05", RECEIVABLES),
        ("G06", SALE), ("G07", SETTLED), ("G08", OPERATING)]


def test_the_two_legs_and_what_does_not_fire(db_session):
    quiet = rcf_t2_trigger(_data(), _rcf(), ENABLED)  # +20%, nothing tagged
    assert (quiet["status"], quiet["fired"]) == ("not_triggered", False) and "+20.0%" in quiet["reason"]
    grown = rcf_t2_trigger({**_data(), "document": []}, _rcf(**GROWN), ENABLED)
    assert grown["fired"] and grown["fired_by"] == "FFO up 40.0% on FY2024 (25% or more), revenue growth not tagged"
    outgrows = rcf_t2_trigger({**_data(), "document": []}, _rcf(**GROWN, revenue=1150e6, prior_revenue=1000e6),
                              ENABLED)  # FFO +40% against revenue +15%: 25 points faster
    assert outgrows["fired_by"] == ("FFO up 40.0% on FY2024 (25% or more), 25.0 points above revenue growth of "
                                    "15.0%")
    growing = rcf_t2_trigger(_data(), _rcf(**GROWN, revenue=1350e6, prior_revenue=1000e6), ENABLED)  # +35%: 5 points
    assert growing["fired"] is False and "against revenue +35.0%" in growing["reason"]
    tagged = rcf_t2_trigger({**_data(), "document": []}, _rcf(inflows=[(*REFUND_TAG, 60e6)]), ENABLED)
    assert tagged["fired_by"] == "tagged one-off inflows of 60.0 million (10.0% of FFO, above 5%)"
    small = rcf_t2_trigger(_data(), _rcf(inflows=[(*REFUND_TAG, 20e6)]), ENABLED)  # 3.3% of FFO
    assert small["fired"] is False
    skipped = rcf_t2_trigger(_data(), _rcf(**GROWN), SKIPPED)
    assert (skipped["status"], skipped["fired"]) == ("skipped", False)
    assert db_session.scalars(select(ModelRun)).first() is None


def test_a_cfo_rise_from_working_capital_alone_does_not_fire():
    metrics = _rcf()
    col = metrics[1]["columns"][0]
    col["cfo"] += 300e6  # working capital released: CFO up, FFO unchanged
    col["wc_change"] += 300e6
    assert rcf_t2_trigger(_data(), metrics, ENABLED)["fired"] is False


def test_a_fired_trigger_needs_the_agent_and_without_text_there_is_no_adjustment(db_session):
    with pytest.raises(PrecheckAgentError, match="Trigger Agent is required"):
        rcf_t2_trigger(_data(), _rcf(**GROWN), ENABLED)
    with pytest.raises(PrecheckAgentError, match="no 'rcf_t2' response"):
        rcf_t2_trigger(_data(), _rcf(**GROWN), ENABLED, backend=scripted_backend({"icr_t1": {}}), mode="scripted")
    result = rcf_t2_trigger({**_data(), "document": []}, _rcf(**GROWN), ENABLED)
    assert (result["status"], result["decided_by"], result["unresolved"]) == ("no_adjustment", "no_text", False)


def test_an_operating_inflow_outside_working_capital_is_removed(db_session):
    result = _run(_says("removed", [PROCEEDS]), db_session)
    assert (result["status"], result["removed"], result["unresolved"]) == ("removed", 90e6, False)
    metrics = _rcf(**GROWN)
    m = apply_rcf_t2(metrics, result)[1]
    col = m["columns"][0]
    assert (col["cfo_before_trigger"], col["cfo"], col["ffo_before_trigger"], col["ffo"]) == (
        730e6, 640e6, 700e6, 610e6)
    assert (col["rcf"], col["rcf_to_debt"]["ratio"]) == (560e6, pytest.approx(0.28))
    assert col["rcf_to_debt_cfo"] == pytest.approx(590e6 / 2000e6)  # the comparison moves too
    assert col["rcf_to_debt"]["score"] == _col("x", FY25, 610e6)["rcf_to_debt"]["score"]
    assert metrics[1]["columns"][0]["cfo"] == 730e6  # the input is untouched
    assert m["trigger_summary"] == "trigger RCF/Debt T2: one-off inflows removed" and not m["trigger_escalated"]
    assert escalations_for(PASS, COMPLIANT, [m], None, None, {"rcf_t2": result}) == []
    labels = [r["label"] for r in measure_card(m)["result_rows"]]
    assert labels[:4] == ["RCF / debt", "RCF / debt as calculated (before trigger RCF/Debt T2)",
                          "Risk score as calculated (before trigger RCF/Debt T2)",
                          "Risk score (after trigger RCF/Debt T2)"]
    assert rcf_t2_card(result)["label"] == "INFLOWS REMOVED: RECOMPUTED"


def test_a_working_capital_release_inside_the_lines_gets_no_adjustment(db_session):
    inside = _inflow("I1", "working_capital_release", "working_capital", "G05", RECEIVABLES, "$40 million",
                     line="us-gaap:IncreaseDecreaseInAccountsReceivable")
    result = _run(_says("no_adjustment", [inside]), db_session)
    assert (result["status"], result["unresolved"]) == ("no_adjustment", False)
    assert "I1 inside the working-capital lines (FFO already excludes it)" in result["reason"]
    bogus = _inflow("I2", "operating_inflow", "insurance", "G01", INSURANCE, "$90 million", line="us-gaap:Made")
    held = _run(_says("no_adjustment", [bogus]), db_session)
    assert held["agent"]["held_back"][0]["reason"] == "us-gaap:Made isn't one of the working-capital lines FFO subtracts"


def test_a_release_outside_the_lines_is_removed_only_when_one_time(db_session):
    release = _inflow("I1", "working_capital_release", "supplemental_payment", "G04", CATCHUP, "$70 million",
                      one_time=_q("G04", "a timing catch-up"))
    result = _run(_says("removed", [release]), db_session)
    assert (result["status"], result["removed"]) == ("removed", 70e6)
    plain = _inflow("I1", "working_capital_release", "supplemental_payment", "G04", CATCHUP, "$70 million")
    kept = _run(_says("no_adjustment", [plain]), db_session)
    assert kept["status"] == "no_adjustment" and any("not described as one-time" in n for n in kept["notes"])
    unknown = _run(_says("unresolved", [release]), db_session, data=_data(wc={}))  # composition can't be determined
    assert (unknown["status"], unknown["unresolved"]) == ("unresolved", True)


def test_a_recurring_inflow_gets_no_adjustment(db_session):
    by_tags = _rcf(**GROWN)
    for col in by_tags[1]["columns"]:
        col["tagged_inflows"] = [{"concept": REFUND_TAG[0], "category": "tax_refund", "value": 55e6}]
    refund = _inflow("I1", "operating_inflow", "tax_refund", "G02", REFUND, "$60 million")
    result = _run(_says("no_adjustment", [refund]), db_session, by_tags)
    assert result["status"] == "no_adjustment" and result["inflows"][0]["recurrence"] == (
        "tagged in 3 of 3 years (FY2025, FY2024, FY2023)")
    by_quotes = _inflow("I1", "operating_inflow", "insurance", "G01", INSURANCE, "$90 million",
                        other_years=[_q("G03", REFUND_ROW, amount="$55 million")])
    quoted = _run(_says("no_adjustment", [by_quotes]), db_session)
    assert quoted["status"] == "no_adjustment" and quoted["inflows"][0]["outcome"] == "recurring"


def test_unquantified_is_unresolved_and_investing_gets_no_adjustment(db_session):
    settlement = {"item_id": "U1", "kind": "operating_inflow", "category": "litigation", "description": "Settlement",
                  "evidence": _q("G07", SETTLED)}
    result = _run(_says("unresolved", unquantified=[settlement]), db_session)
    assert (result["status"], result["unresolved"]) == ("unresolved", True)
    metrics = apply_rcf_t2(_rcf(**GROWN), result)
    [e] = escalations_for(PASS, COMPLIANT, metrics[1:], None, None, {"rcf_t2": result})
    assert (e["id"], e["measure"]) == ("rcf_t2:rcf", "rcf")
    sale = _inflow("I1", "investing", "other", "G06", SALE, "$200 million")
    invest = _run(_says("no_adjustment", [sale]), db_session)
    assert (invest["status"], invest["removed"]) == ("no_adjustment", 0.0)
    first = _run(_says("removed", [PROCEEDS], [settlement]), db_session)  # a quantified removal comes first
    assert (first["status"], first["removed"], first["unresolved"]) == ("removed", 90e6, False)


def test_t1_and_t2_net_on_the_same_column_and_share_one_decision(db_session):
    """T1 adds back a one-off cash cost (300), T2 removes an inflow (90); an item T1 counted isn't counted again."""
    cost = "We paid $300 million in cash for restructuring and severance in 2025."
    data = _data()
    data["document"][0]["text"] += "\n" + cost
    metrics = _rcf(ffo=700e6, prior=500e6)
    metrics[1]["columns"][0]["rcf_to_debt"]["score"] = 9.5  # T1 fires on the score
    pid = next(p["passage_id"] for p in rcf_t1_passages(data)["passages"] if p["text"] == cost)
    t1 = rcf_t1_trigger(data, metrics, ENABLED, session=db_session, mode="scripted", case_id="EX_FY2025",
                        backend=scripted_backend({"rcf_t1": {
                            "status": "added_back", "unquantified": [], "rationale": "Scripted.",
                            "missing_information": [],
                            "one_offs": [{"item_id": "O1", "kind": "cash_cost", "category": "restructuring",
                                          "description": "Severance", "recurring": False, "recurring_evidence": None,
                                          "evidence": _q(pid, cost, amount="$300 million")}]}}))
    assert (t1["status"], t1["cash_costs"]) == ("added_back", 300e6)
    metrics = apply_rcf_t1(metrics, t1)
    t2 = _run(_says("removed", [PROCEEDS]), db_session, metrics, data, t1)
    metrics = apply_rcf_t2(metrics, t2)
    col = metrics[1]["columns"][0]
    assert (col["ffo_before_trigger"], col["ffo"]) == (700e6, 910e6)  # 700 + 300 − 90
    assert t2["inputs"]["ffo"] == 1000e6  # T2 sees T1's FFO
    labels = [r["label"] for r in measure_card(metrics[1])["result_rows"]]
    assert "RCF / debt after trigger RCF/Debt T1, before trigger RCF/Debt T2" in labels
    assert "Risk score (after trigger RCF/Debt T1 and RCF/Debt T2)" in labels
    unresolved = _run(_says("unresolved", unquantified=[{"item_id": "U1", "kind": "operating_inflow",
                                                         "category": "litigation", "description": "x",
                                                         "evidence": _q("G07", SETTLED)}]),
                      db_session, apply_rcf_t1(_rcf(ffo=700e6, prior=500e6), {**t1, "unresolved": True,
                                                                                  "unresolved_why": "x"}), data, t1)
    m = apply_rcf_t2(apply_rcf_t1(_rcf(ffo=700e6, prior=500e6), {**t1, "unresolved": True, "unresolved_why": "x"}),
                     unresolved)
    [e] = escalations_for(PASS, COMPLIANT, m[1:], None, None, {"rcf_t1": {**t1, "unresolved": True},
                                                               "rcf_t2": unresolved})
    assert e["checks"] == ["rcf_t1", "rcf_t2"]
