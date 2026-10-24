"""Financial Overview end to end over the committed NVIDIA filing: runner, routes, CLI and migration.

The filing folder is copied into a temporary ``edgar_filings/`` root, so the project's own
folder is never read or written. The Pre-check Agent is required, so every run gets scripted
agent responses (no network, no model).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from app.api.deps import get_session
from app.cli import main
from app.core.hashing import content_hash
from app.main import create_app
from app.migrate import FINANCIAL_OVERVIEW_TABLES, migrate
from app.models.base import init_db
from app.models.orm import FinancialOverviewOverrideRow, FinancialOverviewReviewRow, FinancialOverviewRunRow
from app.services.fo import covenants, industry, loader, precheck
from app.services.fo import models as fo_models
from app.services.fo.review import FoReviewError, override_measure, review_run
from app.services.fo.runner import FinancialOverviewRunner, FoRunError, composite_for
from app.services.fo.workbench import fo_view

NVDA = Path(__file__).resolve().parents[2] / "examples" / "sec" / "nvda-2026"
FOLDER = "NVDA 10-K FY2026"
DOUBT = ("The Company has suffered recurring losses from operations and has a net capital deficiency that raise "
         "substantial doubt about its ability to continue as a going concern.")
COMPLIED = "we complied with the required covenants, which are non-financial in nature, under the outstanding notes"
BREACH = ("As of January 25, 2026, we were not in compliance with the maximum leverage ratio covenant under our "
          "credit agreement, and the lenders have not waived the breach.")


def _agent_says(status, *findings):
    return {"status": status, "rationale": "Scripted.", "missing_information": [], "findings": list(findings)}


def _covenants_say(status, *findings):
    return {**_agent_says(status, *findings), "headroom": []}


GROQ = ("In December 2025, we entered into a non‑exclusive license agreement with Groq, Inc., or Groq, for its "
        "language processing unit technology and hired certain Groq employees. No customer contracts, existing "
        "products, or equity interests were purchased.")
GROQ_NOT_MATERIAL = "Pro forma results of operations have not been presented because the effect was not material."


def _deals_say(status, *deals):
    return {"status": status, "deals": list(deals), "rationale": "Scripted.", "missing_information": []}


GROQ_DEAL = {"deal_id": "D1", "kind": "acquisition", "description": "Groq technology license and team",
             "timing": "in_year", "presentation": "not_applicable", "materiality": "not_material",
             "outcome": "not_material",
             "evidence": [{"fact": "deal", "passage_id": "P04", "quote": GROQ, "date": "2025-12"},
                          {"fact": "consideration", "passage_id": "P04", "quote": "$13.0 billion paid at closing",
                           "amount": "$13.0 billion"},
                          {"fact": "not_material", "passage_id": "P04", "quote": GROQ_NOT_MATERIAL}]}


def responses(gc=None, cov=None, ad=None, pp=None, t2=None):
    """Scripted Pre-check Agent responses for NVIDIA: a clean auditor's report, the debt note's (C01) statement
    that it complied with its covenants, the Groq deal (P04), not material, and a billed (not prepaid) business
    model. NVIDIA's CapEx is above 1.5× depreciation, so DSCR trigger T2 fires: by default the Trigger Agent finds no
    growth spending described (the score is held). Its FFO is up 25% or more but not over 10 points faster than revenue, so RCF/Debt
    trigger T2 doesn't fire."""
    return {"going_concern": gc or _agent_says("pass"),
            "covenants": cov or _covenants_say("compliant", {"passage_id": "C01", "classification": "in_compliance",
                                                             "quote": COMPLIED}),
            "acquisitions": ad or _deals_say("no_adjustment", GROQ_DEAL),
            "prepaid": pp or _model_says("not_applicable", "billed"),
            "dscr_t2": t2 or T2_NO_GROWTH}


T2_NO_GROWTH = {"status": "no_growth", "growth": [], "committed": [], "maintenance_capex": None,
                "growth_capex": None, "rationale": "Scripted.", "missing_information": []}


def _model_says(status, model, *evidence):
    return {"status": status, "model": model, "model_evidence": list(evidence), "payer_split_disclosed": False,
            "payer_revenue": [], "contract_liabilities": None, "rationale": "Scripted.", "missing_information": []}


def agent(**kw):
    return precheck.scripted_backend(responses(**kw))


def runner(session, backend=None, **kw):
    return FinancialOverviewRunner(session, agent_backend=backend or agent(), agent_mode="scripted", **kw)


@pytest.fixture()
def auditor_doubt(monkeypatch):
    """The filing's auditor's report gains a going-concern paragraph (NVIDIA's has none)."""
    real = precheck.going_concern_passages

    def with_doubt(data):
        gc = real(data)
        auditor = [{"item": "8", "text": DOUBT}, *gc["passages"]["auditor"]]
        for i, p in enumerate(auditor, 1):
            p["passage_id"] = f"A{i:02d}"  # the going-concern paragraph is A01
        gc["passages"]["auditor"] = auditor
        gc["audit_reports"] = sorted({*gc["audit_reports"], "8"})
        return gc

    monkeypatch.setattr(precheck, "going_concern_passages", with_doubt)
    return lambda status="fail": agent(gc=_agent_says(status, {
        "passage_id": "A01", "classification": "auditor_going_concern_paragraph", "quote": DOUBT}))


BREACH_RESPONSE = _covenants_say(
    "breach", {"passage_id": "C01", "classification": "breach_not_waived", "quote": BREACH},
    {"passage_id": "C02", "classification": "in_compliance", "quote": COMPLIED})


@pytest.fixture()
def covenant_breach(monkeypatch):
    """The filing's debt note gains an unwaived covenant breach (NVIDIA's says it complied)."""
    real = covenants.covenant_passages

    def with_breach(data):
        cov = real(data)
        passages = [{"item": "F", "section": "debt_note", "section_label": "Debt note", "text": BREACH,
                     "hits": ["not in compliance", "covenant", "breach"], "also_in": []}, *cov["passages"]]
        for i, p in enumerate(passages, 1):
            p["passage_id"] = f"C{i:02d}"  # the breach is C01
        cov["passages"] = passages
        return cov

    monkeypatch.setattr(covenants, "covenant_passages", with_breach)
    return lambda: agent(cov=BREACH_RESPONSE)


@pytest.fixture(scope="module")
def filings_root(tmp_path_factory):
    root = tmp_path_factory.mktemp("edgar_filings")
    shutil.copytree(NVDA, root / FOLDER)
    return root


@pytest.fixture(autouse=True)
def use_filings_root(filings_root, monkeypatch):
    monkeypatch.setattr(loader, "FILINGS_ROOT", filings_root)


