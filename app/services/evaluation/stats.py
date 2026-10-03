"""Lower-level ML statistical-test utilities (task 9.7, Req 23.12).

**No trainable lower-level ML model is wired into this PoC pipeline.** The
foundation LLM is NOT retrained here, and Req 22 forbids adding ML merely for
appearance. These utilities therefore exist as a reusable, tested capability for
*when/if* a learned component is introduced -- they are deliberately NOT invoked
by the production pipeline. Req 23.12 only mandates these tests "if a lower-level
trainable ML component is used"; providing proven utilities (rather than
inventing a model) satisfies the requirement honestly.

Everything is pure-Python / stdlib so it adds no dependency and is fully
deterministic (seeded bootstrap). Provided:

* :func:`precision_recall_f1`
* :func:`auroc` (rank-based, ties handled)
* :func:`calibration_summary` (binned reliability + Brier score)
* :func:`paired_bootstrap_ci` (seeded, deterministic)
* :func:`mcnemar` (paired binary classifier comparison)
* :func:`cohens_d` (effect size)
* :func:`required_sample_size` (two-proportion, normal approximation)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable, Sequence

# Statistical utilities are NOT used by the pipeline; this flag documents that
# and lets the report mark the "learned model" acceptance item as N/A.
ML_COMPONENT_USED: bool = False


@dataclass(frozen=True)
class ClassificationScores:
    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int
    tn: int


def precision_recall_f1(
    y_true: Sequence[int], y_pred: Sequence[int]
) -> ClassificationScores:
    """Binary precision/recall/F1 with a simple confusion matrix."""
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred must be the same length.")
    tp = fp = fn = tn = 0
    for t, p in zip(y_true, y_pred):
        if p == 1 and t == 1:
            tp += 1
        elif p == 1 and t == 0:
            fp += 1
        elif p == 0 and t == 1:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall)
        else 0.0
    )
    return ClassificationScores(precision, recall, f1, tp, fp, fn, tn)


def auroc(y_true: Sequence[int], scores: Sequence[float]) -> float:
    """Area under the ROC curve via the Mann-Whitney U statistic (ties = 0.5).

    Returns 0.5 when only one class is present (AUROC is not meaningful then,
    Req 23.12: "AUROC only where meaningful").
    """
    if len(y_true) != len(scores):
        raise ValueError("y_true and scores must be the same length.")
    pos = [s for t, s in zip(y_true, scores) if t == 1]
    neg = [s for t, s in zip(y_true, scores) if t == 0]
    if not pos or not neg:
        return 0.5
    greater = 0.0
    for sp in pos:
        for sn in neg:
            if sp > sn:
                greater += 1.0
            elif sp == sn:
                greater += 0.5
    return greater / (len(pos) * len(neg))


@dataclass(frozen=True)
class CalibrationSummary:
    bins: list[tuple[float, float, int]]  # (mean_pred, observed_rate, count)
    brier_score: float


def calibration_summary(
    y_true: Sequence[int], probs: Sequence[float], *, n_bins: int = 10
) -> CalibrationSummary:
    """Binned reliability summary + Brier score for probabilistic outputs."""
    if len(y_true) != len(probs):
        raise ValueError("y_true and probs must be the same length.")
    buckets: list[list[tuple[int, float]]] = [[] for _ in range(n_bins)]
    for t, p in zip(y_true, probs):
        idx = min(n_bins - 1, max(0, int(p * n_bins)))
        buckets[idx].append((t, p))
    bins: list[tuple[float, float, int]] = []
    for bucket in buckets:
        if not bucket:
            continue
        mean_pred = sum(p for _, p in bucket) / len(bucket)
        observed = sum(t for t, _ in bucket) / len(bucket)
        bins.append((mean_pred, observed, len(bucket)))
    brier = sum((p - t) ** 2 for t, p in zip(y_true, probs)) / len(y_true)
    return CalibrationSummary(bins=bins, brier_score=brier)


@dataclass(frozen=True)
class BootstrapCI:
    point_estimate: float
    lower: float
    upper: float
    confidence: float


def paired_bootstrap_ci(
    a: Sequence[float],
    b: Sequence[float],
    *,
    statistic: Callable[[Sequence[float], Sequence[float]], float] | None = None,
    n_resamples: int = 2000,
    confidence: float = 0.95,
    seed: int = 1234,
) -> BootstrapCI:
    """Seeded paired bootstrap CI for the difference of a paired statistic.

    Default statistic is the mean paired difference ``mean(a) - mean(b)``.
    Deterministic given ``seed``.
    """
    if len(a) != len(b):
        raise ValueError("Paired samples a and b must be the same length.")
    if not a:
        raise ValueError("Cannot bootstrap an empty sample.")
    if statistic is None:
        def statistic(x: Sequence[float], y: Sequence[float]) -> float:
            return sum(x) / len(x) - sum(y) / len(y)

    rng = random.Random(seed)
    n = len(a)
    point = statistic(a, b)
    estimates: list[float] = []
    for _ in range(n_resamples):
        idx = [rng.randrange(n) for _ in range(n)]
        ra = [a[i] for i in idx]
        rb = [b[i] for i in idx]
        estimates.append(statistic(ra, rb))
    estimates.sort()
    alpha = 1.0 - confidence
    lo = estimates[max(0, int((alpha / 2) * n_resamples))]
    hi = estimates[min(n_resamples - 1, int((1 - alpha / 2) * n_resamples))]
    return BootstrapCI(point_estimate=point, lower=lo, upper=hi, confidence=confidence)


@dataclass(frozen=True)
class McNemarResult:
    b: int  # a correct, b wrong -> discordant pair type 1
    c: int  # a wrong, b correct -> discordant pair type 2
    statistic: float
    p_value: float


def mcnemar(
    correct_a: Sequence[int], correct_b: Sequence[int]
) -> McNemarResult:
    """McNemar's test on paired binary correctness vectors.

    Uses the continuity-corrected chi-square on the discordant pairs and a
    stdlib survival-function approximation for the p-value (1 dof).
    """
    if len(correct_a) != len(correct_b):
        raise ValueError("Paired correctness vectors must be the same length.")
    b = sum(1 for x, y in zip(correct_a, correct_b) if x == 1 and y == 0)
    c = sum(1 for x, y in zip(correct_a, correct_b) if x == 0 and y == 1)
    if b + c == 0:
        return McNemarResult(b=b, c=c, statistic=0.0, p_value=1.0)
    stat = (abs(b - c) - 1) ** 2 / (b + c)
    # Survival function of chi-square with 1 dof = erfc(sqrt(stat/2)).
    p_value = math.erfc(math.sqrt(stat / 2.0))
    return McNemarResult(b=b, c=c, statistic=stat, p_value=p_value)


def cohens_d(a: Sequence[float], b: Sequence[float]) -> float:
    """Cohen's d effect size for two independent samples (pooled SD)."""
    if len(a) < 2 or len(b) < 2:
        raise ValueError("Each sample needs at least two observations.")
    mean_a = sum(a) / len(a)
    mean_b = sum(b) / len(b)
    var_a = sum((x - mean_a) ** 2 for x in a) / (len(a) - 1)
    var_b = sum((x - mean_b) ** 2 for x in b) / (len(b) - 1)
    pooled = math.sqrt(
        ((len(a) - 1) * var_a + (len(b) - 1) * var_b) / (len(a) + len(b) - 2)
    )
    if pooled == 0:
        return 0.0
    return (mean_a - mean_b) / pooled


