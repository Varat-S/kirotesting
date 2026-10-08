"""Topic orchestration — build TopicConclusions from validated inputs (M12/M17).

An orchestrator agent SYNTHESIZES: it consumes validated ParameterResults, the
topic risk score and relevant limitations/conflicts, and emits a claim-level
conclusion. It computes nothing. Quote-not-calculate (Req 34) is enforced by the
deterministic validator with the known validated parameter values before a
conclusion is accepted; any invented/mismatched number is rejected.

This module turns a VALID orchestrator output into a typed ``TopicConclusion``
(or ``StructuringConclusion``) and persists it append-only.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.orm import TopicConclusionRow
from app.schemas.agentic import (
    AcceptanceState,
    ClaimCategory,
    ConclusionClaim,
    Materiality,
    StructuringConclusion,
    Topic,
    TopicConclusion,
)


def build_topic_conclusion(
    topic: Topic,
    parsed: dict,
    *,
    analysis_run_id: str,
    orchestrator_run_id: str,
    score_reference: str,
    selected_candidate_id: str | None = None,
) -> TopicConclusion:
    """Convert a validated orchestrator output into a typed TopicConclusion."""
    assessment = _claim(parsed.get("overall_assessment"), default_category="assessment")
    kwargs = dict(
        topic=topic,
        analysis_run_id=analysis_run_id,
        overall_assessment=assessment,
        strengths=[_claim(c, "strength") for c in parsed.get("strengths", [])],
        weaknesses=[_claim(c, "weakness") for c in parsed.get("weaknesses", [])],
        key_drivers=[_claim(c, "driver") for c in parsed.get("key_drivers", [])],
        material_risks=[_claim(c, "risk") for c in parsed.get("material_risks", [])],
        open_questions=list(parsed.get("open_questions", [])),
        unresolved_contradictions=list(parsed.get("unresolved_contradictions", [])),
        score_reference=score_reference,
        orchestrator_run_id=orchestrator_run_id,
        challenge_status="clean",
        acceptance_state=AcceptanceState.ACCEPTED,
    )
    if topic is Topic.STRUCTURING:
        return StructuringConclusion(
            selected_candidate_id=selected_candidate_id
            or parsed.get("selected_candidate_id"),
            **kwargs,
        )
    return TopicConclusion(**kwargs)


def _claim(data, default_category: str) -> ConclusionClaim:
    data = data or {}
    try:
        category = ClaimCategory(data.get("category", default_category))
    except ValueError:
        category = ClaimCategory(default_category)
    try:
        materiality = Materiality(data.get("materiality", "medium"))
    except ValueError:
        materiality = Materiality.MEDIUM
    return ConclusionClaim(
        claim_id=data.get("claim_id") or f"cl_{uuid.uuid4().hex[:12]}",
        category=category,
        text=data.get("text", ""),
        parameter_result_ids=list(data.get("parameter_result_ids", [])),
        evidence_ids=list(data.get("evidence_ids", [])),
        materiality=materiality,
        uncertainty=data.get("uncertainty"),
    )


class ConclusionProvenanceError(ValueError):
    """Raised when a material conclusion claim lacks traceable provenance."""


def validate_conclusion_provenance(
    conclusion: TopicConclusion,
    *,
    accepted_parameter_ids: set[str],
) -> None:
    """Require material claims to carry traceable provenance (item 5 / Req 35).

    Every material strength/weakness/driver/risk and the overall_assessment must
    carry at least one of {parameter_result_ids, evidence_ids}, and every
    referenced ParameterResult id must be in the accepted set for the run (exists,
    same run, accepted/current, not superseded). High-materiality claims that
    cite a parameter must reference an accepted one.
    """
    material_claims = [
        conclusion.overall_assessment,
        *conclusion.strengths,
        *conclusion.weaknesses,
        *conclusion.key_drivers,
        *conclusion.material_risks,
    ]
    for claim in material_claims:
        if claim.materiality is Materiality.HIGH or claim.category in (
            ClaimCategory.ASSESSMENT, ClaimCategory.RISK, ClaimCategory.DRIVER
        ):
            if not claim.parameter_result_ids and not claim.evidence_ids:
                raise ConclusionProvenanceError(
                    f"Material claim {claim.claim_id!r} carries no parameter or "
                    "evidence provenance."
                )
        for prid in claim.parameter_result_ids:
            if prid not in accepted_parameter_ids:
                raise ConclusionProvenanceError(
                    f"Claim {claim.claim_id!r} references ParameterResult {prid!r} "
                    "that is not accepted/current for this run."
                )


class ConclusionStore:
    """Persists TopicConclusions append-only."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def persist(
        self,
        conclusion: TopicConclusion,
        *,
        case_id: str,
        snapshot_version: int,
        conclusion_id: str | None = None,
    ) -> TopicConclusionRow:
        row = TopicConclusionRow(
            id=conclusion_id or f"tc_{uuid.uuid4().hex[:16]}",
            analysis_run_id=conclusion.analysis_run_id,
            case_id=case_id,
            snapshot_version=snapshot_version,
            topic=conclusion.topic.value,
            payload=conclusion.model_dump(mode="json"),
            selected_candidate_id=getattr(conclusion, "selected_candidate_id", None),
            challenge_status=conclusion.challenge_status,
            orchestrator_run_id=conclusion.orchestrator_run_id,
            supersedes_id=conclusion.supersedes_id,
            acceptance_state=conclusion.acceptance_state.value,
        )
        self._session.add(row)
        self._session.flush()
        return row
