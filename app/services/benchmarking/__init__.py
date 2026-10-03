"""Benchmarking service: small-cohort-aware peer statistics. Unstable
percentiles are suppressed or labelled; benchmarks are anomaly signals only.

Public API (Milestone 5 task 5.4):

* :mod:`peers` -- :class:`PeerBenchmarker` computing raw peer values, rank,
  median, min/max, quartiles, and reliability-gated P90/P95.
"""

from app.services.benchmarking.peers import (
    BENCHMARK_METHOD,
    DEFAULT_MIN_SAMPLE_FOR_PERCENTILES,
    BenchmarkResult,
    PeerBenchmarker,
    PeerValue,
)

__all__ = [
    "BENCHMARK_METHOD",
    "DEFAULT_MIN_SAMPLE_FOR_PERCENTILES",
    "BenchmarkResult",
    "PeerBenchmarker",
    "PeerValue",
]
