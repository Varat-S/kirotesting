"""Final cross-topic credit orchestration (Milestone 17.2 / Req 16).

The Credit Orchestrator receives ONLY accepted/reconciled conclusions and scores
(Business, Financial, Structuring, Obligor, Facility) and builds a coherent
cross-topic draft. It recomputes nothing. The Cross-Topic Challenge agent then
detects contradictions BETWEEN topics; a material unresolved one drives a bounded
targeted reanalysis or human escalation (via the shared challenge loop).

This module provides the deterministic guards around the cross-topic step: it
verifies every input belongs to the SAME analysis run and is the ACCEPTED
version before the orchestrator consumes it (no stale/cross-run inputs).
"""

from __future__ import annotations

from app.schemas.agentic import RiskScore, TopicConclusion


class CrossTopicInputError(ValueError):
    """Raised when cross-topic inputs are stale, cross-run or unaccepted."""


def verify_cross_topic_inputs(
    *,
    analysis_run_id: str,
    conclusions: list[TopicConclusion],
    scores: list[RiskScore],
) -> None:
    """Guard that all inputs are accepted and from ``analysis_run_id``.

    The Credit Orchestrator must never consume a stale descendant or an artifact
    from a different run (Remediation 1 / 13; Req 16.1).
    """
    for c in conclusions:
        if c.analysis_run_id != analysis_run_id:
            raise CrossTopicInputError(
                f"Conclusion for {c.topic.value} belongs to run "
                f"{c.analysis_run_id!r}, not {analysis_run_id!r}."
            )
        if c.acceptance_state.value != "accepted":
            raise CrossTopicInputError(
                f"Conclusion for {c.topic.value} is {c.acceptance_state.value}, "
                "not accepted (stale descendant)."
            )
    for s in scores:
        if s.analysis_run_id != analysis_run_id:
            raise CrossTopicInputError(
                f"Score {s.kind.value} belongs to run {s.analysis_run_id!r}, "
                f"not {analysis_run_id!r}."
            )
        if s.acceptance_state.value != "accepted":
            raise CrossTopicInputError(
                f"Score {s.kind.value} is {s.acceptance_state.value}, not accepted."
            )
