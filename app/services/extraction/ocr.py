"""OCR fallback parser (task 3.5) -- optional / last resort.

OCR is a last-resort extraction path used only when an evidence document is a
scanned image with no extractable text layer. To keep the test suite green in
environments without a system OCR engine, the OCR backend is PLUGGABLE:

* :class:`OcrBackend` is a protocol: ``image_to_text(data: bytes) -> str``.
* :class:`TesseractBackend` wraps :mod:`pytesseract` + the ``tesseract`` binary
  when available; :func:`tesseract_available` reports whether it can be used.
* Callers may inject any backend (including a stub) for deterministic tests.

Extracted text is parsed with the same deterministic field-pattern logic as the
free-form PDF text parser, and every emitted fact is clearly flagged
``extraction_method=ocr`` (Req 3.1). Missing fields are emitted as MISSING, never
fabricated.
"""

from __future__ import annotations

import re
import uuid
from typing import Protocol

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
    name="ocr_parser", version="1.0.0", extraction_method=ExtractionMethod.OCR
)


class OcrBackend(Protocol):
    """Pluggable OCR backend contract."""

    def image_to_text(self, data: bytes) -> str:
        """Return recognized text for an image given as raw bytes."""


def tesseract_available() -> bool:
    """Return True when :mod:`pytesseract` and the tesseract binary are usable."""
    try:
        import pytesseract
    except ImportError:
        return False
    try:
        pytesseract.get_tesseract_version()
    except Exception:  # binary missing / not on PATH
        return False
    return True


class TesseractBackend:
    """OCR backend backed by :mod:`pytesseract` (requires the tesseract binary)."""

    def image_to_text(self, data: bytes) -> str:  # pragma: no cover - env dependent
        import io

        import pytesseract
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            return pytesseract.image_to_string(image)


class OcrParser(BaseParser):
    """Extract fielded facts from a scanned image via a pluggable OCR backend."""

    def __init__(self, backend: OcrBackend | None = None) -> None:
        self._backend = backend

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
        page: int = 1,
        scale: str | None = None,
        currency: str | None = None,
        target_scale: str = "millions",
        audit_hook: ParserAuditHook | None = None,
    ) -> ParseResult:
        """OCR ``data`` (image bytes) then extract configured fields.

        If no backend was injected a :class:`TesseractBackend` is used; when the
        backend/binary is unavailable a :class:`ParserError` is raised so callers
        (and tests) can skip gracefully.
        """
        backend = self._backend
        if backend is None:
            if not tesseract_available():
                raise ParserError(
                    "No OCR backend available (pytesseract/tesseract not installed)."
                )
            backend = TesseractBackend()

        text = backend.image_to_text(data)
        numeric_fields = numeric_fields or set()
        compiled = {
            name: re.compile(p) if isinstance(p, str) else p
            for name, p in field_patterns.items()
        }

        result = ParseResult(metadata=self.metadata)
        for name, pattern in compiled.items():
            match = pattern.search(text)
            source_ref = SourceRef(document_id=document_id, page=page)
            if match is None:
                result.facts.append(
                    CanonicalFact(
                        fact_id=str(uuid.uuid4()),
                        name=name,
                        status=FactStatus.MISSING,
                        extraction_method=ExtractionMethod.OCR,
                        source_refs=[source_ref],
                        created_by=self.metadata.created_by,
                    )
                )
                continue
            value = (
                match.group(1).strip() if match.groups() else match.group(0).strip()
            )
            normalized = None
            raw_unit = None
            if name in numeric_fields:
                raw_unit = currency
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
                    raw_unit=raw_unit,
                    normalized_value=normalized.value if normalized else None,
                    normalized_unit=normalized.unit if normalized else None,
                    currency=currency if name in numeric_fields else None,
                    scale=scale if name in numeric_fields else None,
                    normalization_method=normalized.method if normalized else None,
                    status=FactStatus.UNVERIFIED,
                    extraction_method=ExtractionMethod.OCR,
                    confidence=None,
                    source_refs=[source_ref],
                    created_by=self.metadata.created_by,
                )
            )

        self._log(result, audit_hook, document_id=document_id)
        return result
