"""Shared parser interface and metadata for deterministic extraction (Milestone 3).

Every deterministic parser in this package (XBRL, XLSX/CSV, PDF table, PDF text,
OCR) is an independently testable unit that satisfies a common Definition of
Done (tasks.md Milestone 3):

* writes ``CanonicalFact``-compatible objects;
* preserves entity / period / unit / scale / currency metadata;
* produces :class:`~app.schemas.evidence.SourceRef` references;
* handles missing fields without inventing values (never coerces missing to 0);
* logs parser **name/version** metadata.

This module defines:

* :class:`ParserMetadata` -- immutable (name, version) identity for a parser,
  recorded on every emitted fact (``normalization_method``/``created_by``) and
  logged via an audit hook.
* :class:`BaseParser` -- the common interface: a ``metadata`` property and a
  :meth:`parse` method returning a :class:`ParseResult`.
* :class:`ParseResult` -- the facts a parser produced plus the parser metadata,
  so callers always know *which parser* and *which version* produced them.

Parser *precedence* is configuration (the ``parser_precedence`` ConfigRegistry
kind), NOT hardcoded here; reconciliation that chooses between parser outputs is
Milestone 4 and is intentionally absent from this module.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from app.schemas.enums import ExtractionMethod
from app.schemas.evidence import CanonicalFact

# An audit callback accepts (parser_name, parser_version, fact_count, extra).
ParserAuditHook = Callable[[str, str, int, dict[str, Any]], None]


@dataclass(frozen=True)
class ParserMetadata:
    """Immutable identity of a parser: its name and version (DoD: logging).

    The ``created_by`` string embedded on emitted facts is ``"{name}@{version}"``
    so provenance is explicit and reproducible.
    """

    name: str
    version: str
    extraction_method: ExtractionMethod

    @property
    def created_by(self) -> str:
        return f"{self.name}@{self.version}"

    def as_dict(self) -> dict[str, str]:
        return {
            "parser_name": self.name,
            "parser_version": self.version,
            "extraction_method": self.extraction_method.value,
        }


@dataclass
class ParseResult:
    """Facts produced by a single parser run plus the parser metadata."""

    metadata: ParserMetadata
    facts: list[CanonicalFact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def fact_count(self) -> int:
        return len(self.facts)

    def value_facts(self) -> list[CanonicalFact]:
        """Facts that carry a value (status not one of the non-value states)."""
        return [f for f in self.facts if f.raw_value is not None]


class ParserError(ValueError):
    """Raised for an input a parser cannot process (malformed/absent structure).

    Parsers raise this for truly unparseable *inputs*. Individual absent
    fields/concepts/columns are NOT errors: they are represented as missing
    facts or omitted, never fabricated.
    """


class BaseParser(ABC):
    """Common interface implemented by every deterministic parser.

    Subclasses expose a :attr:`metadata` identity and implement :meth:`parse`.
    The :meth:`parse` signature is parser-specific (bytes, path, DataFrame),
    so it is declared with ``*args``/``**kwargs`` here; each concrete parser
    documents its own call shape.
    """

    @property
    @abstractmethod
    def metadata(self) -> ParserMetadata:
        """Return this parser's immutable (name, version, method) identity."""

    @abstractmethod
    def parse(self, *args: Any, **kwargs: Any) -> ParseResult:
        """Parse an input and return a :class:`ParseResult`."""

    def _log(self, result: ParseResult, audit_hook: ParserAuditHook | None, **extra: Any) -> None:
        """Log parser name/version metadata for a run (DoD: logging)."""
        if audit_hook is not None:
            audit_hook(
                self.metadata.name,
                self.metadata.version,
                result.fact_count,
                {**self.metadata.as_dict(), **extra},
            )


# Default deterministic parser precedence recorded as a versioned config
# artifact (ConfigRegistry kind ``parser_precedence``). Structured/typed sources
# rank above lossy ones. This is DATA, not resolution logic: Milestone 4 reads
# it to reconcile; Milestone 3 only emits per-parser facts.
DEFAULT_PARSER_PRECEDENCE: dict[str, Any] = {
    "order": [
        ExtractionMethod.XBRL.value,
        ExtractionMethod.XLSX.value,
        ExtractionMethod.CSV.value,
        ExtractionMethod.PDF_TABLE.value,
        ExtractionMethod.PDF_TEXT.value,
        ExtractionMethod.OCR.value,
    ],
    "note": "Deterministic precedence only; reconciliation applies it (Milestone 4).",
}
