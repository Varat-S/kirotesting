"""Golden test for the XBRL / structured-filing parser (task 3.1).

Asserts the parser maps taxonomy concepts to canonical facts with
``taxonomy_concept``, entity, period and scale metadata, source references, and
raw-vs-normalized values; that it emits an explicit MISSING fact for a requested
absent concept (never fabricated); and that it logs parser name/version.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.schemas.enums import ExtractionMethod, FactStatus
from app.services.extraction.base import ParserError
from app.services.extraction.xbrl import XbrlParser

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _facts_by_concept(facts):
    return {f.taxonomy_concept: f for f in facts}


def test_xbrl_golden_expected_values() -> None:
    data = (FIXTURES / "sample_filing.xbrl").read_bytes()
    logged: list[tuple] = []

    parser = XbrlParser()
    result = parser.parse(
        data,
        document_id="doc-xbrl-1",
        audit_hook=lambda n, v, c, extra: logged.append((n, v, c, extra)),
    )

    by_concept = _facts_by_concept(result.facts)
    assert set(by_concept) == {
        "us-gaap:Revenues",
        "us-gaap:NetIncomeLoss",
        "us-gaap:Assets",
    }

    revenue = by_concept["us-gaap:Revenues"]
    assert revenue.raw_value == "1500"
    assert revenue.currency == "USD"
    assert revenue.scale == "millions"
    assert revenue.normalized_value == pytest.approx(1500.0)
    assert revenue.normalized_unit == "USD_million"
    assert revenue.entity_id == "ACME-CORP"
    assert revenue.period_type == "duration"
    assert revenue.period_start == date(2023, 1, 1)
    assert revenue.period_end == date(2023, 12, 31)
    assert revenue.fiscal_year == 2023
    assert revenue.extraction_method is ExtractionMethod.XBRL
    assert revenue.status is FactStatus.UNVERIFIED

    # Source ref carries the taxonomy concept.
    assert revenue.source_refs[0].taxonomy_concept == "us-gaap:Revenues"
    assert revenue.source_refs[0].document_id == "doc-xbrl-1"

    # Instant context for a balance-sheet concept.
    assets = by_concept["us-gaap:Assets"]
    assert assets.period_type == "instant"
    assert assets.period_end == date(2023, 12, 31)

    # Parser name/version logged.
    assert logged and logged[0][0] == "xbrl_parser"
    assert logged[0][1] == "1.0.0"


def test_xbrl_absent_concept_is_missing_not_fabricated() -> None:
    data = (FIXTURES / "sample_filing.xbrl").read_bytes()
    parser = XbrlParser()
    result = parser.parse(
        data,
        document_id="doc-xbrl-1",
        concepts=["us-gaap:Revenues", "us-gaap:GoodwillImpairmentLoss"],
    )
    by_concept = _facts_by_concept(result.facts)
    missing = by_concept["us-gaap:GoodwillImpairmentLoss"]
    assert missing.status is FactStatus.MISSING
    assert missing.raw_value is None
    assert missing.normalized_value is None  # never zero


def test_xbrl_malformed_raises_parser_error() -> None:
    parser = XbrlParser()
    with pytest.raises(ParserError):
        parser.parse(b"<xbrli:xbrl><not closed", document_id="doc-bad")
