"""Agentic orchestration layer (Milestones 10-17).

Wires the already-built primitives — evidence router, agent runtime, DAG
executor, deterministic validation, parameter engine and scoring engine — into
the Business, Financial and Structuring workflows, the topic orchestrators and
the bounded challenge/rerun loop.

Nothing here performs official arithmetic or assigns scores: narrow agents emit
interpretations that become validated ParameterResults; the deterministic
engines compute parameters and scores; orchestrators synthesize; challengers
identify defects that drive targeted, descendant-aware reruns.
"""

from __future__ import annotations

from app.services.orchestration.challenge_loop import ChallengeLoop, parse_findings
from app.services.orchestration.conclusions import (
    ConclusionStore,
    build_topic_conclusion,
)
from app.services.orchestration.credit import verify_cross_topic_inputs
from app.services.orchestration.promotion import (
    ParameterPromoter,
    PromotionRejected,
    ValidatedAgentOutput,
    promote_mitigant_output,
    promote_narrow_output,
)
from app.services.orchestration.structuring import (
    feasible_candidate_ids,
    validate_selection,
)

__all__ = [
    "ParameterPromoter",
    "ValidatedAgentOutput",
    "PromotionRejected",
    "promote_narrow_output",
    "promote_mitigant_output",
    "ConclusionStore",
    "build_topic_conclusion",
    "ChallengeLoop",
    "parse_findings",
    "validate_selection",
    "feasible_candidate_ids",
    "verify_cross_topic_inputs",
]
