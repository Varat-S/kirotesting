"""Structuring orchestration + selection (Milestone 17.1).

The Structuring Orchestrator selects among FEASIBLE candidates only and records
the choice on the StructuringConclusion (never a mutable flag on the candidate).
It cannot override a deterministic failure: a selection that names an infeasible
or unknown candidate is rejected. Re-selection creates a NEW conclusion version
preserving the prior (append-only).
"""

from __future__ import annotations

from app.schemas.agentic import CandidateFeasibility


class InfeasibleSelectionError(ValueError):
    """Raised when a selection names a non-feasible or unknown candidate."""


def feasible_candidate_ids(verdicts: list[CandidateFeasibility]) -> set[str]:
    """The ids of candidates whose ACCEPTED feasibility verdict is feasible."""
    return {
        v.candidate_id
        for v in verdicts
        if v.feasible and v.acceptance_state.value == "accepted"
    }


def validate_selection(
    selected_candidate_id: str | None,
    verdicts: list[CandidateFeasibility],
    *,
    require_selection: bool = False,
) -> str | None:
    """Validate an orchestrator's candidate selection against feasibility.

    Returns the selected id when valid. Raises ``InfeasibleSelectionError`` when
    the selection names a candidate that is not feasible (the orchestrator may
    NOT override a deterministic failure, Req 13.6). ``None`` is allowed unless
    ``require_selection`` is set (e.g. no feasible candidate exists -> the
    structuring conclusion legitimately selects nothing).
    """
    feasible = feasible_candidate_ids(verdicts)
    if selected_candidate_id is None:
        if require_selection and feasible:
            raise InfeasibleSelectionError(
                "A feasible candidate exists but none was selected."
            )
        return None
    if selected_candidate_id not in feasible:
        raise InfeasibleSelectionError(
            f"Selected candidate {selected_candidate_id!r} is not feasible; the "
            "orchestrator cannot override a deterministic failure."
        )
    return selected_candidate_id