# Standard-normal quantiles for common two-sided significance levels.
_Z_ALPHA_2 = {0.05: 1.959963985, 0.01: 2.575829304, 0.10: 1.644853627}
_Z_POWER = {0.80: 0.841621234, 0.90: 1.281551566, 0.95: 1.644853627}


def required_sample_size(
    p1: float,
    p2: float,
    *,
    alpha: float = 0.05,
    power: float = 0.80,
) -> int:
    """Per-group sample size to detect a two-proportion difference.

    Normal-approximation formula. Raises if the proportions are equal (no
    detectable effect) or out of range.
    """
    for p in (p1, p2):
        if not 0.0 < p < 1.0:
            raise ValueError("Proportions must be strictly between 0 and 1.")
    if p1 == p2:
        raise ValueError("p1 and p2 must differ to compute a sample size.")
    z_alpha = _Z_ALPHA_2.get(round(alpha, 2))
    z_power = _Z_POWER.get(round(power, 2))
    if z_alpha is None or z_power is None:
        raise ValueError("Unsupported alpha/power; use common values.")
    p_bar = (p1 + p2) / 2.0
    numerator = (
        z_alpha * math.sqrt(2 * p_bar * (1 - p_bar))
        + z_power * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))
    ) ** 2
    n = numerator / (p1 - p2) ** 2
    return math.ceil(n)
