"""Small-cohort-aware peer benchmark layer (Req 10.1-10.7; task 5.4).

Compares a borrower metric to a defined peer cohort as an ANOMALY SIGNAL ONLY,
never a credit threshold (Req 10.7). The layer is honest about small samples:

* Always reports raw peer values, rank, median, min/max, and a simple
  distribution (quartiles) (Req 10.1).
* Computes P90/P95 ONLY when the sample size permits; otherwise they are
  suppressed (left ``None``) and ``percentiles_reliable`` is False so a caller
  cannot mistake an unstable percentile for a reliable one (Req 10.1, 10.2).
* Records ``benchmark_method``, ``sample_size``, the cohort definition +
  version, and the benchmark date (Req 10.3).
* Never double-counts the borrower in its own cohort (Req 10.5).
* Labels synthetic/illustrative peer values explicitly (Req 10.4).
* Is reproducible from source data (Req 10.6): same inputs -> same output.

The minimum sample size for reliable percentiles is CONFIGURATION (part of the
``peers`` artifact); nothing is a silent in-code default (Req 21.1).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.models.orm import Benchmark
from app.services.audit.log import ActorType, AuditLog, EventType

# A cohort must have at least this many peers before P90/P95 are treated as
# reliable. Default is illustrative; override via the ``peers`` config.
DEFAULT_MIN_SAMPLE_FOR_PERCENTILES = 8

BENCHMARK_METHOD = "empirical_rank_v1"


@dataclass(frozen=True)
class PeerValue:
    """A single peer observation for a metric."""

    entity_id: str
    value: float
    synthetic: bool = False


@dataclass
class BenchmarkResult:
    """Small-cohort-aware benchmark result (design.md "Benchmark result")."""

    metric_name: str
    cohort_definition: dict[str, Any]
    cohort_version: int | None
    benchmark_date: date | None
    sample_size: int
    benchmark_method: str
    borrower_value: float | None
    raw_peer_values: list[float]
    rank: int | None
    median: float | None
    minimum: float | None
    maximum: float | None
    p25: float | None = None
    p75: float | None = None
    p90: float | None = None
    p95: float | None = None
    percentiles_reliable: bool = False
    synthetic: bool = False
    detail: str | None = None
    # Explicit, machine-readable reminder that this is an anomaly signal only.
    usage: str = "anomaly_signal_only"

    def as_payload(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "cohort_definition": self.cohort_definition,
            "cohort_version": self.cohort_version,
            "benchmark_date": self.benchmark_date.isoformat()
            if self.benchmark_date
            else None,
            "sample_size": self.sample_size,
            "benchmark_method": self.benchmark_method,
            "borrower_value": self.borrower_value,
            "raw_peer_values": self.raw_peer_values,
            "rank": self.rank,
            "median": self.median,
            "min": self.minimum,
            "max": self.maximum,
            "p25": self.p25,
            "p75": self.p75,
            "p90": self.p90,
            "p95": self.p95,
            "percentiles_reliable": self.percentiles_reliable,
            "synthetic": self.synthetic,
            "usage": self.usage,
            "detail": self.detail,
        }


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation percentile (deterministic). ``q`` in [0, 1]."""
    if not sorted_values:
        raise ValueError("percentile of empty sequence")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_values[lo]
    frac = pos - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


