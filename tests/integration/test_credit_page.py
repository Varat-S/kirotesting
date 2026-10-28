"""The case credit view: BO, FO and RMS on one page, with RMS re-assessed after every decision made on it.

Seeds the escalated HCA FO run and the BO handoff of ``test_rms_runner`` into a file database and drives the
page through the browser routes. No network, no model.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.base import create_engine_and_session, init_db
from app.services.fo import review
from app.services.rms.runner import records
from tests.integration.test_rms_runner import BO_RUN, CASE, FO_RUN, seed

PAGE = f"/cases/{CASE}/credit.html"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app.api import deps
    from app.core.config import get_settings

    db = tmp_path / "data" / "main.db"
    db.parent.mkdir()
    engine, factory = create_engine_and_session(f"sqlite:///{db.as_posix()}")
    init_db(engine)
    with factory() as session:
        seed(session)
        session.commit()
    engine.dispose()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db.as_posix()}")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    # The seeded FO payload is trimmed to what RMS reads; FO's re-rendering of its own pages isn't under test.
    monkeypatch.setattr(review, "write_review_outputs", lambda *a, **k: {})
    get_settings.cache_clear()
    deps._session_factory.cache_clear()
    with TestClient(create_app()) as test_client:
        test_client.output_root = tmp_path / "out"
        yield test_client
    deps._session_factory.cache_clear()
    get_settings.cache_clear()


def post(client, path, data):
    response = client.post(path, data=data, follow_redirects=False)
    assert response.status_code == 303, response.text
    return client.get(response.headers["location"]).text


def test_the_page_shows_bo_and_fo_before_any_assessment(client):
    page = client.get(PAGE).text
    assert "HCA Healthcare, Inc.: credit view" in page
    assert "No assessment of these runs yet" in page
    assert "satisfactory" in page and "senior unsecured tier 2" in page  # M4 verdict and placement
    assert "Waiting for an analyst (1)" in page and 'class="grid fo-override-form"' in page
    assert "Accept the Business Overview" in page
    assert client.get("/cases/NOPE/credit.html").status_code == 404


def test_every_decision_on_the_page_reassesses_rms(client):
    # 1. A request: the FO escalation is still open, so RMS refers.
    page = post(client, f"/cases/{CASE}/credit/assess",
                {"fo_run": FO_RUN, "bo_run": BO_RUN, "requested_amount_m": "2000", "tenor_years": "5"})
    assert "Assessed: refer." in page and "RMS-G02" in page
    assert "pending human review" in page  # the BO handoff isn't reviewed yet (a limitation)
    # 2. The analyst overrides the escalated measure: RMS re-assesses at once and approves.
    page = post(client, f"/cases/{CASE}/credit/fo/{FO_RUN}/override",
                {"measure": "liquidity", "decision": "override", "score": "8", "reviewer": "A. Analyst",
                 "reason": "Breach cured after the year-end", "bo_run": BO_RUN})
    assert "Decision recorded; RMS re-assessed: approve." in page
    assert "Waiting for an analyst" not in page and "liquidity: override 3 -&gt; 8 (A. Analyst)" in page
    assert "RMS-M01" in page  # liquidity now scores 8: a minimum-liquidity covenant
    # 3. The reviewer rejects the BO handoff: RMS refers on it.
    page = post(client, f"/cases/{CASE}/credit/bo/{BO_RUN}/review",
                {"decision": "reject", "reviewer": "B. Reviewer", "reason": "Payer mix unsupported",
                 "fo_run": FO_RUN})
    assert "Decision recorded; RMS re-assessed: refer." in page and "RMS-G10" in page
    assert "Accept the Business Overview" not in page
    history = records(client.output_root, CASE)
    assert [r["verdict"] for r in history] == ["refer", "approve", "refer"]
    assert [r["sources"]["bo_review_status"] for r in history] == ["pending", "pending", "rejected"]
    assert history[2]["supersedes"] == history[1]["run_id"]


def test_a_decision_made_elsewhere_marks_the_assessment_out_of_date(client):
    post(client, f"/cases/{CASE}/credit/assess", {"fo_run": FO_RUN, "bo_run": BO_RUN, "requested_amount_m": "2000"})
    # The BO workbench's review route re-assesses through this hook after recording the decision.
    from app.api import deps, workbench
    from app.services.bo.handoff import review_handoff

    with deps._session_factory()() as session:
        review_handoff(session, "boh-hca", reviewer="B. Reviewer", reason="Checked", decision="accept")
        workbench._reassess_rms(session, BO_RUN)
        session.commit()
    assert [r["sources"]["bo_review_status"] for r in records(client.output_root, CASE)] == ["pending", "accepted"]
    # A decision recorded outside the browser (here straight through the FO service) leaves it out of date.
    with deps._session_factory()() as session:
        review.override_measure(session, FO_RUN, measure="liquidity", decision="accept",
                                reviewer="A. Analyst", reason="Engine score stands")
        session.commit()
    page = client.get(PAGE).text
    assert "Out of date:" in page and "FO analyst decisions were recorded since" in page
    page = post(client, f"/cases/{CASE}/credit/assess",
                {"fo_run": FO_RUN, "bo_run": BO_RUN, "requested_amount_m": "2000"})
    assert "Out of date:" not in page and "Assessed: approve." in page


def test_an_assessment_by_an_earlier_rms_version_still_shows_and_asks_for_a_reassessment(client):
    import json

    post(client, f"/cases/{CASE}/credit/assess", {"fo_run": FO_RUN, "bo_run": BO_RUN, "requested_amount_m": "2000"})
    path = records(client.output_root, CASE)[-1]["_path"]
    old = json.loads(open(path, encoding="utf-8").read())
    # What an rms-1.0 record looks like: sized at the FO level, no inputs, no review tracking.
    old["capacity"]["fo_level"] = old["capacity"].pop("level")
    old["capacity"].pop("inputs_m")
    for key in ("bo_review_status", "fo_decisions"):
        old["sources"].pop(key)
    open(path, "w", encoding="utf-8").write(json.dumps(old))
    page = client.get(PAGE).text
    assert "Debt capacity:" in page and "sized at grade level low" in page
    assert "made by an earlier RMS version" in page


@pytest.fixture()
def no_run_detail(monkeypatch):
    """The seeded FO payload is trimmed to what RMS reads, too thin for the FO workbench's run detail: render the
    workbench without it (the way back doesn't depend on it)."""
    from app.api import financial_overview

    class NoDetail(dict):  # takes the route's extra keys, but the page skips the run section
        def __bool__(self):
            return False

    monkeypatch.setattr(financial_overview, "fo_view", lambda session, run_id: NoDetail())


def test_the_fo_workbench_opened_from_the_credit_view_leads_back_to_it(client, no_run_detail):
    page = client.get(PAGE).text
    link = f"/cases/{CASE}/fo.html?run_id={FO_RUN}&amp;credit=1&amp;bo_run={BO_RUN}"
    assert link in page
    workbench = client.get(link.replace("&amp;", "&")).text
    back = f'href="/cases/{CASE}/credit.html?fo_run={FO_RUN}&amp;bo_run={BO_RUN}"'
    assert 'id="credit-back"' in workbench and back in workbench
    post(client, f"/cases/{CASE}/credit/assess", {"fo_run": FO_RUN, "bo_run": BO_RUN, "requested_amount_m": "2000"})
    # A decision on the FO workbench, from the credit view: back to the credit view, re-assessed.
    response = client.post(f"/cases/{CASE}/fo/runs/{FO_RUN}/override", follow_redirects=False,
                           data={"measure": "liquidity", "decision": "override", "score": "8", "reviewer": "A",
                                 "reason": "Cured", "credit": "1", "bo_run": BO_RUN})
    assert response.status_code == 303
    assert response.headers["location"].startswith(f"/cases/{CASE}/credit.html?fo_run={FO_RUN}&bo_run={BO_RUN}")
    page = client.get(response.headers["location"]).text
    assert "Decision recorded; RMS re-assessed: approve." in page
    # Without the credit context the FO workbench behaves as before.
    assert 'id="credit-back"' not in client.get(f"/cases/{CASE}/fo.html?run_id={FO_RUN}").text


def test_the_fo_decision_forms_carry_the_credit_context():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "app/api/templates/fo_scorecard.html.j2").read_text(encoding="utf-8")
    carried = ('{% if credit %}<input type="hidden" name="credit" value="1">'
               '<input type="hidden" name="bo_run" value="{{ credit.bo_run }}">{% endif %}')
    assert source.count(carried) == 3  # both going-concern forms and the measure override


