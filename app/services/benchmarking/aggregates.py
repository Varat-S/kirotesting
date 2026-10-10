"""Method A — published INDUSTRY AGGREGATE benchmarking (deterministic).

This module compares a borrower's deterministic metrics with validated industry
aggregates from the workbook-derived reference dataset. It is a different method
from the empirical peer benchmark in :mod:`app.services.benchmarking.peers` and
shares none of its statistics:

* an industry aggregate is ONE number per metric (sum of numerators over sum of
  denominators across an industry). It is not a median, a percentile or a peer
  observation, so a result here carries no rank, no percentile and no sample
  distribution — those fields do not exist on the result;
* aggregates are never converted into ``PeerValue`` objects and never passed to
  ``PeerBenchmarker`` (see :func:`app.services.benchmarking.peer_cohort.build_peer_cohort`,
  which rejects them);
* the output is contextual. It never produces a risk band and is never an input
  to the scoring engine.

Before any comparison, :func:`assess_comparability` checks the borrower metric's
definition against the benchmark's — numerator, denominator, debt basis, EBITDA
basis, interest basis, lease treatment, balance basis, period basis, accounting
basis, units, the reporting period against the data vintage, and whether the
industry figure is published, derived or assumed — and returns one of
``comparable`` / ``comparable_with_caveats`` / ``not_comparable`` /
``unavailable`` together with the reasons. Nothing is suppressed silently.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.hashing import content_hash
from app.models.orm import IndustryBenchmarkComparisonRow
from app.services.audit.log import ActorType, AuditLog, EventType
from app.services.benchmarking.sector_config import SectorConfig
from app.services.benchmarking.workbook import industry_values

INDUSTRY_AGGREGATE_METHOD = "industry_aggregate_damodaran_v1"
COMPARISON_ENGINE_VERSION = "industry-aggregate-comparison-1.0.0"

COMPARABLE = "comparable"
COMPARABLE_WITH_CAVEATS = "comparable_with_caveats"
NOT_COMPARABLE = "not_comparable"
UNAVAILABLE = "unavailable"
COMPARISON_STATES = (COMPARABLE, COMPARABLE_WITH_CAVEATS, NOT_COMPARABLE, UNAVAILABLE)

USAGE = "contextual_only_not_a_credit_threshold"

# Statistics that exist only for an empirical peer distribution. They must never
# appear on an industry-aggregate result.
PEER_ONLY_FIELDS = frozenset(
    {"rank", "median", "min", "max", "minimum", "maximum", "p25", "p75", "p90",
     "p95", "percentile", "percentiles_reliable", "raw_peer_values", "sample_size"}
)


class ReferenceDataError(ValueError):
    """The reference dataset lacks metadata the sector configuration requires."""


@dataclass
class BorrowerMetric:
    """A deterministic borrower metric presented for comparison.

    ``state`` is one of ``ok`` / ``missing_input`` / ``requires_review`` /
    ``not_meaningful``. ``value`` is None unless ``state == "ok"``; a missing
    input is never zero.
    """

    metric_id: str
    state: str
    value: float | None = None
    units: str | None = None
    period: str | None = None
    fiscal_year: int | None = None
    fact_ids: list[str] = field(default_factory=list)
    definition: dict[str, Any] = field(default_factory=dict)
    detail: str | None = None
    history: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "state": self.state,
            "value": self.value if self.state == "ok" else None,
            "units": self.units,
            "period": self.period,
            "fiscal_year": self.fiscal_year,
            "fact_ids": sorted(self.fact_ids),
            "definition": self.definition,
            "detail": self.detail,
        }


def check_required_metadata(config: SectorConfig, dataset: dict[str, Any]) -> None:
    """Every metadata path the configuration requires must be present."""
    missing = []
    for path in config["required_metadata"]:
        node: Any = dataset
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if node in (None, "", [], {}):
            missing.append(path)
    if missing:
        raise ReferenceDataError(
            f"Reference dataset is missing required metadata {missing}; no "
            "benchmark comparison can be made."
        )


def _months_between(period_end: str | None, vintage: str | None) -> int | None:
    if not period_end or not vintage:
        return None
    try:
        py, pm = int(period_end[:4]), int(period_end[5:7])
        vy, vm = int(vintage[:4]), int(vintage[5:7])
    except (ValueError, IndexError):
        return None
    return abs((vy - py) * 12 + (vm - pm))


def assess_comparability(
    config: SectorConfig,
    comparison: dict[str, Any],
    borrower: BorrowerMetric,
    benchmark: dict[str, Any] | None,
    *,
    vintage: str | None,
) -> dict[str, Any]:
    """Decide whether a borrower metric may be compared with a benchmark.

    Returns ``{"state", "reasons", "caveats", "checks", "warnings"}``. The
    definitional verdict (``not_comparable``) takes precedence over data
    availability (``unavailable``), and both sets of reasons are retained.
    """
    rules = config["comparability"]
    reasons: list[str] = []
    caveats: list[str] = []
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    not_comparable = unavailable = False

    borrower_spec = config["borrower_metrics"][comparison["borrower_metric"]]
    bench_id = comparison["benchmark_metric"]
    bench_spec = config["benchmark_metrics"].get(bench_id) if bench_id else None

    if bench_id is None:
        unavailable = True
        reasons.append("no_industry_benchmark_in_reference_data")
    elif benchmark is None:
        unavailable = True
        reasons.append("benchmark_metric_absent_from_dataset")

    if bench_spec is not None:
        use = bench_spec["use"]
        if use == "diagnostic_only":
            unavailable = True
            reasons.append("benchmark_not_fit_for_comparison:declared_diagnostic_only")
        elif use == "context_only":
            not_comparable = True
            reasons.append("benchmark_declared_context_only")

        b_desc = {**borrower_spec["descriptor"], "units": borrower_spec.get("units")}
        k_desc = {**bench_spec["descriptor"], "units": bench_spec.get("units")}
        equivalences = rules.get("equivalences", {})
        for dimension, rule in rules["dimension_rules"].items():
            b_value, k_value = b_desc.get(dimension), k_desc.get(dimension)
            check = {"dimension": dimension, "borrower": b_value, "benchmark": k_value}
            if b_value is None or k_value is None:
                check["outcome"] = "not_applicable"
            elif "unknown" in (b_value, k_value):
                check["outcome"] = "unknown"
                if rule.get("unknown") == "caveat":
                    caveats.append(
                        f"{dimension}: the industry figure's treatment is not "
                        f"documented (borrower: {b_value})."
                    )
            elif b_value == k_value:
                check["outcome"] = "match"
            elif any({b_value, k_value} == set(pair) for pair in equivalences.get(dimension, [])):
                check["outcome"] = "equivalent_with_caveat"
                key = f"{dimension}:{b_value}~{k_value}"
                caveats.append(
                    rules.get("equivalence_caveats", {}).get(
                        key, f"{dimension}: {b_value} treated as equivalent to {k_value}."
                    )
                )
            elif rule["mismatch"] == "not_comparable":
                check["outcome"] = "mismatch"
                not_comparable = True
                reasons.append(f"{dimension}_mismatch:borrower={b_value},benchmark={k_value}")
            else:
                check["outcome"] = "mismatch_caveat"
                caveats.append(
                    f"{dimension}: borrower uses {b_value}; the industry figure "
                    f"uses {k_value}."
                )
            checks.append(check)
        caveats.extend(bench_spec.get("caveats", []))

    if benchmark is not None:
        status = benchmark["status"]
        verdict = rules["value_status_rules"].get(status)
        checks.append({"dimension": "benchmark_value_status", "benchmark": status,
                       "outcome": verdict or "accepted"})
        if verdict == "not_comparable":
            not_comparable = True
            reasons.append(f"benchmark_value_{status}")
            reasons.extend(r for r in benchmark["reasons"] if r.startswith("depends_on_"))
            warnings.append("W-HC-ASSUMED-PRINCIPAL")
        elif verdict == "unavailable":
            unavailable = True
            reasons.append(f"benchmark_value_{status}")
            reasons.extend(benchmark["reasons"])
        elif verdict:
            caveats.append(verdict)
            warnings.append("W-HC-DERIVED")
        for reason in benchmark["reasons"]:
            if reason == "external_reference_rebound_to_local":
                caveats.append(
                    "The workbook formula referenced an external workbook; it was "
                    "re-derived from the local assumptions sheet and reconciled."
                )
            elif reason.startswith("depends_on_methodology_switch:"):
                caveats.append(
                    "Depends on the workbook's methodology switch "
                    f"{reason.split(':', 1)[1]!r}."
                )

    # Units / currency: ratios, multiples and day counts are currency-neutral.
    units = borrower_spec.get("units")
    checks.append({
        "dimension": "currency",
        "borrower": units,
        "outcome": "requires_same_currency" if units == "currency" else "currency_neutral",
    })

    gap = _months_between(borrower.period, vintage)
    max_gap = config["benchmark"]["max_period_gap_months"]
    checks.append({"dimension": "period_vs_vintage", "borrower": borrower.period,
                   "benchmark": vintage, "gap_months": gap,
                   "outcome": "unknown" if gap is None else
                   ("within_tolerance" if gap <= max_gap else "caveat")})
    if bench_spec is not None and gap is not None and gap > max_gap:
        caveats.append(
            f"Borrower period {borrower.period} and industry data vintage "
            f"{vintage} are {gap} months apart (configured maximum {max_gap})."
        )
        warnings.append("W-HC-PERIOD-GAP")

    if borrower.state != "ok":
        unavailable = True
        reasons.append(f"borrower_metric_{borrower.state}")

    if not_comparable:
        state = NOT_COMPARABLE
    elif unavailable:
        state = UNAVAILABLE
    elif caveats:
        state = COMPARABLE_WITH_CAVEATS
    else:
        state = COMPARABLE
    return {
        "state": state,
        "reasons": _unique(reasons),
        "caveats": _unique(caveats),
        "checks": checks,
        "warnings": _unique(warnings),
    }


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


class IndustryAggregateBenchmarker:
    """Compare borrower metrics with validated industry aggregates (Method A)."""

    method = INDUSTRY_AGGREGATE_METHOD

    def __init__(
        self,
        config: SectorConfig,
        dataset: dict[str, Any],
        *,
        dataset_version: int,
        industry_label: str | None = None,
    ) -> None:
        if config.method != INDUSTRY_AGGREGATE_METHOD:
            raise ReferenceDataError(
                f"Sector configuration selects method {config.method!r}; this "
                f"engine implements {INDUSTRY_AGGREGATE_METHOD!r}."
            )
        check_required_metadata(config, dataset)
        self._config = config
        self._dataset = dataset
        self._dataset_version = dataset_version
        self._industry = industry_label or config.industry_label
        self._values = industry_values(dataset, self._industry)
        self._vintage = dataset["vintage"]["data_as_of"]

    @property
    def industry_label(self) -> str:
        return self._industry

    def source_provenance(self) -> dict[str, Any]:
        """Lineage shared by every comparison built from this dataset."""
        return {
            "benchmark_method": self.method,
            "comparison_engine_version": COMPARISON_ENGINE_VERSION,
            "industry_label": self._industry,
            "source_name": self._config["benchmark"]["source_name"],
            "source_workbook_sha256": self._dataset["source"]["sha256"],
            "source_workbook_filename": self._dataset["source"]["filename"],
            "data_vintage": self._vintage,
            "reference_dataset_hash": self._dataset["dataset_hash"],
            "reference_dataset_version": self._dataset_version,
            "importer_version": self._dataset["importer_version"],
            "workbook_mapping_hash": self._dataset["mapping_hash"],
            "sector_config_kind": self._config.kind,
            "sector_config_version": self._config.version,
            "sector_config_hash": self._config.content_hash,
            "analytical_status": self._config["analytical_status"],
            "aggregate_nature": self._dataset["aggregate_nature"],
        }

    def compare_all(self, borrower_metrics: dict[str, BorrowerMetric]) -> list[dict[str, Any]]:
        out = []
        for comparison in self._config["comparisons"]:
            borrower = borrower_metrics.get(comparison["borrower_metric"])
            if borrower is None:
                borrower = BorrowerMetric(
                    metric_id=comparison["borrower_metric"], state="missing_input",
                    detail="Metric was not computed for this case.",
                )
            out.append(self.compare(comparison, borrower))
        return out

    def compare(self, comparison: dict[str, Any], borrower: BorrowerMetric) -> dict[str, Any]:
        bench_id = comparison["benchmark_metric"]
        benchmark = self._values.get(bench_id) if bench_id else None
        verdict = assess_comparability(
            self._config, comparison, borrower, benchmark, vintage=self._vintage
        )
        state = verdict["state"]
        usable = state in (COMPARABLE, COMPARABLE_WITH_CAVEATS)
        spec = self._config["borrower_metrics"][comparison["borrower_metric"]]

        aggregate: dict[str, Any] | None = None
        if benchmark is not None:
            aggregate = {
                "benchmark_metric_id": bench_id,
                "label": benchmark["label"],
                "definition": benchmark["definition"],
                "workbook_note": benchmark["note"],
                "units": benchmark["units"],
                "value_status": benchmark["status"],
                "declared_use": benchmark["declared_use"],
                # The number is exposed only when the comparison is permitted, so
                # an incompatible or assumed figure cannot be read as a benchmark.
                "value": benchmark["value"] if usable else None,
                "value_withheld": (not usable) and benchmark["value"] is not None,
                "source": {
                    "sheet": benchmark["sheet"],
                    "cell": benchmark["cell"],
                    "formula": benchmark["formula"],
                    "dependencies": benchmark["dependencies"],
                    "validation_reasons": benchmark["reasons"],
                },
            }

        difference = relative = direction = None
        position = "not_assessed"
        if usable and aggregate is not None:
            base = float(aggregate["value"])
            difference = float(borrower.value) - base
            relative = difference / abs(base) if base != 0 else None
            band = self._config["benchmark"]["in_line_relative_band"]
            if relative is not None and abs(relative) <= band:
                direction = "in_line"
            else:
                direction = "above" if difference > 0 else "below"
            adverse = spec.get("adverse_direction")
            if direction == "in_line":
                position = "in_line_with_industry_aggregate"
            elif adverse in ("up", "down"):
                worse = (direction == "above") == (adverse == "up")
                position = (
                    "weaker_than_industry_aggregate" if worse
                    else "stronger_than_industry_aggregate"
                )

        provenance = self.source_provenance()
        result: dict[str, Any] = {
            "comparison_id": comparison["comparison_id"],
            "benchmark_method": self.method,
            "sector_id": self._config.sector_id,
            "industry_label": self._industry,
            "comparison_state": state,
            "reasons": verdict["reasons"],
            "caveats": verdict["caveats"],
            "warnings": verdict["warnings"],
            "comparability_checks": verdict["checks"],
            "borrower": borrower.as_dict(),
            "industry_aggregate": aggregate,
            "difference": difference,
            "relative_difference": relative,
            "direction_vs_aggregate": direction,
            "position_vs_aggregate": position,
            "own_history": borrower.history,
            "adverse_direction": spec.get("adverse_direction"),
            "usage": USAGE,
            "provenance": provenance,
        }
        result["input_hash"] = content_hash(
            {
                "comparison_id": result["comparison_id"],
                "provenance": provenance,
                "benchmark": benchmark,
                "borrower": result["borrower"],
                "own_history": borrower.history,
            }
        )
        return result


class StaleBenchmarkError(RuntimeError):
    """A draft's industry benchmark is stale or superseded; it cannot be finalized."""


