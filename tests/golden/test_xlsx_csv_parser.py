"""Golden tests for the XLSX / CSV financial-table parser (task 3.2).

Asserts row label capture (``source_label``), scale/currency metadata, period
resolution from column headers, raw-vs-normalized values, cell-level source
references, empty-cell -> MISSING (never zero), and error handling for a missing
sheet.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.schemas.enums import ExtractionMethod, FactStatus
from app.services.extraction.base import ParserError
from app.services.extraction.xlsx_csv import CsvParser, XlsxParser

FIXTURES = Path(__file__).resolve().parent / "fixtures"

PERIOD_HEADERS = {
    "FY2022": {"fiscal_year": 2022},
    "FY2023": {"fiscal_year": 2023},
}


def _index(facts):
    return {(f.source_label, f.fiscal_year): f for f in facts}


def test_csv_golden_expected_values() -> None:
    data = (FIXTURES / "sample_financials.csv").read_bytes()
    logged: list[tuple] = []
    parser = CsvParser()
    result = parser.parse(
        data,
        document_id="doc-csv-1",
        sheet_name="financials",
        scale="millions",
        currency="USD",
        period_headers=PERIOD_HEADERS,
        audit_hook=lambda *a: logged.append(a),
    )
    idx = _index(result.facts)

    revenue_23 = idx[("Revenue", 2023)]
    assert revenue_23.raw_value == "1,500"
    assert revenue_23.normalized_value == pytest.approx(1500.0)
    assert revenue_23.normalized_unit == "USD_million"
    assert revenue_23.currency == "USD"
    assert revenue_23.scale == "millions"
    assert revenue_23.extraction_method is ExtractionMethod.CSV
    assert revenue_23.status is FactStatus.UNVERIFIED

    # Source ref locates sheet / row / cell.
    ref = revenue_23.source_refs[0]
    assert ref.table == "financials"
    assert ref.row_label == "Revenue"
    assert ref.cell == "C2"  # column C (FY2023), row 2 (first data row)

    # Accounting-parenthesis negative preserved.
    debt_23 = idx[("Total Debt", 2023)]
    assert debt_23.normalized_value == pytest.approx(-750.0)

    # Empty cell -> MISSING, never zero (Req 4.4).
    cash_22 = idx[("Cash", 2022)]
    assert cash_22.status is FactStatus.MISSING
    assert cash_22.raw_value is None
    assert cash_22.normalized_value is None

    assert logged and logged[0][0] == "csv_parser"


def test_xlsx_golden_expected_values() -> None:
    data = (FIXTURES / "sample_financials.xlsx").read_bytes()
    parser = XlsxParser()
    result = parser.parse(
        data,
        document_id="doc-xlsx-1",
        sheet_name="IncomeStatement",
        scale="millions",
        currency="USD",
        period_headers=PERIOD_HEADERS,
    )
    idx = _index(result.facts)
    revenue_23 = idx[("Revenue", 2023)]
    assert revenue_23.normalized_value == pytest.approx(1500.0)
    assert revenue_23.source_refs[0].table == "IncomeStatement"

    cash_22 = idx[("Cash", 2022)]
    assert cash_22.status is FactStatus.MISSING


def test_xlsx_missing_sheet_raises() -> None:
    data = (FIXTURES / "sample_financials.xlsx").read_bytes()
    parser = XlsxParser()
    with pytest.raises(ParserError):
        parser.parse(data, document_id="d", sheet_name="DoesNotExist")


def test_csv_empty_raises() -> None:
    parser = CsvParser()
    with pytest.raises(ParserError):
        parser.parse(b"", document_id="d")