def test_the_fo_workbench_keeps_the_way_back_after_a_bad_decision(client, no_run_detail):
    response = client.post(f"/cases/{CASE}/fo/runs/{FO_RUN}/override",
                           data={"measure": "liquidity", "decision": "override", "score": "", "reviewer": "A",
                                 "reason": "r", "credit": "1", "bo_run": BO_RUN})
    assert response.status_code == 400 and 'id="credit-back"' in response.text


def test_a_bo_decision_from_the_credit_view_returns_to_it(client, monkeypatch):
    from app.api import workbench
    from app.api.credit import credit_context

    # The seed has a handoff but no recorded BO memo, which the workbench page itself needs.
    monkeypatch.setattr(workbench, "_bo_run_for_case", lambda s, c, r: {"handoff": {"handoff_id": "boh-hca"}})
    post(client, f"/cases/{CASE}/credit/assess", {"fo_run": FO_RUN, "bo_run": BO_RUN, "requested_amount_m": "2000"})
    response = client.post(f"/cases/{CASE}/bo/runs/{BO_RUN}/review", follow_redirects=False,
                           data={"decision": "reject", "reviewer": "B", "reason": "Unsupported", "credit": "1",
                                 "fo_run": FO_RUN})
    assert response.status_code == 303
    page = client.get(response.headers["location"]).text
    assert "Decision recorded; RMS re-assessed: refer." in page and "RMS-G10" in page
    # Its review form carries the context, and the page shows the way back.
    html = workbench._env.get_template("bo_workbench.html.j2").render(
        runs=[], view=None, credit=credit_context(CASE, FO_RUN, BO_RUN))
    assert 'id="credit-back"' in html and f"/cases/{CASE}/credit.html?fo_run={FO_RUN}" in html


def test_a_bad_decision_shows_the_error_and_changes_nothing(client):
    page = client.post(f"/cases/{CASE}/credit/fo/{FO_RUN}/override",
                       data={"measure": "liquidity", "decision": "override", "score": "", "reviewer": "A",
                             "reason": "r", "bo_run": BO_RUN})
    assert page.status_code == 400 and "An override needs a score from 1 to 10." in page.text
    bad = client.post(f"/cases/{CASE}/credit/assess",
                      data={"fo_run": FO_RUN, "bo_run": BO_RUN, "requested_amount_m": "0"})
    assert bad.status_code == 400 and "Not assessed:" in bad.text
    assert records(client.output_root, CASE) == []


def test_a_blank_amount_gives_a_debt_capacity_report(client):
    page = post(client, f"/cases/{CASE}/credit/assess", {"fo_run": FO_RUN, "bo_run": BO_RUN, "requested_amount_m": ""})
    assert "Assessed: capacity only." in page and "Debt capacity report: no requested amount, no verdict" in page
    assert "None" not in page.split('name="requested_amount_m"')[1][:40]  # the re-assess fields stay blank
    [record] = records(client.output_root, CASE)
    assert record["verdict"] == "capacity_only" and record["request"]["requested_amount_m"] is None
