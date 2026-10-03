"""Evaluation summary report generator (task 9.9, Req 23.14).

Builds an end-to-end evaluation summary from the harness outputs and writes it
under a tmp reports dir, then asserts acceptance-target coverage:

* >= 1 temporal backtest
* >= 4 missing-data ablations
* >= 1 A/B comparison
* rolling-vs-expanding only if a learned model is used -> N/A here (no ML),
  marked N/A with justification.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.services.evaluation import (
    AblationVariant,
    AcceptanceCoverage,
    BacktestItem,
    EvaluationSummary,
    VariantOutcome,
    load_manifest,
    run_ablations,
    run_blinded_ab,
    run_expanding_window_backtest,
    score_extraction,
    write_report,
)
from app.services.evaluation.ab_testing import VariantOutput

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "evals" / "manifests" / "acme_fy2023_manifest.json"

SOURCES = [
    {"document_id": "doc-xbrl", "tags": {"xbrl"}},
    {"document_id": "doc-csv", "tags": {"csv", "xlsx"}},
    {"document_id": "doc-pdf", "tags": {"pdf"}},
    {"document_id": "doc-fuel", "tags": {"fuel"}},
    {"document_id": "doc-debt", "tags": {"pdf", "debt_maturity"}},
]


def _ablation_eval(variant: AblationVariant, surviving: list) -> VariantOutcome:
    tags = set()
    for s in surviving:
        tags |= set(s.get("tags", ()))
    produced = {"revenue": 1500.0, "net_income": 250.0, "total_assets": 4200.0,
                "total_debt": 750.0, "load_factor": 0.85}
    missing: list[str] = []
    escalations: list[str] = []
    human = False
    if "xbrl" not in tags:
        for f in ("revenue", "net_income", "total_assets"):
            produced.pop(f, None)
            missing.append(f)
        escalations.append("R-DATA-MISSING-CRITICAL-01")
        human = True
    if "xlsx" not in tags and "csv" not in tags:
        produced.pop("total_debt", None)
        missing.append("total_debt")
        escalations.append("R-DATA-MISSING-CRITICAL-01")
        human = True
    return VariantOutcome(
        produced_fields=produced,
        missing_fields=missing,
        available_metrics=["operating_margin", "net_debt_to_ebitda", "load_factor"],
        escalations=sorted(set(escalations)),
        total_claims=5,
        human_review_triggered=human,
    )


def test_evaluation_summary_report_written_with_acceptance_coverage(tmp_path: Path) -> None:
    manifest = load_manifest(MANIFEST_PATH)

    # --- extraction metrics ---
    extracted = {
        "revenue": {
            "value": 1500.0, "entity_id": "ACME-CORP", "currency": "USD",
            "scale": "millions", "period_end": "2023-12-31", "status": "verified",
            "source_document_ids": ["doc-xbrl-acme-fy2023"],
        }
    }
    extraction_score = score_extraction(extracted, manifest=manifest)

    # --- system: temporal backtest ---
    backtest = run_expanding_window_backtest(
        [
            BacktestItem("hist", datetime(2023, 6, 30, tzinfo=timezone.utc), "filing"),
            BacktestItem("future", datetime(2024, 6, 30, tzinfo=timezone.utc), "news"),
        ],
        [datetime(2024, 1, 15, tzinfo=timezone.utc)],
    )

    # --- missing-data robustness: ablations (>= 4 variants) ---
    ablation = run_ablations(SOURCES, _ablation_eval, manifest=manifest)

    # --- generative: blinded A/B ---
    ab = run_blinded_ab(
        VariantOutput(
            "model_good",
            ["fuel_price_exposure", "near_term_debt_maturity"],
            [{"grounded": True, "supported": True}],
        ),
        VariantOutput(
            "model_weak",
            ["fuel_price_exposure"],
            [{"grounded": False, "supported": False}],
        ),
        manifest=manifest,
    )

    summary = EvaluationSummary(
        case_id=manifest.case_id,
        manifest_version=manifest.manifest_version,
        extraction=extraction_score.as_dict(),
        deterministic={"facts_metrics_reproducible": True},
        generative=ab.as_dict(),
        system={"backtest": backtest.as_dict()},
        missing_data_robustness=ablation.as_dict(),
        acceptance=AcceptanceCoverage(
            temporal_backtests=1,
            ablations=len(ablation.variants),
            ab_comparisons=1,
        ),
    )

    path = write_report(summary, tmp_path / "reports")
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))

    # The five metric groups are present (Req 23.14).
    metrics = data["metrics"]
    assert set(metrics) == {
        "extraction",
        "deterministic",
        "generative",
        "system",
        "missing_data_robustness",
    }

    # Acceptance targets.
    targets = data["acceptance_targets"]
    assert targets["temporal_backtest"]["met"] is True
    assert targets["ablations"]["actual"] >= 4
    assert targets["ablations"]["met"] is True
    assert targets["ab_comparison"]["met"] is True

    # Rolling-vs-expanding is N/A because no learned model is used.
    rolling = targets["rolling_vs_expanding"]
    assert rolling["learned_model_used"] is False
    assert rolling["met"] is True
    assert "N/A" in rolling["note"]

    assert data["all_acceptance_targets_met"] is True
    assert data["ml_component_used"] is False


def test_report_committed_reports_dir_exists() -> None:
    """The committed evals/reports tree exists for produced artifacts."""
    assert (ROOT / "evals" / "reports").is_dir()
