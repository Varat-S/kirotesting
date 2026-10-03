"""Fact-status distinctness tests (Req 4.4 / 5.4).

Covers the critical rule that ``missing`` is NEVER numerical zero and that the
data-quality states remain distinct, plus the provenance guarantee (Req 3.5).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.enums import NON_VALUE_STATUSES, FactStatus
from app.schemas.evidence import CanonicalFact, SourceRef

REF = SourceRef(document_id="DOC-1", page=3)


def _fact(**overrides) -> CanonicalFact:
    base = dict(
        fact_id="F-1",
        name="total_debt",
        status=FactStatus.VERIFIED,
        normalized_value=1000.0,
        normalized_unit="USD_millions",
        source_refs=[REF],
    )
    base.update(overrides)
    return CanonicalFact(**base)


def test_all_statuses_are_distinct() -> None:
    values = [s.value for s in FactStatus]
    assert len(values) == len(set(values)) == 7
    assert set(values) == {
        "verified",
        "unverified",
        "conflicting",
        "missing",
        "not_applicable",
        "not_disclosed",
        "stale",
    }


def test_missing_is_not_zero() -> None:
    """A missing fact carries no value; a zero fact is a distinct, real value."""
    missing = _fact(status=FactStatus.MISSING, normalized_value=None, raw_value=None)
    zero = _fact(status=FactStatus.VERIFIED, normalized_value=0.0, raw_value="0")

    assert missing.is_missing is True
    assert missing.normalized_value is None
    assert zero.is_missing is False
    assert zero.normalized_value == 0.0
    # The two are not interchangeable.
    assert missing.status is not zero.status


@pytest.mark.parametrize("status", sorted(NON_VALUE_STATUSES, key=lambda s: s.value))
def test_non_value_status_rejects_numeric_value(status: FactStatus) -> None:
    """missing / not_applicable / not_disclosed must not carry a value."""
    with pytest.raises(ValidationError):
        _fact(status=status, normalized_value=0.0, raw_value=None)
    with pytest.raises(ValidationError):
        _fact(status=status, normalized_value=None, raw_value="0")


def test_every_fact_requires_a_source_ref() -> None:
    """Req 3.5: every factual field carries at least one source reference."""
    with pytest.raises(ValidationError):
        _fact(source_refs=[])


def test_raw_and_normalized_preserved_separately() -> None:
    """Req 4.4: raw extracted value/unit kept distinct from normalized."""
    fact = _fact(
        raw_value="1,000",
        raw_unit="thousands",
        normalized_value=1_000_000.0,
        normalized_unit="USD",
        normalization_method="scale_thousands_to_units",
    )
    assert fact.raw_value == "1,000"
    assert fact.raw_unit == "thousands"
    assert fact.normalized_value == 1_000_000.0
    assert fact.normalized_unit == "USD"


def test_qualitative_fact_need_not_populate_financial_fields() -> None:
    """Req 4.2: qualitative facts may omit financial metadata."""
    fact = CanonicalFact(
        fact_id="Q-1",
        name="management_commentary",
        status=FactStatus.UNVERIFIED,
        raw_value="Experienced management team",
        source_refs=[REF],
    )
    assert fact.currency is None
    assert fact.period_type is None
    assert fact.normalized_value is None
