"""Method B — empirical company-peer cohort contract.

The empirical benchmark itself is unchanged: :class:`PeerBenchmarker`
(``empirical_rank_v1``) still computes rank, median, quartiles and the
small-sample-gated P90/P95. This module defines the INTEGRATION CONTRACT that
turns real company-level observations into the ``PeerValue`` list it consumes,
with the lineage a cohort needs:

* peer identity, reporting period, metric definition and source provenance on
  every observation;
* explicit exclusion (with a reason) of the subject borrower, of duplicates, of
  observations with a different metric definition or period basis, of
  observations without provenance and of non-usable values;
* a hard refusal of anything that is not a company-level observation. An
  industry aggregate, or a value synthesized from one, can never enter a peer
  cohort — that would manufacture a peer distribution out of a single number.

No peer data is acquired here. The workbook's company sheet lists identities
only and holds no financial observations, so it cannot populate a cohort.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.core.hashing import content_hash
from app.services.benchmarking.peers import BenchmarkResult, PeerBenchmarker, PeerValue

PEER_COHORT_CONTRACT_VERSION = "empirical-peer-cohort-1.0.0"
COMPANY_OBSERVATION = "company_observation"
INDUSTRY_AGGREGATE = "industry_aggregate"


class PeerCohortError(ValueError):
    """Raised when non-company data is offered as a peer observation."""


@dataclass(frozen=True)
class PeerObservation:
    """One real, company-level observation of one metric for one period."""

    entity_id: str
    legal_name: str
    metric_definition_id: str
    metric_definition_version: int | str
    value: float | None
    period_end: date
    period_basis: str  # annual | ttm | quarterly
    source: dict[str, Any] = field(default_factory=dict)  # document/fact lineage
    record_type: str = COMPANY_OBSERVATION
    data_quality: str = "unverified"  # verified | unverified
    synthetic: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "legal_name": self.legal_name,
            "metric_definition_id": self.metric_definition_id,
            "metric_definition_version": str(self.metric_definition_version),
            "value": self.value,
            "period_end": self.period_end.isoformat(),
            "period_basis": self.period_basis,
            "source": self.source,
            "record_type": self.record_type,
            "data_quality": self.data_quality,
            "synthetic": self.synthetic,
        }


@dataclass
class PeerCohort:
    """A resolved cohort: accepted peers plus every exclusion and its reason."""

    metric_definition_id: str
    metric_definition_version: str
    period_basis: str
    borrower_entity_id: str
    included: list[PeerObservation]
    excluded: list[dict[str, Any]]
    cohort_name: str
    cohort_version: int | None

    def peer_values(self) -> list[PeerValue]:
        return [PeerValue(o.entity_id, float(o.value), synthetic=o.synthetic)
                for o in self.included]

    def definition(self) -> dict[str, Any]:
        """The cohort definition recorded on the benchmark result."""
        payload = {
            "contract_version": PEER_COHORT_CONTRACT_VERSION,
            "cohort_name": self.cohort_name,
            "metric_definition_id": self.metric_definition_id,
            "metric_definition_version": self.metric_definition_version,
            "period_basis": self.period_basis,
            "peers": [o.as_dict() for o in self.included],
            "exclusions": self.excluded,
            "data_quality": sorted({o.data_quality for o in self.included}),
        }
        payload["cohort_hash"] = content_hash(payload)
        return payload


def build_peer_cohort(
    observations: list[PeerObservation],
    *,
    borrower_entity_id: str,
    metric_definition_id: str,
    metric_definition_version: int | str,
    period_basis: str = "annual",
    cohort_name: str = "unnamed",
    cohort_version: int | None = None,
    max_period_gap_days: int = 366,
    reference_period_end: date | None = None,
) -> PeerCohort:
    """Resolve company observations into a cohort; exclude with reasons.

    Raises :class:`PeerCohortError` if any observation is not a company-level
    record (e.g. an industry aggregate), rather than silently dropping it: an
    aggregate offered as a peer is a caller bug, not a data-quality exclusion.
    """
    for obs in observations:
        if obs.record_type != COMPANY_OBSERVATION:
            raise PeerCohortError(
                f"{obs.entity_id!r} has record_type {obs.record_type!r}; only "
                "company-level observations may enter an empirical peer cohort. "
                "Industry aggregates are benchmarked with the industry-aggregate "
                "method and are never treated as peers."
            )

    version = str(metric_definition_version)
    included: list[PeerObservation] = []
    excluded: list[dict[str, Any]] = []
    seen: set[str] = set()

    def exclude(obs: PeerObservation, reason: str) -> None:
        excluded.append({"entity_id": obs.entity_id, "legal_name": obs.legal_name,
                         "reason": reason})

    # Per entity, the most recent period is considered first and kept.
    for obs in sorted(
        observations, key=lambda o: (o.entity_id, -o.period_end.toordinal())
    ):
        if obs.entity_id == borrower_entity_id:
            exclude(obs, "subject_borrower")
        elif obs.entity_id in seen:
            exclude(obs, "duplicate_entity")
        elif obs.metric_definition_id != metric_definition_id:
            exclude(obs, f"different_metric_definition:{obs.metric_definition_id}")
        elif str(obs.metric_definition_version) != version:
            exclude(obs, f"different_definition_version:{obs.metric_definition_version}")
        elif obs.period_basis != period_basis:
            exclude(obs, f"different_period_basis:{obs.period_basis}")
        elif obs.value is None:
            exclude(obs, "value_unavailable")
        elif not obs.source:
            exclude(obs, "missing_source_provenance")
        elif (
            reference_period_end is not None
            and abs((obs.period_end - reference_period_end).days) > max_period_gap_days
        ):
            exclude(obs, "reporting_period_out_of_range")
        else:
            seen.add(obs.entity_id)
            included.append(obs)

    return PeerCohort(
        metric_definition_id=metric_definition_id,
        metric_definition_version=version,
        period_basis=period_basis,
        borrower_entity_id=borrower_entity_id,
        included=included,
        excluded=excluded,
        cohort_name=cohort_name,
        cohort_version=cohort_version,
    )


def benchmark_against_cohort(
    benchmarker: PeerBenchmarker,
    cohort: PeerCohort,
    *,
    metric_name: str,
    borrower_value: float | None,
    benchmark_date: date | None = None,
) -> BenchmarkResult:
    """Run the existing empirical benchmark over a resolved cohort."""
    return benchmarker.benchmark(
        metric_name,
        cohort.borrower_entity_id,
        borrower_value,
        cohort.peer_values(),
        cohort_definition=cohort.definition(),
        cohort_version=cohort.cohort_version,
        benchmark_date=benchmark_date,
    )
