"""Small-cohort-aware peer benchmark tests (Req 10.1-10.7; task 5.4)."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.models.orm import Benchmark
from app.services.audit.log import AuditLog, EventType
from app.services.benchmarking.peers import PeerBenchmarker, PeerValue


def test_small_cohort_suppresses_percentiles() -> None:
    """Req 10.1/10.2: a 4-peer cohort suppresses unstable P90/P95."""
    bench = PeerBenchmarker(min_sample_for_percentiles=8)
    result = bench.benchmark(
        "net_debt_to_ebitda",
        "DAL",
        borrower_value=3.0,
        peers=[
            PeerValue("UAL", 3.2),
            PeerValue("AAL", 3.1),
            PeerValue("LUV", 1.5),
            PeerValue("ALK", 2.0),
        ],
    )
    assert result.sample_size == 4
    assert result.percentiles_reliable is False
    assert result.p90 is None and result.p95 is None
    # Raw values + median + min/max are ALWAYS reported (Req 10.1).
    assert result.raw_peer_values == [1.5, 2.0, 3.1, 3.2]
    assert result.median == pytest.approx(2.55)
    assert result.minimum == 1.5 and result.maximum == 3.2


def test_large_cohort_permits_percentiles() -> None:
    bench = PeerBenchmarker(min_sample_for_percentiles=8)
    peers = [PeerValue(f"P{i}", float(i)) for i in range(1, 11)]  # 10 peers 1..10
    result = bench.benchmark("x", "DAL", borrower_value=5.0, peers=peers)
    assert result.sample_size == 10
    assert result.percentiles_reliable is True
    assert result.p90 is not None and result.p95 is not None


def test_borrower_not_double_counted() -> None:
    """Req 10.5: the borrower is removed from its own peer set."""
    bench = PeerBenchmarker()
    result = bench.benchmark(
        "x",
        "DAL",
        borrower_value=3.0,
        peers=[PeerValue("DAL", 3.0), PeerValue("UAL", 3.2), PeerValue("AAL", 2.0)],
    )
    assert result.sample_size == 2  # DAL excluded
    assert "DAL" not in [None]  # sanity
    assert result.raw_peer_values == [2.0, 3.2]


def test_rank_is_deterministic() -> None:
    bench = PeerBenchmarker()
    result = bench.benchmark(
        "x",
        "DAL",
        borrower_value=3.0,
        peers=[PeerValue("UAL", 3.2), PeerValue("AAL", 2.0), PeerValue("LUV", 1.5)],
    )
    # borrower 3.0 is above 2.0 and 1.5 -> rank 3 of 4.
    assert result.rank == 3


def test_synthetic_values_labelled() -> None:
    bench = PeerBenchmarker()
    result = bench.benchmark(
        "x",
        "DAL",
        borrower_value=3.0,
        peers=[PeerValue("SYN", 2.0, synthetic=True), PeerValue("UAL", 3.2)],
    )
    assert result.synthetic is True


def test_benchmark_is_anomaly_signal_only() -> None:
    """Req 10.7: result explicitly marks itself an anomaly signal, never a threshold."""
    bench = PeerBenchmarker()
    result = bench.benchmark("x", "DAL", 3.0, [PeerValue("UAL", 3.2)])
    assert result.usage == "anomaly_signal_only"
    assert "ANOMALY SIGNAL ONLY" in (result.detail or "")


def test_reproducible_from_source() -> None:
    """Req 10.6: identical inputs -> identical output."""
    bench = PeerBenchmarker()
    peers = [PeerValue("UAL", 3.2), PeerValue("AAL", 2.0)]
    a = bench.benchmark("x", "DAL", 3.0, list(peers))
    b = bench.benchmark("x", "DAL", 3.0, list(peers))
    assert a.as_payload() == b.as_payload()


def test_persist_records_method_sample_and_cohort(db_session: Session) -> None:
    audit = AuditLog(db_session)
    bench = PeerBenchmarker(session=db_session, audit=audit, case_id="C1")
    result = bench.benchmark(
        "net_debt_to_ebitda",
        "DAL",
        3.0,
        [PeerValue("UAL", 3.2), PeerValue("AAL", 2.0)],
        cohort_definition={"industry": "us_airlines", "members": ["UAL", "AAL"]},
        cohort_version=1,
        benchmark_date=date(2023, 12, 31),
    )
    bench.persist(result)
    row = db_session.query(Benchmark).filter_by(case_id="C1").one()
    assert row.benchmark_method == "empirical_rank_v1"
    assert row.sample_size == 2
    assert row.cohort_version == 1
    assert row.benchmark_date == date(2023, 12, 31)
    events = audit.events_for_case("C1")
    assert any(e.event_type == EventType.BENCHMARK_GENERATED.value for e in events)
