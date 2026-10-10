"""Sector qualitative-risk context for the existing narrow agents.

No new agents are created. For a case with a completed sector benchmark, each
EXISTING narrow agent receives — as DATA in its task input — the sector risk
dimensions the sector configuration assigns to it (e.g. product recalls and
regulatory exposure go to ``regulatory_material_events``), plus the narrative
passages its existing routing spec already selects. The agent may answer with
structured ``sector_observations``.

An observation is evidence-backed context, never a score:

* it must name a dimension assigned to that agent;
* ``relevance`` / ``direction`` / ``confidence`` must be allowed values;
* a ``relevant`` observation must cite admitted evidence, and every cited id
  must be admitted;
* it may not carry a score, band or rating.

Validation is all-or-nothing per agent output, mirroring parameter promotion:
one invalid observation rejects that agent's whole observation set. A dimension
is never required — an agent with no relevant evidence simply reports
``not_assessable`` or omits it.
"""

from __future__ import annotations

from typing import Any

from app.prompts.agent_schemas import (
    SECTOR_OBSERVATION_CONFIDENCE,
    SECTOR_OBSERVATION_DIRECTION,
    SECTOR_OBSERVATION_RELEVANCE,
)

MAX_ROUTED_PASSAGES = 25
_SCORE_KEYS = frozenset({"score", "band", "risk_band", "risk_score", "rating", "grade"})


def dimensions_for_agent(sector_payload: dict[str, Any], agent_id: str) -> list[dict[str, Any]]:
    return [
        {"dimension_id": d["id"], "title": d["title"]}
        for d in sector_payload.get("qualitative_risk_dimensions", [])
        if agent_id in d.get("agents", [])
    ]


def sector_context_for_agent(
    sector_payload: dict[str, Any],
    agent_id: str,
    routed_narrative: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """The bounded sector context for one narrow agent, or None if not relevant."""
    dimensions = dimensions_for_agent(sector_payload, agent_id)
    if not dimensions:
        return None
    passages = sorted(routed_narrative or [], key=lambda p: str(p.get("evidence_id")))
    return {
        "sector_id": sector_payload["sector"]["sector_id"],
        "sector_display_name": sector_payload["sector"]["display_name"],
        "risk_dimensions": dimensions,
        "observation_values": {
            "relevance": SECTOR_OBSERVATION_RELEVANCE,
            "direction": SECTOR_OBSERVATION_DIRECTION,
            "confidence": SECTOR_OBSERVATION_CONFIDENCE,
        },
        "routed_narrative": [
            {"evidence_id": p.get("evidence_id"), "text": p.get("text"),
             "candidate_topics": p.get("candidate_topics", [])}
            for p in passages[:MAX_ROUTED_PASSAGES]
        ],
        "guidance": (
            "Report a dimension only where the routed evidence addresses it. "
            "Observations are context for analysts; they are not scores."
        ),
    }


def validate_sector_observations(
    parsed: dict[str, Any],
    *,
    allowed_dimensions: set[str],
    admitted_evidence_ids: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return ``(accepted_observations, issues)``; any issue accepts nothing."""
    raw = parsed.get("sector_observations")
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], ["sector_observations must be a list."]
    issues: list[str] = []
    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            issues.append("A sector observation is not an object.")
            continue
        dimension = item.get("dimension_id")
        label = f"observation {dimension!r}"
        if dimension not in allowed_dimensions:
            issues.append(f"{label} is not a dimension assigned to this agent.")
        if dimension in seen:
            issues.append(f"{label} is reported more than once.")
        seen.add(dimension)
        scored = sorted(set(map(str.lower, map(str, item))) & _SCORE_KEYS)
        if scored:
            issues.append(f"{label} carries score field(s) {scored}; observations are not scores.")
        relevance = item.get("relevance")
        if relevance not in SECTOR_OBSERVATION_RELEVANCE:
            issues.append(f"{label} has invalid relevance {relevance!r}.")
        direction = item.get("direction", "unclear")
        if direction not in SECTOR_OBSERVATION_DIRECTION:
            issues.append(f"{label} has invalid direction {direction!r}.")
        confidence = item.get("confidence", "low")
        if confidence not in SECTOR_OBSERVATION_CONFIDENCE:
            issues.append(f"{label} has invalid confidence {confidence!r}.")
        evidence_ids = item.get("evidence_ids") or []
        if relevance == "relevant" and not evidence_ids:
            issues.append(f"{label} is 'relevant' but cites no evidence.")
        unknown = sorted(e for e in evidence_ids if e not in admitted_evidence_ids)
        if unknown:
            issues.append(f"{label} cites unadmitted evidence {unknown}.")
        accepted.append({
            "dimension_id": dimension,
            "relevance": relevance,
            "direction": direction,
            "confidence": confidence,
            "evidence_ids": sorted(evidence_ids),
            "review_flag": bool(item.get("review_flag", False)),
            "note": item.get("note"),
        })
    return ([], issues) if issues else (accepted, [])
