"""FO prepaid-model pre-check: the passages (Item 1 and the revenue note only), the agent's reading of the business
model and its verified evidence, the outcome (first match) and the DSO weight in the composite.

Synthetic 10-K text and facts only; the agent runs on a scripted offline backend (no network, no model).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.orm import ModelRun
from app.services.fo.precheck import PrecheckAgentError, scripted_backend
from app.services.fo.prepaid import apply_prepaid_adjustments, prepaid_passages, prepaid_precheck
from app.services.fo.runner import escalations_for
from app.services.fo.scoring import SCORING_DEFAULTS, composite_risk
from app.services.llm.client import FakeLLMBackend

FY25, FY24 = "2025-01-01--2025-12-31", "2024-01-01--2024-12-31"
SENIOR = ("We operate 120 senior living communities, where residents pay monthly service fees in advance of the "
          "month in which the services are provided.")
MIXED = ("About half of our revenue comes from private-pay residents who pay in advance; the rest is billed to "
         "Medicare and Medicaid after services are provided.")
HOSPITAL = ("We operate 40 acute care hospitals and bill Medicare, Medicaid and commercial insurers after we provide "
            "services to patients.")
NOTE_TEXT = ("We disaggregate revenue by payer source (dollars in millions). Contract liabilities consist of fees "
             "received in advance from residents.")
TABLE = ["Payer source", "2025", "2024", "Private pay", "2,700", "2,500", "Medicare", "150", "140", "Medicaid", "150",
         "160", "Total", "3,000", "2,800"]


def _fact(concept, period, value):
    return {"concept": concept, "period_key": period, "value": value, "type": "numeric", "dimensions": {},
            "unit": "USD"}


def _data(*, item1=(SENIOR,), table=TABLE, cl=None, ticker="EXCO"):
    facts = [_fact("us-gaap:Revenues", FY25, 3_000e6), _fact("us-gaap:Revenues", FY24, 2_800e6),
             _fact("us-gaap:AccountsReceivableNetCurrent", "2025-12-31", 60e6),
             _fact("us-gaap:AccountsReceivableNetCurrent", "2024-12-31", 55e6)]
    if cl is not None:
        facts.append(_fact("us-gaap:ContractWithCustomerLiability", "2025-12-31", cl))
    notes = ["Revenues", "3,000", "NOTE 1 — ACCOUNTING POLICIES",
             "We prepare our statements in accordance with generally accepted accounting principles.",
             "NOTE 2 — REVENUE", NOTE_TEXT, *table, "NOTE 3 — INCOME TAXES",
             "Our effective tax rate was 21% in 2025, compared with 22% in 2024, on pre-tax income."]
    return {"meta": {"entity_name": "Example Co", "ticker": ticker, "period_end": "2025-12-31", "fiscal_year": "2025"},
            "facts": facts, "labels": {}, "sections": [],
            "document": [{"item": "1", "title": "Business", "text": "\n".join(item1)},
                         {"item": "1A", "title": "Risk Factors", "text": "Residents may pay in advance or not at all."},
                         {"item": "8", "title": "Financial Statements", "text": "\n".join(notes)}]}


def _payer(payer, basis, year, quote, amount, pid="R02"):
    return {"payer": payer, "basis": basis, "year": year, "passage_id": pid, "quote": quote, "amount": amount}


def _says(status, model="prepaid", *, evidence=(("B01", SENIOR),), payers=(), split=False, cl=None):
    return {"prepaid": {"status": status, "model": model,
                        "model_evidence": [{"passage_id": p, "quote": q} for p, q in evidence],
                        "payer_split_disclosed": split, "payer_revenue": list(payers), "contract_liabilities": cl,
                        "rationale": "Scripted.", "missing_information": []}}


BILLED = [_payer("Medicare", "billed", "current", "Medicare | 150", "150"),
          _payer("Medicaid", "billed", "current", "Medicaid | 150", "150"),
          _payer("Medicare", "billed", "prior", "Medicare | 150 | 140", "140"),
          _payer("Medicaid", "billed", "prior", "Medicaid | 150 | 160", "160"),
          _payer("Private pay", "prepaid", "current", "Private pay | 2,700", "2,700")]


def _run(data, response, db_session):
    return prepaid_precheck(data, session=db_session, backend=scripted_backend(response), mode="scripted",
                            case_id="EX_FY2025")


def test_the_agent_reads_only_item_1_and_the_revenue_note():
    pp = prepaid_passages(_data())
    assert [(p["passage_id"], p["kind"]) for p in pp["passages"]] == [("B01", "business_model"),
                                                                      ("R01", "revenue_note"), ("R02", "revenue_note")]
    assert pp["passages"][2]["text"] == " | ".join(TABLE) and pp["passages"][2]["scale"] == 1e6
    # Not read: the risk factors, the income statement's "Revenues" line (before the notes), the tax note.
    assert all("tax rate" not in p["text"] and "or not at all" not in p["text"] for p in pp["passages"])


def test_a_billed_model_with_contract_liabilities_below_receivables_is_not_applicable(db_session):
    result = _run(_data(item1=[HOSPITAL], cl=20e6), _says("not_applicable", "billed",
                                                          evidence=[("B01", HOSPITAL)]), db_session)
    assert (result["status"], result["decided_by"], result["factor"], result["model"]) == (
        "not_applicable", "agent", None, "billed")
    fo = [{"key": "dso", "columns": []}]
    assert apply_prepaid_adjustments(fo, result) is fo


def test_the_agent_reads_every_filing_with_item_1_or_a_revenue_note(db_session):
    with pytest.raises(PrecheckAgentError, match="Pre-check Agent is required"):
        prepaid_precheck(_data(item1=[HOSPITAL], cl=20e6))  # no list decides for it: the agent must read Item 1
    with pytest.raises(PrecheckAgentError, match="no 'prepaid' response"):
        prepaid_precheck(_data(), backend=scripted_backend({"covenants": {"status": "compliant"}}), mode="scripted")
    nothing = _data(item1=[], table=[], cl=90e6)
    nothing["document"] = [d for d in nothing["document"] if d["item"] != "8"]
    result = prepaid_precheck(nothing, session=db_session, backend=FakeLLMBackend(), mode="scripted")
    assert (result["status"], result["decided_by"], result["agent"]) == ("weight_zero", "no_text", None)
    assert db_session.scalars(select(ModelRun)).first() is None  # nothing to read: no model call


def test_the_agents_reading_of_the_model_must_be_in_its_item_1_quotes(db_session):
    no_advance = _run(_data(item1=[HOSPITAL], cl=20e6), _says("unresolved", "prepaid",
                                                              evidence=[("B01", HOSPITAL)]), db_session)
    assert (no_advance["model"], no_advance["status"]) == (None, "not_applicable")  # nothing says "in advance"
    assert any("no verified Item 1 quote says customers pay in advance" in n for n in no_advance["notes"])
    only_advance = _run(_data(cl=20e6), _says("weight_half", "mixed"), db_session)  # SENIOR says nothing is billed
    assert (only_advance["model"], only_advance["status"]) == ("prepaid", "unresolved")
    assert any("read as prepaid" in n for n in only_advance["notes"])


def test_payer_split_disclosed_recomputes_dso_on_billed_revenue_at_normal_weight(db_session):
    result = _run(_data(cl=20e6), _says("billed_revenue", payers=BILLED, split=True), db_session)
    assert (result["status"], result["factor"]) == ("billed_revenue", 1.0)
    assert (result["model"], result["model_basis"]) == ("prepaid", "Item 1: paid in advance (B01)")
    assert result["billed_revenue"] == {"current": 300e6, "prior": 300e6}
    dso = {"key": "dso", "columns": [
        {"heading": "FY25", "period": FY25, "receivables": 60e6, "revenue": 3_000e6, "dso": 7.3, "prior_dso": 7.17,
         "change": 0.13, "reason": None},
        {"heading": "FY24", "period": FY24, "receivables": 55e6, "revenue": 2_800e6, "dso": 7.17, "prior_dso": None,
         "change": None, "reason": "x"}]}
    [m] = apply_prepaid_adjustments([dso], result)
    cur, prior = m["columns"]
    assert (cur["revenue_total"], cur["revenue"]) == (3_000e6, 300e6)
    assert cur["dso"] == pytest.approx(73.0) and prior["dso"] == pytest.approx(55e6 / (300e6 / 365))
    assert cur["change"] == pytest.approx(cur["dso"] - prior["dso"]) and m["prepaid_factor"] == 1.0
    assert dso["columns"][0]["revenue"] == 3_000e6  # the input is untouched


def test_no_split_and_contract_liabilities_at_least_receivables_is_weight_zero(db_session):
    result = _run(_data(cl=90e6), _says("weight_zero"), db_session)
    assert (result["status"], result["factor"], result["cl_ge_receivables"]) == ("weight_zero", 0.0, True)


def test_no_split_and_a_mixed_model_is_weight_half(db_session):
    data = _data(item1=[SENIOR, MIXED], table=["Total", "3,000", "2,800"], cl=20e6)
    result = _run(data, _says("weight_half", "mixed", evidence=[("B01", SENIOR), ("B02", MIXED)]), db_session)
    assert (result["status"], result["factor"]) == ("weight_half", 0.5)
    assert result["model_basis"] == "Item 1: paid in advance (B01, B02) and billed (B02)"


def test_a_prepaid_model_without_split_or_cover_is_unresolved_and_escalates_dso(db_session):
    result = _run(_data(table=["Total", "3,000", "2,800"], cl=20e6), _says("unresolved"), db_session)
    assert (result["status"], result["model"], result["review_required"]) == ("unresolved", "prepaid", True)
    [m] = apply_prepaid_adjustments([{"key": "dso", "columns": [{"heading": "FY25", "score": 5.5}]}], result)
    assert m["prepaid_unresolved"] and m["prepaid_factor"] is None
    [e] = escalations_for({"status": "pass"}, {"status": "compliant"}, [m], None, result)
    assert (e["id"], e["measure"], e["engine_score"]) == ("prepaid:dso", "dso", 5.5)
    deal = {"status": "unresolved", "reason": "A deal", "unresolved_measures": ["dso"],
            "deals": [{"deal_id": "D1", "effects": {"unresolved": ["dso"]}}]}
    [both] = escalations_for({"status": "pass"}, {"status": "compliant"}, [m], deal, result)
    assert both["checks"] == ["acquisitions", "prepaid"] and "and: A prepaid business model" in both["reason"]


def test_evidence_that_does_not_hold_up_is_held_back(db_session):
    payers = [_payer("Medicare", "billed", "current", "Medicare | 150", "15"),  # not in the quote
              _payer("Medicaid", "billed", "current", "Medicare | 150", "150"),  # names another payer
              _payer("Medicare", "billed", "current", "Medicare | 150", "150", pid="B01")]  # not the revenue note
    result = _run(_data(cl=20e6), _says("billed_revenue", evidence=[("R01", NOTE_TEXT[:60])], payers=payers,
                                        split=True), db_session)
    reasons = [h["reason"] for h in result["agent"]["held_back"]]
    assert reasons == ["business-model evidence must come from the Item 1 business description",
                       "the amount 15 is not in the quote", "the payer isn't named in the quote",
                       "revenue by payer must come from the revenue note"]
    # No verified Item 1 quote, so no prepaid model; contract liabilities below receivables.
    assert result["status"] == "not_applicable" and not result["agent"]["status_consistent"]


def test_billed_lines_above_reported_revenue_are_not_used(db_session):
    big = [_payer("Private pay", "billed", "current", "Private pay | 2,700", "2,700"),
           _payer("Medicare", "billed", "current", "Medicare | 150", "150"),
           _payer("Medicaid", "billed", "current", "Medicaid | 150", "150"),
           _payer("Total", "billed", "current", "Total | 3,000", "3,000")]
    result = _run(_data(cl=90e6), _says("billed_revenue", payers=big, split=True), db_session)
    assert result["status"] == "weight_zero" and any("more than the reported revenue" in n for n in result["notes"])


def _composite(factor):
    v = dict(SCORING_DEFAULTS)
    fo = [{"key": "leverage", "columns": [{"heading": "FY", "period": FY25, "composite": {"score": 4.0}}]},
          {"key": "liquidity", "columns": [{"heading": "FY", "score": 3}]},
          {"key": "rcf", "columns": [{"heading": "FY", "rcf_to_debt": {"score": 5}}]},
          {"key": "dscr", "columns": [{"heading": "FY", "score": 2}]},
          {"key": "icr", "columns": [{"heading": "FY", "score": 2}]},
          {"key": "margin", "columns": [{"heading": "FY", "score": 6}]},
          {"key": "dso", "prepaid_factor": factor, "columns": [{"heading": "FY", "score": 9, "dso": 8.0}]}]
    return composite_risk(fo, {"values": v})


@pytest.mark.parametrize("factor, dso_weight", [(0.0, 0.0), (0.5, 3.75), (1.0, 7.5)])
def test_the_dso_weight_is_7_5_percent_times_the_factor_and_the_rest_scale_pro_rata(factor, dso_weight):
    c = _composite(factor)
    rows = {r["key"]: r for r in c["rows"]}
    scale = (100 - dso_weight) / (100 - 7.5)
    assert rows["dso"]["weight"] == pytest.approx(dso_weight)
    assert rows["leverage"]["weight"] == pytest.approx(25 * scale) and rows["icr"]["weight"] == pytest.approx(10 * scale)
    assert sum(r["weight"] for r in c["rows"]) == pytest.approx(100)
    assert rows["dso"]["share"] == pytest.approx(dso_weight / 100)
    assert not any("half weight" in n for n in rows["dso"]["notes"])  # the factor replaces the < 15 days rule
    if factor != 1:
        assert any(a.startswith("Prepaid model: DSO weight 7.5%") for a in c["adjustments"])
    plain = composite_risk([{**r, "prepaid_factor": None} if r["key"] == "dso" else r for r in _fo_rows()],
                           {"values": dict(SCORING_DEFAULTS)})
    assert any("half weight" in n for n in {r["key"]: r for r in plain["rows"]}["dso"]["notes"])


def _fo_rows():
    return [{"key": "leverage", "columns": [{"heading": "FY", "period": FY25, "composite": {"score": 4.0}}]},
            {"key": "liquidity", "columns": [{"heading": "FY", "score": 3}]},
            {"key": "dso", "columns": [{"heading": "FY", "score": 9, "dso": 8.0}]}]
