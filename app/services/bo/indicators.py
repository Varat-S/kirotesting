"""Deterministic business indicators for the BO agents (no LLM arithmetic).

Two existing calculation authorities are reused; nothing is recomputed here:

* ``MetricEngine`` results already computed for the case (revenue growth,
  operating margin, capex/revenue, cash from operations less capex) enter the
  run through the existing ``metric_to_parameter`` adapter;
* revenue breakdowns along a single reported dimension (segment, geography,
  product/service, payer category, ...) are computed by the ``ParameterEngine``
  with its registered ``safe_ratio`` and ``hhi`` formulas, always against the
  CONSOLIDATED revenue of the same period.

Every indicator carries a :class:`CalculationTrace` (numerator, denominator,
units, period, source fact ids, formula id, definition version, state).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.schemas.agentic import ParameterResult, ParameterStatus, Topic
from app.schemas.bo import CalculationTrace
from app.services.parameters.engine import ParameterEngine
from app.services.parameters.metric_adapter import metric_to_parameter
from app.services.parameters.registry import (
    ParameterDefinition,
    ParameterDefinitionRegistry,
)

# Metrics shared with the Financial Overview through the common calculation
# layer. BO uses them as context only and draws no debt-capacity conclusion.
_CONTEXT_METRICS: dict[str, dict[str, Any]] = {
    "revenue_growth": {
        "label": "Revenue growth, year on year",
        "numerator": "revenue - revenue_prior", "denominator": "revenue_prior",
    },
    "operating_margin": {
        "label": "Operating income / revenue",
        "numerator": "operating_income", "denominator": "revenue",
    },
    "capex_to_revenue": {
        "label": "Capital expenditure / revenue",
        "numerator": "capex", "denominator": "revenue",
    },
    "free_cash_flow": {
        "label": "Cash from operations less capital expenditure",
        "numerator": "cfo - capex", "denominator": None,
        "caveat": (
            "Operating cash flow less capital expenditure. This is NOT "
            "contractual cash available for debt service; debt-service capacity "
            "is assessed in the Financial Overview."
        ),
    },
}

MAX_MEMBERS_PER_AXIS = 8
SUM_TOLERANCE = 0.01


@dataclass
class IndicatorSet:
    results: list[ParameterResult] = field(default_factory=list)
    traces: list[CalculationTrace] = field(default_factory=list)

    def trace_for(self, parameter_result_id: str) -> CalculationTrace | None:
        return next(
            (t for t in self.traces if t.parameter_result_id == parameter_result_id),
            None,
        )

    def known_values(self) -> dict[str, float]:
        """``{parameter_result_id: value}`` for usable numeric indicators."""
        return {
            r.parameter_result_id: r.value
            for r in self.results
            if r.status is ParameterStatus.OK
            and isinstance(r.value, (int, float)) and not isinstance(r.value, bool)
        }

    def agent_view(self) -> list[dict[str, Any]]:
        """What an agent may quote (by exact ``parameter_result_id``)."""
        by_id = {r.parameter_result_id: r for r in self.results}
        return [
            {
                "parameter_result_id": t.parameter_result_id,
                "parameter_id": t.parameter_id,
                "label": t.label,
                "value": t.value,
                "unit": t.units,
                "period": t.period,
                "numerator": (t.numerator or {}).get("name"),
                "denominator": (t.denominator or {}).get("name"),
                "calculation_state": t.calculation_state,
                "status": by_id[t.parameter_result_id].status.value,
                "caveat": t.caveat,
            }
            for t in self.traces
        ]


def _short(qualified: str) -> str:
    local = re.split(r"[:}]", qualified)[-1]
    local = re.sub(r"(Axis|Member)$", "", local)
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", local).lower()


def build_indicators(evidence, metrics, *, analysis_run_id: str,
                     near_zero_floor: float = 1.0) -> IndicatorSet:
    out = IndicatorSet()
    period = evidence.as_of_date.isoformat() if evidence.as_of_date else None

    for name, meta in _CONTEXT_METRICS.items():
        metric = metrics.get(name)
        if metric is None:
            continue
        result = metric_to_parameter(metric, analysis_run_id=analysis_run_id)
        out.results.append(result)
        inputs = metric.inputs or {}
        out.traces.append(CalculationTrace(
            parameter_result_id=result.parameter_result_id,
            parameter_id=result.parameter_id,
            label=meta["label"],
            value=metric.result,
            units=metric.units,
            period=metric.period or period,
            numerator={"name": meta["numerator"], "inputs": inputs},
            denominator=({"name": meta["denominator"]}
                         if meta["denominator"] else None),
            source_fact_ids=list(metric.input_fact_ids),
            formula_id=metric.formula_id,
            definition_version=f"{metric.metric_definition_id}@"
                               f"{metric.metric_definition_version}",
            calculation_state=metric.state.value,
            caveat=meta.get("caveat"),
        ))

    _revenue_breakdowns(evidence, out, analysis_run_id, near_zero_floor, period)
    return out


def _revenue_breakdowns(evidence, out, analysis_run_id, floor, period):
    if period is None:
        return
    total = None
    by_axis: dict[str, list[tuple[str, float, str | None]]] = {}
    for key, dq in sorted(evidence.data_quality.items()):
        try:
            entity, _year, period_type, end, name, dims, *_rest = json.loads(key)
        except (ValueError, TypeError):
            continue
        if name != "revenue" or period_type != "FY" or end != period:
            continue
        if dq.value is None or dq.state in {"conflicting", "missing"}:
            continue
        if not dims:
            total = (float(dq.value), dq.selected_fact_id)
        elif len(dims) == 1:
            axis, member = dims[0]
            by_axis.setdefault(axis, []).append(
                (member, float(dq.value), dq.selected_fact_id))
    if total is None:
        return
    total_value, total_fact = total

    for axis, members in sorted(by_axis.items()):
        if len(members) < 2:
            continue
        members = sorted(members, key=lambda m: (-m[1], m[0]))[:MAX_MEMBERS_PER_AXIS]
        axis_id = _short(axis)
        definitions = [
            ParameterDefinition(f"revenue_share.{axis_id}.{_short(m)}", Topic.BUSINESS,
                                "safe_ratio", "ratio", units="ratio",
                                description=f"{m} revenue / consolidated revenue.")
            for m, _, _ in members
        ]
        hhi_id = f"revenue_hhi.{axis_id}"
        definitions.append(ParameterDefinition(
            hhi_id, Topic.BUSINESS, "hhi", "index", units="index",
            description=f"Revenue concentration (HHI) along {axis}."))
        engine = ParameterEngine(ParameterDefinitionRegistry(definitions),
                                 near_zero_floor=floor)

        for (member, value, fact_id), definition in zip(members, definitions):
            facts = [f for f in (fact_id, total_fact) if f]
            computed = engine.compute(
                definition.parameter_id,
                {"numerator": value, "denominator": total_value},
                analysis_run_id=analysis_run_id, source_fact_ids=facts)
            out.results.append(computed.result)
            out.traces.append(CalculationTrace(
                parameter_result_id=computed.result.parameter_result_id,
                parameter_id=definition.parameter_id,
                label=f"Share of consolidated revenue: {_short(member)} ({axis_id})",
                value=computed.result.value, units="ratio", period=period,
                numerator={"name": f"revenue[{member}]", "value": value,
                           "fact_id": fact_id},
                denominator={"name": "consolidated_revenue", "value": total_value,
                             "fact_id": total_fact},
                source_fact_ids=facts,
                formula_id=definition.formula_id,
                definition_version=definition.formula_version,
                calculation_state=computed.state.value,
            ))

        member_sum = sum(v for _, v, _ in members)
        complete = abs(member_sum - total_value) <= SUM_TOLERANCE * abs(total_value)
        facts = [f for _, _, f in members if f]
        computed = engine.compute(
            hhi_id, {"shares": [v for _, v, _ in members]},
            analysis_run_id=analysis_run_id, source_fact_ids=facts)
        result = computed.result
        caveat = None
        if not complete:
            # Members that overlap or do not cover the total would make an HHI
            # misleading; keep it out of use rather than present it as a fact.
            caveat = ("Reported members do not sum to consolidated revenue; the "
                      "index is not a complete concentration measure.")
            result = result.model_copy(update={
                "status": ParameterStatus.REQUIRES_REVIEW, "notes": caveat})
        out.results.append(result)
        out.traces.append(CalculationTrace(
            parameter_result_id=result.parameter_result_id,
            parameter_id=hhi_id,
            label=f"Revenue concentration index (HHI): {axis_id}",
            value=result.value, units="index", period=period,
            numerator={"name": "sum of squared member shares",
                       "members": [_short(m) for m, _, _ in members]},
            denominator={"name": "sum_of_reported_members", "value": member_sum},
            source_fact_ids=facts,
            formula_id="hhi",
            definition_version=definitions[-1].formula_version,
            calculation_state=("requires_review" if not complete
                               else computed.state.value),
            caveat=caveat,
        ))
