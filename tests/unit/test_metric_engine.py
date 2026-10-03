"""Deterministic metric engine tests (Req 8.1, 8.2, 8.4, 8.6; task 5.2)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.orm import Metric
from app.schemas.enums import FactStatus
from app.services.audit.log import AuditLog, EventType
from app.services.metrics.definitions import MetricDefinitionRegistry
from app.services.metrics.engine import (
    ENGINE_VERSION,
    MetricEngine,
    MetricInput,
    MetricState,
)


def _defs(db_session: Session) -> MetricDefinitionRegistry:
    reg = MetricDefinitionRegistry(db_session)
    reg.register_defaults()
    return reg


def _in(name: str, value: float | None, status: FactStatus = FactStatus.VERIFIED,
        fact_id: str | None = None) -> MetricInput:
    return MetricInput(name=name, value=value, status=status, fact_id=fact_id or name)


def test_net_debt_to_ebitda_golden_value(db_session: Session) -> None:
    """Golden example: ND/EBITDA = (total_debt - cash) / ebitda."""
    reg = _defs(db_session)
    engine = MetricEngine()
    definition = reg.latest("net_debt_to_ebitda")
    result = engine.compute(
        definition,
        {
            "total_debt": _in("total_debt", 7000.0),
            "unrestricted_cash": _in("unrestricted_cash", 1000.0),
            "ebitda": _in("ebitda", 2000.0),
        },
        metric_name="net_debt_to_ebitda",
    )
    assert result.state is MetricState.OK
    assert result.result == pytest.approx(3.0)  # (7000-1000)/2000
    assert result.formula_id == "F-ND-EBITDA-01"
    assert result.metric_definition_version == definition.version
    assert result.engine_version == ENGINE_VERSION


def test_zero_denominator_returns_requires_review(db_session: Session) -> None:
    """Req 8.4: a zero/near-zero denominator -> requires_review, not 0/None."""
    reg = _defs(db_session)
    engine = MetricEngine(near_zero_floor=1.0)
    result = engine.compute(
        reg.latest("net_debt_to_ebitda"),
        {
            "total_debt": _in("total_debt", 7000.0),
            "unrestricted_cash": _in("unrestricted_cash", 1000.0),
            "ebitda": _in("ebitda", 0.0),
        },
    )
    assert result.state is MetricState.REQUIRES_REVIEW
    assert result.result is None


def test_negative_denominator_is_economically_misleading(db_session: Session) -> None:
    reg = _defs(db_session)
    engine = MetricEngine()
    result = engine.compute(
        reg.latest("net_debt_to_ebitda"),
        {
            "total_debt": _in("total_debt", 7000.0),
            "unrestricted_cash": _in("unrestricted_cash", 1000.0),
            "ebitda": _in("ebitda", -500.0),
        },
    )
    assert result.state is MetricState.REQUIRES_REVIEW
    assert result.result is None


def test_missing_input_returns_missing_input_state(db_session: Session) -> None:
    """Req 8.4: a missing input yields missing_input (missing is never zero)."""
    reg = _defs(db_session)
    engine = MetricEngine()
    result = engine.compute(
        reg.latest("net_debt_to_ebitda"),
        {
            "total_debt": _in("total_debt", 7000.0),
            "unrestricted_cash": _in("unrestricted_cash", 1000.0),
            "ebitda": MetricInput.missing("ebitda"),
        },
    )
    assert result.state is MetricState.MISSING_INPUT
    assert result.result is None


def test_conflicting_input_returns_requires_review(db_session: Session) -> None:
    """Req 8.4: a conflicting input fact routes the metric to requires_review."""
    reg = _defs(db_session)
    engine = MetricEngine()
    result = engine.compute(
        reg.latest("net_debt_to_ebitda"),
        {
            "total_debt": _in("total_debt", 7000.0, FactStatus.CONFLICTING),
            "unrestricted_cash": _in("unrestricted_cash", 1000.0),
            "ebitda": _in("ebitda", 2000.0),
        },
    )
    assert result.state is MetricState.REQUIRES_REVIEW
    assert result.result is None


def test_net_debt_additive_metric(db_session: Session) -> None:
    reg = _defs(db_session)
    engine = MetricEngine()
    result = engine.compute(
        reg.latest("net_debt"),
        {
            "total_debt": _in("total_debt", 7000.0),
            "unrestricted_cash": _in("unrestricted_cash", 1200.0),
        },
    )
    assert result.state is MetricState.OK
    assert result.result == pytest.approx(5800.0)


def test_identical_rerun_is_deterministic(db_session: Session) -> None:
    """Req 8.6: same inputs + same versions -> identical result."""
    reg = _defs(db_session)
    engine = MetricEngine()
    definition = reg.latest("interest_coverage")
    inputs = {
        "ebitda": _in("ebitda", 2000.0),
        "interest_expense": _in("interest_expense", 500.0),
    }
    a = engine.compute(definition, dict(inputs))
    b = engine.compute(definition, dict(inputs))
    assert a.result == b.result == pytest.approx(4.0)
    assert a.as_payload() == b.as_payload()


def test_persist_emits_metric_calculated(db_session: Session) -> None:
    reg = _defs(db_session)
    audit = AuditLog(db_session)
    engine = MetricEngine(session=db_session, audit=audit, case_id="C1")
    result = engine.compute(
        reg.latest("operating_margin"),
        {
            "operating_income": _in("operating_income", 300.0),
            "revenue": _in("revenue", 1000.0),
        },
    )
    engine.persist(result)
    rows = db_session.query(Metric).filter_by(case_id="C1").all()
    assert len(rows) == 1
    assert rows[0].result == pytest.approx(0.3)
    events = audit.events_for_case("C1")
    assert any(e.event_type == EventType.METRIC_CALCULATED.value for e in events)


def test_persist_stores_explicit_state_not_zero(db_session: Session) -> None:
    reg = _defs(db_session)
    engine = MetricEngine(session=db_session, case_id="C2")
    result = engine.compute(
        reg.latest("cash_conversion"),
        {"cfo": _in("cfo", 100.0), "ebitda": _in("ebitda", 0.0)},
    )
    engine.persist(result)
    row = db_session.query(Metric).filter_by(case_id="C2").one()
    assert row.result is None
    assert row.explicit_state == MetricState.REQUIRES_REVIEW.value