@pytest.fixture()
def nvda_as_hospital(tmp_path, monkeypatch):
    """NVIDIA mapped to the one industry group with benchmarks, so every measure scores."""
    ref = tmp_path / "reference"
    ref.mkdir()
    (ref / "Test_Mapping.json").write_text(json.dumps(
        {"NVDA": {"company_name": "NVIDIA", "industry_group": "Hospitals/Healthcare Facilities"}}), encoding="utf-8")
    (ref / "Benchmarks.json").write_text(
        (industry.MAPPINGS_DIR / "Benchmarks.json").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(industry, "MAPPINGS_DIR", ref)


@pytest.fixture()
def client(db_session, tmp_path, monkeypatch):
    """The app, with the scripted agent standing in for the live provider."""
    from app.core.config import get_settings

    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "web_out"))  # browser runs write here, not to output/
    get_settings.cache_clear()
    monkeypatch.setattr(precheck, "live_backend", lambda: agent())
    monkeypatch.setattr(precheck, "agent_available", lambda: (True, None))
    monkeypatch.setattr(fo_models, "available_models", lambda force=False: (TEST_MODELS, None))  # no network
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_runner_records_a_reproducible_run(db_session):
    fo = runner(db_session)
    first = fo.run_filing(FOLDER)
    second = fo.run_filing(FOLDER, "NVDA_AGAIN")
    assert first.case_id.startswith("NVDA_FY")
    assert first.run_id != second.run_id
    row = db_session.get(FinancialOverviewRunRow, first.run_id)
    assert row.content_hash == content_hash(row.payload)
    assert row.human_review_status == "pending"
    keys = [m["key"] for m in row.payload["metrics"]]
    assert keys == ["overall", "dscr", "icr", "leverage", "rcf", "liquidity", "margin", "dso"]
    assert row.payload["filing"]["fact_count"] > 1000
    assert first.paths == {}  # no output root: nothing written
    assert first.payload["metrics"] == second.payload["metrics"]  # same filing, same answers, same scores
    gc = row.payload["precheck"]["going_concern"]
    assert (gc["status"], gc["mode"], gc["decided_by"]) == ("pass", "scripted", "agent") and row.status == "completed"
    assert row.payload["gate"]["agent_triggers"] == "enabled" and row.payload["gate"]["measures_counted"]


def test_without_the_agent_nothing_is_recorded(db_session):
    with pytest.raises(precheck.PrecheckAgentError, match="Pre-check Agent is required"):
        FinancialOverviewRunner(db_session).run_filing(FOLDER, "NVDA_NO_AGENT")
    only_gc = precheck.scripted_backend({"going_concern": _agent_says("pass")})
    with pytest.raises(precheck.PrecheckAgentError, match="no 'covenants' response"):
        runner(db_session, only_gc).run_filing(FOLDER, "NVDA_NO_COV")  # the covenant search hits
    assert db_session.query(FinancialOverviewRunRow).count() == 0


def test_going_concern_fail_waits_for_the_analyst_then_continues(db_session, auditor_doubt, tmp_path):
    result = runner(db_session, auditor_doubt(), output_root=tmp_path / "out").run_filing(FOLDER, "NVDA_GC")
    row = db_session.get(FinancialOverviewRunRow, result.run_id)
    assert (row.status, row.human_review_status, row.composite_score) == ("awaiting_review", "escalated", None)
    gc = result.payload["precheck"]["going_concern"]
    assert (gc["status"], gc["decided_by"], gc["review_required"]) == ("fail", "agent", True)
    assert result.payload["composite"]["score"] == 10  # computed, withheld until the decision
    assert all(m.get("superseded") for m in result.payload["metrics"][1:])
    assert result.payload["metrics"][0]["used"] == 0  # no measure counted
    assert result.payload["gate"] == {"going_concern": "fail", "liquidity_floor": None, "covenants": "compliant",
                                      "liquidity_adjustment": None, "acquisitions": "no_adjustment",
                                      "deal_adjustments": [], "deal_unresolved": [], "prepaid": "not_applicable",
                                      "dso_weight_factor": None, "disagreements": [], "measures_counted": False,
                                      "agent_triggers": "skipped",
                                      "triggers": {"dscr_t1": "skipped", "dscr_t2": "skipped", "dscr_t3": "skipped",
                                                   "icr_t1": "skipped", "icr_t2": "skipped",
                                                   "icr_t3": "skipped", "leverage_t1": "skipped",
                                                   "leverage_t2": "skipped", "leverage_t3": "skipped",
                                                   "leverage_t4": "skipped", "rcf_t1": "skipped",
                                                   "rcf_t2": "skipped", "rcf_t3": "skipped", "rcf_t4": "skipped",
                                                   "margin_t1": "skipped", "margin_t2": "skipped", "dso_t1": "skipped",
                                                   "liquidity_t1": "skipped", "liquidity_t2": "skipped",
                                                   "liquidity_t3": "skipped"},
                                      "requires_decision": True}
    assert [e["type"] for e in result.payload["escalations"]] == ["run_decision"]
    summary = result.summary()
    assert summary["agent_triggers"] == "skipped"
    assert summary["composite_score"] is None and "fo-review" in summary["next_step"]
    transcript = json.loads(result.paths["precheck_transcript.json"].read_text(encoding="utf-8"))
    assert set(transcript) == {"going_concern", "covenants", "acquisitions", "prepaid"}
    html = result.paths["financial_overview.html"].read_text(encoding="utf-8")
    assert "Withheld: the going-concern fail is awaiting" in html
    assert "Pre-check Agent result" in html and "auditor going concern paragraph" in html  # the agent's answer
    assert "Going-concern pre-check:" in html and "awaiting analyst decision" in html  # header summary

    decided = review_run(db_session, result.run_id, decision="continue", reviewer="A. Analyst",
                         reason="Refinancing agreed after year-end.", output_root=tmp_path / "out")
    assert (decided["run_status"], decided["composite_score"], decided["credit_rating"]) == ("completed", 10, "D+")
    assert "not counted; agent triggers skipped" in decided["result"]
    assert row.human_review_status == "continued" and content_hash(row.payload) == row.content_hash
    folder = result.paths["financial_overview.html"].parent
    assert json.loads((folder / "review.json").read_text(encoding="utf-8"))["decision"] == "continue"
    html = (folder / "financial_overview.html").read_text(encoding="utf-8")
    assert "Analysis continued by A. Analyst" in html and "Superseded by going-concern fail" in html
    with pytest.raises(FoReviewError, match="already has a recorded decision"):
        review_run(db_session, result.run_id, decision="stop", reviewer="B", reason="again")


def test_covenants_on_the_real_filing_are_read_by_the_agent(db_session):
    cov = runner(db_session).build(FOLDER)["precheck"]["covenants"]
    assert (cov["status"], cov["outcome"], cov["decided_by"]) == ("compliant", "d", "agent")
    assert cov["basis"][0]["classification"] == "in_compliance" and "complied" in cov["basis"][0]["quote"]
    assert cov["rules"]["status"] == "compliant"  # the cross-check agrees


