"""Shared enums for the evidence model.

The fact-status enum encodes the distinct data-quality states required by
Requirement 4.4 / 5.4. Each state is distinct and ``missing`` is NEVER
numerical zero. ``not_applicable``, ``not_disclosed``, and ``stale`` are kept
separate from ``missing`` so downstream logic cannot collapse them.
"""

from __future__ import annotations

from enum import Enum


class FactStatus(str, Enum):
    """Allowed fact data-quality states (Req 4.4)."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    CONFLICTING = "conflicting"
    MISSING = "missing"
    NOT_APPLICABLE = "not_applicable"
    NOT_DISCLOSED = "not_disclosed"
    STALE = "stale"


# States for which a numerical value must NOT be present. In particular a
# ``missing`` fact can never carry a value (and never numerical zero).
NON_VALUE_STATUSES: frozenset[FactStatus] = frozenset(
    {
        FactStatus.MISSING,
        FactStatus.NOT_APPLICABLE,
        FactStatus.NOT_DISCLOSED,
    }
)


class EntityType(str, Enum):
    """Entity classification (Req 2.1)."""

    PARENT = "parent"
    OPERATING_SUBSIDIARY = "operating_subsidiary"
    BORROWER = "borrower"
    GUARANTOR = "guarantor"
    OTHER = "other"


class ExtractionMethod(str, Enum):
    """How a fact was produced (precedence per Req 3.1)."""

    XBRL = "xbrl"
    INLINE_XBRL = "inline_xbrl"
    XLSX = "xlsx"
    CSV = "csv"
    PDF_TABLE = "pdf_table"
    PDF_TEXT = "pdf_text"
    OCR = "ocr"
    LLM = "llm"
    MANUAL = "manual"
