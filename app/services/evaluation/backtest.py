"""Temporal backtesting + re-run-mode controller (task 9.3, Req 19.7, 20.4, 23.7).

Two independent capabilities live here:

1. **Re-run modes (Req 19.7).** The same historical evidence can be re-run in
   two VISIBLY DISTINGUISHED modes:

   * :attr:`RerunMode.REPRODUCE_HISTORICAL` -- reproduce the historical run
     using the ORIGINAL configuration versions recorded on the snapshot.
   * :attr:`RerunMode.REEVALUATE_LATEST` -- re-evaluate the same evidence using
     the LATEST configuration versions.

   :class:`RerunController` records which mode was used and resolves the config
   versions accordingly, so a caller (and an audit reader) can always tell the
   two apart.

2. **Temporal backtesting + leakage (Req 20.4, 23.7).** An expanding-window
   backtest replays evidence in chronological folds, and at every fold the
   availability-by-cutoff predicate from :mod:`app.core.temporal` is applied so
   future-dated items (filings/news/peer/market-macro) are REJECTED and never
   mixed into a past fold. Rolling-window designs are only meaningful when a
   LEARNED model is present; this PoC has none, so rolling windows are reported
   as not-applicable (with justification) rather than fabricated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping

from app.core.temporal import EligibilityReason, check_temporal_eligibility


class RerunMode(str, Enum):
    """Two visibly distinguished historical re-run modes (Req 19.7)."""

    REPRODUCE_HISTORICAL = "reproduce_historical"  # original config versions
    REEVALUATE_LATEST = "reevaluate_latest"  # latest config versions


@dataclass(frozen=True)
class RerunResolution:
    """The resolved config versions for a re-run, labelled by mode."""

    mode: RerunMode
    config_versions: dict[str, int]
    label: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "label": self.label,
            "config_versions": dict(self.config_versions),
        }


class RerunController:
    """Resolve config versions for a historical re-run in a labelled mode.

    ``original_versions`` are the versions recorded on the historical snapshot;
    ``latest_versions`` are the current registry versions. The controller never
    silently mixes the two: a mode selects exactly one set.
    """

    def __init__(
        self,
        *,
        original_versions: Mapping[str, int],
        latest_versions: Mapping[str, int],
    ) -> None:
        self._original = dict(original_versions)
        self._latest = dict(latest_versions)

    def resolve(self, mode: RerunMode) -> RerunResolution:
        if mode is RerunMode.REPRODUCE_HISTORICAL:
            return RerunResolution(
                mode=mode,
                config_versions=dict(self._original),
                label="Reproduce historical run (ORIGINAL config versions)",
            )
        if mode is RerunMode.REEVALUATE_LATEST:
            return RerunResolution(
                mode=mode,
                config_versions=dict(self._latest),
                label="Re-evaluate historical evidence (LATEST config versions)",
            )
        raise ValueError(f"Unknown re-run mode: {mode!r}")  # pragma: no cover

    def distinguishable(self) -> bool:
        """True when the two modes resolve to different config versions."""
        return self._original != self._latest


# ---------------------------------------------------------------------------
# Expanding-window backtest with leakage enforcement
# ---------------------------------------------------------------------------


@dataclass
class BacktestItem:
    """One dated evidence item for a backtest."""

    item_id: str
    available_at: datetime | None
    data_type: str  # "filing" | "news" | "peer" | "market_macro" | ...
    payload: Any = None


@dataclass
class FoldResult:
    """Result of one expanding-window fold at a given cutoff."""

    cutoff: datetime
    admitted: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)  # item_id -> reason

    def as_dict(self) -> dict[str, Any]:
        return {
            "cutoff": self.cutoff.isoformat(),
            "admitted": list(self.admitted),
            "rejected": dict(self.rejected),
        }


@dataclass
class BacktestResult:
    """Full expanding-window backtest result (Req 23.7)."""

    design: str
    folds: list[FoldResult] = field(default_factory=list)
    rolling_window_applicable: bool = False
    rolling_window_note: str = ""

    def rejected_future_count(self) -> int:
        """Total future-dated items rejected across all folds (never mixed in)."""
        return sum(
            1
            for fold in self.folds
            for reason in fold.rejected.values()
            if reason == EligibilityReason.FUTURE_DATED.value
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "design": self.design,
            "rolling_window_applicable": self.rolling_window_applicable,
            "rolling_window_note": self.rolling_window_note,
            "folds": [f.as_dict() for f in self.folds],
        }


def run_expanding_window_backtest(
    items: Iterable[BacktestItem],
    cutoffs: Iterable[datetime],
    *,
    has_learned_model: bool = False,
) -> BacktestResult:
    """Replay ``items`` across chronological expanding-window ``cutoffs``.

    At each fold cutoff, every item is admitted only when it was available by
    that cutoff (Req 20.3); future-dated items are rejected (Req 20.4) and never
    mixed into a past fold (Req 23.7). Because this PoC has no learned model, a
    rolling-window design is reported as not-applicable with justification
    rather than invented.
    """
    items = list(items)
    result = BacktestResult(design="expanding_window")
    for cutoff in sorted(cutoffs):
        fold = FoldResult(cutoff=cutoff)
        for item in items:
            verdict = check_temporal_eligibility(item.available_at, cutoff)
            if verdict.eligible:
                fold.admitted.append(item.item_id)
            else:
                fold.rejected[item.item_id] = verdict.reason.value
        result.folds.append(fold)

    if has_learned_model:  # pragma: no cover - PoC has no learned model
        result.rolling_window_applicable = True
        result.rolling_window_note = "Rolling-window design applied to learned model."
    else:
        result.rolling_window_applicable = False
        result.rolling_window_note = (
            "No learned lower-level ML model is used in this PoC; a rolling-window "
            "design is not applicable (Req 23.7). Expanding-window backtest only."
        )
    return result


def reject_future_item(
    item: BacktestItem, evidence_cutoff_timestamp: datetime
) -> tuple[bool, str]:
    """Return ``(rejected, reason)`` for a single item against a cutoff.

    A convenience for leakage tests: a future-dated filing/news/peer/market-macro
    item must be rejected (Req 20.4).
    """
    verdict = check_temporal_eligibility(item.available_at, evidence_cutoff_timestamp)
    if verdict.eligible:
        return False, EligibilityReason.ELIGIBLE.value
    return True, verdict.reason.value
