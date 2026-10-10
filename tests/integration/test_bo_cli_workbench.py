"""BO-only CLI, workbench views, review action and migration (Acceptance B, I, J)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect

from app.api.deps import get_session
from app.cli import main
from app.main import create_app
from app.migrate import BUSINESS_OVERVIEW_TABLES, migrate
from app.models.base import create_engine_and_session, init_db
from app.models.orm import AgentRunRow
from app.services.acquisition.sec_edgar import write_bundle
from app.services.bo.plans import BO_ACTIVE_AGENT_IDS
from tests.bo_helpers import claim, hyp
from tests.sec_helpers import bundle

ANCHOR = "limited number of third-party manufacturers"


def _package(tmp_path):
    write_bundle(bundle(), tmp_path / "filing")
    package = {
        "as_of_date": "2024-12-31",
        "evidence_cutoff_timestamp": "2026-01-01T00:00:00Z",
        "borrower_entity_id": "POC",
        "entities": [{"entity_id": "POC", "legal_name": "Offline Company",
                      "entity_type": "borrower", "borrower_flag": True,
                      "expected_consolidation_scope": "consolidated"}],
        "sources": [],
        "sec_bundles": [{"bundle": "filing/bundle.json", "entity_id": "POC"}],
    }
    path = tmp_path / "package.json"
    path.write_text(json.dumps(package), encoding="utf-8")
    return path


def _script(tmp_path):
    empty = {"parameters": [], "hypotheses": []}
    script = {agent: empty for agent in BO_ACTIVE_AGENT_IDS[:6]}
    script["bo_customers_contracts"] = {
        "parameters": [],
        "hypotheses": [hyp(
            "BO2-H001",
            "The company depends on a limited number of manufacturers and customers.",
            "fact", "negative", "supported", support=[ANCHOR],
            quotes=[(ANCHOR, "We depend on a limited number of third-party "
                             "manufacturers and customers")])],
    }
    script["orchestrate"] = {"overall_assessment": claim(
        "bo_overall", "assessment", "executive",
        "Provisional view: dependence on few manufacturers and customers is the "
        "one validated business finding.", ["BO2-H001"], claim_type="fact")}
    script["challenge"] = {"challenges": []}
    path = tmp_path / "script.json"
    path.write_text(json.dumps(script), encoding="utf-8")
    return path


def _cli(tmp_path, capsys, *args):
    main(["--database", str(tmp_path / "case.db"), "--output", str(tmp_path / "out"),
          *args])
    return json.loads(capsys.readouterr().out)


def test_cli_runs_bo_only_from_a_real_sec_bundle_and_records_review(tmp_path, capsys):
    summary = _cli(tmp_path, capsys, "run-bo-case", "POC_BO",
                   "--package", str(_package(tmp_path)),
                   "--responses", str(_script(tmp_path)))
    assert summary["scope"] == "business_overview_only"
    assert summary["execution_mode"] == "offline_simulation"
    assert summary["conclusion_status"] == "accepted"
    assert summary["human_review_status"] == "pending"
    assert summary["agents"]["business_orchestrator"] == "validated"
    assert summary["agents"]["business_challenge"] == "validated"
    # BO6 had one validated primary finding to consider and ran.
    assert summary["ai_calls"] == 8
    assert summary["business_risk_score"] == {"status": "unavailable", "band": None}
    for path in summary["outputs"].values():
        assert (tmp_path / "out" / "bo" / "POC_BO").as_posix() in path.replace("\\", "/")

    engine, factory = create_engine_and_session(f"sqlite:///{tmp_path / 'case.db'}")
    try:
        with factory() as session:
            agents = {r.agent_id for r in session.query(AgentRunRow)}
            assert agents == set(BO_ACTIVE_AGENT_IDS)
    finally:
        engine.dispose()

    review = _cli(tmp_path, capsys, "bo-review", summary["handoff_id"],
                  "--reviewer", "analyst.a", "--reason", "Checked against the filing.")
    assert review["human_review_status"] == "accepted" and review["review_id"]


def test_cli_default_backend_asserts_nothing(tmp_path, capsys):
    summary = _cli(tmp_path, capsys, "run-bo-case", "POC_EMPTY",
                   "--package", str(_package(tmp_path)))
    assert summary["conclusion_status"] == "unavailable"
    assert summary["agents"]["business_challenge"] == "skipped"
    assert summary["challenge_rounds"] == []
    with pytest.raises(ValueError, match="no validated Business conclusion"):
        _cli(tmp_path, capsys, "bo-review", summary["handoff_id"],
             "--reviewer", "a", "--reason", "b")


@pytest.fixture()
def client(tmp_path, capsys):
    summary = _cli(tmp_path, capsys, "run-bo-case", "POC_BO",
                   "--package", str(_package(tmp_path)),
                   "--responses", str(_script(tmp_path)))
    engine, factory = create_engine_and_session(f"sqlite:///{tmp_path / 'case.db'}")
    init_db(engine)

    def override():
        session = factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_session] = override
    with TestClient(app) as test_client:
        yield test_client, summary
    engine.dispose()


def test_workbench_shows_run_status_hypotheses_and_previews(client):
    http, summary = client
    run_id = summary["analysis_run_id"]
    runs = http.get("/cases/POC_BO/bo/runs").json()["runs"]
    assert [r["analysis_run_id"] for r in runs] == [run_id]
    view = http.get(f"/cases/POC_BO/bo/runs/{run_id}").json()
    assert view["conclusion_status"] == "accepted"
    assert view["evidence_status"]["documents"] >= 1
    assert {a["agent_id"]: a["status"] for a in view["agents"]}[
        "business_challenge"] == "validated"
    assert [h["hypothesis_id"] for h in view["accepted_hypotheses"]] == ["BO2-H001"]
    assert view["rejected_hypotheses"] == [] and view["contradictions"] == []
    assert "open_questions" in view["open_questions"]

    page = http.get("/cases/POC_BO/bo.html")
    assert page.status_code == 200
    for label in ("Evidence status", "Agent execution status",
                  "Accepted and qualified hypotheses", "Rejected hypotheses",
                  "Contradictions", "Open questions", "Business memo preview",
                  "Audit report preview", "OFFLINE SIMULATION"):
        assert label in page.text
    memo = http.get(f"/cases/POC_BO/bo/runs/{run_id}/memo.html")
    audit = http.get(f"/cases/POC_BO/bo/runs/{run_id}/audit.html")
    assert "Business Overview" in memo.text and "Explainability" in audit.text
    assert http.get(f"/cases/POC_BO/bo/runs/{run_id}/memo.json").json()[
        "document"] == "business_overview_memo"
    assert http.get(f"/cases/POC_BO/bo/runs/{run_id}/handoff.json").json()[
        "human_review_status"] == "pending"
    assert http.get("/bo").status_code == 200
    assert "Business Overview" in http.get("/").text
    assert http.get(f"/cases/OTHER/bo/runs/{run_id}").status_code == 404
    assert http.get("/cases/POC_BO/bo/runs/ar_missing").status_code == 404


def test_viewing_never_approves_and_review_is_an_explicit_action(client):
    http, summary = client
    run_id = summary["analysis_run_id"]
    for _ in range(2):
        http.get("/cases/POC_BO/bo.html")
        http.get(f"/cases/POC_BO/bo/runs/{run_id}/audit.json")
    assert http.get(f"/cases/POC_BO/bo/runs/{run_id}").json()["handoff"][
        "human_review_status"] == "pending"
    missing = http.post(f"/cases/POC_BO/bo/runs/{run_id}/review",
                        data={"reviewer": "a", "decision": "accept"})
    assert missing.status_code == 422  # a reason is mandatory
    done = http.post(f"/cases/POC_BO/bo/runs/{run_id}/review",
                     data={"reviewer": "analyst.a", "reason": "Reviewed.",
                           "decision": "accept"})
    assert done.status_code == 200 and done.json()["human_review_status"] == "accepted"
    again = http.post(f"/cases/POC_BO/bo/runs/{run_id}/review",
                      data={"reviewer": "b", "reason": "x", "decision": "reject"})
    assert again.status_code == 409
    assert "analyst.a" in http.get("/cases/POC_BO/bo.html").text


def test_migration_reports_the_business_overview_tables(tmp_path):
    from app.models.orm import (
        AcceptedBusinessOverviewRow,
        HypothesisRecordRow,
        RetrievalAttemptRow,
    )

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    try:
        init_db(engine)
        for table in (AcceptedBusinessOverviewRow, HypothesisRecordRow,
                      RetrievalAttemptRow):
            table.__table__.drop(engine)
        changes = migrate(engine)
        assert {f"table:{t}" for t in BUSINESS_OVERVIEW_TABLES} <= set(changes)
        assert set(BUSINESS_OVERVIEW_TABLES) <= set(inspect(engine).get_table_names())
        assert migrate(engine) == []
    finally:
        engine.dispose()