def test_acquisitions_on_the_real_filing_are_read_by_the_agent(db_session, tmp_path):
    result = runner(db_session, output_root=tmp_path / "out").run_filing(FOLDER, "NVDA_AD")
    ad = result.payload["precheck"]["acquisitions"]
    assert (ad["status"], ad["decided_by"], ad["review_required"]) == ("no_adjustment", "agent", False)
    [deal] = ad["deals"]
    assert (deal["timing"], deal["materiality"], deal["outcome"]) == ("in_year", "not_material", "not_material")
    assert "from the date 2025-12" in deal["timing_basis"] and ad["agent"]["held_back_evidence"] == []
    assert result.payload["escalations"] == [] and result.summary()["acquisitions"]["deals"] == [
        {"deal_id": "D1", "kind": "acquisition", "outcome": "not_material"}]
    dso = next(m for m in result.payload["metrics"] if m["key"] == "dso")
    assert "revenue_reported" not in dso["columns"][0] and not dso.get("deal_note")  # nothing to adjust
    html = result.paths["financial_overview.html"].read_text(encoding="utf-8")
    assert "Pre-check: acquisitions and divestitures" in html and "NO ADJUSTMENT" in html
    assert "Pre-check Agent result: acquisitions and divestitures" in html and "Groq technology license" in html


NVDA_SALE = ("On October 26, 2025, we sold our Example networking business for proceeds of $15 billion. The Example "
             "networking business contributed revenue of $2.1 billion in fiscal year 2026 prior to the sale.")


@pytest.fixture()
def nvda_sale(monkeypatch):
    """The filing's acquisitions note gains a material sale of a business (NVIDIA's has none)."""
    from app.services.fo import acquisitions

    real = acquisitions.deal_passages

    def with_sale(data):
        ad = real(data)
        ad["passages"].append({"item": "F", "section": "acquisitions_note",
                               "section_label": "Acquisitions or dispositions note", "kind": "text",
                               "text": NVDA_SALE, "scale": None, "passage_id": "P05"})
        return ad

    monkeypatch.setattr(acquisitions, "deal_passages", with_sale)

    def sale(*revenue):
        evidence = [{"fact": "deal", "passage_id": "P05", "quote": NVDA_SALE.split(". ")[0], "date": "2025-10-26"},
                    {"fact": "consideration", "passage_id": "P05", "quote": "for proceeds of $15 billion",
                     "amount": "$15 billion"}, *revenue]
        deal = {"deal_id": "D2", "kind": "divestiture", "description": "Example networking business",
                "timing": "in_year", "presentation": "continuing", "materiality": "not_stated",
                "outcome": "remove_divested_revenue" if revenue else "unresolved", "evidence": evidence}
        return agent(ad=_deals_say("adjusted" if revenue else "unresolved", GROQ_DEAL, deal))

    return sale


def test_a_divestiture_with_its_revenue_disclosed_recomputes_dso(db_session, nvda_sale):
    backend = nvda_sale({"fact": "divested_revenue", "passage_id": "P05", "quote": "contributed revenue of $2.1 billion",
                         "amount": "$2.1 billion"})
    payload = runner(db_session, backend).run_filing(FOLDER, "NVDA_SALE").payload
    ad = payload["precheck"]["acquisitions"]
    assert (ad["status"], [d["outcome"] for d in ad["deals"]]) == ("adjusted", ["not_material",
                                                                                "remove_divested_revenue"])
    col = next(m for m in payload["metrics"] if m["key"] == "dso")["columns"][0]
    assert col["revenue"] == col["revenue_reported"] - 2.1e9
    assert col["dso"] == pytest.approx(col["receivables"] / (col["revenue"] / 365))
    assert payload["gate"]["deal_adjustments"] == ["dso"] and payload["escalations"] == []
    [rec] = payload["recomputations"]  # the pre-check's recomputation, step by step
    assert (rec["step"], rec["measure"], rec["inputs"]) == (
        "acquisitions", "dso", [{"label": "Net revenue", "before": "215,938.0", "after": "213,838.0"}])
    assert {"label": "DSO", "before": "65.0 days", "after": "65.7 days", "score": False} in rec["results"]


def test_an_unresolved_deal_escalates_dso_then_the_analyst_overrides(db_session, nvda_sale, nvda_as_hospital,
                                                                     tmp_path):
    result = runner(db_session, nvda_sale(), output_root=tmp_path / "out").run_filing(FOLDER, "NVDA_DEAL")
    ad = result.payload["precheck"]["acquisitions"]
    assert (ad["status"], ad["unresolved_measures"]) == ("unresolved", ["dso"])
    assert ad["deals"][1]["materiality"] == "material"  # $15 billion is 7.3% of total assets
    [e] = result.payload["escalations"]
    assert (e["id"], e["measure"], e["check"]) == ("acquisitions:dso", "dso", "acquisitions")
    dso = next(m for m in result.payload["metrics"] if m["key"] == "dso")
    assert e["engine_score"] == dso["columns"][0]["score"] is not None and dso["deal_unresolved"]
    assert result.summary()["run_status"] == "awaiting_review" and "--measure dso" in result.summary()["next_step"]
    html = result.paths["financial_overview.html"].read_text(encoding="utf-8")
    assert "(provisional)" in html and "awaiting analyst decision on DSO" in html
    assert "acquisition or divestiture unresolved" in html
    decided = override_measure(db_session, result.run_id, measure="dso", decision="override", score=7, reviewer="A",
                               reason="The sold business ran low DSO.", output_root=tmp_path / "out")
    assert (decided["run_status"], decided["human_review_status"]) == ("completed", "overridden")
    assert decided["result"].startswith("Days sales outstanding (DSO): overridden by analyst to 7")
    html = (tmp_path / "out" / "fo" / "NVDA_DEAL" / result.run_id / "financial_overview.html").read_text(
        encoding="utf-8")
    assert "OVERRIDDEN BY ANALYST" in html and "Unresolved acquisition or divestiture" not in html
    assert "the analyst has decided this measure" in html
    view = fo_view(db_session, result.run_id)
    assert view["acquisitions"] == "Overridden by analyst"  # the runs list's Acquisitions column
    assert [d["outcome_label"] for d in view["overview"]["acquisitions"]["deals"]] == ["not material",
                                                                                      "Overridden by analyst"]
    expected = composite_for(result.payload, {"dso": 7})["columns"][0]["score"]
    assert decided["composite_score"] == expected


def test_the_agent_reads_nvidias_business_model_as_billed(db_session):
    pp = runner(db_session).build(FOLDER)["precheck"]["prepaid"]
    assert (pp["status"], pp["decided_by"], pp["model"]) == ("not_applicable", "agent", "billed")
    assert pp["agent"]["inputs"]["passages"][0]["passage_id"] == "B01"  # Item 1, then the revenue note
    assert pp["cl_ge_receivables"] is False  # $2.6 billion of contract liabilities, $38.5 billion receivables


MIXED_MODEL = ("Customers pay for our software subscriptions in advance, while hardware is billed to customers "
               "after shipment.")