def ensure_benchmark_current(session: Session, registry, benchmark: dict[str, Any] | None) -> None:
    """Block finalization of a memo whose benchmark is no longer current.

    No-op for a snapshot without a sector benchmark. Otherwise the sector
    configuration and reference dataset recorded on the draft must still be the
    latest registered versions, and every comparison row the draft references
    must still be ``accepted``. A stale draft is not edited; the case must be
    re-run to produce a new draft.
    """
    if not benchmark:
        return
    sector, reference = benchmark["sector"], benchmark["reference"]
    latest_config = registry.latest(sector["config_kind"])
    if latest_config is None or (
        latest_config.version != sector["config_version"]
        or latest_config.content_hash != sector["config_hash"]
    ):
        raise StaleBenchmarkError(
            f"Sector configuration {sector['config_kind']} v{sector['config_version']} "
            "is no longer current; re-run the case before finalizing."
        )
    latest_dataset = registry.latest(reference["reference_dataset_kind"])
    if latest_dataset is None or (
        latest_dataset.version != reference["reference_dataset_version"]
        or latest_dataset.content_hash != reference["reference_dataset_registry_hash"]
    ):
        raise StaleBenchmarkError(
            "The industry reference dataset has changed since this draft was "
            "built (a different workbook or mapping); re-run the case before "
            "finalizing."
        )
    row_ids = benchmark.get("comparison_row_ids") or []
    if row_ids:
        states = dict(
            session.execute(
                select(
                    IndustryBenchmarkComparisonRow.id,
                    IndustryBenchmarkComparisonRow.acceptance_state,
                ).where(IndustryBenchmarkComparisonRow.id.in_(row_ids))
            ).all()
        )
        stale = sorted(i for i in row_ids if states.get(i) != "accepted")
        if stale:
            raise StaleBenchmarkError(
                f"{len(stale)} industry-benchmark comparison(s) on this draft "
                "were superseded by a later run; finalize the current draft "
                "instead."
            )


