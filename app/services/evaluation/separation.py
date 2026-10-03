"""Contemporaneous-vs-future evaluation separation (task 9.6, Req 23.6).

Two scoring surfaces are kept strictly separate:

* :func:`score_contemporaneous_accuracy` scores factual accuracy ONLY against
  ``manifest.contemporaneous_ground_truth`` (and ``verified_facts``). It never
  reads ``future_outcome`` -- there is no code path by which a future outcome
  can enter contemporaneous scoring.

* :func:`study_predictive_usefulness` reads ``manifest.future_outcome`` to study
  predictive usefulness WITHOUT feeding it back into inputs at T or into
  contemporaneous scoring.

:func:`assert_no_future_leakage_in_inputs` is a guard used by leakage-style
tests: the model inputs assembled at time T must contain no key from the future
outcome. Future outcomes must NEVER leak into inputs at T, NEVER be treated as
evidence the model was expected to know, and NEVER be mixed into contemporaneous
factual-accuracy scoring (Req 23.6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.services.evaluation.manifest import GroundTruthManifest


class FutureLeakageError(AssertionError):
    """Raised when a future outcome is found inside model inputs at time T."""


@dataclass
class ContemporaneousAccuracy:
    total: int
    correct: int

    @property
    def accuracy(self) -> float | None:
        return (self.correct / self.total) if self.total else None

    def as_dict(self) -> dict[str, Any]:
        return {"total": self.total, "correct": self.correct, "accuracy": self.accuracy}


def score_contemporaneous_accuracy(
    produced: Mapping[str, Any] | None,
    *,
    manifest: GroundTruthManifest | None,
) -> ContemporaneousAccuracy:
    """Score factual accuracy ONLY against contemporaneous ground truth.

    Reads ``manifest.contemporaneous_ground_truth``; never touches
    ``future_outcome`` (there is no parameter or branch that could). Raises when
    no manifest is declared (Req 23.5).
    """
    if manifest is None:
        raise ValueError(
            "score_contemporaneous_accuracy requires a declared manifest (Req 23.5)."
        )
    produced = produced or {}
    truth = manifest.contemporaneous_ground_truth
    total = 0
    correct = 0
    for key, expected in truth.items():
        total += 1
        if key in produced and produced[key] == expected:
            correct += 1
    return ContemporaneousAccuracy(total=total, correct=correct)


@dataclass
class PredictiveUsefulness:
    """Predictive-usefulness study from future_outcome (NOT accuracy scoring)."""

    signalled: bool
    outcome_occurred: bool
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "signalled": self.signalled,
            "outcome_occurred": self.outcome_occurred,
            "note": self.note,
        }


def study_predictive_usefulness(
    contemporaneous_signal: bool,
    *,
    manifest: GroundTruthManifest | None,
    outcome_field: str = "default_occurred",
) -> PredictiveUsefulness:
    """Study whether a contemporaneous signal aligned with a FUTURE outcome.

    This is explicitly separate from factual-accuracy scoring. It reads
    ``manifest.future_outcome`` (which is never fed into inputs at T) purely to
    assess predictive usefulness. Raises when no manifest is declared.
    """
    if manifest is None:
        raise ValueError("study_predictive_usefulness requires a declared manifest.")
    if manifest.future_outcome is None:
        return PredictiveUsefulness(
            signalled=contemporaneous_signal,
            outcome_occurred=False,
            note="No future_outcome declared; predictive usefulness not studied.",
        )
    occurred = bool(getattr(manifest.future_outcome, outcome_field, False))
    return PredictiveUsefulness(
        signalled=contemporaneous_signal,
        outcome_occurred=occurred,
        note=(
            "Future outcome used for predictive-usefulness study only; it was "
            "never an input at T nor part of contemporaneous accuracy."
        ),
    )


def assert_no_future_leakage_in_inputs(
    inputs_at_t: Mapping[str, Any],
    *,
    manifest: GroundTruthManifest,
) -> None:
    """Assert the model inputs at time T contain no future-outcome information.

    Checks that none of the ``future_outcome`` field names (and no obviously
    future-tagged key) appears in ``inputs_at_t``. Raises
    :class:`FutureLeakageError` on violation (Req 23.6).
    """
    if manifest.future_outcome is None:
        return
    future_keys = set(manifest.future_outcome.model_dump(exclude_none=False).keys())
    present = future_keys & set(inputs_at_t.keys())
    if present:
        raise FutureLeakageError(
            f"Future-outcome fields leaked into inputs at T: {sorted(present)}"
        )
    for key in inputs_at_t:
        if str(key).startswith("future_") or str(key).endswith("_outcome"):
            raise FutureLeakageError(
                f"Future-tagged key {key!r} must not appear in inputs at T."
            )