def test_a_mixed_model_halves_the_dso_weight(db_session, monkeypatch, nvda_as_hospital, tmp_path):
    from app.services.fo import prepaid

    real = prepaid.prepaid_passages

    def with_model(data):
        pp = real(data)
        pp["business"].append({"item": "1", "kind": "business_model", "text": MIXED_MODEL, "scale": None,
                               "passage_id": "B99"})
        pp["passages"] = pp["business"] + pp["note"]
        return pp

    monkeypatch.setattr(prepaid, "prepaid_passages", with_model)
    response = _model_says("weight_half", "mixed", {"passage_id": "B99", "quote": MIXED_MODEL})
    result = runner(db_session, agent(pp=response), output_root=tmp_path / "out").run_filing(FOLDER, "NVDA_PP")
    pp = result.payload["precheck"]["prepaid"]
    assert (pp["status"], pp["factor"], pp["model"]) == ("weight_half", 0.5, "mixed")
    assert result.payload["gate"]["dso_weight_factor"] == 0.5 and result.payload["escalations"] == []
    rows = {r["key"]: r for r in result.payload["metrics"][0]["rows"]}
    assert rows["dso"]["weight"] == 3.75 and rows["leverage"]["weight"] == pytest.approx(25 * 96.25 / 92.5)
    html = result.paths["financial_overview.html"].read_text(encoding="utf-8")
    assert "Pre-check: prepaid model" in html and "DSO WEIGHT 50%" in html and "Prepaid model: DSO weight 7.5%" in html
    assert set(json.loads(result.paths["precheck_transcript.json"].read_text(encoding="utf-8"))) == {
        "going_concern", "covenants", "acquisitions", "prepaid"}


UNVERIFIED_FAIL = _agent_says("fail", {"passage_id": "A01", "classification": "auditor_going_concern_paragraph",
                                       "quote": "conditions raise substantial doubt about the Company's solvency"})


def test_an_agent_fail_the_evidence_does_not_support_goes_to_the_analyst(db_session, tmp_path):
    result = runner(db_session, agent(gc=UNVERIFIED_FAIL), output_root=tmp_path / "out").run_filing(FOLDER, "NVDA_GCD")
    payload = result.payload
    assert payload["precheck"]["going_concern"]["status"] == "pass"  # the quote isn't in the filing: held back
    [e] = payload["escalations"]
    assert (e["type"], e["disagreement"], e["verified"]) == ("run_decision", True, "pass")
    assert payload["gate"]["disagreements"] == ["going_concern:disagreement"]
    summary = result.summary()
    assert summary["run_status"] == "awaiting_review" and "use_verified|continue|stop" in summary["next_step"]
    assert summary["provisional_composite_score"] == payload["composite"]["score"]
    html = result.paths["financial_overview.html"].read_text(encoding="utf-8")
    assert "the agent read FAIL against the verified evidence" in html and "(provisional)" in html

    decided = review_run(db_session, result.run_id, decision="use_verified", reviewer="A",
                         reason="The quoted paragraph is not in the auditor's report.", output_root=tmp_path / "out")
    assert (decided["run_status"], decided["human_review_status"]) == ("completed", "verified_accepted")
    assert decided["composite_score"] == payload["composite"]["score"]


def test_siding_with_the_agents_fail_scores_the_run_as_a_going_concern_fail(db_session, tmp_path):
    result = runner(db_session, agent(gc=UNVERIFIED_FAIL), output_root=tmp_path / "out").run_filing(FOLDER, "NVDA_GCA")
    with pytest.raises(FoReviewError, match="'continue' or 'stop' or 'use_verified'"):
        review_run(db_session, result.run_id, decision="accept", reviewer="A", reason="r")
    decided = review_run(db_session, result.run_id, decision="continue", reviewer="A", reason="Agree with the agent.",
                         output_root=tmp_path / "out")
    assert (decided["run_status"], decided["composite_score"], decided["credit_rating"]) == ("completed", 10, "D+")
    html = (result.paths["financial_overview.html"]).read_text(encoding="utf-8")
    assert "Analysis continued by A" in html and "Superseded by going-concern fail" in html


def test_a_measure_disagreement_waits_for_the_override(db_session, nvda_as_hospital):
    result = runner(db_session, agent(pp=_model_says("unresolved", "prepaid"))).run_filing(FOLDER, "NVDA_PPD")
    [e] = result.payload["escalations"]
    assert (e["id"], e["measure"], e["disagreement"]) == ("prepaid:dso", "dso", True)
    assert "the agent read unresolved; the verified evidence gives not applicable" in e["reason"]
    decided = override_measure(db_session, result.run_id, measure="dso", decision="accept", reviewer="A",
                               reason="The model is billed.")
    assert (decided["run_status"], decided["human_review_status"]) == ("completed", "accepted")


def test_covenant_breach_escalates_liquidity_then_the_analyst_overrides(db_session, covenant_breach,
                                                                        nvda_as_hospital, tmp_path):
    result = runner(db_session, covenant_breach(), output_root=tmp_path / "out").run_filing(FOLDER, "NVDA_COV")
    row = db_session.get(FinancialOverviewRunRow, result.run_id)
    assert (row.status, row.human_review_status, row.composite_score) == ("awaiting_review", "escalated", None)
    payload = result.payload
    assert (payload["precheck"]["covenants"]["status"], payload["precheck"]["covenants"]["decided_by"]) == (
        "breach", "agent")
    assert payload["gate"]["covenants"] == "breach" and payload["gate"]["agent_triggers"] == "enabled"
    liquidity = next(m for m in payload["metrics"] if m["key"] == "liquidity")["columns"][0]
    assert liquidity["revolver"] == 0 and liquidity["score"] == 10
    recs = {r["measure"]: r for r in payload["recomputations"] if r["step"] == "covenants"}
    assert {"label": "Section score (worse of the two)", "before": "1", "after": "10", "score": True,
            "unchanged": False} in recs["liquidity"]["results"]
    assert recs["overall"]["results"][0]["after"] == "7"  # the composite floored at 7
    [escalation] = payload["escalations"]
    assert (escalation["type"], escalation["measure"], escalation["engine_score"]) == ("measure_override",
                                                                                         "liquidity", 10)
    summary = result.summary()
    assert summary["provisional_composite_score"] == payload["composite"]["score"]
    assert "fo-override" in summary["next_step"]
    html = result.paths["financial_overview.html"].read_text(encoding="utf-8")
    assert "Pre-check: covenants" in html and "(provisional)" in html and "Set to 0: covenant breach" in html
    assert "Covenant pre-check:" in html and "awaiting analyst decision on the liquidity-and-maturity score" in html
    assert "Pre-check Agent result: covenants" in html and "breach not waived" in html

    with pytest.raises(FoReviewError, match="waiting for a measure override"):
        review_run(db_session, result.run_id, decision="continue", reviewer="A", reason="r")
    with pytest.raises(FoReviewError, match="score from 1 to 10"):
        override_measure(db_session, result.run_id, measure="liquidity", decision="override", reviewer="A",
                         reason="r", score=11)
    decided = override_measure(db_session, result.run_id, measure="liquidity", decision="override", score=6,
                               reviewer="A. Analyst", reason="Amendment signed after year-end.",
                               output_root=tmp_path / "out")
    expected = composite_for(payload, {"liquidity": 6})["columns"][0]["score"]
    assert (decided["run_status"], decided["composite_score"]) == ("completed", expected)
    assert expected != payload["composite"]["score"] and row.human_review_status == "overridden"
    assert content_hash(row.payload) == row.content_hash  # the recorded payload is unchanged
    folder = result.paths["financial_overview.html"].parent
    assert json.loads((folder / "overrides.json").read_text(encoding="utf-8"))[0]["analyst_score"] == 6
    html = (folder / "financial_overview.html").read_text(encoding="utf-8")
    assert "Overridden by analyst: 6 (engine 10), by A. Analyst" in html and "(after the analyst's decision)" in html
    with pytest.raises(FoReviewError, match="not awaiting review"):
        override_measure(db_session, result.run_id, measure="liquidity", decision="accept", reviewer="B",
                         reason="again")