class PeerBenchmarker:
    """Compute small-cohort-aware peer benchmarks for a metric."""

    def __init__(
        self,
        *,
        min_sample_for_percentiles: int = DEFAULT_MIN_SAMPLE_FOR_PERCENTILES,
        session: Session | None = None,
        audit: AuditLog | None = None,
        case_id: str | None = None,
    ) -> None:
        self._min_sample = int(min_sample_for_percentiles)
        self._session = session
        self._audit = audit
        self._case_id = case_id

    def benchmark(
        self,
        metric_name: str,
        borrower_entity_id: str,
        borrower_value: float | None,
        peers: list[PeerValue],
        *,
        cohort_definition: dict[str, Any] | None = None,
        cohort_version: int | None = None,
        benchmark_date: date | None = None,
    ) -> BenchmarkResult:
        """Benchmark a borrower against its peer cohort.

        The borrower is removed from the peer set if present so it is never
        double-counted (Req 10.5). Percentiles are only computed when the sample
        permits (Req 10.2); otherwise they are suppressed and
        ``percentiles_reliable`` is False.
        """
        # Prevent borrower double-count (Req 10.5).
        clean_peers = [p for p in peers if p.entity_id != borrower_entity_id]
        peer_values = sorted(p.value for p in clean_peers)
        sample_size = len(peer_values)
        synthetic = any(p.synthetic for p in clean_peers)

        median = _percentile(peer_values, 0.5) if peer_values else None
        minimum = peer_values[0] if peer_values else None
        maximum = peer_values[-1] if peer_values else None
        p25 = _percentile(peer_values, 0.25) if peer_values else None
        p75 = _percentile(peer_values, 0.75) if peer_values else None

        # Percentile reliability gate (Req 10.1, 10.2): only expose P90/P95 when
        # the sample is large enough; otherwise suppress (leave None).
        reliable = sample_size >= self._min_sample
        p90 = _percentile(peer_values, 0.90) if (reliable and peer_values) else None
        p95 = _percentile(peer_values, 0.95) if (reliable and peer_values) else None

        rank = self._rank(borrower_value, peer_values)

        detail = (
            "ANOMALY SIGNAL ONLY — never a credit threshold. "
            + (
                f"Sample size {sample_size} < {self._min_sample}: "
                "P90/P95 suppressed as unstable."
                if not reliable
                else f"Sample size {sample_size} permits percentiles."
            )
        )

        return BenchmarkResult(
            metric_name=metric_name,
            cohort_definition=cohort_definition or {},
            cohort_version=cohort_version,
            benchmark_date=benchmark_date,
            sample_size=sample_size,
            benchmark_method=BENCHMARK_METHOD,
            borrower_value=borrower_value,
            raw_peer_values=peer_values,
            rank=rank,
            median=median,
            minimum=minimum,
            maximum=maximum,
            p25=p25,
            p75=p75,
            p90=p90,
            p95=p95,
            percentiles_reliable=reliable,
            synthetic=synthetic,
            detail=detail,
        )

    def _rank(
        self, borrower_value: float | None, peer_values: list[float]
    ) -> int | None:
        """1-based rank of the borrower among (borrower + peers), ascending.

        Deterministic: ties place the borrower after equal-or-lower peers.
        """
        if borrower_value is None:
            return None
        count_below_or_equal = sum(1 for v in peer_values if v <= borrower_value)
        return count_below_or_equal + 1

    def persist(self, result: BenchmarkResult) -> Benchmark:
        """Persist a benchmark result and emit ``benchmark_generated`` (Req 18.2)."""
        if self._session is None:
            raise ValueError("PeerBenchmarker.persist requires a SQLAlchemy session.")
        row = Benchmark(
            case_id=self._case_id,
            metric_name=result.metric_name,
            cohort_definition=result.cohort_definition,
            cohort_version=result.cohort_version,
            benchmark_date=result.benchmark_date,
            sample_size=result.sample_size,
            benchmark_method=result.benchmark_method,
            borrower_value=result.borrower_value,
            raw_peer_values=list(result.raw_peer_values),
            rank=result.rank,
            median=result.median,
            minimum=result.minimum,
            maximum=result.maximum,
            p25=result.p25,
            p75=result.p75,
            p90=result.p90,
            p95=result.p95,
            percentiles_reliable=result.percentiles_reliable,
            synthetic=result.synthetic,
        )
        self._session.add(row)
        self._session.flush()

        if self._audit is not None:
            self._audit.record(
                EventType.BENCHMARK_GENERATED,
                case_id=self._case_id,
                actor_type=ActorType.SYSTEM,
                after=result.as_payload(),
                reason=(
                    f"Benchmark for {result.metric_name!r} "
                    f"(n={result.sample_size}, anomaly signal only)."
                ),
            )
        return row
