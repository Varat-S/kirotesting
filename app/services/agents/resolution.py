"""Accepted / current artifact resolution (Milestone 5 / Remediation 13 / Req 33).

Analytical artifacts are append-only and may have multiple versions within one
``analysis_run_id`` (a targeted rerun creates a NEW version rather than mutating
the old one). The accepted/current artifact is resolved ONLY by explicit
lineage/acceptance fields — never by latest timestamp, max primary key or
insertion order. Orchestrators and finalization consume only ``accepted``
versions; superseded versions remain queryable history.

This module is deterministic and never calls an LLM.
"""

from __future__ import annotations

from typing import Iterable, TypeVar

from app.schemas.agentic import AcceptanceState

T = TypeVar("T")


def accepted(items: Iterable[T]) -> list[T]:
    """Return only the items whose ``acceptance_state`` is ``accepted``."""
    out = []
    for item in items:
        state = _acceptance_state(item)
        if state == AcceptanceState.ACCEPTED.value:
            out.append(item)
    return out


def accepted_one(items: Iterable[T]) -> T | None:
    """Return the single accepted item, or ``None`` if there is not exactly one.

    Raises ``ValueError`` if more than one accepted version exists for what must
    be a single-valued artifact — an invariant violation the caller must fix
    rather than silently pick a winner.
    """
    matches = accepted(items)
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError(
            f"Expected at most one accepted artifact, found {len(matches)}; "
            "lineage is inconsistent (Remediation 13)."
        )
    return matches[0]


def supersede(previous: T, new: T) -> None:
    """Mark ``previous`` superseded and link ``new`` to it (append-only lineage).

    Mutates the two in-memory objects' lineage fields; it does not delete or
    rewrite history. Works on Pydantic models and ORM rows alike (both expose
    ``acceptance_state`` / ``supersedes_id``).
    """
    _set(previous, "acceptance_state", AcceptanceState.SUPERSEDED.value)
    prev_id = _identity(previous)
    if prev_id is not None:
        _set(new, "supersedes_id", prev_id)


def _acceptance_state(item: object) -> str | None:
    value = getattr(item, "acceptance_state", None)
    if value is None and isinstance(item, dict):
        value = item.get("acceptance_state")
    return value.value if isinstance(value, AcceptanceState) else value


def _identity(item: object) -> str | None:
    for attr in (
        "parameter_result_id",
        "score_id",
        "feasibility_id",
        "challenge_id",
        "id",
    ):
        value = getattr(item, attr, None)
        if value is None and isinstance(item, dict):
            value = item.get(attr)
        if value is not None:
            return value
    return None


def _set(item: object, attr: str, value: str) -> None:
    if isinstance(item, dict):
        item[attr] = value
    else:
        setattr(item, attr, value)
