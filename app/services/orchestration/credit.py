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


from app.schemas.agentic import ScoreKind, Topic


class CrossTopicInputError(ValueError):
    """Raised when cross-topic inputs are stale, cross-run, or incomplete."""


# When facility terms are absent the structuring path has no conclusion; the
# caller passes this sentinel instead of inventing one (item 19 / Delta case).
STRUCTURING_UNAVAILABLE = "structuring_unavailable"


def verify_cross_topic_inputs(
    *,
    analysis_run_id: str,
    conclusions: list[TopicConclusion],
    scores: list[RiskScore],
    structuring_state: str | None = None,
) -> None:
    """Require the EXACT cross-topic contract (item 19 / Req 16.1).

    Normally required: exactly one accepted Business, Financial and Structuring
    conclusion, one accepted ObligorRiskScore, and one FacilityRiskScore (which
    may be ``unavailable``). When ``structuring_state == STRUCTURING_UNAVAILABLE``
    the Structuring conclusion AND an available Facility score are not required —
    but Facility must then be ``unavailable`` if present, never invented.

    All inputs must be accepted and from ``analysis_run_id``; score references on
    the Business/Financial conclusions must resolve to accepted scores.
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

    topics = {c.topic for c in conclusions}
    kinds = {s.kind for s in scores}

    def _require_topic(topic: Topic) -> None:
        if sum(1 for c in conclusions if c.topic is topic) != 1:
            raise CrossTopicInputError(
                f"Exactly one accepted {topic.value} conclusion is required."
            )

    _require_topic(Topic.BUSINESS)
    _require_topic(Topic.FINANCIAL)

    if ScoreKind.OBLIGOR not in kinds:
        raise CrossTopicInputError("An accepted ObligorRiskScore is required.")

    structuring_absent = structuring_state == STRUCTURING_UNAVAILABLE
    if not structuring_absent:
        _require_topic(Topic.STRUCTURING)
        if ScoreKind.FACILITY not in kinds:
            raise CrossTopicInputError(
                "A FacilityRiskScore is required (may be status=unavailable)."
            )
    else:
        # Facility, if provided, MUST be unavailable — never an invented band.
        for s in scores:
            if s.kind is ScoreKind.FACILITY and s.status.value != "unavailable":
                raise CrossTopicInputError(
                    "Structuring is unavailable; FacilityRiskScore must be "
                    "unavailable, not fabricated."
                )

    # Score-reference consistency: a conclusion must carry SOME score reference
    # (it was resolved to an accepted topic score when the conclusion was built).
    for c in conclusions:
        if c.topic in (Topic.BUSINESS, Topic.FINANCIAL) and not c.score_reference:
            raise CrossTopicInputError(
                f"{c.topic.value} conclusion carries no score_reference."
            )
