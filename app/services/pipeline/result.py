from dataclasses import dataclass, field
from pathlib import Path

from app.models.orm import Snapshot
from app.schemas.snapshots import CanonicalEvidenceSnapshot
from app.services.metrics.engine import MetricResult


@dataclass
class PreparedCaseResult:
    """Deterministic processing completed, before the first LLM call."""

    evidence_snapshot: CanonicalEvidenceSnapshot
    metrics: dict[str, MetricResult]
    benchmarks: dict
    trends: dict
    escalations: list[dict]
    rejected_sources: list[dict]
    mapping_issues: list[dict]
    coverage: dict
    recorded_stages: list[dict] = field(default_factory=list)
    # Deterministic sector benchmark payload; None unless the package opted in.
    industry_benchmarking: dict | None = None

    def as_payload(self):
        evidence = self.evidence_snapshot.model_dump(mode="json")
        payload = {
            "stage": "before_llm",
            "case_id": evidence["case_id"],
            "evidence_version": evidence["snapshot_version"],
            "evidence": evidence,
            "metrics": {
                name: metric.as_payload() for name, metric in self.metrics.items()
            },
            "benchmarks": self.benchmarks,
            "trends": self.trends,
            "escalations": self.escalations,
            "rejected_sources": self.rejected_sources,
            "mapping_issues": self.mapping_issues,
            "coverage": self.coverage,
            "next_llm_step": "qualitative_extraction",
            "next_llm_input": {"canonical_evidence": evidence},
            "llm_calls": 0,
            "recorded_stages": self.recorded_stages,
        }
        if self.industry_benchmarking is not None:
            payload["industry_benchmarking"] = self.industry_benchmarking
        return payload


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
    industry_benchmarking: dict | None = None
