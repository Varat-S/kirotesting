"""XBRL / structured-filing parser (task 3.1).

Deterministically maps XBRL taxonomy concepts to
:class:`~app.schemas.evidence.CanonicalFact` objects. For each numeric fact the
parser records:

* ``taxonomy_concept`` (e.g. ``us-gaap:Revenues``) on the fact and in a
  :class:`~app.schemas.evidence.SourceRef`;
* the reporting ``entity_id`` and (where resolvable) the context period
  (instant vs duration -> ``period_start``/``period_end``/``period_type``);
* ``scale`` from the XBRL ``decimals``/``scale`` attribute and ``currency`` from
  the unit reference, with raw vs normalized values kept separate (via
  :mod:`app.services.extraction.normalization`).

Input is parsed with the standard-library XML parser (``xml.etree``) so no
third-party dependency is required. A minimal subset of XBRL instance semantics
is supported: ``<xbrli:context>`` elements (entity + period), ``<xbrli:unit>``
elements (currency measure), and concept facts referencing them via
``contextRef``/``unitRef``.

Error handling (Req 4.5, 23.2): a malformed document raises
:class:`~app.services.extraction.base.ParserError`. A concept that references a
missing context/unit, or a requested concept that is absent, is represented as a
MISSING fact (never fabricated) when the caller asks for it explicitly; unknown
concepts in the instance are parsed on a best-effort basis and skipped only when
they carry no usable context.
"""

from __future__ import annotations

import uuid
from xml.etree import ElementTree as ET

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
    normalize_period,
)

_XBRLI = "http://www.xbrl.org/2003/instance"
_METADATA = ParserMetadata(
    name="xbrl_parser",
    version="1.0.0",
    extraction_method=ExtractionMethod.XBRL,
)


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


