"""Golden tests for the PDF table (3.3) and free-form PDF text (3.4) parsers.

These skip gracefully when ``pdfplumber`` is not installed (environment limit).
They assert page/table source refs, raw-vs-normalized values, and that absent
configured fields produce MISSING facts (never fabricated).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.schemas.enums import ExtractionMethod, FactStatus
from app.services.extraction.pdf_table import PdfTableParser, pdfplumber_available
from app.services.extraction.pdf_text import PdfTextParser

FIXTURES = Path(__file__).resolve().parent / "fixtures"

pytestmark = pytest.mark.skipif(
    not pdfplumber_available(), reason="pdfplumber not installed"
)


def test_pdf_table_golden_expected_values() -> None:
    data = (FIXTURES / "sample_table.pdf").read_bytes()
    logged: list[tuple] = []
    parser = PdfTableParser()
    result = parser.parse(
        data,
        document_id="doc-pdf-1",
        scale="millions",
        currency="USD",
        period_headers={"FY2022": {"fiscal_year": 2022}, "FY2023": {"fiscal_year": 2023}},
        audit_hook=lambda *a: logged.append(a),
    )
    value_facts = result.value_facts()
    assert value_facts, "expected at least one extracted value"

    revenue = next(
        f for f in value_facts if f.source_label == "Revenue" and f.fiscal_year == 2023
    )
    assert revenue.normalized_value == pytest.approx(1500.0)
    assert revenue.normalized_unit == "USD_million"
    assert revenue.extraction_method is ExtractionMethod.PDF_TABLE
    # Source ref records the page and the table.
    ref = revenue.source_refs[0]
    assert ref.page == 1
    assert ref.table is not None and ref.table.startswith("page1:table")
    assert logged and logged[0][0] == "pdf_table_parser"


def test_pdf_text_golden_expected_values() -> None:
    data = (FIXTURES / "sample_narrative.pdf").read_bytes()
    parser = PdfTextParser()
    result = parser.parse(
        data,
        document_id="doc-pdf-2",
        field_patterns={
            "borrower": r"Borrower is ([A-Za-z0-9 ]+?), a Delaware",
            "revenue": r"Total Revenue for the fiscal year was USD ([\d,]+) million",
            "goodwill": r"Goodwill impairment of USD ([\d,]+)",
        },
        numeric_fields={"revenue"},
        scale="millions",
        currency="USD",
    )
    by_name = {f.name: f for f in result.facts}

    assert by_name["borrower"].raw_value == "ACME Corporation"
    assert by_name["borrower"].source_refs[0].page == 1

    revenue = by_name["revenue"]
    assert revenue.raw_value == "1,500"
    assert revenue.normalized_value == pytest.approx(1500.0)
    assert revenue.normalized_unit == "USD_million"

    # Configured but absent -> MISSING, never fabricated.
    goodwill = by_name["goodwill"]
    assert goodwill.status is FactStatus.MISSING
    assert goodwill.raw_value is None
    assert goodwill.normalized_value is None
