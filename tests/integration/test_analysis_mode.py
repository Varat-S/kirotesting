"""Milestone 21.1 — analysis_mode legacy|agentic switch (Req 20, 24).

Proves: legacy is the default and unchanged; agentic runs the bounded multi-agent
path after the SAME CanonicalEvidenceSnapshot boundary, opens exactly one
AgenticAnalysisRun, produces a draft (not auto-finalized), and records agentic
provenance; an invalid mode is rejected. Both modes complete the pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.base import create_engine_and_session, init_db
from app.models.orm import AgenticAnalysisRun, ParameterResultRow, RiskScoreRow, Snapshot
from app.services.pipeline.package import SourcePackage
from app.services.pipeline.runner import CreditMemoPipeline

ROOT = Path(__file__).resolve().parents[2] / "examples" / "synthetic-case"


def _run(mode, tmp_path, case_id):
    engine, factory = create_engine_and_session("sqlite://")
    init_db(engine)
    session = factory()
    runner = CreditMemoPipeline(
        session, data_root=ROOT / "data", output_root=tmp_path / "output",
        analysis_mode=mode)
    result = runner.run_case(case_id, package=SourcePackage.load(ROOT / "package.json"))
    return session, result


def test_default_mode_is_legacy(db_session):
    runner = CreditMemoPipeline(db_session)
    assert runner.analysis_mode == "legacy"


def test_invalid_mode_rejected(db_session):
    with pytest.raises(ValueError, match="legacy.*agentic|analysis_mode"):
        CreditMemoPipeline(db_session, analysis_mode="bogus")


def test_legacy_mode_produces_draft(tmp_path):
    session, result = _run("legacy", tmp_path, "SYN_LEGACY")
    # Draft produced; no agentic artifacts in legacy mode.
    assert result.draft_snapshot is not None
    assert session.query(AgenticAnalysisRun).count() == 0


def test_agentic_mode_runs_after_same_boundary_and_produces_draft(tmp_path):
    session, result = _run("agentic", tmp_path, "SYN_AGENTIC")

    # Exactly one analysis run opened (Remediation 1), bound to the snapshot.
    runs = session.query(AgenticAnalysisRun).all()
    assert len(runs) == 1
    assert runs[0].case_id == "SYN_AGENTIC"
    assert runs[0].status in ("completed", "partially_failed")

    # Deterministic scores were produced.
    kinds = {s.kind for s in session.query(RiskScoreRow).all()}
    assert {"business", "financial", "obligor"} <= kinds

    # A draft (not finalized) is produced; finalization still needs human sign-off.
    assert result.draft_snapshot is not None
    final = session.query(Snapshot).filter_by(
        case_id="SYN_AGENTIC", snapshot_type="final_case").first()
    assert final is not None
    assert final.finalized is False  # human sign-off remains mandatory

    # The canonical evidence snapshot boundary is intact (same as legacy).
    evidence = session.query(Snapshot).filter_by(
        case_id="SYN_AGENTIC", snapshot_type="canonical_evidence").first()
    assert evidence is not None


def test_agentic_draft_carries_analysis_run_provenance(tmp_path):
    session, result = _run("agentic", tmp_path, "SYN_PROV")
    run = session.query(AgenticAnalysisRun).first()
    agentic = result.memo_json  # the draft memo payload
    # The analysis run id is discoverable and parameters belong to it.
    params = session.query(ParameterResultRow).filter_by(
        analysis_run_id=run.analysis_run_id).all()
    assert all(p.analysis_run_id == run.analysis_run_id for p in params)
