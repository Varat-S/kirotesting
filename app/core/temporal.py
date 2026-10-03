"""Temporal-leakage controls (Requirement 20, Requirement 1.9).

A single reusable predicate decides whether an external item is *eligible* for a
historical run: an item is eligible only when it was **available by** the case's
``evidence_cutoff_timestamp``. Eligibility is keyed on ``available_at`` (when the
information became knowable), NOT on ``retrieved_at`` (when the pipeline happened
to download it) -- see Req 20.3.

The predicate is deliberately data-type agnostic (Req 20.1): it applies
identically to public filings, news, peer-company filings, market data,
fuel/macro data, rating actions, external benchmark data, and retrieved web
content. Callers pass only timestamps, so the same function guards every data
type.

A future-dated item (one whose ``available_at`` is after the cutoff) is rejected
for a historical run (Req 20.4). Items with an unknown ``available_at`` are
treated as *not eligible* for a historical run rather than silently admitted
(no silent defaults, Req 21.1); the caller receives an explicit reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class EligibilityReason(str, Enum):
    """Why an item was deemed eligible or ineligible for a historical run."""

    ELIGIBLE = "eligible"
    FUTURE_DATED = "future_dated"
    MISSING_AVAILABLE_AT = "missing_available_at"


@dataclass(frozen=True)
class EligibilityResult:
    """Outcome of a temporal-eligibility check.

    ``eligible`` is the boolean verdict; ``reason`` records why. The result is
    forward-compatible with the escalation engine (Milestone 5): an ineligible
    item can be surfaced with its machine-readable ``reason``.
    """

    eligible: bool
    reason: EligibilityReason

    def __bool__(self) -> bool:  # convenience: ``if result:``
        return self.eligible


def _as_utc(value: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC for safe comparison."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def check_temporal_eligibility(
    available_at: datetime | None,
    evidence_cutoff_timestamp: datetime,
) -> EligibilityResult:
    """Return whether an item is eligible for a historical run at the cutoff.

    Eligibility is based solely on ``available_at`` versus the cutoff
    (Req 20.3). An item is eligible when ``available_at <= cutoff``. An item
    available strictly after the cutoff is rejected as future-dated (Req 20.4).
    An unknown ``available_at`` is ineligible (surfaced, not silently admitted).

    ``retrieved_at`` is intentionally NOT a parameter: when the pipeline
    downloaded an item never affects historical eligibility.
    """
    if available_at is None:
        return EligibilityResult(False, EligibilityReason.MISSING_AVAILABLE_AT)

    cutoff = _as_utc(evidence_cutoff_timestamp)
    available = _as_utc(available_at)

    if available > cutoff:
        return EligibilityResult(False, EligibilityReason.FUTURE_DATED)
    return EligibilityResult(True, EligibilityReason.ELIGIBLE)


def is_eligible(
    available_at: datetime | None,
    evidence_cutoff_timestamp: datetime,
) -> bool:
    """Boolean convenience wrapper around :func:`check_temporal_eligibility`."""
    return bool(check_temporal_eligibility(available_at, evidence_cutoff_timestamp))
