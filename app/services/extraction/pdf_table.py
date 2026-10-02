"""PDF table parser (task 3.3).

Extracts tabular facts from a PDF using :mod:`pdfplumber` (if installed). Each
extracted cell becomes a :class:`~app.schemas.evidence.CanonicalFact` whose
:class:`~app.schemas.evidence.SourceRef` records the ``page`` number and the
``table`` index on that page, plus the ``row_label`` and ``cell`` coordinate.

The actual cell -> fact mapping reuses :class:`TableParser` so scale/currency/
period handling and the "empty cell -> MISSING, never zero" rule are identical
to the XLSX/CSV parser (Req 4.4, 4.5, 23.2).

Error handling: if ``pdfplumber`` is unavailable the parser raises
:class:`~app.services.extraction.base.ParserError` (tests skip gracefully). A
PDF that yields no detectable table on the requested page produces an empty
result with a warning rather than inventing values.
"""

from __future__ import annotations

import io

from app.schemas.enums import ExtractionMethod
from app.services.extraction.base import (
    BaseParser,
    ParserAuditHook,
    ParserError,
    ParseResult,
    ParserMetadata,
)
from app.services.extraction.xlsx_csv import TableParser

_METADATA = ParserMetadata(
    name="pdf_table_parser",
    version="1.0.0",
    extraction_method=ExtractionMethod.PDF_TABLE,
)


def pdfplumber_available() -> bool:
    """Return True when :mod:`pdfplumber` can be imported."""
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        return False
    return True


class PdfTableParser(BaseParser):
    """Extract tabular facts from a PDF with page/table source references."""

    def __init__(self) -> None:
        self._table_mapper = TableParser(_METADATA)

    @property
    def metadata(self) -> ParserMetadata:
        return _METADATA

    def parse(
        self,
        data: bytes,
        *,
        document_id: str,
        pages: list[int] | None = None,
        scale: str | None = None,
        currency: str | None = None,
        period_headers: dict[str, dict] | None = None,
        target_scale: str = "millions",
        audit_hook: ParserAuditHook | None = None,
    ) -> ParseResult:
        """Parse tables from ``data`` (PDF bytes) into canonical facts.

        ``pages`` optionally restricts extraction to 1-based page numbers.
        """
        try:
            import pdfplumber
        except ImportError as exc:  # pragma: no cover - env dependent
            raise ParserError(
                "pdfplumber is required to parse PDF tables but is not installed."
            ) from exc

        result = ParseResult(metadata=self.metadata)
        try:
            pdf = pdfplumber.open(io.BytesIO(data))
        except Exception as exc:
            raise ParserError(f"Unreadable PDF document: {exc}") from exc

        with pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                if pages is not None and page_index not in pages:
                    continue
                tables = page.extract_tables() or []
                if not tables:
                    result.warnings.append(
                        f"No table detected on page {page_index}."
                    )
                    continue
                for t_index, table in enumerate(tables):
                    rows = [
                        ["" if cell is None else str(cell) for cell in row]
                        for row in table
                    ]
                    sheet_name = f"page{page_index}:table{t_index}"
                    sub = self._table_mapper.parse_rows(
                        rows,
                        document_id=document_id,
                        sheet_name=sheet_name,
                        scale=scale,
                        currency=currency,
                        period_headers=period_headers,
                        target_scale=target_scale,
                    )
                    # Attach page number to each source ref (pdfplumber tables).
                    for fact in sub.facts:
                        fact.source_refs = [
                            ref.model_copy(update={"page": page_index})
                            for ref in fact.source_refs
                        ]
                    result.facts.extend(sub.facts)
                    result.warnings.extend(sub.warnings)

        self._log(result, audit_hook, document_id=document_id)
        return result
