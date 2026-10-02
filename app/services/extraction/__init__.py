"""Extraction service: deterministic parsers (split) plus AI qualitative
extraction. Every fact carries a source reference; AI facts are never
auto-verified.

Milestone 3 ships the deterministic, independently-testable parsers below. Each
satisfies the shared Definition of Done (CanonicalFact-compatible output,
entity/period/unit/scale/currency metadata, source references, missing-field
handling without invented values, golden fixtures, expected-value tests, and
parser name/version logging). Parser *precedence* is a versioned configuration
artifact (``DEFAULT_PARSER_PRECEDENCE``); reconciliation between parsers is
Milestone 4 and is intentionally not implemented here.
"""

from app.services.extraction.base import (
    DEFAULT_PARSER_PRECEDENCE,
    BaseParser,
    ParserError,
    ParseResult,
    ParserMetadata,
)
from app.services.extraction.ocr import OcrParser, tesseract_available
from app.services.extraction.pdf_table import PdfTableParser, pdfplumber_available
from app.services.extraction.pdf_text import PdfTextParser
from app.services.extraction.xbrl import XbrlParser
from app.services.extraction.xlsx_csv import CsvParser, TableParser, XlsxParser

__all__ = [
    "BaseParser",
    "ParseResult",
    "ParserError",
    "ParserMetadata",
    "DEFAULT_PARSER_PRECEDENCE",
    "XbrlParser",
    "CsvParser",
    "XlsxParser",
    "TableParser",
    "PdfTableParser",
    "PdfTextParser",
    "OcrParser",
    "pdfplumber_available",
    "tesseract_available",
]
