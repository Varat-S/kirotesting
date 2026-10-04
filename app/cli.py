"""Local offline runner and explicit review/finalization commands."""

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from app.models.base import create_engine_and_session, init_db
from app.models.orm import Snapshot
from app.schemas.snapshots import FinalCaseSnapshot
from app.services.audit.log import AuditLog
from app.services.pipeline.package import SourcePackage
from app.services.pipeline.runner import CreditMemoPipeline
from app.services.review.workflow import HumanReviewWorkflow


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Offline credit memo PoC; outputs require explicit human review."
    )
    parser.add_argument(
        "--database", default="data/credit_memo.db", help="SQLite database path"
    )
    parser.add_argument("--output", default="output")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run-case", help="Process evidence and produce an unapproved draft"
    )
    run.add_argument("case_id")
    run.add_argument(
        "--package", type=Path, default=Path("examples/synthetic-case/package.json")
    )
    approve = commands.add_parser(
        "approve", help="Record your explicit approval of the exact draft content"
    )
    approve.add_argument("case_id")
    approve.add_argument("--version", type=int, required=True)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--reason", required=True)
    finalize = commands.add_parser(
        "finalize", help="Finalize a previously approved draft"
    )
    finalize.add_argument("case_id")
    finalize.add_argument("--version", type=int, required=True)
    finalize.add_argument("--reviewer", required=True)
    finalize.add_argument("--pdf", action="store_true")
    args = parser.parse_args(argv)
    database = Path(args.database).resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    engine, factory = create_engine_and_session(f"sqlite:///{database.as_posix()}")
    init_db(engine)
    try:
        with factory() as session:
            runner = CreditMemoPipeline(
                session, data_root=database.parent, output_root=args.output
            )
            if args.command == "run-case":
                result = runner.run_case(
                    args.case_id, package=SourcePackage.load(args.package)
                )
                summary = {
                    "case_id": args.case_id,
                    "status": "draft",
                    "evidence_snapshot_version": result.evidence_snapshot.snapshot_version,
                    "draft_version": result.draft_snapshot.snapshot_version,
                    "outputs": {k: str(v) for k, v in result.output_paths.items()},
                    "admitted_sources": len(result.admitted_source_hashes),
                    "rejected_sources": len(result.rejected_sources),
                    "mandatory_escalations": sum(
                        e["mandatory"] and e["status"] == "open"
                        for e in result.escalations
                    ),
                }
            elif args.command == "approve":
                row = session.scalars(
                    select(Snapshot).where(
                        Snapshot.case_id == args.case_id,
                        Snapshot.snapshot_type == "final_case",
                        Snapshot.snapshot_version == args.version,
                    )
                ).one()
                if row.finalized:
                    raise ValueError("The snapshot is already finalized.")
                review = HumanReviewWorkflow(
                    session, audit=AuditLog(session), case_id=args.case_id
                ).sign_off_recommendation(
                    reviewer=args.reviewer,
                    snapshot=FinalCaseSnapshot.model_validate(row.payload),
                    reason=args.reason,
                )
                summary = {
                    "case_id": args.case_id,
                    "approved_version": args.version,
                    "review_id": review.review_id,
                }
            else:
                frozen, output = runner.finalize_case(
                    args.case_id,
                    args.version,
                    signed_off_by=args.reviewer,
                    produce_pdf=args.pdf,
                )
                summary = {
                    "case_id": args.case_id,
                    "status": "final",
                    "snapshot_version": frozen.snapshot_version,
                    "content_hash": frozen.content_hash,
                    "outputs": {
                        "json": str(output.json_path),
                        "html": str(output.html_path),
                        "pdf": str(output.pdf_path) if output.pdf_path else None,
                    },
                }
            session.commit()
            print(json.dumps(summary, indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
