"""Definition/rule version linkage + history immutability (Req 8.8, 11.6; task 5.7)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.models.orm import Metric
from app.schemas.enums import FactStatus
from app.services.metrics.definitions import MetricDefinitionRegistry
from app.services.metrics.engine import MetricEngine, MetricInput


def test_historical_metric_stays_linked_to_definition_version(
    db_session: Session,
) -> None:
    """Req 8.8: changing a definition does NOT rewrite historical outputs.

    A metric computed under v1 keeps pointing at v1 after the definition is
    revised to v2; the stored result is unchanged and reproducible from v1.
    """
    reg = MetricDefinitionRegistry(db_session)
    reg.register(
        "net_debt",
        {
            "formula_id": "F-NET-DEBT-01",
            "inputs": ["total_debt", "unrestricted_cash"],
            "included": ["total_debt"],
            "excluded": ["restricted_cash"],
            "units": "currency",
        },
    )
    v1 = reg.latest("net_debt")
    engine = MetricEngine(session=db_session, case_id="C1")
    r1 = engine.compute(
        v1,
        {
            "total_debt": MetricInput("total_debt", 5000.0, FactStatus.VERIFIED, "d"),
            "unrestricted_cash": MetricInput(
                "unrestricted_cash", 1000.0, FactStatus.VERIFIED, "c"
            ),
        },
        metric_name="net_debt",
    )
    engine.persist(r1)

    # Now change the definition (e.g. exclude operating leases differently).
    reg.register(
        "net_debt",
        {
            "formula_id": "F-NET-DEBT-01",
            "inputs": ["total_debt", "unrestricted_cash"],
            "included": ["total_debt"],
            "excluded": ["restricted_cash", "operating_leases"],
            "units": "currency",
        },
    )
    v2 = reg.latest("net_debt")
    assert v2.version == 2 and v1.version == 1

    # The persisted historical metric still references v1 and its result is
    # untouched (history is not rewritten).
    stored = db_session.query(Metric).filter_by(case_id="C1").one()
    assert stored.metric_definition_version == 1
    assert stored.result == pytest.approx(4000.0)

    # Reproduce the historical run from the exact v1 definition.
    replay = engine.compute(
        reg.get("net_debt", 1),
        {
            "total_debt": MetricInput("total_debt", 5000.0, FactStatus.VERIFIED, "d"),
            "unrestricted_cash": MetricInput(
                "unrestricted_cash", 1000.0, FactStatus.VERIFIED, "c"
            ),
        },
        metric_name="net_debt",
    )
    assert replay.metric_definition_version == 1
    assert replay.result == pytest.approx(stored.result)
