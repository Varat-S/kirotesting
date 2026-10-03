"""Unit tests for contemporaneous-vs-future separation (task 9.6, Req 23.6).

Proves: factual accuracy is scored ONLY against contemporaneous ground truth;
there is no path by which a future outcome enters accuracy scoring; a future
outcome placed into inputs at T is rejected by the leakage guard; and predictive
usefulness reads future_outcome WITHOUT affecting contemporaneous scoring.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.evaluation import (
    FutureLeakageError,
    assert_no_future_leakage_in_inputs,
    load_manifest,
    score_contemporaneous_accuracy,
    study_predictive_usefulness,
)

MANIFEST_PATH = (
    Path(__file__).resolve().parents[2]
    / "evals"
    / "manifests"
    / "acme_fy2023_manifest.json"
)


def test_contemporaneous_accuracy_only_reads_contemporaneous_truth() -> None:
    m = load_manifest(MANIFEST_PATH)
    produced = {
        "liquidity_adequate_at_t": True,
        "leverage_within_illustrative_threshold": True,
        "borrower_entity": "ACME-CORP",
        # A future-outcome key sneaked into produced output is simply ignored:
        # it is not part of contemporaneous ground truth, so it cannot score.
        "rating_downgrade": True,
    }
    acc = score_contemporaneous_accuracy(produced, manifest=m)
    assert acc.total == len(m.contemporaneous_ground_truth)
    assert acc.correct == len(m.contemporaneous_ground_truth)
    assert acc.accuracy == 1.0


def test_future_outcome_never_in_contemporaneous_scoring() -> None:
    """The scorer universe is exactly the contemporaneous keys, nothing future."""
    m = load_manifest(MANIFEST_PATH)
    # Even if we pass the future outcome values as "produced", total stays the
    # size of contemporaneous truth; future keys are not scored.
    acc = score_contemporaneous_accuracy(
        {**m.contemporaneous_ground_truth, "default_occurred": False}, manifest=m
    )
    assert acc.total == len(m.contemporaneous_ground_truth)


def test_leakage_guard_rejects_future_outcome_in_inputs() -> None:
    m = load_manifest(MANIFEST_PATH)
    good_inputs = {"revenue": 1500.0, "liquidity_adequate_at_t": True}
    assert_no_future_leakage_in_inputs(good_inputs, manifest=m)  # no raise

    leaky = {"revenue": 1500.0, "rating_downgrade": True}
    with pytest.raises(FutureLeakageError):
        assert_no_future_leakage_in_inputs(leaky, manifest=m)


def test_leakage_guard_rejects_future_tagged_keys() -> None:
    m = load_manifest(MANIFEST_PATH)
    with pytest.raises(FutureLeakageError):
        assert_no_future_leakage_in_inputs({"future_default": True}, manifest=m)
    with pytest.raises(FutureLeakageError):
        assert_no_future_leakage_in_inputs({"q4_outcome": 1}, manifest=m)


def test_predictive_usefulness_reads_future_without_touching_accuracy() -> None:
    m = load_manifest(MANIFEST_PATH)
    study = study_predictive_usefulness(
        contemporaneous_signal=True, manifest=m, outcome_field="rating_downgrade"
    )
    assert study.outcome_occurred is True
    assert study.signalled is True
    assert "never an input at T" in study.note
