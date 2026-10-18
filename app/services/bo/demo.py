"""One-command BO specialists demonstration (selected roster + validators + reports).

Runs phases 1-3 of the Business Overview for a case, writes the structured JSON
and the three human-readable reports, and optionally benchmarks the run against
a previous one. The deterministic foundation is prepared once; a baseline, when
given, is replayed through the current validators without calling a model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.models.orm import HypothesisRecordRow
from app.services.bo import benchmark
from app.services.bo.demo_report import (
    build_payload,
    render_benchmark,
    write_reports,
)
from app.services.bo.reporting import execution_mode
from app.services.bo.runner import BusinessOverviewRunner
from app.services.bo.transcript import RecordingBackend
from app.services.pipeline.runner import CreditMemoPipeline


def run_demo(
    session,
    case_id: str,
    *,
    package,
    backend,
    data_root,
    output_root,
    coverage_checks=None,
    baseline_calls: str | Path | None = None,
    baseline_label: str | None = None,
    prices: dict[str, float] | None = None,
    sector="auto",
    prepared=None,
    bo_roster="v2",
) -> dict[str, Any]:
    output_root = Path(output_root)
    if prepared is None:
        prepared = CreditMemoPipeline(
            session, data_root=data_root, output_root=output_root,
        ).prepare_case(case_id, package=package)

    columns: list[tuple[str, str, dict | None]] = []
    notes: list[str] = []
    replayed = None
    if baseline_calls is not None:
        calls = benchmark.load_baseline_calls(baseline_calls)
        label = baseline_label or Path(baseline_calls).parent.name
        recorded = [r.payload for r in session.execute(
            select(HypothesisRecordRow)
            .where(HypothesisRecordRow.analysis_run_id == label)
            .where(HypothesisRecordRow.acceptance_state == "accepted")).scalars()]
        columns.append((
            f"Baseline {label}", "as its own validator recorded it",
            benchmark.recorded_baseline_metrics(calls, recorded or None)))
        replay = RecordingBackend(benchmark.replay_backend(calls))
        replay_run = BusinessOverviewRunner(
            session, backend=replay, data_root=data_root, output_root=output_root,
            coverage_checks=coverage_checks, sector=sector, bo_roster=bo_roster,
        ).run_specialists(case_id, prepared=prepared)
        replayed = build_payload(replay_run, calls=replay.calls,
                                 execution_mode="offline_simulation")
        # The replay made no model call; carry over the baseline's own usage.
        replayed["metrics"]["tokens"] = columns[0][2]["tokens"]
        replayed["metrics"]["model_calls"] = 0
        columns.append((
            "Baseline, re-validated",
            "same model responses replayed through the current validators",
            replayed["metrics"]))
        notes += [
            "The baseline column shows what the earlier validator accepted. The "
            "re-validated column replays those exact model responses (no model "
            "call) under the current rules, which separates the effect of the "
            "validator changes from the effect of the prompt, retrieval and "
            "indicator changes.",
            "The baseline responses were produced before coverage questions "
            "existed, so they could not cite any; their coverage is therefore "
            "counted as unresolved, not as a model failure.",
            "Baseline rows marked 'not checked' are measures the earlier "
            "validator did not compute.",
        ]

    recorder = RecordingBackend(backend)
    run = BusinessOverviewRunner(
        session, backend=recorder, data_root=data_root, output_root=output_root,
        coverage_checks=coverage_checks, sector=sector, bo_roster=bo_roster,
    ).run_specialists(case_id, prepared=prepared)
    mode = execution_mode(backend)
    payload = build_payload(run, calls=recorder.calls, execution_mode=mode, prices=prices)
    folder = output_root / "bo" / case_id / run.analysis_run_id
    bench_html = None
    if columns:
        columns.append(("This run", f"{mode.replace('_', ' ')}, current prompts, "
                        "retrieval and validators", payload["metrics"]))
        notes.append("One run per configuration: differences include ordinary "
                     "run-to-run variation of the model and are not a statistical "
                     "result.")
        checks = benchmark.known_problem_checks(replayed, payload)
        payload["benchmark"] = {
            "columns": [{"title": t, "description": d, "metrics": m}
                        for t, d, m in columns],
            "known_problem_checks": checks, "notes": notes}
        bench_html = render_benchmark(payload, columns, notes, checks)
    paths = write_reports(payload, folder, benchmark=bench_html)
    if replayed is not None:
        replay_path = folder / "baseline_revalidated.json"
        replay_path.write_text(json.dumps(replayed, indent=1, default=str),
                               encoding="utf-8", newline="\n")
        paths["baseline_revalidated.json"] = replay_path
    return {"payload": payload, "paths": paths, "folder": folder}
