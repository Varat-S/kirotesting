"""Sector benchmarking pipeline stage (deterministic; no LLM).

Runs AFTER the CanonicalEvidenceSnapshot boundary and the baseline metric /
trend stages, only for a case whose package opts in with ``sector_benchmark``.
A case without it is untouched.

Flow::

    reference workbook bytes ──► import + validate ──► versioned reference dataset
    package identity + admitted narrative ──► sector classification
    reviewed evidence + MetricEngine results ──► borrower metrics (+ own history)
    borrower metrics × validated industry aggregates ──► comparability ──► comparisons

The workbook is benchmark reference data. It is hashed, registered through the
ConfigRegistry and audited, but it is never ingested as a borrower document,
never produces a ``Fact`` and never enters the evidence snapshot.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.config_registry import ConfigRegistry
from app.services.audit.log import ActorType, AuditLog, EventType
from app.services.benchmarking.aggregates import (
    BorrowerMetric,
    ComparisonStore,
    IndustryAggregateBenchmarker,
    summarize,
)
from app.services.benchmarking.classification import (
    CompanyIdentity,
    SectorClassifier,
    benchmarking_permitted,
)
from app.services.benchmarking.sector_config import (
    SectorConfig,
    register_reference_dataset,
    register_sector_config,
)
from app.services.benchmarking.workbook import import_workbook
from app.services.metrics.engine import MetricResult
from app.services.metrics.trends import TrendAnalyzer, TrendPoint
from app.services.parameters.engine import ParameterEngine
from app.services.pipeline.inputs import fiscal_pools, metric_inputs

SECTOR_STAGE_VERSION = "sector-benchmark-stage-1.0.0"
_PLACEHOLDER_RUN = "sector_benchmark_stage"
_MAX_DESCRIPTION_PASSAGES = 200


class SectorBenchmarkError(ValueError):
    """The sector benchmark could not be prepared for this case."""


@dataclass
class SectorBenchmarkStage:
    """Result of the stage: the memo/agent payload plus rows still to persist."""

    payload: dict[str, Any]
    config: SectorConfig
    dataset_version: int | None = None
    comparisons: list[dict[str, Any]] = field(default_factory=list)

    @property
    def completed(self) -> bool:
        return self.payload["status"] == "completed"

    def config_versions(self, *, include_dataset: bool) -> dict[str, int]:
        versions = {self.config.kind: self.config.version}
        if include_dataset and self.dataset_version is not None:
            versions[self.config.dataset_kind] = self.dataset_version
        return versions


def register_for_package(registry: ConfigRegistry, package) -> SectorConfig | None:
    """Register the sector configuration a package opts into, if any."""
    request = getattr(package, "sector_benchmark", None)
    if request is None:
        return None
    return register_sector_config(registry, request.sector_id)


# --------------------------------------------------------------------- inputs


def _role_value(inputs, names: list[str]):
    """Signed sum of named fields. Returns ``(value, fact_ids, problem)``.

    A missing field makes the whole role missing (never zero); a conflicting
    field makes it need review.
    """
    total, fact_ids = 0.0, []
    for raw in names:
        sign, name = (-1.0, raw[1:]) if raw.startswith("-") else (1.0, raw)
        item = inputs.get(name)
        if item is not None and item.status.value == "conflicting":
            return None, fact_ids, ("requires_review", f"Input {name!r} is conflicting.")
        if item is None or not item.usable:
            return None, fact_ids, ("missing_input", f"Input {name!r} is missing.")
        total += sign * float(item.value)
        if item.fact_id:
            fact_ids.append(item.fact_id)
    return total, fact_ids, None


def _from_metric_engine(spec, metric: MetricResult | None, metric_id: str) -> BorrowerMetric:
    if metric is None:
        return BorrowerMetric(
            metric_id=metric_id, state="missing_input", units=spec.get("units"),
            detail=f"Metric {spec['metric']!r} is not defined for this case.",
            definition={"source": "metric_engine", "metric_definition_id": spec["metric"]},
        )
    return BorrowerMetric(
        metric_id=metric_id,
        state=metric.state.value,
        value=metric.result if metric.state.value == "ok" else None,
        units=spec.get("units") or metric.units,
        period=metric.period,
        fiscal_year=metric.fiscal_year,
        fact_ids=list(metric.input_fact_ids),
        definition={
            "source": "metric_engine",
            "metric_definition_id": metric.metric_definition_id,
            "metric_definition_version": metric.metric_definition_version,
            "formula_id": metric.formula_id,
            "engine_version": metric.engine_version,
        },
        detail=metric.detail,
    )


class _BorrowerMetricBuilder:
    """Compute the configured borrower metrics for every fiscal year."""

    def __init__(self, config: SectorConfig, evidence, all_metrics, borrower: str,
                 *, near_zero_floor: float) -> None:
        self._config = config
        self._specs = config["borrower_metrics"]
        self._engine = ParameterEngine(near_zero_floor=near_zero_floor)
        self._borrower = borrower
        self._all_metrics = all_metrics
        pools, facts_by_id = fiscal_pools(evidence)
        self._periods = {
            (year, end): metric_inputs(fields, facts_by_id, evidence)
            for (entity, year, end), fields in pools.items()
            if entity == borrower
        }

    def years(self) -> list[tuple[int, str]]:
        return sorted(self._periods)

    def _parameter(self, metric_id, spec, year, end, computed) -> BorrowerMetric:
        inputs = self._periods.get((year, end), {})
        values: dict[str, Any] = {}
        fact_ids: list[str] = []
        problem = None
        if "from_metrics" in spec:
            for role, source in spec["from_metrics"].items():
                other = computed.get(source)
                if other is None or other.state != "ok":
                    state = other.state if other is not None else "missing_input"
                    problem = problem or (
                        state, f"Component metric {source!r} is {state}.")
                    values[role] = None
                else:
                    values[role] = other.value
                    fact_ids.extend(other.fact_ids)
        else:
            for role, names in spec["inputs"].items():
                value, ids, issue = _role_value(inputs, names)
                values[role] = value
                fact_ids.extend(ids)
                problem = problem or issue
        computation = self._engine.compute(
            spec["parameter_id"], values, analysis_run_id=_PLACEHOLDER_RUN,
            source_fact_ids=fact_ids,
        )
        state, detail = computation.state.value, computation.detail
        if problem is not None and problem[0] == "requires_review":
            state, detail = problem
        elif problem is not None and state == "missing_input":
            detail = problem[1]
        return BorrowerMetric(
            metric_id=metric_id,
            state=state,
            value=computation.result.value if state == "ok" else None,
            units=spec.get("units"),
            period=end,
            fiscal_year=year,
            fact_ids=sorted(set(fact_ids)),
            definition={
                "source": "parameter_engine",
                "parameter_id": spec["parameter_id"],
                "formula_id": computation.result.formula_id,
                "formula_version": computation.result.formula_version,
                "engine_version": self._engine.engine_version,
                "inputs": spec.get("inputs") or spec.get("from_metrics"),
            },
            detail=detail,
        )

    def _series(self, metric_id, spec, year, end) -> BorrowerMetric:
        """Multi-year compound growth over the configured window."""
        trend = self._config["trend"]
        field_name = spec["series"]["field"]
        observed = []
        for (y, e), inputs in sorted(self._periods.items()):
            item = inputs.get(field_name)
            if y <= year and item is not None and item.usable:
                observed.append((y, float(item.value), item.fact_id))
        observed = observed[-trend["max_years"]:]
        definition = {
            "source": "parameter_engine", "parameter_id": spec["parameter_id"],
            "series_field": field_name, "engine_version": self._engine.engine_version,
        }
        years = [y for y, _, _ in observed]
        contiguous = years == list(range(years[0], years[-1] + 1)) if years else False
        if len(observed) < trend["min_years"] or not contiguous or years[-1] != year:
            return BorrowerMetric(
                metric_id=metric_id, state="missing_input", units=spec.get("units"),
                period=end, fiscal_year=year, definition=definition,
                detail=(
                    f"Needs {trend['min_years']}-{trend['max_years']} consecutive "
                    f"annual {field_name} observations ending FY{year}; found {years}."
                ),
            )
        fact_ids = [f for _, _, f in (observed[0], observed[-1]) if f]
        computation = self._engine.compute(
            spec["parameter_id"],
            {"begin": observed[0][1], "end": observed[-1][1],
             "periods": years[-1] - years[0]},
            analysis_run_id=_PLACEHOLDER_RUN, source_fact_ids=fact_ids,
        )
        state = computation.state.value
        definition.update({
            "formula_id": computation.result.formula_id,
            "formula_version": computation.result.formula_version,
            "window": {"from_fiscal_year": years[0], "to_fiscal_year": years[-1]},
        })
        return BorrowerMetric(
            metric_id=metric_id, state=state,
            value=computation.result.value if state == "ok" else None,
            units=spec.get("units"), period=end, fiscal_year=year,
            fact_ids=fact_ids, definition=definition, detail=computation.detail,
        )

    def for_period(self, year: int, end: str, current: dict[str, MetricResult] | None = None
                   ) -> dict[str, BorrowerMetric]:
        engine_metrics = current if current is not None else self._all_metrics.get(
            (self._borrower, year, end), {})
        computed: dict[str, BorrowerMetric] = {}
        ordered = sorted(self._specs.items(), key=lambda kv: "from_metrics" in kv[1])
        for metric_id, spec in ordered:
            if spec["source"] == "metric_engine":
                computed[metric_id] = _from_metric_engine(
                    spec, engine_metrics.get(spec["metric"]), metric_id)
            elif "series" in spec:
                computed[metric_id] = self._series(metric_id, spec, year, end)
            else:
                computed[metric_id] = self._parameter(metric_id, spec, year, end, computed)
        return computed


def _history(config: SectorConfig, per_year: dict[int, dict[str, BorrowerMetric]]
             ) -> dict[str, dict[str, Any]]:
    """Own-history trend per metric using the existing TrendAnalyzer."""
    specs = config["borrower_metrics"]
    directed = {m: s["adverse_direction"] for m, s in specs.items()
                if s.get("adverse_direction") in ("up", "down")}
    analyzer = TrendAnalyzer(directed, break_threshold=config["trend"]["break_threshold"])
    out: dict[str, dict[str, Any]] = {}
    for metric_id in specs:
        points = [
            TrendPoint(year, metrics[metric_id].value
                       if metrics[metric_id].state == "ok" else None)
            for year, metrics in sorted(per_year.items())
        ]
        series = [{"fiscal_year": p.fiscal_year, "value": p.value} for p in points]
        if metric_id in directed:
            payload = analyzer.analyze(metric_id, points).as_payload()
            payload["series"] = series
            out[metric_id] = payload
        else:
            out[metric_id] = {
                "metric_name": metric_id, "adverse_direction": None,
                "direction": "not_assessed", "series": series,
                "observed_years": [p.fiscal_year for p in points if p.value is not None],
            }
    return out


# ---------------------------------------------------------------------- stage


def _descriptions(evidence) -> list[dict[str, Any]]:
    passages = [
        {"evidence_id": p.get("evidence_id"), "text": p.get("text") or ""}
        for p in (evidence.narrative_evidence or [])
        if p.get("text")
    ]
    return sorted(passages, key=lambda p: str(p["evidence_id"]))[:_MAX_DESCRIPTION_PASSAGES]


def _identity(package, evidence) -> CompanyIdentity:
    request = package.sector_benchmark
    entity = next(e for e in package.entities if e.entity_id == package.borrower_entity_id)
    declared = request.identity
    return CompanyIdentity(
        entity_id=entity.entity_id,
        legal_name=entity.legal_name,
        cik=declared.cik,
        ticker=declared.ticker or (entity.tickers[0] if entity.tickers else None),
        reported_sic=declared.reported_sic,
        sic_source=declared.sic_source,
        sic_retrieved_at=(declared.sic_retrieved_at.isoformat()
                          if declared.sic_retrieved_at else None),
        identity_verification=declared.identity_verification,
        descriptions=_descriptions(evidence),
    )


def run_sector_benchmark(
    session,
    *,
    case_id: str,
    package,
    evidence,
    metrics: dict[str, MetricResult],
    all_metrics,
    registry: ConfigRegistry,
    config: SectorConfig,
    audit: AuditLog,
    near_zero_floor: float,
) -> SectorBenchmarkStage:
    """Run the deterministic sector benchmarking stage for one case."""
    request = package.sector_benchmark
    data = Path(request.reference_workbook).read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    if request.reference_workbook_sha256 and request.reference_workbook_sha256 != sha256:
        raise SectorBenchmarkError(
            "Reference workbook hash does not match the package's declared SHA-256."
        )
    imported = import_workbook(
        data, config.content, filename=Path(request.reference_workbook).name)
    dataset, report = imported.dataset, imported.report
    dataset_version, dataset_registry_hash = register_reference_dataset(
        registry, config, dataset)
    audit.record(
        EventType.REFERENCE_DATASET_IMPORTED,
        case_id=case_id,
        actor_type=ActorType.SYSTEM,
        after={
            "source_sha256": sha256,
            "dataset_hash": dataset["dataset_hash"],
            "dataset_kind": config.dataset_kind,
            "dataset_version": dataset_version,
            "importer_version": dataset["importer_version"],
            "status_counts": report["status_counts"],
            "issue_counts": report["issue_counts"],
        },
        reason="Benchmark reference workbook imported and validated (reference data, not borrower evidence).",
        linked_objects=[f"config:{config.dataset_kind}:{dataset_version}"],
    )

    classifier = SectorClassifier(config, dataset, session=session, audit=audit)
    classification = classifier.classify(_identity(package, evidence))
    prior = classifier.accepted(case_id, package.borrower_entity_id)
    if (
        prior is not None
        and prior.status == "human_override"
        and (prior.payload.get("human_override") or {}).get("previous_classification_hash")
        == classification["classification_hash"]
    ):
        # The automatic inputs are unchanged, so the recorded human decision stands.
        classification = prior.payload
        classification_id = prior.id
    else:
        row = classifier.persist(
            classification, case_id=case_id,
            supersedes_id=prior.id if prior is not None else None)
        classification_id = row.id

    reference = {
        "source_name": config["benchmark"]["source_name"],
        "source_workbook_filename": dataset["source"]["filename"],
        "source_workbook_sha256": sha256,
        "data_vintage": dataset["vintage"]["data_as_of"],
        "reference_dataset_hash": dataset["dataset_hash"],
        "reference_dataset_kind": config.dataset_kind,
        "reference_dataset_version": dataset_version,
        "reference_dataset_registry_hash": dataset_registry_hash,
        "importer_version": dataset["importer_version"],
        "workbook_mapping_hash": dataset["mapping_hash"],
        "aggregate_nature": dataset["aggregate_nature"],
        "validation": {
            "status_counts": report["status_counts"],
            "issue_counts": report["issue_counts"],
            "corrections": dataset["correction_log"]["corrections"],
        },
        "documentation_sources": dataset["documentation"]["sources"],
    }
    payload: dict[str, Any] = {
        "stage_version": SECTOR_STAGE_VERSION,
        "status": "classification_requires_review",
        "sector": config.identity(),
        "classification_id": classification_id,
        "classification": classification,
        "benchmark_method": config.method,
        "industry_label": None,
        "reference": reference,
        "comparisons": [],
        "summary": summarize([]),
        "standing_limitations": list(config["comparability"]["standing_limitations"]),
        "informational": [r["message"] for r in config["rules"]["informational"]],
        "affects": {"narrative": True, "official_scores": False},
        "qualitative_risk_dimensions": list(config.get("qualitative_risk_dimensions", [])),
        "inapplicable_base_metrics": list(config.get("inapplicable_base_metrics", [])),
    }
    stage = SectorBenchmarkStage(payload=payload, config=config,
                                 dataset_version=dataset_version)
    if not benchmarking_permitted(classification):
        return stage

    industry = classification["industry_label"]
    builder = _BorrowerMetricBuilder(
        config, evidence, all_metrics, package.borrower_entity_id,
        near_zero_floor=near_zero_floor)
    as_of = evidence.as_of_date
    target = (as_of.year, as_of.isoformat())
    targets = [k for k in builder.years() if k[1] == as_of.isoformat()]
    if len(targets) == 1:
        target = targets[0]
    per_year: dict[int, dict[str, BorrowerMetric]] = {}
    window = [k for k in builder.years() if k[1] <= target[1]][-config["trend"]["max_years"]:]
    for year, end in window:
        per_year[year] = builder.for_period(
            year, end, current=metrics if (year, end) == target else None)
    current = per_year.get(target[0]) or builder.for_period(*target, current=metrics)
    per_year[target[0]] = current
    histories = _history(config, per_year)
    for metric_id, metric in current.items():
        metric.history = histories[metric_id]

    benchmarker = IndustryAggregateBenchmarker(
        config, dataset, dataset_version=dataset_version, industry_label=industry)
    comparisons = benchmarker.compare_all(current)
    payload.update({
        "status": "completed",
        "industry_label": industry,
        "as_of_period": {"fiscal_year": target[0], "period_end": target[1]},
        "comparisons": comparisons,
        "summary": summarize(comparisons),
        "history_window": [{"fiscal_year": y, "period_end": e} for y, e in window],
    })
    stage.comparisons = comparisons
    return stage


def persist_comparisons(
    session, stage: SectorBenchmarkStage, *, case_id: str, snapshot_version: int,
    analysis_run_id: str | None, audit: AuditLog,
) -> list[str]:
    """Persist the stage's comparisons once the analysis run (if any) is known."""
    rows = ComparisonStore(session, audit=audit).persist_all(
        stage.comparisons, case_id=case_id, snapshot_version=snapshot_version,
        analysis_run_id=analysis_run_id)
    return [row.id for row in rows]
