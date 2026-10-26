"""RMS from the database: an analyst's FO decision is applied, and an earlier assessment is redone after it.

An escalated HCA FO run (the RMS fixture with a liquidity escalation) and a BO handoff carrying the Module 4
sample are seeded; the decision goes through FO's own ``override_measure``. No network, no model.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.cli import main
from app.core.hashing import content_hash
from app.models.base import create_engine_and_session, init_db
from app.models.orm import AcceptedBusinessOverviewRow, FinancialOverviewRunRow
from app.services.fo.review import override_measure
from app.services.rms import RmsRequest
from app.services.rms.runner import RmsRunError, assess_case, reassess_after_fo_review, records
from tests.unit.test_rms import FO, LIQUIDITY_ESCALATION, M4, handoff

CASE, FO_RUN, BO_RUN = "HCA_FY2025", "fo-hca", "ar_hca"


def seed(session):
    payload = {**FO, "escalations": [{**LIQUIDITY_ESCALATION, "measure_name": "Liquidity and debt maturity"}]}
    session.add(FinancialOverviewRunRow(
        run_id=FO_RUN, case_id=CASE, filing_folder="HCA 10-K FY2025", folder_signature="sig",
        engine_version="fo-engine-test", reference_hash="ref", status="awaiting_review",
        human_review_status="escalated", payload=payload, content_hash=content_hash(payload),
        started_at=datetime(2026, 10, 1, tzinfo=timezone.utc)))
    session.add(AcceptedBusinessOverviewRow(
        handoff_id="boh-hca", analysis_run_id=BO_RUN, case_id=CASE, snapshot_version=1,
        payload={**handoff(M4), "handoff_id": "boh-hca", "business_topic_conclusion_id": "btc-hca",
                 "conclusion_status": "accepted", "unresolved_challenges": [], "human_review_status": "pending"},
        content_hash="h"))
    session.flush()


def test_an_fo_override_reassesses_the_case(db_session, tmp_path):
    seed(db_session)
    first = assess_case(db_session, CASE, output_root=tmp_path, request=RmsRequest(2000))
    assert first["verdict"] == "refer" and any(g.startswith("RMS-G02") for g in first["gates"])
    override_measure(db_session, FO_RUN, measure="liquidity", decision="override", score=8,
                     reviewer="A. Analyst", reason="Covenant breach cured after the year-end")
    second = reassess_after_fo_review(db_session, FO_RUN, tmp_path)
    assert second["supersedes"] == first["run_id"]
    assert second["verdict"] == "approve" and not any(g.startswith("RMS-G02") for g in second["gates"])
    assert second["fo_decisions"] == ["liquidity: override 3 -> 8 (A. Analyst)"]
    record = json.loads(open(second["output"], encoding="utf-8").read())
    assert record["sources"]["fo_run_id"] == FO_RUN and record["sources"]["bo_analysis_run_id"] == BO_RUN
    assert len(record["sources"]["fo_decisions"]["overrides"]) == 1
    assert record["request"]["requested_amount_m"] == 2000  # the same request, redone
    assert [r["run_id"] for r in records(tmp_path, CASE)] == [first["run_id"], second["run_id"]]


def test_a_decision_on_a_run_rms_never_assessed_changes_nothing(db_session, tmp_path):
    seed(db_session)
    assert reassess_after_fo_review(db_session, FO_RUN, tmp_path) is None
    assert records(tmp_path, CASE) == []


def test_without_a_request_the_latest_one_is_reused(db_session, tmp_path):
    seed(db_session)
    with pytest.raises(RmsRunError, match="give a requested amount"):
        assess_case(db_session, CASE, output_root=tmp_path)
    assess_case(db_session, CASE, output_root=tmp_path, request=RmsRequest(1500, tenor_years=3))
    again = assess_case(db_session, CASE, output_root=tmp_path)
    assert again["requested_amount_m"] == 1500
    with pytest.raises(RmsRunError, match="No Business Overview handoff"):
        assess_case(db_session, CASE, output_root=tmp_path, bo_run_id="ar_missing")


def test_cli_run_rms_then_fo_override_reassesses(tmp_path, capsys, monkeypatch):
    from app.services.fo import review

    # The seeded payload is trimmed to what RMS reads; FO's own re-rendering of its pages isn't under test here.
    monkeypatch.setattr(review, "write_review_outputs", lambda *a, **k: {})
    db = tmp_path / "main.db"
    engine, factory = create_engine_and_session(f"sqlite:///{db.as_posix()}")
    init_db(engine)
    with factory() as session:
        seed(session)
        session.commit()
    engine.dispose()
    common = ["--database", str(db), "--output", str(tmp_path / "out")]
    main([*common, "run-rms", CASE, "--amount", "2000"])
    assert json.loads(capsys.readouterr().out)["verdict"] == "refer"
    main([*common, "fo-override", FO_RUN, "--measure", "liquidity", "--decision", "override", "--score", "8",
          "--reviewer", "A. Analyst", "--reason", "Covenant breach cured"])
    out = json.loads(capsys.readouterr().out)
    assert out["run_status"] == "completed" and out["rms"]["verdict"] == "approve"
    with pytest.raises(ValueError, match="need --amount"):
        main([*common, "run-rms", CASE, "--tenor", "5"])
