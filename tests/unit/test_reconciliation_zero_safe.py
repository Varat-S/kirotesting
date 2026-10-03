"""Zero-safe comparison + tolerance-config unit tests (task 4.2, Req 7.1-7.4, 21.1)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.config_registry import ConfigRegistry
from app.schemas.enums import FactStatus
from app.services.reconciliation.service import (
    DEFAULT_TOLERANCES,
    ComparisonMethod,
    Reconciler,
    ToleranceConfig,
)


def _reconciler(db_session: Session) -> Reconciler:
    registry = ConfigRegistry(db_session)
    tol = ToleranceConfig.from_registry(registry)
    return Reconciler(tol, session=db_session, case_id="C")


def test_relative_delta_only_above_near_zero_floor(db_session: Session) -> None:
    rec = _reconciler(db_session)
    # Denominator 1000 >> floor 1.0 -> relative.
    above = rec.compare_values("revenue", 1000.0, 1004.0)
    assert above.comparison_method is ComparisonMethod.RELATIVE
    # delta 4.0 over the larger magnitude 1004 (zero-safe denominator).
    assert above.relative_delta == pytest.approx(4.0 / 1004.0)


def test_division_guard_uses_larger_magnitude_denominator(db_session: Session) -> None:
    """Zero-safe: the divisor is max(|a|,|b|), so a 0 operand never divides.

    With 0.0 vs 2.0 the denominator is 2.0 (> floor 1.0), so a relative delta is
    computed against 2.0 -- never against the 0.0 operand.
    """
    rec = _reconciler(db_session)
    result = rec.compare_values("revenue", 0.0, 2.0)
    assert result.comparison_method is ComparisonMethod.RELATIVE
    assert result.relative_delta == pytest.approx(1.0)  # 2.0 / 2.0
    assert result.resolved_state is FactStatus.CONFLICTING


def test_below_floor_denominator_never_divides(db_session: Session) -> None:
    """When BOTH magnitudes are at/below the floor, use absolute (no division)."""
    rec = _reconciler(db_session)
    result = rec.compare_values("revenue", 0.0, 0.9)
    assert result.comparison_method is ComparisonMethod.ABSOLUTE
    assert result.relative_delta is None  # division avoided near zero
    assert result.resolved_state is FactStatus.VERIFIED  # 0.9 <= abs tol 1.0


def test_exact_zero_vs_zero(db_session: Session) -> None:
    rec = _reconciler(db_session)
    result = rec.compare_values("revenue", 0.0, 0.0)
    assert result.comparison_method is ComparisonMethod.EXACT
    assert result.resolved_state is FactStatus.VERIFIED


def test_tolerance_config_registers_default_versioned(db_session: Session) -> None:
    """Default tolerances are registered through the versioned registry, not silent."""
    registry = ConfigRegistry(db_session)
    tol = ToleranceConfig.from_registry(registry)
    assert tol.version == 1
    assert registry.latest("tolerances").version == 1
    assert tol.near_zero_floor == DEFAULT_TOLERANCES["near_zero_floor"]


def test_tolerance_config_no_silent_default_when_disabled(db_session: Session) -> None:
    """Req 21.1: with register_default=False an absent config raises, not defaults."""
    registry = ConfigRegistry(db_session)
    with pytest.raises(ValueError):
        ToleranceConfig.from_registry(registry, register_default=False)


def test_field_specific_tolerance_resolution(db_session: Session) -> None:
    registry = ConfigRegistry(db_session)
    tol = ToleranceConfig.from_registry(registry)
    ratio = tol.for_field("ratio")
    assert ratio.relative is None  # ratios compare on absolute band
    assert ratio.absolute == 0.01
    unknown = tol.for_field("some_other_field")
    assert unknown.relative == DEFAULT_TOLERANCES["default_relative"]
