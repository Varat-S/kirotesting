"""Evaluation summary report generator (task 9.9, Req 23.14).

Aggregates the harness outputs into a single structured summary and writes it to
``evals/reports`` (tests write to a tmp path). The report groups metrics into:

* **extraction** -- golden extraction scores (field/numeric/citation accuracy,
  required-field recall by field type);
* **deterministic** -- reproducibility of facts/metrics/rule triggers;
* **generative** -- A/B comparison outcomes (objective, blinded);
* **system** -- temporal backtest + re-run-mode coverage;
* **missing_data_robustness** -- ablation scores + ``performance_drop``.

It also records ACCEPTANCE-TARGET coverage (Req 23.14):

* >=1 temporal backtest
* >=1 rolling-vs-expanding comparison -- only if a LEARNED model is used (this
  PoC has none, so the item is marked N/A with justification);
* >=4 missing-data ablations
* >=1 A/B comparison
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.evaluation.stats import ML_COMPONENT_USED


@dataclass
class AcceptanceCoverage:
    """Acceptance-target coverage per Req 23.14."""

    temporal_backtests: int = 0
    ablations: int = 0
    ab_comparisons: int = 0
    rolling_vs_expanding_comparisons: int = 0
    learned_model_used: bool = ML_COMPONENT_USED

    def status(self) -> dict[str, Any]:
        rolling_required = self.learned_model_used
        return {
            "temporal_backtest": {
                "required": 1,
                "actual": self.temporal_backtests,
                "met": self.temporal_backtests >= 1,
            },
            "ablations": {
                "required": 4,
                "actual": self.ablations,
                "met": self.ablations >= 4,
            },
            "ab_comparison": {
                "required": 1,
                "actual": self.ab_comparisons,
                "met": self.ab_comparisons >= 1,
            },
            "rolling_vs_expanding": {
                "required_if_learned_model": True,
                "learned_model_used": self.learned_model_used,
                "actual": self.rolling_vs_expanding_comparisons,
                "met": (not rolling_required)
                or self.rolling_vs_expanding_comparisons >= 1,
                "note": (
                    "N/A: no learned lower-level ML model is used in this PoC "
                    "(Req 22, 23.7/23.12). Rolling-vs-expanding comparison is not "
                    "applicable; expanding-window backtest is provided."
                )
                if not self.learned_model_used
                else "Rolling-vs-expanding comparison required for the learned model.",
            },
        }

    def all_met(self) -> bool:
        return all(item["met"] for item in self.status().values())


@dataclass
class EvaluationSummary:
    """The structured evaluation summary report (Req 23.14)."""

    case_id: str
    manifest_version: str
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    extraction: dict[str, Any] = field(default_factory=dict)
    deterministic: dict[str, Any] = field(default_factory=dict)
    generative: dict[str, Any] = field(default_factory=dict)
    system: dict[str, Any] = field(default_factory=dict)
    missing_data_robustness: dict[str, Any] = field(default_factory=dict)
    acceptance: AcceptanceCoverage = field(default_factory=AcceptanceCoverage)

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "manifest_version": self.manifest_version,
            "generated_at": self.generated_at,
            "ml_component_used": self.acceptance.learned_model_used,
            "metrics": {
                "extraction": self.extraction,
                "deterministic": self.deterministic,
                "generative": self.generative,
                "system": self.system,
                "missing_data_robustness": self.missing_data_robustness,
            },
            "acceptance_targets": self.acceptance.status(),
            "all_acceptance_targets_met": self.acceptance.all_met(),
        }


def write_report(summary: EvaluationSummary, out_dir: str | Path) -> Path:
    """Serialize ``summary`` to ``<out_dir>/evaluation_summary_<case>.json``.

    Returns the written path. ``out_dir`` is created if absent (tests pass a
    tmp_path; the committed ``evals/reports`` tree holds produced artifacts).
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"evaluation_summary_{summary.case_id}.json"
    path.write_text(
        json.dumps(summary.as_dict(), indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return path
