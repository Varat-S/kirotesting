"""Local offline runner and explicit review/finalization commands."""

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import select

from app.models.base import create_engine_and_session, init_db
from app.models.orm import Snapshot
from app.schemas.snapshots import FinalCaseSnapshot
from app.services.audit.log import AuditLog
from app.services.pipeline.package import SourcePackage
from app.services.pipeline.runner import CreditMemoPipeline
from app.services.review.workflow import HumanReviewWorkflow
from app.core.config import get_settings
from app.services.acquisition.sec_edgar import SecEdgarClient, EdgarError, write_bundle
from app.services.extraction.sec.bundle import SecFilingBundle
from app.services.pipeline.sec import sec_source_package


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
    listing = commands.add_parser(
        "sec-list",
        help="List SEC annual reports; requires SEC_USER_AGENT contact identification",
    )
    listing.add_argument("ticker")
    listing.add_argument("--include-amendments", action="store_true")
    fetch = commands.add_parser(
        "sec-fetch", help="Download a bundle without admitting it to a case"
    )
    fetch.add_argument("ticker")
    selection = fetch.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--fy", type=int, help="Discovery hint from the report date; accession is exact"
    )
    selection.add_argument("--accession")
    fetch.add_argument("--with-proxy", action="store_true")
    fetch.add_argument("--destination", type=Path)
    sec_run = commands.add_parser(
        "run-sec-case",
        help="Run a local or fetched SEC bundle through the controlled offline pipeline",
    )
    source = sec_run.add_mutually_exclusive_group(required=True)
    source.add_argument("--bundle", type=Path)
    source.add_argument("--ticker")
    sec_run.add_argument("case_id")
    sec_run.add_argument("--fy", type=int)
    sec_run.add_argument("--accession")
    sec_run.add_argument("--with-proxy", action="store_true")
    sec_run.add_argument(
        "--cutoff",
        type=lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
        required=True,
    )
    sec_run.add_argument("--as-of", type=date.fromisoformat)
    sec_run.add_argument("--entity-id")
    sec_run.add_argument("--entity-name")
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
    if (
        args.command == "run-sec-case"
        and args.ticker
        and (bool(args.fy) == bool(args.accession))
    ):
        parser.error("A live SEC run requires exactly one of --fy or --accession.")
    database = Path(args.database).resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    engine, factory = create_engine_and_session(f"sqlite:///{database.as_posix()}")
    init_db(engine)
    try:
        with factory() as session:
            runner = CreditMemoPipeline(
                session, data_root=database.parent, output_root=args.output
            )
            if args.command in {"sec-list", "sec-fetch"} or (
                args.command == "run-sec-case" and args.ticker
            ):
                client = SecEdgarClient(
                    get_settings().sec_user_agent, audit=AuditLog(session)
                )
                company = client.lookup_ticker(args.ticker)
                filings = client.list_filings(company["cik"])
                if args.command == "sec-list":
                    summary = {
                        "company": company,
                        "filings": [
                            {**f.metadata(), "fiscal_year_hint": f.fiscal_year}
                            for f in client.annual_reports(
                                filings, args.include_amendments
                            )
                        ],
                        "fetches": client.fetch_metadata,
                    }
                    session.commit()
                    print(json.dumps(summary, indent=2))
                    return
                matches = [
                    f
                    for f in filings
                    if (
                        f.accession == args.accession
                        if args.accession
                        else f.form == "10-K" and f.fiscal_year == args.fy
                    )
                ]
                if not matches:
                    raise EdgarError(
                        "No filing matched; use sec-list and choose an explicit accession."
                    )
                # Never silently select between two annual filings/amendments.
                if len(matches) != 1:
                    raise EdgarError(
                        "Multiple filings matched; select an explicit accession."
                    )
                filing = matches[0]
                bundle = client.fetch_bundle(
                    company,
                    filing,
                    proxy=client.proxy_for(filings, filing)
                    if args.with_proxy
                    else None,
                )
                if args.command == "sec-fetch":
                    destination = (
                        args.destination
                        or Path("data/sec") / company["ticker"] / filing.accession
                    )
                    manifest = write_bundle(bundle, destination)
                    summary = {
                        "bundle_manifest": str(manifest),
                        "file_count": len(bundle.files),
                        "status": "downloaded; not admitted",
                        "fetches": client.fetch_metadata,
                    }
                    session.commit()
                    print(json.dumps(summary, indent=2))
                    return
            if args.command in {"run-case", "run-sec-case"}:
                if args.command == "run-sec-case":
                    if args.bundle:
                        bundle = SecFilingBundle.load(args.bundle)
                    package = sec_source_package(
                        bundle,
                        cutoff=args.cutoff,
                        entity_id=args.entity_id,
                        legal_name=args.entity_name
                        or (company["name"] if args.ticker else None),
                        as_of_date=args.as_of,
                    )
                else:
                    package = SourcePackage.load(args.package)
                result = runner.run_case(args.case_id, package=package)
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
