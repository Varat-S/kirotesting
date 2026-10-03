"""Metric-definition registry tests (Req 8.2, 8.3, 8.8; task 5.1)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.orm import MetricDefinition
from app.services.metrics.definitions import (
    DEFAULT_METRIC_DEFINITIONS,
    MetricDefinitionRegistry,
)


def test_register_assigns_version_one_and_states_components(db_session: Session) -> None:
    reg = MetricDefinitionRegistry(db_session)
    resolved = reg.register(
        "net_debt",
        {
            "formula_id": "F-NET-DEBT-01",
            "inputs": ["total_debt", "unrestricted_cash"],
            "included": ["total_debt"],
            "excluded": ["restricted_cash"],
            "units": "currency",
        },
    )
    assert resolved.version == 1
    # Req 8.3: definition states which components are included/excluded.
    assert resolved.included == ["total_debt"]
    assert resolved.excluded == ["restricted_cash"]
    assert resolved.inputs == ["total_debt", "unrestricted_cash"]


def test_identical_definition_is_idempotent(db_session: Session) -> None:
    reg = MetricDefinitionRegistry(db_session)
    first = reg.register("net_debt", DEFAULT_METRIC_DEFINITIONS["net_debt"])
    second = reg.register("net_debt", DEFAULT_METRIC_DEFINITIONS["net_debt"])
    assert first.version == second.version == 1
    assert (
        db_session.query(MetricDefinition)
        .filter_by(metric_definition_id="net_debt")
        .count()
        == 1
    )


def test_definition_change_bumps_version_and_does_not_rewrite_history(
    db_session: Session,
) -> None:
    """Req 8.8: a changed definition is a NEW version; history is preserved."""
    reg = MetricDefinitionRegistry(db_session)
    v1 = reg.register(
        "ebitda_metric",
        {"formula_id": "F-X", "inputs": ["op_income"], "included": ["op_income"]},
    )
    v2 = reg.register(
        "ebitda_metric",
        {
            "formula_id": "F-X",
            "inputs": ["op_income", "d_and_a"],
            "included": ["op_income", "d_and_a"],
        },
    )
    assert v1.version == 1 and v2.version == 2
    # The old version is still retrievable unchanged (historical linkage).
    old = reg.get("ebitda_metric", 1)
    assert old is not None and old.included == ["op_income"]
    assert v1.content_hash != v2.content_hash


def test_definition_change_triggers_regression_and_audit(db_session: Session) -> None:
    """Req 8.8: a definition change flags regression + emits an audit event."""
    emitted: list[tuple] = []

    def hook(metric_id, version, before, after):  # noqa: ANN001
        emitted.append((metric_id, version, before, after))

    reg = MetricDefinitionRegistry(db_session, audit_hook=hook)
    reg.register("net_debt", {"formula_id": "F", "inputs": ["a", "b"]})
    assert reg.regression_triggered == set()  # first registration is not a change
    reg.register("net_debt", {"formula_id": "F", "inputs": ["a", "b", "c"]})
    assert "net_debt" in reg.regression_triggered
    # Both registrations emit a change event (initial + change).
    assert [e[1] for e in emitted] == [1, 2]


def test_register_requires_formula_id_no_silent_default(db_session: Session) -> None:
    reg = MetricDefinitionRegistry(db_session)
    try:
        reg.register("bad", {"inputs": ["a"]})
    except ValueError as exc:
        assert "formula_id" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for missing formula_id")


def test_register_defaults_registers_full_set(db_session: Session) -> None:
    reg = MetricDefinitionRegistry(db_session)
    resolved = reg.register_defaults()
    assert "net_debt_to_ebitda" in resolved
    assert resolved["net_debt_to_ebitda"].formula_id == "F-ND-EBITDA-01"
    assert set(reg.all_latest()) == set(DEFAULT_METRIC_DEFINITIONS)
