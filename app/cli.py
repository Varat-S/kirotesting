"""Local offline runner and explicit review/finalization commands."""

import argparse
import json
import sys
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
    bo_run = commands.add_parser(
        "run-bo-case",
        help="Run the Business Overview module only (no FO/SO/credit agents)",
    )
    bo_run.add_argument("case_id")
    bo_source = bo_run.add_mutually_exclusive_group(required=True)
    bo_source.add_argument("--package", type=Path)
    bo_source.add_argument(
        "--bundle", type=Path, help="A local SEC bundle manifest (bundle.json)"
    )
    bo_run.add_argument(
        "--cutoff", type=lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")),
        help="Evidence cut-off (required with --bundle)",
    )
    bo_run.add_argument("--entity-id")
    bo_run.add_argument("--entity-name")
    bo_run.add_argument(
        "--manifest", type=Path,
        help="Reviewer-controlled evaluation manifest; writes evaluation.json",
    )
    bo_backend = bo_run.add_mutually_exclusive_group()
    bo_backend.add_argument(
        "--responses", type=Path,
        help="Scripted offline responses (JSON); the run is an offline simulation",
    )
    bo_backend.add_argument(
        "--live", action="store_true",
        help="Use the configured live provider (LLM_PROVIDER); incurs model cost",
    )
    bo_run.add_argument("--pdf", action="store_true")
    bo_run.add_argument(
        "--roster", choices=("v2", "v3"), default="v2",
        help=("BO agent roster. 'v2' is the legacy six-specialist roster (default, "
              "unchanged). 'v3' is the four-module eight-primary roster with the "
              "stronger module-completeness and Agent 6 gates. The active roster is "
              "always reported in the run summary."),
    )
    bo_run.add_argument(
        "--enable-m4-debt-specialist", action="store_true",
        help=("Also run the Module 4 debt-structure / refinancing specialist "
              "inside the orchestrated BO run (v3 roster only), alongside "
              "m4_strategy_capital, so both feed one governed Module 4 output. "
              "Default off for backward compatibility. The effective flag is "
              "recorded in the run metadata and reported in the summary."),
    )
    bo_spec = commands.add_parser(
        "run-bo-specialists",
        help="Run the six BO specialists and validators only; write the demo reports",
    )
    bo_spec.add_argument("case_id")
    bo_spec_source = bo_spec.add_mutually_exclusive_group(required=True)
    bo_spec_source.add_argument("--package", type=Path)
    bo_spec_source.add_argument("--bundle", type=Path)
    bo_spec.add_argument(
        "--cutoff", type=lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")))
    bo_spec.add_argument("--entity-id")
    bo_spec.add_argument("--entity-name")
    bo_spec_backend = bo_spec.add_mutually_exclusive_group(required=True)
    bo_spec_backend.add_argument("--responses", type=Path,
                                 help="Scripted offline responses (JSON)")
    bo_spec_backend.add_argument("--replay", type=Path,
                                 help="Replay a previous run's recorded responses")
    bo_spec_backend.add_argument("--live", action="store_true",
                                 help="Use the configured live provider (model cost)")
    bo_spec.add_argument("--coverage", type=Path,
                         help="Case-specific coverage tests (JSON)")
    bo_spec.add_argument("--baseline", type=Path,
                         help="Previous run's specialists_calls.json to benchmark against")
    bo_spec.add_argument("--price-in", type=float,
                         help="Model price per million input tokens (optional)")
    bo_spec.add_argument("--price-out", type=float,
                         help="Model price per million output tokens (optional)")
    bo_spec.add_argument("--roster", choices=("v2", "v3"), default="v2",
                         help="BO specialist roster to execute (default: v2)")
    bo_spec.add_argument(
        "--transaction-borrower-entity-id",
        help="Validated case-spec entity id for the proposed facility obligor; "
             "omit when transaction identity is unavailable")
    bo_review = commands.add_parser(
        "bo-review",
        help="Record your explicit decision on a pending Business Overview handoff",
    )
    bo_review.add_argument("handoff_id")
    bo_review.add_argument("--reviewer", required=True)
    bo_review.add_argument("--reason", required=True)
    bo_review.add_argument("--reject", action="store_true")
    bo_review.add_argument(
        "--acknowledge-unresolved", action="store_true",
        help="Accept although challenger findings remain open (they stay recorded)",
    )
    fo_run = commands.add_parser(
        "run-fo-case",
        help="Run the deterministic Financial Overview over a filing folder in edgar_filings/",
    )
    fo_run.add_argument(
        "case_id", nargs="?",
        help="Case id (default: TICKER_FY<year> from the filing's cover page)",
    )
    fo_run.add_argument(
        "--filing", required=True,
        help='Folder in edgar_filings/, e.g. "HCA 10-K FY2025" (or "edgar_filings/HCA 10-K FY2025")',
    )
    fo_agent = fo_run.add_mutually_exclusive_group(required=True)
    fo_agent.add_argument(
        "--live", action="store_true",
        help="Run the Pre-check Agent (going concern, covenants, acquisitions and divestitures, prepaid model) and "
             "the Trigger Agent (DSCR T2, when CapEx is above 1.5x depreciation; DSCR T3, when cash taxes paid differ "
             "from current tax expense by more than 5%% of CFADS; DSCR T1, when DSCR scores 9 or more; ICR T2, when ICR's interest is from a net or operating tag; ICR T3, when ICR's EBIT is operating income; ICR T1, when ICR scores 8.5 or more; Debt/EBITDAR T1, when net one-offs are above 5%% of EBITDAR or EBITDAR is zero or negative; Debt/EBITDAR T2, when the gross and net leverage scores are 2 or more apart; Debt/EBITDAR T3, when the leverage score with leases is 2 or more worse than without; Debt/EBITDAR T4, when total debt is from a fallback (total tag skipped or finance leases added); RCF/Debt T1, when RCF / debt scores 9 or more or is negative; RCF/Debt T2, when tagged one-off inflows are above 5%% of FFO or FFO is up 25%% or more and over 10 points faster than revenue; RCF/Debt T3, when NCI distributions aren't tagged under a standard concept and NCI is material; RCF/Debt T4, when NCI distributions come from PaymentsToMinorityShareholders; EBITDAR Margin T1, when Debt/EBITDAR T1 removed one-off items; EBITDAR Margin T2, when the margin scores 9 or more or 2 or less and a segment with 30%% or more of revenue has a margin 5 points or more from all segments; DSO T1, when DSO moves by 5 days or more either way; Liquidity T1, when the revolver availability at the year-end isn't tagged or found in the 10-K text; Liquidity T2, when the liquidity coverage or refinancing score is 9 or more after T1; Liquidity T3, when the largest year of maturities in years 2-5 is 25%% or more of total debt) on the configured live provider (model cost). "
             "The agent is required: give --live or --responses.",
    )
    fo_agent.add_argument(
        "--responses", type=Path,
        help='Scripted agent responses (JSON) for an offline run: one going-concern response, or '
             '{"going_concern": ..., "covenants": ..., "acquisitions": ..., "prepaid": ..., "dscr_t1": ..., '
             '"dscr_t2": ..., "dscr_t3": ..., "icr_t1": ..., "icr_t2": ..., "icr_t3": ..., "leverage_t1": ..., "leverage_t2": ..., "leverage_t3": ..., "leverage_t4": ..., "rcf_t1": ..., "rcf_t2": ..., "rcf_t3": ..., "rcf_t4": ..., "margin_t1": ..., "margin_t2": ..., "dso_t1": ..., "liquidity_t1": ..., "liquidity_t2": ..., "liquidity_t3": ...} ("dscr_t1" only needed when DSCR scores 9 or more, "dscr_t2" when '
             'CapEx is above 1.5x depreciation, "dscr_t3" when the cash-tax gap is above 5%% of CFADS, '
             '"icr_t1" when ICR scores 8.5 or more, "icr_t2" when ICR\'s interest is from a net or operating '
             'tag, "icr_t3" when ICR\'s EBIT is operating income, "leverage_t1" when net one-offs are above 5%% of '
             'EBITDAR or EBITDAR is zero or negative, "leverage_t2" when the gross and net leverage scores are 2 '
             'or more apart, "leverage_t3" when the leverage score with leases is 2 or more worse than without, '
             '"leverage_t4" when total debt is from a fallback, "rcf_t1" when RCF / debt scores 9 or more or is '
             'negative, "rcf_t2" when tagged one-off inflows are above 5%% of FFO or FFO is up 25%% or more and over '
             '10 points faster than revenue, "rcf_t3" when NCI distributions aren\'t tagged under a standard '
             'concept and NCI is material, "rcf_t4" when NCI distributions come from '
             'PaymentsToMinorityShareholders, "margin_t1" when Debt/EBITDAR T1 removed one-off items, "margin_t2" '
             'when the EBITDAR margin scores 9 or more or 2 or less and a segment with 30%% or more of revenue has a '
             'margin 5 points or more from all segments, "dso_t1" when DSO moves by 5 days or more either way, '
             '"liquidity_t1" when the revolver availability at the year-end isn\'t tagged or found in the 10-K text, '
             '"liquidity_t2" when the liquidity coverage or refinancing score is 9 or more after liquidity_t1, '
             '"liquidity_t3" when the largest year of maturities in years 2-5 is 25%% or more of total debt)',
    )
    fo_run.add_argument(
        "--model",
        help="With --live: the Vertex text-analysis model for the Pre-check Agent, e.g. gemini-3.5-flash (default: "
             "VERTEX_MODEL_NARROW). Must be one of app/services/fo/models.py's text-analysis candidates.",
    )
    fo_review = commands.add_parser(
        "fo-review",
        help="Record your continue / stop decision on an FO run escalated by a going-concern fail",
    )
    fo_review.add_argument("run_id")
    fo_review.add_argument("--decision", required=True, choices=("continue", "stop", "use_verified"),
                           help="continue / stop on a going-concern fail; use_verified keeps the verified result "
                                "when the agent read FAIL but its evidence didn't hold up")
    fo_review.add_argument("--reviewer", required=True)
    fo_review.add_argument("--reason", required=True)
    fo_override = commands.add_parser(
        "fo-override",
        help="Accept or override the score of a measure an FO run escalated (liquidity after a covenant breach; dso "
             "or leverage after an unresolved acquisition or divestiture; dscr after its trigger T1, T2 or T3 was unresolved; icr after ICR T1, T2 or T3 was unresolved)",
    )
    fo_override.add_argument("run_id")
    fo_override.add_argument("--measure", required=True,
                             help='The escalated measure: "liquidity", "dso", "leverage", "dscr", "icr" or "rcf"')
    fo_override.add_argument("--decision", required=True, choices=("accept", "override"))
    fo_override.add_argument("--score", type=float, help="Your score, 1-10 (required to override)")
    fo_override.add_argument("--reviewer", required=True)
    fo_override.add_argument("--reason", required=True)
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
    rebuild = commands.add_parser("run-m3", help="Run corporate conversion and/or filing-only management assessment")
    rebuild.add_argument("rebuild_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command == "run-m3":
        from app.services.bo.m3_rebuild.__main__ import main as rebuild_main
        raise SystemExit(rebuild_main(args.rebuild_args))
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
            if args.command == "run-bo-case":
                print(json.dumps(_run_bo_case(session, database, args), indent=2))
                session.commit()
                return
            if args.command == "run-fo-case":
                print(json.dumps(_run_fo_case(session, args), indent=2))
                session.commit()
                return
            if args.command == "fo-review":
                from app.services.fo.review import review_run

                print(json.dumps(review_run(
                    session, args.run_id, decision=args.decision, reviewer=args.reviewer,
                    reason=args.reason, output_root=args.output,
                ), indent=2))
                session.commit()
                return
            if args.command == "fo-override":
                from app.services.fo.review import override_measure

                print(json.dumps(override_measure(
                    session, args.run_id, measure=args.measure, decision=args.decision, score=args.score,
                    reviewer=args.reviewer, reason=args.reason, output_root=args.output,
                ), indent=2))
                session.commit()
                return
            if args.command == "run-bo-specialists":
                print(json.dumps(_run_bo_specialists(session, database, args), indent=2))
                session.commit()
                return
            if args.command == "bo-review":
                from app.services.bo.handoff import review_handoff

                print(json.dumps(review_handoff(
                    session, args.handoff_id, reviewer=args.reviewer,
                    reason=args.reason,
                    decision="reject" if args.reject else "accept",
                    acknowledge_unresolved=args.acknowledge_unresolved,
                ), indent=2))
                session.commit()
                return
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




def _live_backend():
    settings = get_settings()
    if settings.llm_provider == "vertex":
        from app.services.llm.providers_vertex import VertexProviderBackend

        backend = VertexProviderBackend(settings)
    else:
        from app.services.llm.providers import RealProviderBackend

        backend = RealProviderBackend(settings)
    backend.check_configuration()
    return backend


def _run_bo_specialists(session, database, args):
    """Six specialists + validators + demonstration reports (no memo, no score)."""
    from app.services.bo import benchmark
    from app.services.bo.demo import run_demo
    from app.services.bo.offline import ScriptedBoBackend
    from app.services.bo.sectors import load_case_checks

    if args.live:
        backend = _live_backend()
    elif args.replay:
        backend = benchmark.replay_backend(benchmark.load_baseline_calls(args.replay))
    else:
        backend = ScriptedBoBackend.from_file(args.responses)
    if args.bundle:
        if args.cutoff is None:
            raise ValueError("--bundle requires --cutoff.")
        package = sec_source_package(
            SecFilingBundle.load(args.bundle), cutoff=args.cutoff,
            entity_id=args.entity_id, legal_name=args.entity_name)
    else:
        package = SourcePackage.load(args.package)
    prices = None
    if args.price_in is not None and args.price_out is not None:
        prices = {"input_per_million": args.price_in,
                  "output_per_million": args.price_out, "currency": "USD"}
    result = run_demo(
        session, args.case_id, package=package, backend=backend,
        data_root=database.parent, output_root=args.output,
        coverage_checks=load_case_checks(args.coverage) if args.coverage else None,
        baseline_calls=args.baseline, prices=prices, bo_roster=args.roster,
        transaction_borrower_entity_id=args.transaction_borrower_entity_id,
    )
    payload = result["payload"]
    metrics = payload["metrics"]
    return {
        "case_id": args.case_id,
        "analysis_run_id": payload["meta"]["analysis_run_id"],
        "scope": payload["meta"]["scope"],
        "execution_mode": payload["meta"]["execution_mode"],
        "models": payload["meta"]["models"],
        "specialists": {a["code"]: a["status"] for a in payload["agents"]},
        "hypotheses": {"generated": metrics["hypotheses_generated"],
                       "usable": metrics["hypotheses_usable"],
                       **metrics["by_admissibility"]},
        "unsupported_inference_flags": metrics["unsupported_inference_flags"],
        "bo_fo_boundary_violations": metrics["bo_fo_boundary_violations"],
        "citations": {"failed": metrics["citation_verification_failures"],
                      "recovered": metrics["citations_recovered"]},
        "coverage": metrics["coverage_by_status"],
        "hypotheses_using_indicators": metrics["hypotheses_using_indicators"],
        "parameters": {"accepted": metrics["parameters_accepted"],
                       "held_back": metrics["parameters_held_back"]},
        "tokens": metrics["tokens"],
        "outputs": {k: str(v) for k, v in result["paths"].items()},
    }




def _run_fo_case(session, args):
    """Run the Financial Overview (going-concern pre-check, then the measures) and summarise the recorded run."""
    from app.services.fo import loader, precheck
    from app.services.fo.runner import FinancialOverviewRunner

    filing = Path(args.filing)
    name = filing.name
    if len(filing.parts) > 1 and filing.resolve().parent != Path(loader.FILINGS_ROOT).resolve():
        raise ValueError(f"--filing must be a folder directly inside {loader.FILINGS_ROOT}.")
    if getattr(args, "live", False):
        backend, mode = precheck.live_backend(getattr(args, "model", None)), "live"
    elif getattr(args, "model", None):
        raise ValueError("--model applies to --live runs only.")
    else:
        backend, mode = precheck.scripted_backend(args.responses), "scripted"
    steps = []

    def show_step(step):  # progress on stderr, so a failed run shows where it stopped
        steps.append(step)
        print(f"[fo] step {len(steps)}: {step}", file=sys.stderr, flush=True)

    try:
        result = FinancialOverviewRunner(session, output_root=args.output, agent_backend=backend,
                                         agent_mode=mode, check_stop=show_step).run_filing(name, args.case_id)
    except loader.FilingFolderError as exc:
        available = [f["name"] for f in loader.list_filings()]
        raise ValueError(f"{exc} Available folders: {available or 'none'}.") from exc
    return result.summary()


def _run_bo_case(session, database, args):
    """Run the BO-only path and summarise the recorded outcome."""
    from app.services.bo.runner import BusinessOverviewRunner

    backend = None
    if args.responses:
        from app.services.bo.offline import ScriptedBoBackend

        backend = ScriptedBoBackend.from_file(args.responses)
    elif args.live:
        backend = _live_backend()
    if args.bundle:
        if args.cutoff is None:
            raise ValueError("--bundle requires --cutoff.")
        package = sec_source_package(
            SecFilingBundle.load(args.bundle), cutoff=args.cutoff,
            entity_id=args.entity_id, legal_name=args.entity_name,
        )
    else:
        package = SourcePackage.load(args.package)
    manifest = None
    if args.manifest:
        from app.services.bo.evaluation import load_manifest

        manifest = load_manifest(args.manifest)
    roster = getattr(args, "roster", "v2")
    enable_m4_debt = bool(getattr(args, "enable_m4_debt_specialist", False))
    result = BusinessOverviewRunner(
        session, backend=backend, data_root=database.parent, output_root=args.output,
        bo_roster=roster, enable_m4_debt_specialist=enable_m4_debt,
    ).run_case(
        args.case_id, package=package, produce_pdf=args.pdf, manifest=manifest,
    )
    evaluation = result.evaluation
    return {
        **({"evaluation": {
            "ground_truth_status": evaluation["ground_truth_status"],
            "citation_validity": evaluation["factual_correctness"]["citation_validity"],
            "trap_checks_passed": evaluation["analytical_reasoning"]["trap_checks_passed"],
            "trap_checks": len(evaluation["analytical_reasoning"]["trap_checks"]),
        }} if evaluation else {}),
        "case_id": result.case_id,
        "analysis_run_id": result.analysis_run_id,
        "scope": "business_overview_only",
        "execution_mode": result.execution_mode,
        # A-1: report the roster that actually ran, taken from the recorded
        # registry version, so a run can never be silently misattributed to a
        # different architecture than the one that produced it.
        "bo_roster": roster,
        "agent_registry_version": (result.audit or {}).get("registry", {}).get("version"),
        # Effective Module 4 debt-specialist flag: what was requested, and whether
        # it actually took effect (only on the v3 roster). Reported from the
        # recorded run metadata, not just the CLI arg.
        "m4_debt_specialist_requested": enable_m4_debt,
        "m4_debt_specialist_enabled": bool(
            result.record.get("m4_debt_specialist_enabled",
                              enable_m4_debt and roster == "v3")),
        "conclusion_status": result.conclusion_status,
        "human_review_status": "pending",
        "handoff_id": result.handoff["handoff_id"],
        "business_risk_score": {
            "status": result.memo["score"]["status"],
            "band": result.memo["score"]["band"],
        },
        "agents": {a: o["status"] for a, o in result.record["agents"].items()},
        "ai_calls": result.audit["execution"]["ai_calls"],
        "challenge_rounds": [
            {"round": r["round"], "outcome": r["outcome"],
             "findings": len(r["findings"])}
            for r in result.record["challenge_rounds"]
        ],
        "remediation_performed": result.record["remediation"] is not None,
        "outputs": {k: str(v) for k, v in result.output_paths.items()},
    }


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