class XbrlParser(BaseParser):
    """Parse an XBRL instance document into canonical facts."""

    @property
    def metadata(self) -> ParserMetadata:
        return _METADATA

    def parse(
        self,
        data: bytes | str,
        *,
        document_id: str,
        concepts: list[str] | None = None,
        target_scale: str = "millions",
        audit_hook: ParserAuditHook | None = None,
    ) -> ParseResult:
        """Parse ``data`` (XBRL instance XML) into a :class:`ParseResult`.

        ``concepts`` optionally restricts output to the named taxonomy concepts;
        any requested concept not present in the instance yields a MISSING fact
        (status ``missing``) so absence is explicit and never fabricated.
        """
        try:
            root = ET.fromstring(data if isinstance(data, bytes) else data.encode())
        except ET.ParseError as exc:
            raise ParserError(f"Malformed XBRL/XML document: {exc}") from exc

        contexts = self._parse_contexts(root)
        units = self._parse_units(root)

        result = ParseResult(metadata=self.metadata)
        seen_concepts: set[str] = set()

        for elem in root:
            tag = _localname(elem.tag)
            if elem.tag.startswith(f"{{{_XBRLI}}}"):
                # Skip XBRL structural elements (contexts, units, schemaRef...).
                continue
            context_ref = elem.get("contextRef")
            if context_ref is None:
                continue  # Not a reported fact.

            concept = self._concept_name(elem)
            seen_concepts.add(concept)
            if concepts is not None and concept not in concepts:
                continue

            fact = self._build_fact(
                elem=elem,
                concept=concept,
                context=contexts.get(context_ref),
                unit=units.get(elem.get("unitRef", "")),
                document_id=document_id,
                target_scale=target_scale,
                result=result,
            )
            if fact is not None:
                result.facts.append(fact)

        # Explicitly emit MISSING facts for requested-but-absent concepts.
        if concepts is not None:
            for concept in concepts:
                if concept not in seen_concepts:
                    result.facts.append(
                        self._missing_fact(concept, document_id)
                    )

        self._log(result, audit_hook, document_id=document_id)
        return result

    # -- context / unit parsing ----------------------------------------------

    def _parse_contexts(self, root: ET.Element) -> dict[str, dict]:
        contexts: dict[str, dict] = {}
        for ctx in root.findall(f"{{{_XBRLI}}}context"):
            ctx_id = ctx.get("id")
            if ctx_id is None:
                continue
            entity_el = ctx.find(f"{{{_XBRLI}}}entity")
            entity_id = None
            if entity_el is not None:
                ident = entity_el.find(f"{{{_XBRLI}}}identifier")
                if ident is not None and ident.text:
                    entity_id = ident.text.strip()
            period_el = ctx.find(f"{{{_XBRLI}}}period")
            instant = start = end = None
            if period_el is not None:
                inst = period_el.find(f"{{{_XBRLI}}}instant")
                s = period_el.find(f"{{{_XBRLI}}}startDate")
                e = period_el.find(f"{{{_XBRLI}}}endDate")
                instant = inst.text.strip() if inst is not None and inst.text else None
                start = s.text.strip() if s is not None and s.text else None
                end = e.text.strip() if e is not None and e.text else None
            contexts[ctx_id] = {
                "entity_id": entity_id,
                "instant": instant,
                "start": start,
                "end": end,
            }
        return contexts

    def _parse_units(self, root: ET.Element) -> dict[str, str]:
        units: dict[str, str] = {}
        for unit in root.findall(f"{{{_XBRLI}}}unit"):
            unit_id = unit.get("id")
            if unit_id is None:
                continue
            measure = unit.find(f"{{{_XBRLI}}}measure")
            if measure is not None and measure.text:
                # e.g. "iso4217:USD" -> "USD"
                units[unit_id] = measure.text.strip().rsplit(":", 1)[-1]
        return units

    def _concept_name(self, elem: ET.Element) -> str:
        """Reconstruct a prefixed concept name from the element QName."""
        if "}" in elem.tag:
            ns, local = elem.tag[1:].split("}", 1)
            prefix = _NAMESPACE_PREFIX.get(ns)
            return f"{prefix}:{local}" if prefix else local
        return elem.tag

    # -- fact construction ----------------------------------------------------

    def _build_fact(
        self,
        *,
        elem: ET.Element,
        concept: str,
        context: dict | None,
        unit: str | None,
        document_id: str,
        target_scale: str,
        result: ParseResult,
    ) -> CanonicalFact | None:
        raw_text = (elem.text or "").strip()
        source_ref = SourceRef(document_id=document_id, taxonomy_concept=concept)

        if context is None:
            result.warnings.append(
                f"Concept {concept!r} references an unknown context; skipped."
            )
            return None

        period = self._resolve_period(context)
        entity_id = context.get("entity_id")

        if not raw_text:
            # Reported but empty -> explicit not_disclosed, never a zero value.
            return CanonicalFact(
                fact_id=str(uuid.uuid4()),
                name=concept,
                taxonomy_concept=concept,
                entity_id=entity_id,
                period_start=period.period_start if period else None,
                period_end=period.period_end if period else None,
                period_type=period.period_type if period else None,
                fiscal_year=period.fiscal_year if period else None,
                status=FactStatus.NOT_DISCLOSED,
                extraction_method=ExtractionMethod.XBRL,
                source_refs=[source_ref],
                created_by=self.metadata.created_by,
            )

        scale = self._resolve_scale(elem)
        normalized = None
        try:
            normalized = normalize_amount(
                raw_text,
                scale=scale,
                currency=unit,
                target_scale=target_scale,
            )
        except NormalizationError:
            # Non-numeric concept value: keep raw, no normalized number.
            normalized = None

        return CanonicalFact(
            fact_id=str(uuid.uuid4()),
            name=concept,
            raw_value=raw_text,
            raw_unit=unit,
            normalized_value=normalized.value if normalized else None,
            normalized_unit=normalized.unit if normalized else None,
            currency=unit,
            scale=scale,
            taxonomy_concept=concept,
            entity_id=entity_id,
            period_start=period.period_start if period else None,
            period_end=period.period_end if period else None,
            period_type=period.period_type if period else None,
            fiscal_year=period.fiscal_year if period else None,
            normalization_method=normalized.method if normalized else None,
            status=FactStatus.UNVERIFIED,
            extraction_method=ExtractionMethod.XBRL,
            source_refs=[source_ref],
            created_by=self.metadata.created_by,
        )

    def _resolve_period(self, context: dict):
        try:
            if context.get("instant"):
                return normalize_period(instant=context["instant"])
            if context.get("start") and context.get("end"):
                return normalize_period(
                    period_start=context["start"], period_end=context["end"]
                )
        except NormalizationError:
            return None
        return None

    def _resolve_scale(self, elem: ET.Element) -> str | None:
        """Derive a scale descriptor from the XBRL ``scale`` attribute.

        XBRL ``scale`` is a power of ten applied to the reported value; map the
        common values to our canonical scale words. Absent -> ``units``.
        """
        scale_attr = elem.get("scale")
        if scale_attr is None:
            return None
        mapping = {"3": "thousands", "6": "millions", "9": "billions", "0": "units"}
        return mapping.get(scale_attr.strip())

    def _missing_fact(self, concept: str, document_id: str) -> CanonicalFact:
        return CanonicalFact(
            fact_id=str(uuid.uuid4()),
            name=concept,
            taxonomy_concept=concept,
            status=FactStatus.MISSING,
            extraction_method=ExtractionMethod.XBRL,
            source_refs=[SourceRef(document_id=document_id, taxonomy_concept=concept)],
            created_by=self.metadata.created_by,
        )


# Known taxonomy namespace -> prefix map for concept reconstruction. Extend as
# needed; unknown namespaces fall back to the local name.
_NAMESPACE_PREFIX: dict[str, str] = {
    "http://fasb.org/us-gaap/2023": "us-gaap",
    "http://fasb.org/us-gaap": "us-gaap",
    "http://xbrl.sec.gov/dei/2023": "dei",
    "http://example.com/poc": "poc",
}
