"""Free-form PDF text extraction (task 3.4).

Extracts fielded facts from narrative PDF text deterministically using a set of
configured label patterns. Text is read with :mod:`pdfplumber` (if installed);
each page's text is matched against caller-supplied field patterns (a mapping of
``field_name`` -> compiled/str regex capturing a value group).

Each match becomes a :class:`~app.schemas.evidence.CanonicalFact` whose
:class:`~app.schemas.evidence.SourceRef` records the ``page`` where the value was
found. Numeric values are normalized (raw preserved separately); non-numeric
matches keep the raw string.

NEVER invents values (Req 3.1, 3.2, 23.2): a configured field that does not
match on any page is emitted as a MISSING fact (status ``missing``) so absence is
explicit; it is never defaulted to zero or guessed.
"""

from __future__ import annotations

import io
import re
import uuid

from app.schemas.enums import ExtractionMethod, FactStatus
from app.schemas.evidence import CanonicalFact, SourceRef
from app.services.extraction.base import (
    BaseParser,
    ParserAuditHook,
    ParserError,
    ParseResult,
    ParserMetadata,
)
from app.services.extraction.normalization import (
    NormalizationError,
    normalize_amount,
)

_METADATA = ParserMetadata(
    name="pdf_text_parser",
    version="1.0.0",
    extraction_method=ExtractionMethod.PDF_TEXT,
)


def pdfplumber_available() -> bool:
    """Return True when :mod:`pdfplumber` can be imported."""
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        return False
    return True


class PdfTextParser(BaseParser):
    """Extract fielded facts from narrative PDF text with page references."""

    @property
    def metadata(self) -> ParserMetadata:
        return _METADATA

    def parse(
        self,
        data: bytes,
        *,
        document_id: str,
        field_patterns: dict[str, str | re.Pattern[str]],
        numeric_fields: set[str] | None = None,
        scale: str | None = None,
        currency: str | None = None,
        target_scale: str = "millions",
        audit_hook: ParserAuditHook | None = None,
    ) -> ParseResult:
        """Parse ``data`` (PDF bytes) extracting the configured fields.

        ``field_patterns`` maps a field name to a regex with a single capture
        group for the value. ``numeric_fields`` names fields whose captured
        value should be normalized as an amount; other fields keep raw text.
        """
        try:
            import pdfplumber
        except ImportError as exc:  # pragma: no cover - env dependent
            raise ParserError(
                "pdfplumber is required to parse PDF text but is not installed."
            ) from exc

        try:
            pdf = pdfplumber.open(io.BytesIO(data))
        except Exception as exc:
            raise ParserError(f"Unreadable PDF document: {exc}") from exc

        numeric_fields = numeric_fields or set()
        compiled = {
            name: re.compile(p) if isinstance(p, str) else p
            for name, p in field_patterns.items()
        }

        result = ParseResult(metadata=self.metadata)
        found: dict[str, tuple[str, int]] = {}

        with pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text() or ""
                for name, pattern in compiled.items():
                    if name in found:
                        continue
                    match = pattern.search(page_text)
                    if match is not None:
                        value = match.group(1).strip() if match.groups() else match.group(0).strip()
                        found[name] = (value, page_index)

        for name, pattern in compiled.items():
            if name not in found:
                # Configured but absent -> explicit MISSING (never fabricated).
                result.facts.append(
                    CanonicalFact(
                        fact_id=str(uuid.uuid4()),
                        name=name,
                        status=FactStatus.MISSING,
                        extraction_method=ExtractionMethod.PDF_TEXT,
                        source_refs=[SourceRef(document_id=document_id)],
                        created_by=self.metadata.created_by,
                    )
                )
                continue

            value, page_index = found[name]
            source_ref = SourceRef(document_id=document_id, page=page_index)
            if name in numeric_fields:
                try:
                    normalized = normalize_amount(
                        value, scale=scale, currency=currency, target_scale=target_scale
                    )
                except NormalizationError:
                    normalized = None
                result.facts.append(
                    CanonicalFact(
                        fact_id=str(uuid.uuid4()),
                        name=name,
                        source_label=name,
                        raw_value=value,
                        raw_unit=currency,
                        normalized_value=normalized.value if normalized else None,
                        normalized_unit=normalized.unit if normalized else None,
                        currency=currency,
                        scale=scale,
                        normalization_method=normalized.method if normalized else None,
                        status=FactStatus.UNVERIFIED,
                        extraction_method=ExtractionMethod.PDF_TEXT,
                        source_refs=[source_ref],
                        created_by=self.metadata.created_by,
                    )
                )
            else:
                result.facts.append(
                    CanonicalFact(
                        fact_id=str(uuid.uuid4()),
                        name=name,
                        source_label=name,
                        raw_value=value,
                        status=FactStatus.UNVERIFIED,
                        extraction_method=ExtractionMethod.PDF_TEXT,
                        source_refs=[source_ref],
                        created_by=self.metadata.created_by,
                    )
                )

        self._log(result, audit_hook, document_id=document_id)
        return result
