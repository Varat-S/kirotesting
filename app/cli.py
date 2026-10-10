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
    parser.add_argument(
        "--analysis-mode",
        choices=["legacy", "agentic"],
        default="legacy",
        help="Downstream analysis path; agentic runs offline with a fake backend",
    )
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
    sec_run.add_argument(
        "--sector", help="Opt into sector benchmarking, e.g. medical_devices"
    )
    sec_run.add_argument(
        "--reference-workbook", type=Path, help="Industry benchmark workbook (.xlsx)"
    )
    sec_run.add_argument("--reported-sic", type=int)
    sec_run.add_argument("--sic-source")
    sec_run.add_argument("--sic-retrieved-at", type=date.fromisoformat)
    benchmark_import = commands.add_parser(
        "benchmark-import",
        help="Validate a benchmark workbook; write the normalized dataset and report",
    )
    benchmark_import.add_argument("workbook", type=Path)
    benchmark_import.add_argument("--sector", default="medical_devices")
    benchmark_import.add_argument("--out", type=Path, required=True)
    override = commands.add_parser(
        "classify-override",
        help="Record a human override of a case's sector classification",
    )
    override.add_argument("case_id")
    override.add_argument("--sector", default="medical_devices")
    override.add_argument("--industry", required=True)
    override.add_argument("--reviewer", required=True)
    override.add_argument("--rationale", required=True)
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
    if args.command == "benchmark-import":
        print(json.dumps(_benchmark_import(args), indent=2))
        return
    database = Path(args.database).resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    engine, factory = create_engine_and_session(f"sqlite:///{database.as_posix()}")
    init_db(engine)
    try:
        with factory() as session:
            runner = CreditMemoPipeline(
                session,
                data_root=database.parent,
                output_root=args.output,
                analysis_mode=args.analysis_mode,
            )
            if args.command == "classify-override":
                print(json.dumps(_classify_override(session, args), indent=2))
                session.commit()
                return
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
                        sector_benchmark=_sector_request(
                            args,
                            client if args.ticker else None,
                            company if args.ticker else None,
                            bundle,
                        ),
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
                if result.industry_benchmarking is not None:
                    bench = result.industry_benchmarking
                    summary["industry_benchmarking"] = {
                        "status": bench["status"],
                        "classification": bench["classification"]["status"],
                        "industry_label": bench["industry_label"],
                        "comparison_states": bench["summary"],
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




def _benchmark_import(args):
    """Validate a workbook and write its normalized dataset + report (no DB)."""
    from app.core.hashing import canonical_json
    from app.services.benchmarking.sector_config import load_sector_config_content
    from app.services.benchmarking.workbook import import_workbook

    config = load_sector_config_content(args.sector)
    imported = import_workbook(
        args.workbook.read_bytes(), config, filename=args.workbook.name
    )
    args.out.mkdir(parents=True, exist_ok=True)
    for name, payload in (
        ("normalized_dataset.json", imported.dataset),
        ("validation_report.json", imported.report),
    ):
        (args.out / name).write_text(
            canonical_json(payload) + "\n", encoding="utf-8", newline="\n"
        )
    report = imported.report
    return {
        "workbook": str(args.workbook),
        "source_sha256": imported.source_sha256,
        "dataset_hash": imported.dataset_hash,
        "industries": report["industries"],
        "data_vintage": report["vintage"]["data_as_of"],
        "status_counts": report["status_counts"],
        "issue_counts": report["issue_counts"],
        "eligible_values": report["acceptance"]["eligible_count"],
        "excluded_values": report["acceptance"]["excluded_count"],
        "outputs": [
            str(args.out / "normalized_dataset.json"),
            str(args.out / "validation_report.json"),
        ],
    }


def _sector_request(args, client, company, bundle):
    """Build the optional sector-benchmark request for an SEC run."""
    if not getattr(args, "sector", None):
        return None
    from app.services.pipeline.package import (
        CompanyIdentityInput,
        SectorBenchmarkInput,
    )

    if args.reference_workbook is None:
        raise ValueError("--sector requires --reference-workbook.")
    if client is not None:
        # Live run: identity and SIC are read from SEC EDGAR for this case.
        profile = client.company_profile(company["cik"])
        source = "SEC EDGAR submissions (" + profile["source_url"] + ")"
        identity = CompanyIdentityInput(
            cik=profile["cik"],
            ticker=company["ticker"],
            reported_sic=profile["sic"],
            sic_source=source,
            sic_retrieved_at=date.today(),
            identity_verification="sec_verified",
        )
    else:
        identity = CompanyIdentityInput(
            cik=bundle.cik,
            ticker=bundle.ticker,
            reported_sic=args.reported_sic,
            sic_source=args.sic_source,
            sic_retrieved_at=args.sic_retrieved_at,
            identity_verification="declared_unverified",
        )
    return SectorBenchmarkInput(
        sector_id=args.sector,
        reference_workbook=str(args.reference_workbook.resolve()),
        identity=identity,
    )


def _classify_override(session, args):
    """Append a human override of the case's accepted sector classification."""
    from app.core.config_registry import ConfigRegistry
    from app.models.orm import SectorClassificationRow
    from app.services.benchmarking.classification import SectorClassifier
    from app.services.benchmarking.sector_config import register_sector_config

    registry = ConfigRegistry(session)
    config = register_sector_config(registry, args.sector)
    latest = registry.latest(config.dataset_kind)
    if latest is None:
        raise ValueError("No reference dataset is registered; run the case first.")
    dataset = registry.get(config.dataset_kind, latest.version).content
    prior = session.scalars(
        select(SectorClassificationRow)
        .where(
            SectorClassificationRow.case_id == args.case_id,
            SectorClassificationRow.sector_id == args.sector,
            SectorClassificationRow.acceptance_state == "accepted",
        )
        .order_by(SectorClassificationRow.created_at.desc())
    ).first()
    if prior is None:
        raise ValueError("No sector classification exists for this case.")
    classifier = SectorClassifier(
        config, dataset, session=session, audit=AuditLog(session)
    )
    updated = classifier.override(
        prior.payload,
        industry_label=args.industry,
        reviewer=args.reviewer,
        rationale=args.rationale,
    )
    row = classifier.persist(updated, case_id=args.case_id, supersedes_id=prior.id)
    return {
        "case_id": args.case_id,
        "classification_id": row.id,
        "supersedes": prior.id,
        "status": row.status,
        "industry_label": row.industry_label,
        "next_step": (
            "Re-run the case; the override applies while the automatic "
            "classification inputs are unchanged."
        ),
    }


if __name__ == "__main__":
    main()
