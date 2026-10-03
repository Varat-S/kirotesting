"""Evaluation harness (Milestone 9).

A GroundTruthManifest-grounded scoring harness that ORCHESTRATES the existing
pipeline services. Core guarantees enforced across this package:

* No scored metric without a declared GroundTruthManifest (Req 23.5).
* Contemporaneous ground truth is strictly separated from future outcomes;
  future outcomes never enter inputs at T or contemporaneous accuracy scoring
  (Req 23.6).
* Missing-data ablations demonstrate graceful degradation and never invent
  values; ``performance_drop`` is reported (Req 23.8).
* Prompt/model A/B testing is blinded and objective-metric-based (Req 23.11).
* Two visibly distinguished historical re-run modes (Req 19.7).
* Temporal backtesting rejects future-dated items across all data types
  (Req 20.4, 23.7).
* End-to-end deterministic reproducibility under pinned versions (Req 23.13).
* Statistical utilities exist for a learned component but are NOT wired into the
  pipeline (no ML for appearance; Req 22, 23.12).
"""

from app.services.evaluation.ab_testing import (
    ABResult,
    VariantOutput,
    run_blinded_ab,
)
from app.services.evaluation.ablation import (
    AblationReport,
    AblationVariant,
    VariantOutcome,
    apply_variant,
    run_ablations,
)
from app.services.evaluation.backtest import (
    BacktestItem,
    BacktestResult,
    RerunController,
    RerunMode,
    reject_future_item,
    run_expanding_window_backtest,
)
from app.services.evaluation.extraction_scorer import (
    ExtractionScore,
    NoDeclaredManifestError,
    score_extraction,
)
from app.services.evaluation.manifest import (
    GroundTruthManifest,
    ManifestError,
    load_manifest,
    load_manifest_dict,
)
from app.services.evaluation.report import (
    AcceptanceCoverage,
    EvaluationSummary,
    write_report,
)
from app.services.evaluation.reproducibility import (
    PinnedVersions,
    RunArtifacts,
    assert_reproducible,
)
from app.services.evaluation.separation import (
    FutureLeakageError,
    assert_no_future_leakage_in_inputs,
    score_contemporaneous_accuracy,
    study_predictive_usefulness,
)

__all__ = [
    "GroundTruthManifest",
    "ManifestError",
    "load_manifest",
    "load_manifest_dict",
    "ExtractionScore",
    "NoDeclaredManifestError",
    "score_extraction",
    "BacktestItem",
    "BacktestResult",
    "RerunController",
    "RerunMode",
    "reject_future_item",
    "run_expanding_window_backtest",
    "AblationReport",
    "AblationVariant",
    "VariantOutcome",
    "apply_variant",
    "run_ablations",
    "ABResult",
    "VariantOutput",
    "run_blinded_ab",
    "FutureLeakageError",
    "assert_no_future_leakage_in_inputs",
    "score_contemporaneous_accuracy",
    "study_predictive_usefulness",
    "PinnedVersions",
    "RunArtifacts",
    "assert_reproducible",
    "AcceptanceCoverage",
    "EvaluationSummary",
    "write_report",
]