def summarize(comparisons: list[dict[str, Any]]) -> dict[str, int]:
    counts = {state: 0 for state in COMPARISON_STATES}
    for comparison in comparisons:
        counts[comparison["comparison_state"]] += 1
    return counts


class ComparisonStore:
    """Append-only persistence of industry-aggregate comparisons."""

    def __init__(self, session: Session, *, audit: AuditLog | None = None) -> None:
        self._session = session
        self._audit = audit

    def accepted(self, case_id: str, sector_id: str) -> list[IndustryBenchmarkComparisonRow]:
        return list(
            self._session.scalars(
                select(IndustryBenchmarkComparisonRow)
                .where(
                    IndustryBenchmarkComparisonRow.case_id == case_id,
                    IndustryBenchmarkComparisonRow.sector_id == sector_id,
                    IndustryBenchmarkComparisonRow.acceptance_state == "accepted",
                )
                .order_by(IndustryBenchmarkComparisonRow.comparison_id)
            )
        )

    def persist_all(
        self,
        comparisons: list[dict[str, Any]],
        *,
        case_id: str,
        snapshot_version: int,
        analysis_run_id: str | None = None,
    ) -> list[IndustryBenchmarkComparisonRow]:
        """Persist comparisons; supersede prior accepted rows whose inputs changed.

        A prior accepted comparison for the same case + comparison id is marked
        ``superseded`` (its payload untouched) and the new row links to it. Rows
        belonging to other cases, and unrelated evidence, are never touched.
        """
        if not comparisons:
            return []
        sector_id = comparisons[0]["sector_id"]
        prior = {row.comparison_id: row for row in self.accepted(case_id, sector_id)}
        rows, superseded = [], []
        for comparison in comparisons:
            previous = prior.get(comparison["comparison_id"])
            supersedes_id = None
            if previous is not None:
                previous.acceptance_state = "superseded"
                supersedes_id = previous.id
                superseded.append(
                    {"id": previous.id, "comparison_id": previous.comparison_id,
                     "inputs_changed": previous.input_hash != comparison["input_hash"]}
                )
            provenance = comparison["provenance"]
            row = IndustryBenchmarkComparisonRow(
                id=f"ibc_{uuid.uuid4().hex[:16]}",
                case_id=case_id,
                snapshot_version=snapshot_version,
                analysis_run_id=analysis_run_id,
                sector_id=sector_id,
                comparison_id=comparison["comparison_id"],
                benchmark_method=comparison["benchmark_method"],
                comparison_state=comparison["comparison_state"],
                payload=comparison,
                source_sha256=provenance["source_workbook_sha256"],
                dataset_hash=provenance["reference_dataset_hash"],
                dataset_version=provenance["reference_dataset_version"],
                sector_config_version=provenance["sector_config_version"],
                sector_config_hash=provenance["sector_config_hash"],
                input_hash=comparison["input_hash"],
                supersedes_id=supersedes_id,
                acceptance_state="accepted",
            )
            self._session.add(row)
            rows.append(row)
        self._session.flush()
        if self._audit is not None:
            if superseded:
                self._audit.record(
                    EventType.INDUSTRY_BENCHMARK_SUPERSEDED,
                    case_id=case_id,
                    actor_type=ActorType.SYSTEM,
                    after={"superseded": superseded},
                    reason="Prior industry-aggregate comparisons superseded by a new run.",
                    linked_objects=[s["id"] for s in superseded],
                )
            self._audit.record(
                EventType.INDUSTRY_BENCHMARK_COMPARED,
                case_id=case_id,
                actor_type=ActorType.SYSTEM,
                after={
                    "benchmark_method": comparisons[0]["benchmark_method"],
                    "industry_label": comparisons[0]["industry_label"],
                    "snapshot_version": snapshot_version,
                    "analysis_run_id": analysis_run_id,
                    "states": summarize(comparisons),
                    "provenance": comparisons[0]["provenance"],
                },
                reason="Industry-aggregate comparisons generated (contextual only).",
                linked_objects=[row.id for row in rows],
            )
        return rows
