"""Read-only agentic analysis workbench views (Milestone 19 / Req 21).

Extends the inspection/workbench surface with read-only views over the agentic
artifacts, scoped to an explicit ``analysis_run_id`` (never inferred from the
snapshot version alone — Remediation 1). Every method is a pure READ: assembling
a view NEVER mutates the database.

Surfaced sections (Req 21.1): analysis runs, parameters + provenance,
deterministic formulas, topic scores, obligor/facility scores, evidence quality,
agent runs, routed evidence packets, prompt/model identity, token usage,
challenges, reruns (lineage), candidate structures, policy failures, base/
downside structure results, topic/cross-topic conclusions, exceptions, approvals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.orm import (
    AgenticAnalysisRun,
    AgentRunRow,
    CandidateFeasibilityRow,
    CandidateStructureRow,
    ChallengeFindingRow,
    EvidencePacketRow,
    ParameterResultRow,
    RiskScoreRow,
    TopicConclusionRow,
)

# The agentic workbench sections (Req 21.1), in deterministic order.
AGENTIC_SECTIONS: tuple[tuple[str, str], ...] = (
    ("analysis_runs", "Analysis Runs"),
    ("parameters", "Parameters"),
    ("parameter_provenance", "Parameter Provenance"),
    ("formulas", "Deterministic Formulas"),
    ("scores", "Topic Scores"),
    ("obligor_score", "Obligor Risk Score"),
    ("facility_score", "Facility Risk Score"),
    ("evidence_quality", "Evidence Quality"),
    ("agent_runs", "Agent Runs"),
    ("evidence_packets", "Routed Evidence Packets"),
    ("prompt_model_identity", "Prompt / Model Identity"),
    ("token_usage", "Token Usage"),
    ("challenges", "Challenges"),
    ("reruns", "Targeted Reruns"),
    ("candidate_structures", "Candidate Structures"),
    ("policy_failures", "Policy Failures"),
    ("structure_results", "Base / Downside Structure Results"),
    ("topic_conclusions", "Topic Conclusions"),
    ("cross_topic", "Cross-Topic Conclusion"),
    ("exceptions", "Unresolved Exceptions"),
    ("approvals", "Human Approvals"),
)


class AgenticWorkbenchError(RuntimeError):
    pass


class AnalysisRunNotFoundError(AgenticWorkbenchError):
    pass


@dataclass(frozen=True)
class AgenticWorkbenchView:
    analysis_run_id: str
    case_id: str
    snapshot_version: int
    status: str
    sections: dict[str, Any]
    section_order: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_run_id": self.analysis_run_id,
            "case_id": self.case_id,
            "snapshot_version": self.snapshot_version,
            "status": self.status,
            "sections": self.sections,
            "section_order": self.section_order,
        }


class AgenticWorkbenchService:
    """Assemble the read-only agentic workbench view for one analysis run."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # -- run discovery (read-only) -------------------------------------------

    def runs_for_case(self, case_id: str) -> list[dict[str, Any]]:
        """List all analysis runs for a case (multiple runs per snapshot)."""
        rows = self._session.execute(
            select(AgenticAnalysisRun)
            .where(AgenticAnalysisRun.case_id == case_id)
            .order_by(AgenticAnalysisRun.started_at.asc())
        ).scalars().all()
        return [self._run_summary(r) for r in rows]

    def assemble(self, analysis_run_id: str) -> AgenticWorkbenchView:
        """Assemble the agentic workbench view for an explicit run (read-only)."""
        run = self._session.get(AgenticAnalysisRun, analysis_run_id)
        if run is None:
            raise AnalysisRunNotFoundError(
                f"No agentic analysis run {analysis_run_id!r}."
            )
        params = self._accepted(ParameterResultRow, analysis_run_id)
        scores = self._accepted(RiskScoreRow, analysis_run_id)
        conclusions = self._accepted(TopicConclusionRow, analysis_run_id)
        agent_runs = self._all(AgentRunRow, analysis_run_id)
        packets = self._all(EvidencePacketRow, analysis_run_id)
        challenges = self._all(ChallengeFindingRow, analysis_run_id)
        candidates = self._all(CandidateStructureRow, analysis_run_id)
        feasibility = self._all(CandidateFeasibilityRow, analysis_run_id)

        sections: dict[str, Any] = {
            "analysis_runs": self.runs_for_case(run.case_id),
            "parameters": [self._param(p) for p in params],
            "parameter_provenance": [self._provenance(p) for p in params],
            "formulas": self._formulas(params),
            "scores": [self._score(s) for s in scores],
            "obligor_score": next((self._score(s) for s in scores
                                   if s.kind == "obligor"), None),
            "facility_score": next((self._score(s) for s in scores
                                    if s.kind == "facility"), None),
            "evidence_quality": [self._evidence_quality(p) for p in params
                                 if p.evidence_quality],
            "agent_runs": [self._agent_run(a) for a in agent_runs],
            "evidence_packets": [self._packet(p) for p in packets],
            "prompt_model_identity": [self._identity(a) for a in agent_runs],
            "token_usage": self._token_usage(run, agent_runs),
            "challenges": [self._challenge(c) for c in challenges],
            "reruns": self._reruns(params, scores, conclusions),
            "candidate_structures": [self._candidate(c, feasibility)
                                     for c in candidates],
            "policy_failures": self._policy_failures(feasibility),
            "structure_results": self._structure_results(feasibility),
            "topic_conclusions": [self._conclusion(c) for c in conclusions
                                  if c.topic != "cross_topic"],
            "cross_topic": next((self._conclusion(c) for c in conclusions
                                 if c.topic == "cross_topic"), None),
            "exceptions": self._exceptions(challenges),
            "approvals": [],  # human approvals surface via the baseline workbench
        }
        industry = self._industry_benchmarks(analysis_run_id)
        if industry:
            # Present only for a run with a sector benchmark, so the view of
            # every other run is unchanged.
            sections["industry_benchmarks"] = industry
        return AgenticWorkbenchView(
            analysis_run_id=analysis_run_id,
            case_id=run.case_id,
            snapshot_version=run.evidence_snapshot_version,
            status=run.status,
            sections=sections,
            section_order=[
                *({"key": k, "title": t} for k, t in AGENTIC_SECTIONS),
                *([{"key": "industry_benchmarks",
                    "title": "Industry Aggregate Benchmarks (contextual)"}]
                  if industry else []),
            ],
        )

    def _industry_benchmarks(self, run_id: str) -> list[dict[str, Any]]:
        """Accepted industry-AGGREGATE comparisons for the run (read-only)."""
        from app.models.orm import IndustryBenchmarkComparisonRow

        rows = self._session.execute(
            select(IndustryBenchmarkComparisonRow)
            .where(IndustryBenchmarkComparisonRow.analysis_run_id == run_id)
            .where(IndustryBenchmarkComparisonRow.acceptance_state == "accepted")
            .order_by(IndustryBenchmarkComparisonRow.comparison_id)
        ).scalars().all()
        return [
            {
                "id": r.id,
                "comparison_id": r.comparison_id,
                "benchmark_method": r.benchmark_method,
                "comparison_state": r.comparison_state,
                "borrower": r.payload["borrower"],
                "industry_aggregate": r.payload["industry_aggregate"],
                "position_vs_aggregate": r.payload["position_vs_aggregate"],
                "own_history_direction": (r.payload.get("own_history") or {}).get(
                    "direction"),
                "reasons": r.payload["reasons"],
                "caveats": r.payload["caveats"],
                "parameter_result_ids": r.payload.get("parameter_result_ids"),
                "provenance": r.payload["provenance"],
                "usage": r.payload["usage"],
            }
            for r in rows
        ]

    # -- queries (read-only) --------------------------------------------------

    def _accepted(self, model, run_id):
        return self._session.execute(
            select(model)
            .where(model.analysis_run_id == run_id)
            .where(model.acceptance_state == "accepted")
        ).scalars().all()

    def _all(self, model, run_id):
        return self._session.execute(
            select(model).where(model.analysis_run_id == run_id)
        ).scalars().all()

    # -- row -> view-data mappers --------------------------------------------

    @staticmethod
    def _run_summary(r: AgenticAnalysisRun) -> dict[str, Any]:
        return {
            "analysis_run_id": r.analysis_run_id,
            "status": r.status,
            "evidence_snapshot_version": r.evidence_snapshot_version,
            "router_version": r.router_version,
            "agent_registry_version": r.agent_registry_version,
            "scoring_config_version": r.scoring_config_version,
            "total_tokens": r.total_tokens,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "completed_at": r.completed_at.isoformat() if r.completed_at else None,
        }

    @staticmethod
    def _param(p: ParameterResultRow) -> dict[str, Any]:
        return {
            "parameter_result_id": p.parameter_result_id,
            "parameter_id": p.parameter_id, "topic": p.topic,
            "method": p.method, "value": p.value, "status": p.status,
            "risk_signal": p.risk_signal, "evidence_quality": p.evidence_quality,
            "materiality": p.materiality,
        }

    @staticmethod
    def _provenance(p: ParameterResultRow) -> dict[str, Any]:
        return {
            "parameter_result_id": p.parameter_result_id,
            "parameter_id": p.parameter_id,
            "method": p.method,
            "formula_id": p.formula_id, "formula_version": p.formula_version,
            "agent_id": p.agent_id, "agent_run_id": p.agent_run_id,
            "source_fact_ids": p.source_fact_ids,
            "source_parameter_ids": p.source_parameter_ids,
            "evidence_ids": p.evidence_ids,
        }

    @staticmethod
    def _formulas(params) -> list[dict[str, Any]]:
        seen: dict[str, dict] = {}
        for p in params:
            if p.method == "deterministic" and p.formula_id:
                seen[p.formula_id] = {"formula_id": p.formula_id,
                                      "formula_version": p.formula_version}
        return sorted(seen.values(), key=lambda d: d["formula_id"])

    @staticmethod
    def _score(s: RiskScoreRow) -> dict[str, Any]:
        return {
            "score_id": s.score_id, "kind": s.kind, "status": s.status,
            "band": s.band, "coverage_weight": s.coverage_weight,
            "contributing_parameter_ids": s.contributing_parameter_ids,
            "missing_required_parameter_ids": s.missing_required_parameter_ids,
            "critical_missing_parameter_ids": s.critical_missing_parameter_ids,
            "applied_overlays": s.applied_overlays,
            "scoring_config_version": s.scoring_config_version,
        }

    @staticmethod
    def _evidence_quality(p: ParameterResultRow) -> dict[str, Any]:
        return {"parameter_id": p.parameter_id,
                "evidence_quality": p.evidence_quality,
                "risk_signal": p.risk_signal, "status": p.status}

    @staticmethod
    def _agent_run(a: AgentRunRow) -> dict[str, Any]:
        return {
            "run_id": a.run_id, "agent_id": a.agent_id, "topic": a.topic,
            "validation_status": a.validation_status,
            "execution_wave": a.execution_wave, "latency_ms": a.latency_ms,
            "reused_from_cache": a.reused_from_cache, "error_state": a.error_state,
        }

    @staticmethod
    def _packet(p: EvidencePacketRow) -> dict[str, Any]:
        return {"packet_hash": p.packet_hash, "agent_id": p.agent_id,
                "router_version": p.router_version, "evidence_ids": p.evidence_ids}

    @staticmethod
    def _identity(a: AgentRunRow) -> dict[str, Any]:
        return {"agent_id": a.agent_id, "prompt_id": a.prompt_id,
                "prompt_hash": a.prompt_hash, "model_id": a.model_id,
                "agent_definition_hash": a.agent_definition_hash}

    @staticmethod
    def _token_usage(run: AgenticAnalysisRun, agent_runs) -> dict[str, Any]:
        cache_hits = sum(1 for a in agent_runs if a.reused_from_cache)
        reruns = sum(1 for a in agent_runs if a.rerun_reason)
        return {
            "total_input_tokens": run.total_input_tokens,
            "total_output_tokens": run.total_output_tokens,
            "total_tokens": run.total_tokens,
            "model_calls": len([a for a in agent_runs if not a.reused_from_cache]),
            "cache_hits": cache_hits,
            "reruns": reruns,
            "per_agent": [{"agent_id": a.agent_id, "usage": a.usage,
                           "latency_ms": a.latency_ms} for a in agent_runs],
        }

    @staticmethod
    def _challenge(c: ChallengeFindingRow) -> dict[str, Any]:
        return {
            "challenge_id": c.challenge_id, "topic": c.topic, "target": c.target,
            "issue_type": c.issue_type, "severity": c.severity, "reason": c.reason,
            "requires_reanalysis": c.requires_reanalysis,
            "requested_rerun_scope": c.requested_rerun_scope,
        }

    @staticmethod
    def _reruns(params, scores, conclusions) -> list[dict[str, Any]]:
        # Lineage: any artifact that supersedes a prior one is a rerun output.
        out = []
        for coll, kind, idattr in ((params, "parameter", "parameter_result_id"),
                                   (scores, "score", "score_id"),
                                   (conclusions, "conclusion", "id")):
            for row in coll:
                if row.supersedes_id:
                    out.append({"kind": kind, "id": getattr(row, idattr),
                                "supersedes_id": row.supersedes_id})
        return out

    @staticmethod
    def _candidate(c: CandidateStructureRow, feasibility) -> dict[str, Any]:
        verdict = next((f for f in feasibility
                        if f.candidate_id == c.candidate_id
                        and f.acceptance_state == "accepted"), None)
        return {
            "candidate_id": c.candidate_id, "payload": c.payload,
            "feasible": verdict.feasible if verdict else None,
        }

    @staticmethod
    def _policy_failures(feasibility) -> list[dict[str, Any]]:
        out = []
        for f in feasibility:
            failures = (f.feasibility_detail or {}).get("failures", [])
            if failures:
                out.append({"candidate_id": f.candidate_id, "failures": failures})
        return out

    @staticmethod
    def _structure_results(feasibility) -> list[dict[str, Any]]:
        return [{"candidate_id": f.candidate_id,
                 "stress": (f.feasibility_detail or {}).get("stress"),
                 "unavailable": (f.feasibility_detail or {}).get("unavailable", [])}
                for f in feasibility]

    @staticmethod
    def _conclusion(c: TopicConclusionRow) -> dict[str, Any]:
        return {"id": c.id, "topic": c.topic, "payload": c.payload,
                "selected_candidate_id": c.selected_candidate_id,
                "challenge_status": c.challenge_status}

    @staticmethod
    def _exceptions(challenges) -> list[dict[str, Any]]:
        return [{"challenge_id": c.challenge_id, "severity": c.severity,
                 "reason": c.reason}
                for c in challenges if c.severity in ("high", "material")]