T1_HELD = {"status": "no_adjustment", "rollover": [], "refinancings": [], "rationale": "Scripted.",
           "missing_information": []}


def test_dscr_trigger_t1_resolved_completes_unresolved_escalates_dscr(db_session, nvda_as_hospital, tmp_path,
                                                                       monkeypatch):
    # NVIDIA's DSCR is far above the trigger: lower it so T1 fires on this filing.
    from app.services.fo import dscr_triggers

    assert runner(db_session).build(FOLDER)["triggers"]["dscr_t1"]["status"] == "not_triggered"
    monkeypatch.setattr(dscr_triggers, "T1_THRESHOLD", 1)
    with pytest.raises(precheck.PrecheckAgentError, match="no 'dscr_t1' response"):
        runner(db_session).run_filing(FOLDER, "NVDA_T1_NO_AGENT")

    # Resolved: the agent and the verified evidence agree on no adjustment. The score stays, nothing waits.
    backend = precheck.scripted_backend({**responses(), "dscr_t1": T1_HELD})
    result = runner(db_session, backend, output_root=tmp_path / "out").run_filing(FOLDER, "NVDA_T1")
    payload = result.payload
    t1 = payload["triggers"]["dscr_t1"]
    assert (t1["fired"], t1["status"], t1["decided_by"], t1["unresolved"]) == (True, "no_adjustment", "agent", False)
    assert t1["principal"]["principal_basis"] in ("maturity_table", "current_portion")
    assert payload["gate"]["triggers"] == {"dscr_t1": "no_adjustment", "dscr_t2": "no_growth",
                                             "dscr_t3": "not_triggered", "icr_t1": "not_triggered",
                                             "icr_t2": "not_triggered", "icr_t3": "not_triggered",
                                             "leverage_t1": "not_triggered", "leverage_t2": "not_triggered",
                                             "leverage_t3": "not_triggered", "leverage_t4": "not_triggered",
                                             "rcf_t1": "not_triggered", "rcf_t2": "not_triggered",
                                             "rcf_t3": "not_triggered", "rcf_t4": "not_triggered",
                                             "margin_t1": "not_triggered", "margin_t2": "not_triggered", "dso_t1": "not_triggered",
                                             "liquidity_t1": "no_revolver", "liquidity_t2": "not_triggered",
                                             "liquidity_t3": "not_triggered"} and t1["passages"]
    assert payload["escalations"] == [] and result.summary()["run_status"] == "completed"
    dscr = next(m for m in payload["metrics"] if m["key"] == "dscr")
    assert not dscr["trigger_escalated"] and dscr["columns"][0]["score"] == t1["score"]
    assert set(json.loads(result.paths["trigger_transcript.json"].read_text(encoding="utf-8"))) == {"dscr_t1", "dscr_t2"}
    html = result.paths["financial_overview.html"].read_text(encoding="utf-8")
    assert "Trigger Agent: DSCR T1" in html and "Trigger Agent result: DSCR T1" in html
    assert "Resolved: the agent and the verified evidence agree" in html and "trigger T1: no adjustment" in html
    assert "T1a: rollover debt removed" in html and "shared with Liquidity T2" in html

    # Unresolved: the agent says adjusted, but gives no evidence. DSCR waits for the analyst.
    backend = precheck.scripted_backend({**responses(), "dscr_t1": {**T1_HELD, "status": "adjusted"}})
    result = runner(db_session, backend, output_root=tmp_path / "out").run_filing(FOLDER, "NVDA_T1_UNRESOLVED")
    t1 = result.payload["triggers"]["dscr_t1"]
    assert (t1["status"], t1["unresolved"]) == ("no_adjustment", True)
    [e] = result.payload["escalations"]
    assert (e["id"], e["measure"], e["engine_score"], e["calculated_score"], e["disagreement"]) == (
        "dscr_t1:dscr", "dscr", t1["score"], t1["score"], True)
    assert result.summary()["run_status"] == "awaiting_review" and "--measure dscr" in result.summary()["next_step"]
    html = result.paths["financial_overview.html"].read_text(encoding="utf-8")
    assert "awaiting analyst decision on DSCR" in html and "trigger T1: no adjustment, unresolved, escalated" in html
    decided = override_measure(db_session, result.run_id, measure="dscr", decision="accept", reviewer="A",
                               reason="No rollover debt; nothing refinanced.", output_root=tmp_path / "out")
    assert (decided["run_status"], decided["human_review_status"]) == ("completed", "accepted")
    html = result.paths["financial_overview.html"].read_text(encoding="utf-8")
    assert "ENGINE SCORE" in html and "The analyst has decided this measure&#39;s score." in html


NVDA_GROWTH = "to support the future growth of our business"


def test_dscr_trigger_t2_recomputes_with_depreciation_or_escalates_when_unresolved(db_session, nvda_as_hospital,
                                                                                    tmp_path):
    # NVIDIA's CapEx ($6,042 million) is 2.5× its depreciation ($2,400 million; D&A of $2,843 million less
    # amortisation): T2 fires whatever DSCR scored. The agent reads the MD&A's growth sentence (K02), no amount.
    t2_says = {**T2_NO_GROWTH, "status": "growth_not_quantified",
               "growth": [{"passage_id": "K02", "quote": NVDA_GROWTH}]}
    result = runner(db_session, agent(t2=t2_says), output_root=tmp_path / "out").run_filing(FOLDER, "NVDA_T2")
    t2 = result.payload["triggers"]["dscr_t2"]
    assert (t2["fired"], t2["status"], t2["unresolved"], t2["maintenance_capex"]) == (
        True, "growth_not_quantified", False, 2_400e6)
    assert (t2["capex"]["capex"], t2["capex"]["depreciation"]) == (6_042e6, 2_400e6)
    assert {s["key"]: s["located"] for s in t2["sections"]} == {"mdna_capex": True, "segment_note": True}
    col = next(m for m in result.payload["metrics"] if m["key"] == "dscr")["columns"][0]
    assert (col["capex_reported"], col["capex"]) == (6_042e6, 2_400e6)
    assert col["cash_flow"] == pytest.approx(col["cash_flow_reported"] + 3_642e6)
    assert result.payload["escalations"] == [] and result.summary()["run_status"] == "completed"
    [rec] = [r for r in result.payload["recomputations"] if r["step"] == "dscr_t2"]
    assert {"label": "Maintenance CapEx", "before": "6,042.0", "after": "2,400.0"} in rec["inputs"]
    assert {"label": "DSCR", "before": "67.80x", "after": "69.88x", "score": False} in rec["results"]
    assert any(x["score"] for x in rec["results"])  # the revised score is shown, even when it doesn't move
    html = result.paths["financial_overview.html"].read_text(encoding="utf-8")
    assert "<h4>Recomputed</h4>" in html and "Maintenance CapEx: 6,042.0 → 2,400.0" in html
    assert "Recomputations (1 step)" in html
    assert "Trigger Agent: DSCR T2" in html and "INPUT CORRECTION: RECOMPUTED" in html
    assert "Total CapEx as tagged (before trigger T2)" in html and "trigger T2: recomputed" in html

    # Unresolved: the agent says committed growth, with no evidence. DSCR waits for the analyst, score held.
    t2_says = {**T2_NO_GROWTH, "status": "committed_growth"}
    result = runner(db_session, agent(t2=t2_says), output_root=tmp_path / "out").run_filing(FOLDER, "NVDA_T2_OPEN")
    t2 = result.payload["triggers"]["dscr_t2"]
    assert (t2["status"], t2["unresolved"]) == ("no_growth", True)
    [e] = result.payload["escalations"]
    assert (e["id"], e["measure"], e["disagreement"]) == ("dscr_t2:dscr", "dscr", True)
    assert result.summary()["run_status"] == "awaiting_review"
    html = result.paths["financial_overview.html"].read_text(encoding="utf-8")
    assert "trigger T2: no adjustment, unresolved, escalated" in html


