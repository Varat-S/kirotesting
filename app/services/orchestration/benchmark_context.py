"""Bounded industry-benchmark context for the agentic layer.

Turns the deterministic sector-benchmark payload into:

1. accepted, deterministic ``ParameterResult`` records for every number an agent
   may refer to (the borrower metric, the industry aggregate and their
   difference), so the existing quote-by-``parameter_result_id`` rule applies to
   benchmark numbers exactly as it does to every other number; and
2. a small, validated ``benchmark_context`` payload for the Financial
   Orchestrator: comparison states, compatibility flags, source metadata and
   those parameter ids. It never contains the workbook, raw cells, company
   lists, or an aggregate that failed validation or comparability.

It also provides the deterministic checks applied to the orchestrator's answer:
an LLM may interpret a deviation but may not state a metric number it did not
quote from an accepted result, and may not assign a score, band or rating.

Benchmark parameter ids are namespaced ``sector_benchmark.*``. No such id exists
in the scoring configuration, so these results cannot contribute to an official
risk score even if they were handed to the scoring engine.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from app.core.hashing import canonical_json, content_hash
from app.schemas.agentic import (
    AcceptanceState,
    Method,
    ParameterResult,
    ParameterStatus,
    Topic,
)
from app.services.benchmarking.aggregates import (
    COMPARABLE,
    COMPARABLE_WITH_CAVEATS,
    USAGE,
)

BENCHMARK_CONTEXT_VERSION = "benchmark-context-1.0.0"
PARAMETER_NAMESPACE = "sector_benchmark."
DIFFERENCE_FORMULA_ID = "industry_aggregate_difference_v1"
MAX_CONTEXT_COMPARISONS = 40
MAX_CONTEXT_CHARS = 60_000

_USABLE = (COMPARABLE, COMPARABLE_WITH_CAVEATS)
_STATE_TO_STATUS = {
    "ok": ParameterStatus.OK,
    "missing_input": ParameterStatus.UNAVAILABLE,
    "not_meaningful": ParameterStatus.UNAVAILABLE,
    "requires_review": ParameterStatus.REQUIRES_REVIEW,
}
_CONTEXT_KEYS = frozenset({
    "context_version", "usage", "sector_id", "sector_display_name",
    "benchmark_method", "industry_label", "data_vintage", "source_name",
    "source_workbook_sha256", "reference_dataset_hash", "reference_dataset_version",
    "sector_config_version", "sector_config_hash", "analytical_status",
    "aggregate_nature", "standing_limitations", "classification", "comparisons",
    "role_constraints", "context_hash",
})
_FORBIDDEN_CONTEXT_KEYS = frozenset({
    "workbook", "cells", "sheets", "companies", "sic_codes", "metrics", "dataset",
    "raw", "documents", "formula",
})
# Keys an orchestrator may not use: official scores are deterministic only.
_SCORE_KEYS = frozenset({
    "score", "scores", "band", "risk_band", "risk_score", "rating", "credit_rating",
    "risk_rating", "grade", "official_score",
})
_METRIC_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_.])-?(?:\d+\.\d+|\d+(?=\s?(?:x\b|%|bps\b|days?\b|percent\b)))"
)


class BenchmarkContextError(ValueError):
    """The benchmark context violates its bounded-input contract."""


def reference_id(comparison: dict[str, Any]) -> str:
    """Stable id of the reference-data cell behind an industry aggregate."""
    source = comparison["industry_aggregate"]["source"]
    sha = comparison["provenance"]["source_workbook_sha256"]
    return f"refdata:{sha[:16]}:{source['sheet']}!{source['cell']}"


def _pr_id() -> str:
    return f"pr_{uuid.uuid4().hex[:16]}"


def build_benchmark_parameter_results(
    sector_payload: dict[str, Any], *, analysis_run_id: str
) -> list[ParameterResult]:
    """Create the deterministic ParameterResults behind the benchmark context.

    Each comparison in ``sector_payload`` is annotated in place with a
    ``parameter_result_ids`` mapping so the persisted comparison, the memo and
    the agent context all point at the same accepted results.
    """
    results: list[ParameterResult] = []
    for comparison in sector_payload["comparisons"]:
        ids: dict[str, str | None] = {"borrower": None, "industry_aggregate": None,
                                      "difference": None}
        borrower = comparison["borrower"]
        definition = borrower.get("definition") or {}
        borrower_pr = ParameterResult(
            parameter_result_id=_pr_id(),
            analysis_run_id=analysis_run_id,
            parameter_id=f"{PARAMETER_NAMESPACE}borrower.{borrower['metric_id']}",
            topic=Topic.FINANCIAL,
            value=borrower["value"] if borrower["state"] == "ok" else None,
            value_type="number" if borrower.get("units") in ("currency", "days") else "ratio",
            method=Method.DETERMINISTIC,
            status=_STATE_TO_STATUS.get(borrower["state"], ParameterStatus.REQUIRES_REVIEW),
            source_fact_ids=list(borrower.get("fact_ids") or []),
            formula_id=str(definition.get("metric_definition_id")
                           or definition.get("parameter_id") or "unknown"),
            formula_version=str(definition.get("metric_definition_version")
                                or definition.get("formula_version") or "unknown"),
            missing_information=(
                [borrower["detail"]] if borrower["state"] in ("missing_input", "not_meaningful")
                and borrower.get("detail") else []),
            notes=(f"calculation_state={borrower['state']}; period={borrower.get('period')}; "
                   f"units={borrower.get('units')}"),
            acceptance_state=AcceptanceState.ACCEPTED,
        )
        results.append(borrower_pr)
        ids["borrower"] = borrower_pr.parameter_result_id

        aggregate = comparison.get("industry_aggregate")
        if comparison["comparison_state"] in _USABLE and aggregate and aggregate["value"] is not None:
            provenance = comparison["provenance"]
            ref = reference_id(comparison)
            aggregate_pr = ParameterResult(
                parameter_result_id=_pr_id(),
                analysis_run_id=analysis_run_id,
                parameter_id=(f"{PARAMETER_NAMESPACE}industry_aggregate."
                              f"{aggregate['benchmark_metric_id']}"),
                topic=Topic.FINANCIAL,
                value=aggregate["value"],
                value_type="number" if aggregate.get("units") == "days" else "ratio",
                method=Method.DETERMINISTIC,
                status=ParameterStatus.OK,
                evidence_ids=[ref],
                formula_id=provenance["benchmark_method"],
                formula_version=provenance["importer_version"],
                input_hash=provenance["reference_dataset_hash"],
                notes=(f"Industry aggregate ({comparison['industry_label']}, "
                       f"{aggregate['value_status']}, vintage {provenance['data_vintage']}); "
                       f"reference data, not a peer observation; {ref}"),
                acceptance_state=AcceptanceState.ACCEPTED,
            )
            difference_pr = ParameterResult(
                parameter_result_id=_pr_id(),
                analysis_run_id=analysis_run_id,
                parameter_id=f"{PARAMETER_NAMESPACE}difference.{comparison['comparison_id']}",
                topic=Topic.FINANCIAL,
                value=comparison["difference"],
                value_type="number",
                method=Method.DETERMINISTIC,
                status=ParameterStatus.OK,
                source_parameter_ids=[borrower_pr.parameter_result_id,
                                      aggregate_pr.parameter_result_id],
                evidence_ids=[ref],
                formula_id=DIFFERENCE_FORMULA_ID,
                formula_version=provenance["comparison_engine_version"],
                input_hash=comparison["input_hash"],
                notes="Borrower metric minus industry aggregate (contextual only).",
                acceptance_state=AcceptanceState.ACCEPTED,
            )
            results.extend([aggregate_pr, difference_pr])
            ids["industry_aggregate"] = aggregate_pr.parameter_result_id
            ids["difference"] = difference_pr.parameter_result_id
        comparison["parameter_result_ids"] = ids
    return results


def build_benchmark_context(sector_payload: dict[str, Any]) -> dict[str, Any]:
    """Build the bounded, validated context for the Financial Orchestrator."""
    if sector_payload.get("status") != "completed":
        raise BenchmarkContextError("No completed sector benchmark to contextualize.")
    reference, sector = sector_payload["reference"], sector_payload["sector"]
    comparisons = []
    for comparison in sector_payload["comparisons"][:MAX_CONTEXT_COMPARISONS]:
        ids = comparison.get("parameter_result_ids")
        if ids is None:
            raise BenchmarkContextError(
                f"Comparison {comparison['comparison_id']!r} has no accepted "
                "parameter results; build them before the context."
            )
        usable = comparison["comparison_state"] in _USABLE
        borrower = comparison["borrower"]
        history = comparison.get("own_history") or {}
        entry: dict[str, Any] = {
            "comparison_id": comparison["comparison_id"],
            "comparison_state": comparison["comparison_state"],
            "reasons": list(comparison["reasons"]),
            "caveats": list(comparison["caveats"]),
            "borrower": {
                "parameter_result_id": ids["borrower"],
                "value": borrower["value"],
                "calculation_state": borrower["state"],
                "units": borrower.get("units"),
                "period": borrower.get("period"),
            },
            "industry_aggregate": None,
            "difference": None,
            "direction_vs_aggregate": comparison["direction_vs_aggregate"] if usable else None,
            "position_vs_aggregate": comparison["position_vs_aggregate"] if usable else "not_assessed",
            "own_history": {
                "direction": history.get("direction", "indeterminate"),
                "observed_fiscal_years": list(history.get("observed_years") or []),
            },
        }
        if usable:
            aggregate = comparison["industry_aggregate"]
            entry["industry_aggregate"] = {
                "parameter_result_id": ids["industry_aggregate"],
                "value": aggregate["value"],
                "units": aggregate.get("units"),
                "value_status": aggregate["value_status"],
                "definition": aggregate["definition"],
                "reference_id": reference_id(comparison),
            }
            entry["difference"] = {
                "parameter_result_id": ids["difference"],
                "value": comparison["difference"],
            }
        comparisons.append(entry)

    classification = sector_payload["classification"]
    context: dict[str, Any] = {
        "context_version": BENCHMARK_CONTEXT_VERSION,
        "usage": USAGE,
        "sector_id": sector["sector_id"],
        "sector_display_name": sector["display_name"],
        "benchmark_method": sector_payload["benchmark_method"],
        "industry_label": sector_payload["industry_label"],
        "data_vintage": reference["data_vintage"],
        "source_name": reference["source_name"],
        "source_workbook_sha256": reference["source_workbook_sha256"],
        "reference_dataset_hash": reference["reference_dataset_hash"],
        "reference_dataset_version": reference["reference_dataset_version"],
        "sector_config_version": sector["config_version"],
        "sector_config_hash": sector["config_hash"],
        "analytical_status": sector["analytical_status"],
        "aggregate_nature": reference["aggregate_nature"],
        "standing_limitations": list(sector_payload["standing_limitations"]),
        "classification": {
            "status": classification["status"],
            "confidence": classification["confidence"],
            "review_notes": [n["code"] for n in classification["review_notes"]],
        },
        "comparisons": comparisons,
        "role_constraints": [
            "Interpret deviations; never recalculate a ratio.",
            "Quote a number only via quoted_values bound to its parameter_result_id.",
            "An industry aggregate is not a median, percentile or peer rank.",
            "A favourable comparison is context, not evidence of low default risk.",
            "Contrast position versus the aggregate with the borrower's own history.",
            "Never assign or alter a score, band or rating.",
            "Never compare a not_comparable or unavailable item.",
        ],
    }
    context["context_hash"] = content_hash(context)
    validate_benchmark_context(context)
    return context


def validate_benchmark_context(context: dict[str, Any]) -> None:
    """Enforce the bounded-input contract; raises :class:`BenchmarkContextError`."""
    unexpected = set(context) - _CONTEXT_KEYS
    if unexpected:
        raise BenchmarkContextError(f"Unexpected benchmark context keys {sorted(unexpected)}.")
    if len(context["comparisons"]) > MAX_CONTEXT_COMPARISONS:
        raise BenchmarkContextError("Too many comparisons in the benchmark context.")
    if len(canonical_json(context)) > MAX_CONTEXT_CHARS:
        raise BenchmarkContextError("Benchmark context exceeds its size bound.")

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            bad = set(node) & _FORBIDDEN_CONTEXT_KEYS
            if bad:
                raise BenchmarkContextError(
                    f"Benchmark context carries raw reference data keys {sorted(bad)}."
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(context)
    for entry in context["comparisons"]:
        usable = entry["comparison_state"] in _USABLE
        aggregate = entry["industry_aggregate"]
        if not usable and (aggregate is not None or entry["difference"] is not None):
            raise BenchmarkContextError(
                f"Comparison {entry['comparison_id']!r} is "
                f"{entry['comparison_state']} but exposes an aggregate value."
            )
        if usable:
            if aggregate is None or aggregate["parameter_result_id"] is None:
                raise BenchmarkContextError(
                    f"Comparison {entry['comparison_id']!r} lacks an accepted "
                    "industry-aggregate parameter result."
                )
            if aggregate["value_status"] not in ("source_aggregate", "derived"):
                raise BenchmarkContextError(
                    f"Comparison {entry['comparison_id']!r} exposes a "
                    f"{aggregate['value_status']} aggregate."
                )


def context_evidence_ids(context: dict[str, Any]) -> list[str]:
    """Reference-data ids an orchestrator claim may cite."""
    return sorted({
        entry["industry_aggregate"]["reference_id"]
        for entry in context["comparisons"] if entry["industry_aggregate"]
    })


def known_parameter_values(results: list[ParameterResult]) -> dict[str, Any]:
    """``{parameter_result_id: value}`` for accepted numeric results."""
    return {
        r.parameter_result_id: r.value
        for r in results
        if r.acceptance_state is AcceptanceState.ACCEPTED
        and r.status is ParameterStatus.OK
        and isinstance(r.value, (int, float)) and not isinstance(r.value, bool)
    }


def _claims(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for key in ("overall_assessment", "strengths", "weaknesses", "key_drivers",
                "material_risks"):
        value = parsed.get(key)
        if isinstance(value, dict):
            claims.append(value)
        elif isinstance(value, list):
            claims.extend(c for c in value if isinstance(c, dict))
    return claims


def _matches(number: float, quoted: list[float]) -> bool:
    """A displayed number may be a quoted value rounded, or its percentage form."""
    for value in quoted:
        for candidate in (value, value * 100.0):
            for digits in range(0, 5):
                if abs(round(candidate, digits) - number) <= 1e-9:
                    return True
    return False


def validate_benchmark_narrative(parsed: dict[str, Any]) -> list[str]:
    """Deterministic guards on an orchestrator answer that used benchmark context.

    Returns a list of issue strings (empty when acceptable):

    * no official score / band / rating may be assigned anywhere in the answer;
    * every metric-like number written in a claim's text (a decimal, or a number
      followed by ``x``, ``%``, ``bps`` or ``days``) must correspond to a value in
      that claim's ``quoted_values`` — i.e. to an accepted ParameterResult. A
      number the model computed itself has no such binding and is rejected.
    """
    issues: list[str] = []

    def scan(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() in _SCORE_KEYS:
                    issues.append(
                        f"official_score_assignment: key {path}{key!r} is not "
                        "permitted; scores are deterministic."
                    )
                scan(value, f"{path}{key}.")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                scan(value, f"{path}{index}.")

    scan(parsed, "")
    for claim in _claims(parsed):
        quoted = [
            float(q["value"]) for q in (claim.get("quoted_values") or [])
            if isinstance(q, dict) and isinstance(q.get("value"), (int, float))
        ] if isinstance(claim.get("quoted_values"), list) else []
        for match in _METRIC_NUMBER.finditer(claim.get("text") or ""):
            number = float(match.group(0))
            if not _matches(abs(number), [abs(v) for v in quoted]):
                issues.append(
                    f"unquoted_number: claim {claim.get('claim_id', '?')!r} states "
                    f"{match.group(0)!r} without a matching quoted ParameterResult."
                )
    return issues
