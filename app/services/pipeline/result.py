from dataclasses import dataclass, field
from pathlib import Path

from app.models.orm import Snapshot
from app.schemas.snapshots import CanonicalEvidenceSnapshot
from app.services.metrics.engine import MetricResult


@dataclass
class PipelineResult:
    evidence_snapshot: CanonicalEvidenceSnapshot
    evidence_row: Snapshot
    draft_snapshot: Snapshot
    metrics: dict[str, MetricResult]
    benchmarks: dict
    trends: dict
    escalations: list[dict]
    admitted_source_hashes: list[str]
    rejected_sources: list[dict]
    model_runs: list[dict]
    mapping_issues: list[dict]
    memo_json: dict
    output_paths: dict[str, Path] = field(default_factory=dict)
    final_snapshot: Snapshot | None = None
    evaluation_artifacts: dict = field(default_factory=dict)
