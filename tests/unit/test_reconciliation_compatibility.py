"""Definition-compatibility check tests (task 4.1, Req 3.7, 6.1-6.3)."""

from __future__ import annotations

from datetime import date

from app.schemas.enums import ExtractionMethod, FactStatus
from app.schemas.evidence import CanonicalFact, SourceRef
from app.services.reconciliation.compatibility import (
    CompatibilityDimension,
    check_compatibility,
    is_structured,
    prefer_structured,
)


def _fact(fact_id: str, method: ExtractionMethod, **overrides) -> CanonicalFact:
    base = dict(
        fact_id=fact_id,
        name="revenue",
        raw_value="1000",
        raw_unit="USD_million",
        normalized_value=1000.0,
        normalized_unit="USD_million",
        currency="USD",
        scale="millions",
        period_start=date(2024, 1, 1),
        period_end=date(2024, 12, 31),
        period_type="duration",
        fiscal_year=2024,
        accounting_basis="GAAP",
        consolidation_scope="consolidated",
        entity_id="DAL",
        restated=False,
        status=FactStatus.UNVERIFIED,
        extraction_method=method,
        source_refs=[SourceRef(document_id=f"doc-{fact_id}")],
    )
    base.update(overrides)
    return CanonicalFact(**base)


def test_fully_compatible_facts_pass_all_dimensions() -> None:
    a = _fact("a", ExtractionMethod.XBRL)
    b = _fact("b", ExtractionMethod.PDF_TEXT)
    result = check_compatibility(a, b)
    assert result.compatible is True
    assert result.failures == []


def test_entity_mismatch_blocks_comparison() -> None:
    a = _fact("a", ExtractionMethod.XBRL)
    b = _fact("b", ExtractionMethod.PDF_TEXT, entity_id="UAL")
    result = check_compatibility(a, b)
    assert result.compatible is False
    assert CompatibilityDimension.ENTITY.value in result.mismatch_dimensions


def test_period_mismatch_blocks_comparison() -> None:
    a = _fact("a", ExtractionMethod.XBRL)
    b = _fact("b", ExtractionMethod.PDF_TEXT, fiscal_year=2023,
              period_start=date(2023, 1, 1), period_end=date(2023, 12, 31))
    result = check_compatibility(a, b)
    assert result.compatible is False
    assert CompatibilityDimension.PERIOD.value in result.mismatch_dimensions


def test_scale_mismatch_is_not_silently_rescaled() -> None:
    a = _fact("a", ExtractionMethod.XBRL)
    b = _fact("b", ExtractionMethod.XLSX, scale="thousands", normalized_unit="USD_thousand")
    result = check_compatibility(a, b)
    assert result.compatible is False
    assert CompatibilityDimension.UNIT_SCALE.value in result.mismatch_dimensions


def test_currency_mismatch_blocks_comparison() -> None:
    a = _fact("a", ExtractionMethod.XBRL)
    b = _fact("b", ExtractionMethod.PDF_TEXT, currency="EUR")
    result = check_compatibility(a, b)
    assert result.compatible is False
    assert CompatibilityDimension.CURRENCY.value in result.mismatch_dimensions


def test_adjusted_vs_gaap_definition_mismatch() -> None:
    a = _fact("a", ExtractionMethod.XBRL, accounting_basis="GAAP")
    b = _fact("b", ExtractionMethod.PDF_TEXT, accounting_basis="management_adjusted")
    result = check_compatibility(a, b)
    assert result.compatible is False
    assert CompatibilityDimension.ACCOUNTING_DEFINITION.value in result.mismatch_dimensions


def test_restated_vs_original_is_a_mismatch() -> None:
    a = _fact("a", ExtractionMethod.XBRL, restated=False)
    b = _fact("b", ExtractionMethod.XBRL, restated=True)
    result = check_compatibility(a, b)
    assert result.compatible is False
    assert CompatibilityDimension.RESTATEMENT.value in result.mismatch_dimensions


def test_prefer_structured_only_when_compatible() -> None:
    structured = _fact("a", ExtractionMethod.XBRL)
    freeform = _fact("b", ExtractionMethod.PDF_TEXT)
    assert is_structured(structured) is True
    assert is_structured(freeform) is False
    assert prefer_structured(structured, freeform) is structured


def test_prefer_structured_returns_none_when_incompatible() -> None:
    """Req 6.2: a failed compatibility check must NOT auto-prefer structured."""
    structured = _fact("a", ExtractionMethod.XBRL, accounting_basis="GAAP")
    freeform = _fact("b", ExtractionMethod.PDF_TEXT, accounting_basis="management_adjusted")
    assert prefer_structured(structured, freeform) is None
