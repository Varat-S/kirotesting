"""Read-only Business Overview inspection views.

Assembles what a reviewer needs to inspect a BO run without opening database
tables: evidence status, agent execution status, accepted / qualified /
rejected hypotheses, contradictions, open questions, and the recorded memo and
audit report. Every function here is a pure READ. The memo and audit come from
the hash-chained stage artifacts recorded when the run finished, so what is
shown is exactly what was produced.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.orm import AcceptedBusinessOverviewRow, AgenticAnalysisRun
from app.services.bo.m3_version import is_m3_v2_block
from app.services.bo.runner import ANALYSIS_MODE
from app.services.pipeline.artifacts import verified_artifacts


class BoRunNotFoundError(LookupError):
    pass


def bo_runs(session: Session, case_id: str | None = None) -> list[dict[str, Any]]:
    """BO-only analysis runs, newest first (optionally for one case)."""
    stmt = (
        select(AgenticAnalysisRun, AcceptedBusinessOverviewRow)
        .join(AcceptedBusinessOverviewRow,
              AcceptedBusinessOverviewRow.analysis_run_id
              == AgenticAnalysisRun.analysis_run_id)
        .where(AgenticAnalysisRun.analysis_mode == ANALYSIS_MODE)
        .order_by(AgenticAnalysisRun.started_at.desc())
    )
    if case_id is not None:
        stmt = stmt.where(AgenticAnalysisRun.case_id == case_id)
    return [
        {
            "analysis_run_id": run.analysis_run_id,
            "case_id": run.case_id,
            "evidence_snapshot_version": run.evidence_snapshot_version,
            "run_status": run.status,
            "started_at": run.started_at.isoformat(),
            "conclusion_status": handoff.payload["conclusion_status"],
            "execution_mode": handoff.payload["execution_mode"],
            "human_review_status": handoff.human_review_status,
            "handoff_id": handoff.handoff_id,
        }
        for run, handoff in session.execute(stmt).all()
    ]


def _handoff_row(session: Session, analysis_run_id: str) -> AcceptedBusinessOverviewRow:
    row = session.execute(
        select(AcceptedBusinessOverviewRow)
        .where(AcceptedBusinessOverviewRow.analysis_run_id == analysis_run_id)
    ).scalars().first()
    if row is None:
        raise BoRunNotFoundError(f"No Business Overview run {analysis_run_id!r}.")
    return row


def bo_artifact(session: Session, analysis_run_id: str, kind: str) -> dict[str, Any]:
    """The recorded ``memo`` / ``audit`` JSON, or the live ``handoff``."""
    row = _handoff_row(session, analysis_run_id)
    if kind == "handoff":
        return dict(row.payload)
    if kind not in ("memo", "audit"):
        raise BoRunNotFoundError(f"Unknown Business Overview artifact {kind!r}.")
    name = f"bo_{kind}:{analysis_run_id}"
    for artifact in verified_artifacts(session, row.case_id, row.snapshot_version):
        if artifact.name == name:
            return json.loads(artifact.payload_json)
    raise BoRunNotFoundError(f"No recorded {kind} for run {analysis_run_id!r}.")


def _m3_v2_agents(module: dict[str, Any] | None) -> list[dict[str, Any]]:
    """M3-v2's two components run outside the agent DAG; list them with their recorded outcome."""
    if not is_m3_v2_block(module):
        return []
    rows = []
    for kind, agent_id, role in (
            ("structure", "m3_structure_conversion", "M3-v2 corporate structure (deterministic)"),
            ("management", "m3_management_assessment", "M3-v2 management + Validator 7")):
        component = (module.get("m3_rebuild") or {}).get(kind) or {}
        status, validation = component.get("status"), component.get("validation_status")
        artifact = component.get("artifact") or {}
        decisions = ((artifact.get("validation") or {}).get("validator_7") or {}).get("decisions") or []
        outcomes = Counter(d.get("outcome") for d in decisions)
        rows.append({
            "agent_id": agent_id, "code": "M3", "role": role,
            "status": ("validated" if status == "complete" and validation == "passed"
                       else "partially_validated" if status == "partial" else status),
            "detail": component.get("error") or (
                "Validator 7: " + ", ".join(f"{n} {o}" for o, n in sorted(outcomes.items()))
                if outcomes else None),
            "runs": 0 if status == "not_attempted" else 1,
            "validation_issues": [l for l in artifact.get("limitations") or []
                                  if "unsupported" in l or "withheld" in l],
        })
    return rows


def bo_view(session: Session, analysis_run_id: str) -> dict[str, Any]:
    """The inspection view for one run."""
    handoff = _handoff_row(session, analysis_run_id)
    memo = bo_artifact(session, analysis_run_id, "memo")
    audit = bo_artifact(session, analysis_run_id, "audit")
    current = [h for h in audit["hypotheses"]["records"]
               if h["acceptance_state"] == "accepted"]

    def brief(h):
        return {k: h.get(k) for k in (
            "hypothesis_id", "agent_id", "section", "claim", "claim_type",
            "direction", "disposition", "admissibility", "confidence",
            "materiality", "missing_information", "validation_notes",
            "corrects_hypothesis_id")}

    snapshot = audit["evidence_snapshot"]
    return {
        "analysis_run_id": analysis_run_id,
        "case": memo["case"],
        "run": memo["run"],
        "conclusion_status": handoff.payload["conclusion_status"],
        "status_text": memo["status"]["text"],
        "handoff": {
            "handoff_id": handoff.handoff_id,
            "human_review_status": handoff.human_review_status,
            "reviewer": handoff.payload.get("reviewer"),
            "reviewed_at": handoff.payload.get("reviewed_at"),
            "unresolved_challenges": handoff.payload["unresolved_challenges"],
            "can_accept": handoff.human_review_status == "pending"
            and handoff.payload["business_topic_conclusion_id"] is not None,
        },
        "evidence_status": {
            "snapshot_version": snapshot["snapshot_version"],
            "content_hash": snapshot["content_hash"],
            "documents": len(audit["sources"]["documents"]),
            "rejected_sources": len(audit["sources"]["rejected_sources"]),
            "facts": snapshot["fact_count"],
            "narrative_passages": snapshot["narrative_passage_count"],
            "human_verified_facts": snapshot["human_verified_fact_count"],
            "retrieval_gaps": len(audit["retrieval"]["gaps"]),
        },
        "agents": [
            *({"agent_id": a["agent_id"], "code": a["code"], "role": a["role"],
               "status": a["status"], "detail": a["detail"], "runs": len(a["runs"]),
               "validation_issues": a["validation_issues"]}
              for a in audit["agents"]),
            *_m3_v2_agents((handoff.payload.get("module_conclusions") or {}).get("M3"))],
        "execution": audit["execution"],
        "hypothesis_statistics": audit["hypotheses"]["statistics"],
        "accepted_hypotheses": [
            brief(h) for h in current if h["disposition"] in ("supported", "qualified")],
        "rejected_hypotheses": [brief(h) for h in current
                                if h["disposition"] == "rejected"],
        "unverified_hypotheses": [brief(h) for h in current
                                  if h["disposition"] == "unverified"],
        "contradictions": audit["reconciliation"]["contradictions"],
        "duplicate_groups": audit["reconciliation"]["duplicate_groups"],
        "open_questions": memo["outstanding"],
        "challenger": audit["challenger"],
        "rerun": audit["rerun"],
    }
