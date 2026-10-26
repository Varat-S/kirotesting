"""Run the authored HCA and fictional examples offline into UNUSED destinations.

From the repository root:
PYTHONPATH="$PWD" .venv/bin/python examples/bo_m3/management_stability/run_examples.py --output <new-directory>

This command never constructs a live provider. HCA's canonical source snapshot
is loaded read-only; new scripted-response recordings go to a separate store.
"""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from app.models.base import create_engine_and_session, init_db
from app.schemas.snapshots import CanonicalEvidenceSnapshot
from app.services.bo.m3_demo import write_m3_demo
from app.services.bo.m3_standalone import run_m3


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-database", type=Path, default=Path("data/m3_phase2_verification_20261026_02.db"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    fixtures = Path(__file__).resolve().parent
    output = args.output.resolve()
    if output.exists():
        parser.error("output must be unused; existing examples/runs are never overwritten")
    source = args.source_database.resolve()
    if not source.is_file():
        parser.error("the preserved canonical source database is missing; no live request will replace it")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    hca = json.loads((fixtures / "hca_fy2025_fixture.json").read_text())
    if hashlib.sha256((root / hca["filing_path"]).read_bytes()).hexdigest() != hca["filing_sha256"]:
        parser.error("local FY2025 filing does not match the authored fixture's source hash")
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as connection:
        row = connection.execute("select payload from snapshots where snapshot_type='canonical_evidence' and case_id=?",
                                 (hca["source_case"],)).fetchone()
    if row is None:
        parser.error("the expected preserved canonical snapshot is absent")
    evidence = CanonicalEvidenceSnapshot.model_validate(json.loads(row[0]))
    abc = json.loads((fixtures / "fictional_negative_fixture.json").read_text())
    fictional = CanonicalEvidenceSnapshot(case_id="ABC_MANAGEMENT_STABILITY_FICTIONAL", as_of_date="2026-02-01",
        entities=[{"entity_id": "ABC", "legal_name": "ABC Corporation", "borrower_flag": True}],
        documents=[{"document_id": "abc-fictional", "filename": "ABC_FICTIONAL.txt", "filing_date": "2026-02-10", "report_date": "2025-12-31"}],
        narrative_evidence=[{"evidence_id": eid, "text": text, "document_id": "abc-fictional", "section": "Executive Officers",
                             "candidate_topics": ["management"], "location": i * 2000}
                            for i, (eid, text) in enumerate(abc["evidence"].items())])
    output.mkdir(parents=True, exist_ok=False)
    engine, factory = create_engine_and_session("sqlite:///" + str(output / "offline_recording.db"))
    init_db(engine)
    summaries = {}
    try:
        with factory() as session:
            for name, snapshot, fixture, structure in (
                ("hca_fy2025", evidence, hca, json.loads((root / "data/test_inputs/hca_simulated_corporate_structure.json").read_text())),
                ("fictional_negative", fictional, abc, None),
            ):
                result = run_m3(session, case_id=snapshot.case_id, evidence=snapshot, metrics={},
                                m3_responses={"single_pass": fixture["response"]}, corporate_structure_document=structure)
                if result.model_called:
                    raise RuntimeError("offline example unexpectedly reports a model call")
                session.commit()
                write_m3_demo(result, output / name)
                view = result.m3_analysis["senior_management_assessment"]
                (output / name / "management_stability.json").write_text(json.dumps(view, indent=2, ensure_ascii=False) + "\n")
                summaries[name] = {"fixture_kind": fixture["fixture_kind"], "model_called": result.model_called,
                    "specialist_validated": result.specialist_validated,
                    "model_run_id": result.validation_stages["model_run_id"],
                    "evidence_supported_findings": result.validation_stages["evidence_supported_findings"],
                    "conclusion": view["consolidated_management_stability_conclusion"]}
    finally:
        engine.dispose()
        if hashlib.sha256(source.read_bytes()).hexdigest() != before:
            raise RuntimeError("preserved source database changed")
    (output / "summary.json").write_text(json.dumps(summaries, indent=2) + "\n")
    print(json.dumps({"output": str(output), "examples": summaries}, indent=2))


if __name__ == "__main__":
    main()
