"""Golden test for the OCR fallback parser (task 3.5).

OCR is optional / last-resort. We exercise the deterministic field-extraction
logic through a STUB backend so the test runs without a system tesseract binary,
and separately assert that invoking the parser without a backend skips cleanly
when tesseract is unavailable. Facts are clearly flagged ``extraction_method=ocr``.
"""

from __future__ import annotations

import pytest

from app.schemas.enums import ExtractionMethod, FactStatus
from app.services.extraction.base import ParserError
from app.services.extraction.ocr import OcrParser, tesseract_available


class _StubBackend:
    """Deterministic OCR backend returning fixed recognized text."""

    def __init__(self, text: str) -> None:
        self._text = text

    def image_to_text(self, data: bytes) -> str:  # noqa: ARG002
        return self._text


def test_ocr_with_stub_backend_expected_values() -> None:
    text = (
        "ACME Corp Scanned Statement\n"
        "Total Revenue USD 1,500 million\n"
        "Net Income USD 250 million\n"
    )
    logged: list[tuple] = []
    parser = OcrParser(backend=_StubBackend(text))
    result = parser.parse(
        b"fake-image-bytes",
        document_id="doc-ocr-1",
        field_patterns={
            "revenue": r"Total Revenue USD ([\d,]+) million",
            "ebitda": r"EBITDA USD ([\d,]+) million",
        },
        numeric_fields={"revenue", "ebitda"},
        scale="millions",
        currency="USD",
        page=3,
        audit_hook=lambda *a: logged.append(a),
    )
    by_name = {f.name: f for f in result.facts}

    revenue = by_name["revenue"]
    assert revenue.raw_value == "1,500"
    assert revenue.normalized_value == pytest.approx(1500.0)
    assert revenue.extraction_method is ExtractionMethod.OCR
    assert revenue.source_refs[0].page == 3

    # Absent field -> MISSING, never fabricated.
    assert by_name["ebitda"].status is FactStatus.MISSING
    assert by_name["ebitda"].raw_value is None

    assert logged and logged[0][0] == "ocr_parser"


def test_ocr_without_backend_skips_when_unavailable() -> None:
    if tesseract_available():
        pytest.skip("tesseract available; no-backend fallback path not exercised")
    parser = OcrParser()
    with pytest.raises(ParserError):
        parser.parse(
            b"img",
            document_id="d",
            field_patterns={"x": r"X ([\d]+)"},
        )
