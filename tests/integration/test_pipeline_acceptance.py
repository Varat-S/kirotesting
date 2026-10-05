"""Acceptance checks execute the production runner against real fixture files."""

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.hashing import content_hash
from app.models.base import create_engine_and_session, init_db
from app.models.orm import AuditEvent, Fact, ModelRun
from app.schemas.snapshots import FinalCaseSnapshot
from app.services.evaluation.manifest import load_manifest
from app.services.pipeline.package import SourcePackage
from app.services.pipeline.runner import CreditMemoPipeline
from app.services.review.workflow import HumanReviewWorkflow, SignOffRequiredError

ROOT = Path(__file__).resolve().parents[2] / "examples" / "synthetic-case"


def fresh_run(root, package=None, backend=None, case_id="SYNTHETIC_2024"):
    engine, factory = create_engine_and_session("sqlite://")
    init_db(engine)
    session = factory()
    runner = CreditMemoPipeline(
        session, data_root=root / "data", output_root=root / "output", backend=backend
    )
    result = runner.run_case(
        case_id, package=package or SourcePackage.load(ROOT / "package.json")
    )
    return engine, session, runner, result


def test_actual_sources_reach_metrics_audit_and_canonical_outputs(db_session, tmp_path):
    package = SourcePackage.load(ROOT / "package.json")
    runner = CreditMemoPipeline(
        db_session, data_root=tmp_path / "data", output_root=tmp_path / "output"
    )
    result = runner.run_case("SYNTHETIC_2024", package=package)
    truth = load_manifest(ROOT / "ground_truth.json")
    for expected in truth.expected_metric_outputs:
        assert result.metrics[expected.metric_id].result == pytest.approx(
            expected.expected_result
        )
    assert result.metrics["operating_margin"].evidence_quality == "verified"
    assert len(result.admitted_source_hashes) == len(package.sources)
    assert result.benchmarks["net_debt_to_ebitda"]["sample_size"] == 1
    assert result.benchmarks["net_debt_to_ebitda"]["p90"] is None
    assert result.trends["net_debt_to_ebitda"]["current_value"] == 3
    assert result.trends["net_debt_to_ebitda"]["previous_value"] == 2.5
    assert not result.mapping_issues
    assert result.memo_json["final_status"] == "draft"
    assert (
        json.loads(result.output_paths["json"].read_text(encoding="utf-8"))
        == result.memo_json
    )
    types = set(db_session.scalars(select(AuditEvent.event_type)))
    assert {
        "document_ingested",
        "fact_extracted",
        "fact_verified",
        "metric_calculated",
        "benchmark_generated",
        "llm_run",
        "draft_memo_generated",
    } <= types
    assert "case_finalized" not in types
    assert len(list(db_session.scalars(select(ModelRun)))) == 3
    facts = list(db_session.scalars(select(Fact)))
    assert any(
        f.original_name == "us-gaap:Revenues" and f.name == "revenue" for f in facts
    )
    assert any(f.extraction_method == "pdf_table" for f in facts)
    with pytest.raises(SignOffRequiredError):
        runner.finalize_case("SYNTHETIC_2024", 1, signed_off_by="officer")
    snapshot = FinalCaseSnapshot.model_validate(result.draft_snapshot.payload)
    HumanReviewWorkflow(db_session, case_id="SYNTHETIC_2024").sign_off_recommendation(
        reviewer="officer", snapshot=snapshot
    )
    frozen, output = runner.finalize_case("SYNTHETIC_2024", 1, signed_off_by="officer")
    assert frozen.id == result.draft_snapshot.id
    assert frozen.finalized
    assert output.memo_json["final_status"] == "final"


def test_actual_runner_reproducibility_including_approved_memo_hash(tmp_path):
    results = []
    resources = []
    try:
        for label in ["first", "second"]:
            engine, session, runner, result = fresh_run(tmp_path / label)
            resources.append((engine, session))
            snapshot = FinalCaseSnapshot.model_validate(result.draft_snapshot.payload)
            # Replay the identical explicit human approval record, as part of
            # the pinned inputs; never fabricate approval inside the runner.
            HumanReviewWorkflow(
                session, case_id="SYNTHETIC_2024"
            ).sign_off_recommendation(
                reviewer="fixture.officer",
                snapshot=snapshot,
                review_id="synthetic-approval-1",
            )
            final, output = runner.finalize_case(
                "SYNTHETIC_2024", 1, signed_off_by="fixture.officer"
            )
            results.append((result, final, output))
        a, b = results
        assert a[0].admitted_source_hashes == b[0].admitted_source_hashes
        assert a[0].evidence_snapshot.model_dump(mode="json") == b[
            0
        ].evidence_snapshot.model_dump(mode="json")
        assert {k: v.as_payload() for k, v in a[0].metrics.items()} == {
            k: v.as_payload() for k, v in b[0].metrics.items()
        }
        assert a[0].escalations == b[0].escalations
        assert [r["parsed"] for r in a[0].model_runs] == [
            r["parsed"] for r in b[0].model_runs
        ]
        assert a[1].payload == b[1].payload
        assert content_hash(a[2].memo_json) == content_hash(b[2].memo_json)
    finally:
        for engine, session in resources:
            session.close()
            engine.dispose()


def test_conflicting_source_blocks_metrics_and_finalization(db_session, tmp_path):
    package = SourcePackage.load(ROOT / "package.json")
    pdf = next(s for s in package.sources if s.parser == "pdf_table")
    conflict = pdf.model_copy(
        update={
            "filename": "conflict.csv",
            "path": None,
            "data": b"Line item,2024\nOperating Revenue,4000\n",
            "parser": "csv",
        }
    )
    package = package.model_copy(update={"sources": [*package.sources, conflict]})
    runner = CreditMemoPipeline(
        db_session, data_root=tmp_path / "data", output_root=tmp_path / "output"
    )
    result = runner.run_case("SYNTHETIC_2024", package=package)
    assert result.metrics["operating_margin"].result is None
    assert any(
        e["rule_id"] == "R-DATA-CONFLICT-01" and e["mandatory"]
        for e in result.escalations
    )
    snapshot = FinalCaseSnapshot.model_validate(result.draft_snapshot.payload)
    HumanReviewWorkflow(db_session, case_id="SYNTHETIC_2024").sign_off_recommendation(
        reviewer="officer", snapshot=snapshot
    )
    with pytest.raises(SignOffRequiredError):
        runner.finalize_case("SYNTHETIC_2024", 1, signed_off_by="officer")
