"""One-command local Business Overview Module 1 demo (Task 12).

Run the APPROVED real NVIDIA FY2026 10-K fixture through the complete Module 1
orchestration with SCRIPTED FakeLLMBackend responses, and export a human-readable
analytical review package (Markdown + standalone HTML) plus machine-readable
JSON. The command requires no knowledge of the internal service constructors.

    python -m app.demo --fixture nvda-2026 --output output/module1-demo

OFFLINE EVALUATION — SCRIPTED MODEL RESPONSES. NOT AN AUTONOMOUS CREDIT
ASSESSMENT OR LENDING RECOMMENDATION.

Real vs scripted (see the report's "Real versus scripted" box): the source
documents, hashes, cutoff, admitted/rejected set, narrative passages and all
deterministic governance (retrieval, premise grounding, materiality rubric,
completeness gate, RED reference governance, escalation) are REAL. The
hypothesis statements, enquiry queries, per-premise testing verdicts and RED
objections are SCRIPTED and anchored to real passages purely for demonstration.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from app.models.base import create_engine_and_session, init_db
from app.services.analysis.module1_demo import (
    FIXTURES,
    DemoError,
    DemoScenario,
    build_demo_scenario,
    run_demo_orchestration,
)
from app.services.analysis.module1_report import (
    build_report_model,
    render_html,
    render_markdown,
)
from app.services.analysis.module1_orchestrator import Stage

WARNING_BANNER = (
    "OFFLINE EVALUATION — SCRIPTED MODEL RESPONSES. "
    "NOT AN AUTONOMOUS CREDIT ASSESSMENT OR LENDING RECOMMENDATION."
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.demo",
        description=(
            "Run the approved NVIDIA fixture through Business Overview Module 1. "
            "Default --mode offline runs the complete orchestration with scripted "
            "FakeLLMBackend responses. --mode live runs the FIRST live-model "
            "evaluation of Stages 1-3 only (hypothesis generation + enquiry "
            "planning via a real provider; deterministic retrieval) and may incur "
            "API charges."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["offline", "live"],
        default="offline",
        help="offline (default; scripted, no network) or live (real LLM, Stages "
        "1-3 only; requires --acknowledge-costs and a configured provider).",
    )
    parser.add_argument(
        "--acknowledge-costs",
        action="store_true",
        help="REQUIRED for --mode live: explicit acknowledgement that live mode "
        "makes external API calls that may incur charges.",
    )
    parser.add_argument(
        "--max-model-calls",
        type=int,
        default=2,
        help="Live mode only: hard cap on model calls for the run (default 2 = "
        "hypothesis generation + enquiry planning). Exceeding it fails visibly.",
    )
    parser.add_argument(
        "--fixture",
        default="nvda-2026",
        choices=sorted(FIXTURES),
        help="Approved source fixture to run (default: nvda-2026).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/module1-demo"),
        help="Directory for the exported report package (default: output/module1-demo).",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=None,
        help="Optional SQLite path for the demo run (default: an ephemeral DB).",
    )
    args = parser.parse_args(argv)

    if args.mode == "live":
        return _run_live(args)

    out_dir: Path = args.output
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"ERROR: cannot create output directory {out_dir}: {exc}")
        return 2

    # Ephemeral DB unless the user asked to keep one. The demo never finalizes.
    if args.database is not None:
        args.database.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite:///{args.database.resolve().as_posix()}"
    else:
        db_url = "sqlite://"  # in-memory

    engine, factory = create_engine_and_session(db_url)
    init_db(engine)
    work_root = Path(tempfile.mkdtemp(prefix="module1-demo-"))
    try:
        with factory() as session:
            print(f"[1/5] Loading approved fixture {args.fixture!r} ...")
            try:
                scenario = build_demo_scenario(
                    args.fixture, session,
                    data_root=work_root / "data", output_root=work_root / "out",
                )
            except DemoError as exc:
                print(f"ERROR: {exc}")
                return 2

            print(
                f"[2/5] Real corpus built: {len(scenario.corpus.passages)} "
                f"narrative passage(s) from document "
                f"{scenario.primary_document_id!r}; "
                f"{len(scenario.admitted_sources)} admitted source(s), "
                f"{len(scenario.rejected_sources)} rejected."
            )
            print(
                "[3/5] Running the complete Module 1 orchestration "
                "(SCRIPTED FakeLLMBackend responses) ..."
            )
            run = run_demo_orchestration(scenario, session)

            print("[4/5] Stage statuses:")
            for stage in Stage:
                st = run.stage(stage)
                state = st.state.value if st is not None else "not_run"
                detail = (st.detail if st is not None else "stage did not run")
                print(f"        {stage.value:28s} {state:14s} {detail}")
            print(f"        {'DISPOSITION':28s} {run.disposition.value}")

            print("[5/5] Exporting review package ...")
            model = build_report_model(scenario, run)
            report_json = out_dir / "module1_review.json"
            report_md = out_dir / "module1_review.md"
            report_html = out_dir / "module1_review.html"
            report_json.write_text(
                json.dumps(model, indent=2, sort_keys=True), encoding="utf-8"
            )
            report_md.write_text(render_markdown(model), encoding="utf-8")
            report_html.write_text(render_html(model), encoding="utf-8")

            print()
            print(WARNING_BANNER)
            print()
            print("Review package written to:")
            print(f"  JSON:     {report_json}")
            print(f"  Markdown: {report_md}")
            print(f"  HTML:     {report_html}")
            print()
            print(
                f"Outcome: {run.disposition.value}. "
                "A blocked / insufficient-evidence result is a legitimate "
                "demonstration of the governance gate."
            )
            # The demo inspects outputs; it never records human approval.
            session.rollback()
    finally:
        engine.dispose()
    return 0


def _run_live(args) -> int:
    """Run the LIVE Stages-1-3 evaluation (real provider). Explicitly gated.

    Live mode NEVER falls back to FakeLLMBackend: if the provider is not
    configured, it fails with guidance rather than silently going offline."""
    from app.core.config import get_settings
    from app.services.analysis.module1_live import (
        LIVE_WARNING_BANNER,
        CallBudgetExceededError,
        LiveEvaluationConfig,
        LiveEvaluationError,
        run_live_evaluation,
    )
    from app.services.analysis.module1_live_report import (
        build_live_report_model,
        render_html as render_live_html,
        render_markdown as render_live_markdown,
    )
    from app.services.llm.providers import (
        ProviderNotConfiguredError,
        RealProviderBackend,
    )

    # Hard gate: explicit cost acknowledgement is mandatory for live mode.
    if not args.acknowledge_costs:
        print(
            "ERROR: live mode makes external API calls that may incur charges.\n"
            "Re-run with --acknowledge-costs to confirm you accept this.\n"
            "A ChatGPT subscription does NOT provide API access or credits; you "
            "need a provider API key with available credits."
        )
        return 2

    settings = get_settings()
    backend = RealProviderBackend(settings)
    try:
        # Validate provider configuration BEFORE any run; never fall back to Fake.
        backend.check_configuration()
    except ProviderNotConfiguredError as exc:
        print(f"ERROR: live provider not configured: {exc}")
        print("Set LLM_PROVIDER, LLM_MODEL and LLM_API_KEY in your environment "
              "(see .env.example). Offline mode remains available without a key.")
        return 2

    out_dir: Path = args.output
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"ERROR: cannot create output directory {out_dir}: {exc}")
        return 2

    if args.database is not None:
        args.database.parent.mkdir(parents=True, exist_ok=True)
        db_url = f"sqlite:///{args.database.resolve().as_posix()}"
    else:
        db_url = "sqlite://"

    engine, factory = create_engine_and_session(db_url)
    init_db(engine)
    work_root = Path(tempfile.mkdtemp(prefix="module1-live-"))
    cfg = LiveEvaluationConfig(max_model_calls=args.max_model_calls)
    print(LIVE_WARNING_BANNER)
    print()
    print(f"[live] provider={settings.llm_provider} model={settings.llm_model} "
          f"max_model_calls={cfg.max_model_calls}")
    try:
        with factory() as session:
            print(f"[1/4] Loading approved fixture {args.fixture!r} and real corpus ...")
            print("[2/4] Stage 1 (LIVE): autonomous hypothesis generation ...")
            print("[3/4] Stage 2 (LIVE): autonomous enquiry planning ...")
            print("[4/4] Stage 3 (deterministic): evidence retrieval ...")
            try:
                result = run_live_evaluation(
                    args.fixture, session, backend=backend,
                    provider=settings.llm_provider,
                    data_root=work_root / "data", output_root=work_root / "out",
                    config=cfg,
                )
            except CallBudgetExceededError as exc:
                print(f"ERROR (budget): {exc}")
                return 3
            except LiveEvaluationError as exc:
                print(f"ERROR (live evaluation): {exc}")
                return 3

            model = build_live_report_model(result)
            rjson = out_dir / "module1_live_review.json"
            rmd = out_dir / "module1_live_review.md"
            rhtml = out_dir / "module1_live_review.html"
            rjson.write_text(json.dumps(model, indent=2, sort_keys=True),
                             encoding="utf-8")
            rmd.write_text(render_live_markdown(model), encoding="utf-8")
            rhtml.write_text(render_live_html(model), encoding="utf-8")

            print()
            print(f"Model calls: {result.model_calls}; token usage: "
                  f"{result.usage_totals}")
            print(f"Hypotheses generated: {len(result.hypotheses.hypotheses)}; "
                  f"enquiries planned: {len(result.enquiry_plan.enquiries)}; "
                  f"dimensions unassessed: {len(result.unassessed_dimensions)}")
            print()
            print("Live review package written to:")
            print(f"  JSON:     {rjson}")
            print(f"  Markdown: {rmd}")
            print(f"  HTML:     {rhtml}")
            session.rollback()
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
