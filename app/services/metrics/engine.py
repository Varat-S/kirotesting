"""Deterministic financial metric engine (Req 8.1, 8.2, 8.4, 8.7; task 5.2).

The engine is the SOLE numerical authority: metrics are computed in code from
canonical facts, with explicit inputs, a formula id, and a definition version.
No LLM arithmetic can override a deterministic metric (there is no LLM in this
milestone, Req 8.7). Given the same inputs + same definition/engine versions the
engine produces IDENTICAL results (Req 8.6).

Explicit bad-denominator states (Req 8.4). A metric NEVER silently returns 0 or
None when an input is bad. Instead it returns an explicit state:

* ``missing_input``   -- a required input fact is missing/not-disclosed, or the
  input simply was not supplied.
* ``requires_review`` -- a required input fact is ``conflicting``, OR a
  denominator is zero / near-zero / economically misleading (e.g. a negative
  EBITDA leverage ratio) and a human must look.
* ``not_meaningful``  -- the metric is defined but cannot yield a meaningful
  ratio for the supplied values in a non-reviewable way.

Each computed metric stores ``metric_definition_id, metric_definition_version,
formula_id, inputs, input_fact_ids, result|explicit_state, period, units,
engine_version`` (Req 8.2) and emits ``metric_calculated`` when wired to audit.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.models.orm import Metric
from app.schemas.enums import NON_VALUE_STATUSES, FactStatus
from app.schemas.evidence import CanonicalFact
from app.services.audit.log import ActorType, AuditLog, EventType
from app.services.metrics.definitions import ResolvedMetricDefinition

# The engine version is a code-level constant: a change in formula behaviour
# bumps it so historical results stay attributable to the engine that produced
# them (Req 8.2, 8.6).
ENGINE_VERSION = "metric-engine-1.0.0"

# Near-zero floor for denominators. Reuses the M4 tolerances concept; the engine
# can be constructed with a floor read from the versioned ``tolerances`` config.
DEFAULT_NEAR_ZERO_FLOOR = 1.0


class MetricState(str, Enum):
    """Explicit non-numeric metric states (Req 8.4)."""

    OK = "ok"
    NOT_MEANINGFUL = "not_meaningful"
    MISSING_INPUT = "missing_input"
    REQUIRES_REVIEW = "requires_review"


@dataclass
class MetricInput:
    """A resolved numeric input for a formula, with its fact linkage.

    An input is either a usable number (``value`` set, ``status`` a value
    status) or an explicit problem (``missing``/``conflicting``). ``missing`` is
    never coerced to zero.
    """

    name: str
    value: float | None
    status: FactStatus
    fact_id: str | None = None

    @classmethod
    def from_fact(cls, name: str, fact: CanonicalFact) -> "MetricInput":
        value = None if fact.status in NON_VALUE_STATUSES else fact.normalized_value
        return cls(name=name, value=value, status=fact.status, fact_id=fact.fact_id)

    @classmethod
    def missing(cls, name: str) -> "MetricInput":
        return cls(name=name, value=None, status=FactStatus.MISSING, fact_id=None)

    @property
    def usable(self) -> bool:
        return self.value is not None and self.status not in {
            FactStatus.MISSING,
            FactStatus.NOT_DISCLOSED,
            FactStatus.NOT_APPLICABLE,
            FactStatus.CONFLICTING,
        }


@dataclass
class MetricResult:
    """A single computed metric result (design.md "Metric result")."""

    metric_name: str
    metric_definition_id: str
    metric_definition_version: int
    formula_id: str
    engine_version: str
    inputs: dict[str, Any]
    input_fact_ids: list[str]
    state: MetricState
    result: float | None = None
    units: str | None = None
    period: str | None = None
    fiscal_year: int | None = None
    detail: str | None = None

    @property
    def explicit_state(self) -> str | None:
        """The stored explicit state, or None when a numeric result exists."""
        return None if self.state is MetricState.OK else self.state.value

    @property
    def is_meaningful(self) -> bool:
        return self.state is MetricState.OK

    def as_payload(self) -> dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "metric_definition_id": self.metric_definition_id,
            "metric_definition_version": self.metric_definition_version,
            "formula_id": self.formula_id,
            "engine_version": self.engine_version,
            "inputs": self.inputs,
            "input_fact_ids": self.input_fact_ids,
            "result": self.result,
            "explicit_state": self.explicit_state,
            "units": self.units,
            "period": self.period,
            "fiscal_year": self.fiscal_year,
            "detail": self.detail,
        }


# A formula takes resolved inputs + a near-zero floor and returns either a float
# result or an explicit (state, detail) tuple when the result is not a number.
FormulaOutput = tuple[float | None, MetricState, str | None]
Formula = Callable[[dict[str, MetricInput], float], FormulaOutput]


def _guard_inputs(
    inputs: dict[str, MetricInput], required: list[str]
) -> FormulaOutput | None:
    """Apply the universal input guards (Req 8.4) before any arithmetic.

    * A required input absent / missing / not-disclosed -> ``missing_input``.
    * A required input ``conflicting`` -> ``requires_review`` (a human must
      pick before the metric is trustworthy).
    Returns an explicit outcome, or ``None`` if all required inputs are usable.
    """
    for name in required:
        inp = inputs.get(name)
        if inp is None or inp.value is None or inp.status in {
            FactStatus.MISSING,
            FactStatus.NOT_DISCLOSED,
            FactStatus.NOT_APPLICABLE,
        }:
            return (None, MetricState.MISSING_INPUT, f"Input {name!r} is missing.")
        if inp.status is FactStatus.CONFLICTING:
            return (
                None,
                MetricState.REQUIRES_REVIEW,
                f"Input {name!r} is conflicting and needs human review.",
            )
    return None


def _ratio(
    numerator: float, denominator: float, floor: float, *, allow_negative_den: bool
) -> FormulaOutput:
    """Zero-safe ratio with explicit bad-denominator states (Req 8.4).

    * ``|denominator| <= floor`` (includes zero / near-zero) -> ``requires_review``:
      the ratio would be unstable/meaningless.
    * A negative denominator for a leverage-style ratio is economically
      misleading -> ``requires_review`` (unless the caller allows negatives).
    """
    if abs(denominator) <= floor:
        return (
            None,
            MetricState.REQUIRES_REVIEW,
            f"Denominator {denominator} is zero/near-zero (floor={floor}).",
        )
    if denominator < 0 and not allow_negative_den:
        return (
            None,
            MetricState.REQUIRES_REVIEW,
            f"Negative denominator {denominator} is economically misleading.",
        )
    return (numerator / denominator, MetricState.OK, None)


# ---------------------------------------------------------------------------
# Formula library. Keyed by formula_id so a metric definition selects its
# formula explicitly and deterministically.
# ---------------------------------------------------------------------------
def _f_net_debt(inp: dict[str, MetricInput], floor: float) -> FormulaOutput:
    nd = inp["total_debt"].value - inp["unrestricted_cash"].value  # type: ignore[operator]
    return (nd, MetricState.OK, None)


def _f_net_debt_to_ebitda(inp: dict[str, MetricInput], floor: float) -> FormulaOutput:
    net_debt = inp["total_debt"].value - inp["unrestricted_cash"].value  # type: ignore[operator]
    return _ratio(net_debt, inp["ebitda"].value, floor, allow_negative_den=False)  # type: ignore[arg-type]


def _f_operating_margin(inp: dict[str, MetricInput], floor: float) -> FormulaOutput:
    return _ratio(
        inp["operating_income"].value, inp["revenue"].value, floor,  # type: ignore[arg-type]
        allow_negative_den=False,
    )


def _f_revenue_growth(inp: dict[str, MetricInput], floor: float) -> FormulaOutput:
    cur, prior = inp["revenue"].value, inp["revenue_prior"].value
    return _ratio(cur - prior, prior, floor, allow_negative_den=False)  # type: ignore[arg-type,operator]


def _f_interest_coverage(inp: dict[str, MetricInput], floor: float) -> FormulaOutput:
    return _ratio(
        inp["ebitda"].value, inp["interest_expense"].value, floor,  # type: ignore[arg-type]
        allow_negative_den=False,
    )


def _f_cash_conversion(inp: dict[str, MetricInput], floor: float) -> FormulaOutput:
    return _ratio(inp["cfo"].value, inp["ebitda"].value, floor, allow_negative_den=False)  # type: ignore[arg-type]


def _f_free_cash_flow(inp: dict[str, MetricInput], floor: float) -> FormulaOutput:
    return (inp["cfo"].value - inp["capex"].value, MetricState.OK, None)  # type: ignore[operator]


def _f_liquidity(inp: dict[str, MetricInput], floor: float) -> FormulaOutput:
    return (
        inp["unrestricted_cash"].value + inp["undrawn_revolver"].value,  # type: ignore[operator]
        MetricState.OK,
        None,
    )


def _f_capex_to_revenue(inp: dict[str, MetricInput], floor: float) -> FormulaOutput:
    return _ratio(inp["capex"].value, inp["revenue"].value, floor, allow_negative_den=False)  # type: ignore[arg-type]


def _f_load_factor(inp: dict[str, MetricInput], floor: float) -> FormulaOutput:
    return _ratio(inp["rpm"].value, inp["asm"].value, floor, allow_negative_den=False)  # type: ignore[arg-type]


def _f_casm(inp: dict[str, MetricInput], floor: float) -> FormulaOutput:
    return _ratio(
        inp["operating_expense"].value, inp["asm"].value, floor,  # type: ignore[arg-type]
        allow_negative_den=False,
    )


FORMULA_LIBRARY: dict[str, Formula] = {
    "F-NET-DEBT-01": _f_net_debt,
    "F-ND-EBITDA-01": _f_net_debt_to_ebitda,
    "F-OP-MARGIN-01": _f_operating_margin,
    "F-REV-GROWTH-01": _f_revenue_growth,
    "F-INT-COV-01": _f_interest_coverage,
    "F-CFO-EBITDA-01": _f_cash_conversion,
    "F-FCF-01": _f_free_cash_flow,
    "F-LIQ-01": _f_liquidity,
    "F-CAPEX-REV-01": _f_capex_to_revenue,
    "F-LOAD-FACTOR-01": _f_load_factor,
    "F-CASM-01": _f_casm,
}


class MetricEngine:
    """Deterministic metric engine over resolved metric definitions."""

    def __init__(
        self,
        *,
        near_zero_floor: float = DEFAULT_NEAR_ZERO_FLOOR,
        session: Session | None = None,
        audit: AuditLog | None = None,
        case_id: str | None = None,
    ) -> None:
        self._floor = float(near_zero_floor)
        self._session = session
        self._audit = audit
        self._case_id = case_id

    def compute(
        self,
        definition: ResolvedMetricDefinition,
        inputs: dict[str, MetricInput],
        *,
        metric_name: str | None = None,
        period: str | None = None,
        fiscal_year: int | None = None,
    ) -> MetricResult:
        """Compute one metric from resolved inputs under a specific definition.

        The definition's declared ``inputs`` are the required inputs; the guards
        (missing/conflicting) run before any arithmetic, so a bad input yields an
        explicit state rather than a number (Req 8.4).
        """
        name = metric_name or definition.metric_definition_id
        required = definition.inputs
        formula = FORMULA_LIBRARY.get(definition.formula_id)
        if formula is None:
            raise ValueError(
                f"No formula registered for formula_id {definition.formula_id!r}."
            )

        input_payload = {
            k: {"value": v.value, "status": v.status.value, "fact_id": v.fact_id}
            for k, v in inputs.items()
        }
        fact_ids = [v.fact_id for v in inputs.values() if v.fact_id is not None]

        guard = _guard_inputs(inputs, required)
        if guard is not None:
            value, state, detail = guard
        else:
            value, state, detail = formula(inputs, self._floor)

        return MetricResult(
            metric_name=name,
            metric_definition_id=definition.metric_definition_id,
            metric_definition_version=definition.version,
            formula_id=definition.formula_id,
            engine_version=ENGINE_VERSION,
            inputs=input_payload,
            input_fact_ids=fact_ids,
            state=state,
            result=value,
            units=definition.units,
            period=period,
            fiscal_year=fiscal_year,
            detail=detail,
        )

    def persist(self, result: MetricResult) -> Metric:
        """Persist a metric result and emit ``metric_calculated`` (Req 18.2)."""
        if self._session is None:
            raise ValueError("MetricEngine.persist requires a SQLAlchemy session.")
        row = Metric(
            case_id=self._case_id,
            metric_name=result.metric_name,
            metric_definition_id=result.metric_definition_id,
            metric_definition_version=result.metric_definition_version,
            formula_id=result.formula_id,
            engine_version=result.engine_version,
            inputs=result.inputs,
            input_fact_ids=list(result.input_fact_ids),
            result=result.result,
            explicit_state=result.explicit_state,
            period=result.period,
            fiscal_year=result.fiscal_year,
            units=result.units,
            detail=result.detail,
        )
        self._session.add(row)
        self._session.flush()

        if self._audit is not None:
            self._audit.record(
                EventType.METRIC_CALCULATED,
                case_id=self._case_id,
                actor_type=ActorType.SYSTEM,
                after=result.as_payload(),
                reason=(
                    f"Metric {result.metric_name!r} computed "
                    f"({result.explicit_state or 'ok'})."
                ),
                linked_objects=list(result.input_fact_ids),
            )
        return row
