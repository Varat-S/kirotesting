"""Unit tests for the statistical utilities (task 9.7, Req 23.12).

The PoC has NO trainable lower-level ML model wired into the pipeline (Req 22):
these utilities exist as a proven capability, demonstrated here on synthetic
data. The ``ML_COMPONENT_USED`` flag documents that no ML is wired in.
"""

from __future__ import annotations

import pytest

from app.services.evaluation.stats import (
    ML_COMPONENT_USED,
    auroc,
    calibration_summary,
    cohens_d,
    mcnemar,
    paired_bootstrap_ci,
    precision_recall_f1,
    required_sample_size,
)


def test_ml_component_not_wired_into_pipeline() -> None:
    """Documented: no learned ML component is used in this PoC pipeline."""
    assert ML_COMPONENT_USED is False


def test_precision_recall_f1_known_confusion() -> None:
    y_true = [1, 1, 1, 0, 0, 0]
    y_pred = [1, 1, 0, 1, 0, 0]
    scores = precision_recall_f1(y_true, y_pred)
    assert scores.tp == 2 and scores.fp == 1 and scores.fn == 1 and scores.tn == 2
    assert scores.precision == pytest.approx(2 / 3)
    assert scores.recall == pytest.approx(2 / 3)
    assert scores.f1 == pytest.approx(2 / 3)


def test_auroc_perfect_and_degenerate() -> None:
    assert auroc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == pytest.approx(1.0)
    # Only one class present -> not meaningful -> 0.5.
    assert auroc([1, 1, 1], [0.3, 0.6, 0.9]) == 0.5


def test_calibration_summary_brier() -> None:
    summary = calibration_summary([1, 0, 1, 0], [0.9, 0.1, 0.8, 0.2], n_bins=5)
    assert 0.0 <= summary.brier_score < 0.1
    assert summary.bins  # non-empty reliability bins


def test_paired_bootstrap_ci_is_deterministic_and_brackets_point() -> None:
    a = [0.9, 0.8, 0.85, 0.95, 0.7, 0.88]
    b = [0.6, 0.55, 0.65, 0.5, 0.6, 0.58]
    ci1 = paired_bootstrap_ci(a, b, seed=42)
    ci2 = paired_bootstrap_ci(a, b, seed=42)
    assert ci1 == ci2  # deterministic
    assert ci1.lower <= ci1.point_estimate <= ci1.upper
    assert ci1.lower > 0  # a is clearly better than b


def test_mcnemar_discordant_pairs() -> None:
    correct_a = [1, 1, 0, 1, 1]
    correct_b = [0, 0, 1, 0, 1]
    result = mcnemar(correct_a, correct_b)
    assert result.b == 3  # a right, b wrong
    assert result.c == 1  # a wrong, b right
    assert 0.0 <= result.p_value <= 1.0


def test_cohens_d_effect_size() -> None:
    a = [10.0, 11.0, 12.0, 13.0]
    b = [1.0, 2.0, 3.0, 4.0]
    assert cohens_d(a, b) > 2.0  # large effect


def test_required_sample_size_sane() -> None:
    n = required_sample_size(0.5, 0.6, alpha=0.05, power=0.80)
    assert n > 100
    with pytest.raises(ValueError):
        required_sample_size(0.5, 0.5)
