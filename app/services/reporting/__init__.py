"""Reporting service (Milestone 8): FinalCaseSnapshot JSON first, then the PDF
memo rendered from it.

Public API (:mod:`memo`):

* :class:`MemoReportGenerator` -- generates the canonical memo JSON from the
  EXACT finalized snapshot (task 8.1, validate-on-write, emits ``memo_generated``
  / ``case_finalized``), then renders a deterministic HTML artifact and PDF from
  that JSON ONLY (task 8.2). JSON is the source of truth; the PDF is a pure
  rendering of it (Req 17.2).
* :func:`detect_pdf_backend` -- reports which offline HTML->PDF backend (if any)
  is installed; PDF production skips gracefully when none is available.
"""

from app.services.reporting.memo import (
    MEMO_SECTIONS,
    MemoOutput,
    MemoReportGenerator,
    OutputIntegrityError,
    PdfBackendUnavailableError,
    detect_pdf_backend,
)

__all__ = [
    "MEMO_SECTIONS",
    "MemoOutput",
    "MemoReportGenerator",
    "OutputIntegrityError",
    "PdfBackendUnavailableError",
    "detect_pdf_backend",
]