def test_browser_and_cli_record_the_analysts_score(client, covenant_breach, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(precheck, "live_backend", covenant_breach)
    posted = client.post("/fo/run", data={"folder": FOLDER, "case_id": "NVDA_COV_WEB"}, follow_redirects=False)
    location = posted.headers["location"]
    run_id = location.split("run_id=")[1]
    workbench = client.get(location).text
    assert "Analyst decision required: Liquidity and debt maturity" in workbench
    base = f"/cases/NVDA_COV_WEB/fo/runs/{run_id}"
    missing = client.post(f"{base}/override", data={"measure": "liquidity", "decision": "override",
                                                    "reviewer": "A", "reason": "r"})
    assert missing.status_code == 400 and "score from 1 to 10" in missing.text
    done = client.post(f"{base}/override", data={"measure": "liquidity", "decision": "accept", "reviewer": "A",
                                                 "reason": "Breach confirmed."}, follow_redirects=False)
    assert done.status_code == 303
    after = client.get(location).text
    assert "Engine score: 10, accepted by the analyst" in after and "Analyst decision required" not in after
    assert client.get(base).json()["human_review_status"] == "accepted"

    script = tmp_path / "agent.json"
    script.write_text(json.dumps(responses(cov=BREACH_RESPONSE)), encoding="utf-8")
    common = ["--database", str(tmp_path / "fo.db"), "--output", str(tmp_path / "out")]
    main([*common, "run-fo-case", "NVDA_COV_CLI", "--filing", FOLDER, "--responses", str(script)])
    summary = json.loads(capsys.readouterr().out)
    assert summary["covenants"]["status"] == "breach" and summary["run_status"] == "awaiting_review"
    main([*common, "fo-override", summary["run_id"], "--measure", "liquidity", "--decision", "override",
          "--score", "7.5", "--reviewer", "A", "--reason", "ok"])
    decided = json.loads(capsys.readouterr().out)
    assert (decided["run_status"], decided["analyst_score"]) == ("completed", 7.5)
    assert Path(decided["outputs"]["overrides.json"]).is_file()


def test_only_an_escalated_run_can_be_reviewed(db_session):
    result = runner(db_session).run_filing(FOLDER)
    with pytest.raises(FoReviewError, match="not awaiting review"):
        review_run(db_session, result.run_id, decision="continue", reviewer="A", reason="r")
    with pytest.raises(FoReviewError, match="'continue' or 'stop'"):
        review_run(db_session, result.run_id, decision="maybe", reviewer="A", reason="r")


def test_runner_writes_outputs_like_bo(db_session, tmp_path):
    result = runner(db_session, output_root=tmp_path / "out").run_filing(FOLDER, "NVDA_FILES")
    folder = tmp_path / "out" / "fo" / "NVDA_FILES" / result.run_id
    assert set(result.paths) == {"financial_overview.json", "financial_overview.html", "precheck_transcript.json",
                                 "trigger_transcript.json"}  # DSCR trigger T2 fires on NVIDIA's CapEx
    assert all(p.parent == folder and p.is_file() for p in result.paths.values())
    saved = json.loads((folder / "financial_overview.json").read_text(encoding="utf-8"))
    assert content_hash(saved) == result.content_hash  # the file is exactly the recorded payload
    html = (folder / "financial_overview.html").read_text(encoding="utf-8")
    assert "Composite risk score" in html and result.run_id in html
    assert "Pre-check Agent result" in html and "Keyword rules (cross-check only)" in html
    assert "keyword rules only" not in html
    assert result.summary()["outputs"]["financial_overview.json"] == str(folder / "financial_overview.json")


def test_unmapped_company_has_no_benchmarks_and_no_composite(db_session):
    payload = runner(db_session).build(FOLDER)
    assert payload["industry"]["industry_group"] is None
    assert payload["composite"]["score"] is None
    assert payload["composite"]["reason"].startswith("Incomplete")
    dscr = next(m for m in payload["metrics"] if m["key"] == "dscr")
    assert dscr["columns"][0]["basis"] == "forward"  # fixed-band measures still score


def test_mapped_company_gets_a_composite_and_rating(db_session, nvda_as_hospital):
    payload = runner(db_session).build(FOLDER)
    assert payload["industry"]["industry_group"] == "Hospitals/Healthcare Facilities"
    composite = payload["composite"]
    assert 1 <= composite["score"] <= 10
    assert composite["rating"][0] in "ABCD"


def test_invalid_case_id_and_folder_are_refused(db_session):
    with pytest.raises(FoRunError):
        runner(db_session).run_filing(FOLDER, "../bad")
    for name in ("..", "../etc", "nope", "a/b"):
        with pytest.raises(loader.FilingFolderError):
            runner(db_session).run_filing(name)


def test_browser_run_list_run_form_workbench_and_artifacts(client, tmp_path):
    page = client.get("/fo")
    assert page.status_code == 200
    assert FOLDER in page.text and "No Financial Overview run has been recorded" in page.text
    assert "Keyword rules only" not in page.text and "Pre-check Agent model" in page.text

    posted = client.post("/fo/run", data={"folder": FOLDER, "case_id": "NVDA_WEB"}, follow_redirects=False)
    assert posted.status_code == 303
    location = posted.headers["location"]
    assert location.startswith("/cases/NVDA_WEB/fo.html?run_id=fo-")
    run_id = location.split("run_id=")[1]
    saved = tmp_path / "web_out" / "fo" / "NVDA_WEB" / run_id
    assert (saved / "financial_overview.json").is_file() and (saved / "financial_overview.html").is_file()

    workbench = client.get(location)
    assert workbench.status_code == 200
    assert "Composite risk score" in workbench.text and "Debt service coverage ratio" in workbench.text
    assert "verified" in workbench.text
    assert str(saved.resolve()) in workbench.text

    base = f"/cases/NVDA_WEB/fo/runs/{run_id}"
    overview = client.get(f"{base}/overview.json").json()
    assert overview["metrics"][0]["key"] == "overall"
    assert client.get(f"{base}/overview.html").status_code == 200
    assert client.get(base).json()["integrity_ok"] is True
    assert [r["run_id"] for r in client.get("/cases/NVDA_WEB/fo/runs").json()["runs"]] == [run_id]
    assert client.get(f"/cases/OTHER/fo/runs/{run_id}/overview.json").status_code == 404
    assert client.get(f"{base}/missing.json").status_code == 404
    assert run_id in client.get("/fo").text


def test_browser_without_a_provider_refuses_to_run(client, monkeypatch):
    def not_configured():
        raise precheck.AgentNotConfiguredError("No live LLM provider is configured.")

    monkeypatch.setattr(precheck, "agent_available", lambda: (False, "No live LLM provider is configured."))
    monkeypatch.setattr(precheck, "live_backend", not_configured)
    page = client.get("/fo").text
    assert "Pre-check Agent not configured" in page and "disabled>Run" in page
    refused = client.post("/fo/run", data={"folder": FOLDER})
    assert refused.status_code == 400 and "Pre-check Agent is required and not available" in refused.text
    assert "No Financial Overview run has been recorded" in client.get("/fo").text


TEST_MODELS = ["gemini-3.1-flash-lite", "gemini-3.5-flash", "gemini-3.1-pro-preview"]


def test_the_analyst_picks_the_agent_model_before_running(client, monkeypatch, db_session):
    page = client.get("/fo").text
    assert '<select id="model" name="model">' in page
    assert 'id="fo-progress" role="status" aria-live="polite" hidden' in page  # shown when Run is clicked
    assert all(f'<option value="{m}"' in page for m in TEST_MODELS) and "tts" not in page
    picked = []

    def on_model(model=None):
        picked.append(model)
        return agent()

    monkeypatch.setattr(precheck, "live_backend", on_model)
    posted = client.post("/fo/run", data={"folder": FOLDER, "case_id": "NVDA_MODEL", "model": "gemini-3.5-flash"},
                         follow_redirects=False)
    assert posted.status_code == 303 and picked == ["gemini-3.5-flash"]
    run = db_session.query(FinancialOverviewRunRow).filter_by(case_id="NVDA_MODEL").one()
    assert run.payload["agent_model"] == "fo-precheck-scripted"  # the scripted stand-in's id in tests
    assert "Pre-check Agent model" in client.get(posted.headers["location"]).text


def test_a_run_stopped_between_steps_records_nothing(db_session):
    from app.services.fo.cancel import FoRunCancelled

    steps = []

    def stop_at_covenants(step):
        steps.append(step)
        if "covenant" in step:
            raise FoRunCancelled(f"Run stopped by the analyst before {step}; nothing was recorded.")

    with pytest.raises(FoRunCancelled, match="before the covenants pre-check"):
        FinancialOverviewRunner(db_session, agent_backend=agent(), agent_mode="scripted",
                                check_stop=stop_at_covenants).run_filing(FOLDER, "NVDA_STOP")
    assert steps == ["reading the filing", "the going-concern pre-check", "the covenants pre-check"]
    assert db_session.query(FinancialOverviewRunRow).count() == 0


def test_the_stop_button_stops_a_browser_run(client, db_session):
    page = client.get("/fo").text
    assert 'name="run_token"' in page and 'id="fo-stop-button"' in page
    token = page.split('id="fo-run-token" value="')[1].split('"')[0]
    assert client.post("/fo/run/cancel", data={"run_token": token}).json() == {"stopping": True}
    stopped = client.post("/fo/run", data={"folder": FOLDER, "case_id": "NVDA_STOP_WEB", "run_token": token})
    assert stopped.status_code == 200 and "Run stopped." in stopped.text
    assert "before reading the filing" in stopped.text
    assert db_session.query(FinancialOverviewRunRow).count() == 0
    assert client.post("/fo/run/cancel", data={"run_token": "bad"}).status_code == 400
    # The token is cleared after its run: a new run with a new token goes through.
    fresh = client.get("/fo").text.split('id="fo-run-token" value="')[1].split('"')[0]
    assert fresh != token
    done = client.post("/fo/run", data={"folder": FOLDER, "case_id": "NVDA_GO", "run_token": fresh},
                       follow_redirects=False)
    assert done.status_code == 303


def test_the_run_reports_its_steps_and_a_failure_names_its_step(client, db_session, monkeypatch):
    from app.services.fo import cancel, runner as fo_runner

    steps = []
    FinancialOverviewRunner(db_session, agent_backend=agent(), agent_mode="scripted",
                            check_stop=steps.append).run_filing(FOLDER, "NVDA_STEPS")
    assert steps[:6] == ["reading the filing", "the going-concern pre-check", "the covenants pre-check",
                         "the acquisitions pre-check", "the prepaid pre-check", "scoring the measures"]
    assert "the ICR trigger T1 check" in steps and "the RCF/Debt trigger T2 check" in steps
    assert steps[-1] == "recording the run and writing its files"
    # The registry the page polls: the step under way, its number, the steps done.
    assert cancel.progress("tok-progress-1") == {"step": None, "number": 0, "done": [], "elapsed": 0}
    cancel.set_step("tok-progress-1", "reading the filing")
    cancel.set_step("tok-progress-1", "the going-concern pre-check")
    got = client.get("/fo/run/progress", params={"run_token": "tok-progress-1"}).json()
    assert (got["step"], got["number"], got["done"]) == ("the going-concern pre-check", 2, ["reading the filing"])
    cancel.clear("tok-progress-1")
    # A fault in a trigger is reported with its step, not as an unreadable filing.
    monkeypatch.setattr(precheck, "live_backend", lambda: agent())

    def broken(*args, **kw):
        raise KeyError("ebit")

    monkeypatch.setattr(fo_runner, "icr_t1_trigger", broken)
    token = client.get("/fo").text.split('id="fo-run-token" value="')[1].split('"')[0]
    failed = client.post("/fo/run", data={"folder": FOLDER, "case_id": "NVDA_FAIL", "run_token": token})
    assert failed.status_code == 500
    assert "Run failed during step" in failed.text and "the ICR trigger T1 check" in failed.text
    assert "Unexpected error (KeyError)" in failed.text and "Could not read this filing" not in failed.text
    assert "Steps completed before it" in failed.text
    assert 'id="fo-progress-step"' in client.get("/fo").text


def test_only_text_analysis_models_can_be_picked(monkeypatch):
    from app.core.config import Settings

    settings = Settings(llm_provider="vertex", vertex_api_key="test-key", vertex_model_narrow="gemini-3.1-flash-lite")
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    assert "gemini-3.5-flash" in fo_models.text_candidates()
    assert not any(t in m for m in fo_models.text_candidates() for t in ("tts", "live", "image", "transcribe"))
    backend = precheck.live_backend("gemini-3.5-flash")
    assert backend.model_for_tier("narrow") == "gemini-3.5-flash"  # this run only
    assert settings.vertex_model_narrow == "gemini-3.1-flash-lite"  # .env's setting is untouched
    with pytest.raises(precheck.AgentNotConfiguredError, match="not one of the text-analysis models"):
        precheck.live_backend("gemini-3.8-flash-tts")


def test_models_are_checked_once_and_cached(monkeypatch):
    from app.core.config import Settings

    settings = Settings(llm_provider="vertex", vertex_api_key="test-key", vertex_model_narrow="gemini-3.1-flash-lite")
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    monkeypatch.setattr(fo_models, "_cache", {"at": 0.0, "models": None, "note": None})
    calls = []

    def probe(client, model):
        calls.append(model)
        return model in ("gemini-3.5-flash", "gemini-3.8-flash")

    monkeypatch.setattr(fo_models, "_probe", probe)
    monkeypatch.setattr("app.services.llm.providers_vertex.VertexProviderBackend._client", lambda self: object())
    models, note = fo_models.available_models()
    assert models == ["gemini-3.1-flash-lite", "gemini-3.8-flash", "gemini-3.5-flash"] and note is None
    assert len(calls) == len(fo_models.text_candidates())  # text-analysis candidates only
    assert fo_models.available_models()[0] == models and len(calls) == len(fo_models.text_candidates())


def test_browser_rejected_agent_output_records_nothing(client, monkeypatch):
    bad = precheck.scripted_backend({**responses(), "going_concern": "not json"})
    monkeypatch.setattr(precheck, "live_backend", lambda: bad)
    failed = client.post("/fo/run", data={"folder": FOLDER, "case_id": "NVDA_BAD"})
    assert failed.status_code == 502 and "output was rejected" in failed.text
    assert "No Financial Overview run has been recorded" in client.get("/fo").text


def test_browser_run_errors_render_a_page_not_a_500(client, tmp_path, monkeypatch):
    bad = client.post("/fo/run", data={"folder": "../x"})
    assert bad.status_code == 400 and "Invalid filing folder name" in bad.text
    empty = tmp_path / "empty"
    (empty / "Not a filing").mkdir(parents=True)
    (empty / "Not a filing" / "notes.htm").write_text("<html><body>no xbrl</body></html>", encoding="utf-8")
    monkeypatch.setattr(loader, "FILINGS_ROOT", empty)
    unreadable = client.post("/fo/run", data={"folder": "Not a filing"})
    assert unreadable.status_code == 400 and "No Inline XBRL" in unreadable.text
    monkeypatch.setattr(loader, "FILINGS_ROOT", tmp_path / "missing")
    assert "No filing folders found" in client.get("/fo").text


def test_browser_agent_run_escalates_and_the_analyst_stops_it(client, auditor_doubt, monkeypatch):
    monkeypatch.setattr(precheck, "live_backend", lambda: auditor_doubt())
    posted = client.post("/fo/run", data={"folder": FOLDER, "case_id": "NVDA_GC_WEB"}, follow_redirects=False)
    location = posted.headers["location"]
    run_id = location.split("run_id=")[1]
    workbench = client.get(location).text
    assert "Analyst decision required" in workbench and "Pre-check: going concern" in workbench
    assert "Withheld: the going-concern fail" in workbench
    base = f"/cases/NVDA_GC_WEB/fo/runs/{run_id}"
    missing = client.post(f"{base}/review", data={"decision": "stop", "reviewer": "", "reason": ""})
    assert missing.status_code == 400 and "reviewer name and a reason are required" in missing.text
    done = client.post(f"{base}/review", data={"decision": "stop", "reviewer": "A. Analyst",
                                               "reason": "Doubt not resolved."}, follow_redirects=False)
    assert done.status_code == 303
    after = client.get(location).text
    assert "Analysis stopped by A. Analyst" in after and "Analyst decision required" not in after
    assert client.get(base).json()["run_status"] == "stopped"
    assert "Not scored: going-concern fail" in client.get(f"{base}/overview.html").text
    assert "Not scored: going-concern fail" in client.get("/fo").text  # the run list too


def test_home_page_links_to_financial_overview(client):
    assert 'href="/fo"' in client.get("/").text


def test_cli_run_fo_case_needs_the_agent(tmp_path, capsys):
    common = ["--database", str(tmp_path / "fo.db"), "--output", str(tmp_path / "out")]
    with pytest.raises(SystemExit):  # neither --live nor --responses
        main([*common, "run-fo-case", "NVDA_CLI", "--filing", FOLDER])
    capsys.readouterr()
    script = tmp_path / "agent.json"
    script.write_text(json.dumps(responses()), encoding="utf-8")
    main([*common, "run-fo-case", "NVDA_CLI", "--filing", FOLDER, "--responses", str(script)])
    summary = json.loads(capsys.readouterr().out)
    assert summary["scope"] == "financial_overview_only"
    assert summary["case_id"] == "NVDA_CLI"
    assert summary["run_id"].startswith("fo-")
    assert set(summary["measures"]) == {"dscr", "icr", "leverage", "rcf", "liquidity", "margin", "dso"}
    out = tmp_path / "out" / "fo" / "NVDA_CLI" / summary["run_id"]
    assert Path(summary["outputs"]["financial_overview.json"]).resolve() == (out / "financial_overview.json").resolve()
    assert (out / "financial_overview.html").is_file()
    assert (summary["going_concern"]["mode"], summary["covenants"]["decided_by"]) == ("scripted", "agent")


def test_cli_scripted_agent_fail_then_fo_review(tmp_path, capsys, auditor_doubt):
    script = tmp_path / "agent.json"
    script.write_text(json.dumps(responses(gc=_agent_says("fail", {
        "passage_id": "A01", "classification": "auditor_going_concern_paragraph", "quote": DOUBT}))),
        encoding="utf-8")
    common = ["--database", str(tmp_path / "fo.db"), "--output", str(tmp_path / "out")]
    main([*common, "run-fo-case", "NVDA_CLI_GC", "--filing", FOLDER, "--responses", str(script)])
    summary = json.loads(capsys.readouterr().out)
    assert summary["run_status"] == "awaiting_review" and summary["going_concern"]["status"] == "fail"
    assert summary["going_concern"]["mode"] == "scripted" and summary["composite_score"] is None
    main([*common, "fo-review", summary["run_id"], "--decision", "continue", "--reviewer", "A", "--reason", "ok"])
    decided = json.loads(capsys.readouterr().out)
    assert (decided["run_status"], decided["composite_score"]) == ("completed", 10)
    assert Path(decided["outputs"]["review.json"]).is_file()


def test_migration_reports_the_financial_overview_table(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    try:
        init_db(engine)
        FinancialOverviewOverrideRow.__table__.drop(engine)
        FinancialOverviewReviewRow.__table__.drop(engine)
        FinancialOverviewRunRow.__table__.drop(engine)
        changes = migrate(engine)
        assert {f"table:{t}" for t in FINANCIAL_OVERVIEW_TABLES} <= set(changes)
        assert set(FINANCIAL_OVERVIEW_TABLES) <= set(inspect(engine).get_table_names())
        assert migrate(engine) == []
    finally:
        engine.dispose()
