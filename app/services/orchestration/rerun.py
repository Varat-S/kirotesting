"""Targeted rerun lifecycle controller (Remediation item 8).

Executes the append-only correction lifecycle a material challenge requests:

    supersede currently-accepted affected artifacts
      -> re-run / recompute each affected node (append NEW accepted versions)
      -> prior versions remain queryable, never mutated in place.

The controller operates over the persisted artifact rows. It is deterministic
about WHICH artifacts to supersede (the challenge decision's rerun scope +
descendants) and leaves the actual production of new versions to injected
callbacks (so the same controller drives LLM reruns, deterministic score
recomputation and conclusion rebuilds). Unaffected branches are never touched.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.orm import (
    ParameterResultRow,
    RiskScoreRow,
    TopicConclusionRow,
)

# A producer re-creates a node's artifact and returns the NEW accepted row's id
# (or None if nothing was produced, e.g. an agent skipped for absent evidence).
Producer = Callable[[str], str | None]


@dataclass
class RerunOutcome:
    superseded: dict[str, list[str]] = field(default_factory=dict)  # kind -> ids
    recomputed: list[str] = field(default_factory=list)  # node ids reproduced


class RerunController:
    """Append-only targeted rerun over persisted analytical artifacts."""

    def __init__(self, session: Session, *, analysis_run_id: str) -> None:
        self._session = session
        self._run = analysis_run_id

    # -- supersession ---------------------------------------------------------

    def supersede_parameters(self, parameter_ids: set[str]) -> list[str]:
        """Mark accepted ParameterResults for the given parameter_ids superseded."""
        rows = self._session.execute(
            select(ParameterResultRow)
            .where(ParameterResultRow.analysis_run_id == self._run)
            .where(ParameterResultRow.acceptance_state == "accepted")
            .where(ParameterResultRow.parameter_id.in_(parameter_ids))
        ).scalars().all()
        return [self._supersede(r, "parameter_result_id") for r in rows]

    def supersede_scores(self, kinds: set[str]) -> list[str]:
        """Mark accepted RiskScores of the given kinds superseded."""
        rows = self._session.execute(
            select(RiskScoreRow)
            .where(RiskScoreRow.analysis_run_id == self._run)
            .where(RiskScoreRow.acceptance_state == "accepted")
            .where(RiskScoreRow.kind.in_(kinds))
        ).scalars().all()
        return [self._supersede(r, "score_id") for r in rows]

    def supersede_conclusions(self, topics: set[str]) -> list[str]:
        """Mark accepted TopicConclusions for the given topics superseded."""
        rows = self._session.execute(
            select(TopicConclusionRow)
            .where(TopicConclusionRow.analysis_run_id == self._run)
            .where(TopicConclusionRow.acceptance_state == "accepted")
            .where(TopicConclusionRow.topic.in_(topics))
        ).scalars().all()
        return [self._supersede(r, "id") for r in rows]

    def _supersede(self, row, id_attr: str) -> str:
        row.acceptance_state = "superseded"
        self._session.flush()
        return getattr(row, id_attr)

    # -- accepted-current resolution -----------------------------------------

    def accepted_parameter(self, parameter_id: str) -> ParameterResultRow | None:
        return self._session.execute(
            select(ParameterResultRow)
            .where(ParameterResultRow.analysis_run_id == self._run)
            .where(ParameterResultRow.parameter_id == parameter_id)
            .where(ParameterResultRow.acceptance_state == "accepted")
        ).scalars().first()

    def accepted_score(self, kind: str) -> RiskScoreRow | None:
        return self._session.execute(
            select(RiskScoreRow)
            .where(RiskScoreRow.analysis_run_id == self._run)
            .where(RiskScoreRow.kind == kind)
            .where(RiskScoreRow.acceptance_state == "accepted")
        ).scalars().first()

    def accepted_conclusion(self, topic: str) -> TopicConclusionRow | None:
        return self._session.execute(
            select(TopicConclusionRow)
            .where(TopicConclusionRow.analysis_run_id == self._run)
            .where(TopicConclusionRow.topic == topic)
            .where(TopicConclusionRow.acceptance_state == "accepted")
        ).scalars().first()
