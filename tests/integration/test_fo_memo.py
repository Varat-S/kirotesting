"""The Financial Overview memo: built from the recorded run only, over the committed NVIDIA filing.

Reuses the FO runner tests' fixtures (a temporary ``edgar_filings/`` root, scripted agent answers,
NVIDIA mapped to the one benchmarked industry group), so no network and no model are involved.
"""

from __future__ import annotations

import json

from app.services.fo.memo import build_memo, render_memo_html
from app.services.fo.review import override_measure, overrides_for, run_header
from app.models.orm import FinancialOverviewRunRow
from tests.integration.test_fo_runner_api import (  # noqa: F401  (fixtures)
    FOLDER,
    client,
    covenant_breach,
    filings_root,
    nvda_as_hospital,
    runner,
    use_filings_root,
)


def _memo(db_session, result, **kw):
    row = db_session.get(FinancialOverviewRunRow, result.run_id)
    return build_memo(result.payload, run=run_header(row), **kw)


def test_a_scored_run_reads_as_a_memo(db_session, nvda_as_hospital):
    result = runner(db_session).run_filing(FOLDER, "NVDA_MEMO")
    memo = _memo(db_session, result)
    assert memo["document"] == "financial_overview_memo" and memo["case"]["borrower"] == "NVIDIA CORP"
    executive = memo["executive"]
    assert executive["measures_scored"] == 7 and executive["composite"]["rating"] == "AAA"
    assert executive["composite"]["score"] == result.payload["composite"]["score"]  # the scorecard's number
    assert executive["headline"].startswith("NVIDIA CORP's financial risk reads very low: composite risk score")
    assert {s["key"] for s in executive["strengths"]} >= {"dscr", "icr", "leverage", "rcf", "liquidity", "margin"}
    [dso] = executive["weaknesses"]  # DSO is scored on its change, and the memo quotes the change
    assert dso["key"] == "dso" and dso["text"].startswith("DSO change: +0.5 days (score 5.4")
    by_key = {m["key"]: m for m in memo["measures"]}
    assert [t["trigger"] for t in by_key["dscr"]["triggers"]] == ["DSCR T2"]
    assert by_key["icr"]["trend"]["direction"] == "unchanged"
    assert memo["run"]["agent_mode"] == "scripted" and not memo["status"]["blocking"]
    assert [p["check"] for p in memo["prechecks"]] == ["going_concern", "covenants", "acquisitions", "prepaid"]
    html = render_memo_html(memo)
    assert "Executive Financial Credit Assessment" in html and "OFFLINE RUN" in html
    assert "Composite Risk Score" in html and "Debt service coverage ratio (DSCR)" in html


def test_unscored_measures_are_named_with_their_reason(db_session):
    result = runner(db_session).run_filing(FOLDER, "NVDA_UNMAPPED")  # NVIDIA has no industry group: no benchmarks
    memo = _memo(db_session, result)
    assert memo["executive"]["composite"]["score"] is None
    assert memo["executive"]["headline"].startswith("No composite score for NVIDIA CORP")
    unscored = {u["measure"]: u["reason"] for u in memo["outstanding"]["unscored"]}
    assert "EBITDAR margin" in unscored and "benchmark" in unscored["EBITDAR margin"]
    assert any("not in the industry mappings" in d for d in memo["outstanding"]["data_limitations"])


def test_an_escalation_is_flagged_then_the_override_appears(db_session, covenant_breach, nvda_as_hospital):
    result = runner(db_session, covenant_breach()).run_filing(FOLDER, "NVDA_MEMO_COV")
    memo = _memo(db_session, result)
    assert memo["status"]["blocking"] and memo["status"]["run_status"] == "awaiting_review"
    [pending] = memo["outstanding"]["pending_overrides"]
    assert pending["title"] == "covenant breach" and pending["engine_score"] == 10
    assert "Requires an analyst" in render_memo_html(memo)
    override_measure(db_session, result.run_id, measure="liquidity", decision="override", score=6, reviewer="A",
                     reason="Waiver obtained after year-end")
    decided = _memo(db_session, result, overrides=overrides_for(db_session, result.run_id))
    assert not decided["outstanding"]["pending_overrides"]
    liquidity = next(m for m in decided["measures"] if m["key"] == "liquidity")
    assert liquidity["score"]["score"] == 6 and "Overridden by analyst: 6" in liquidity["override"]
    assert decided["decisions"]["overrides"][0]["reason"] == "Waiver obtained after year-end"


def test_the_run_saves_its_memo_and_the_browser_shows_it(client, db_session, nvda_as_hospital, tmp_path):
    result = runner(db_session, output_root=tmp_path / "out").run_filing(FOLDER, "NVDA_MEMO_WEB")
    saved = json.loads(result.paths["financial_memo.json"].read_text(encoding="utf-8"))
    assert saved["executive"]["measures_scored"] == 7
    assert "Executive Financial Credit Assessment" in result.paths["financial_memo.html"].read_text(encoding="utf-8")
    base = f"/cases/NVDA_MEMO_WEB/fo/runs/{result.run_id}"
    assert client.get(f"{base}/memo.json").json()["run"]["run_id"] == result.run_id
    page = client.get(f"{base}/memo.html")
    assert page.status_code == 200 and "Financial Overview" in page.text
    workbench = client.get(f"/cases/NVDA_MEMO_WEB/fo.html?run_id={result.run_id}").text
    assert f'src="{base}/memo.html"' in workbench and "Financial memo preview" in workbench
    assert client.get(f"/cases/OTHER/fo/runs/{result.run_id}/memo.json").status_code == 404
