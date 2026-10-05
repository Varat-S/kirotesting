"""Parse an admitted filing bundle into canonical facts and inspectable diagnostics."""

from app.services.extraction.base import ParseResult, ParserMetadata, ParserError
from app.schemas.enums import ExtractionMethod
from .adapter import adapt_facts
from .inline_xbrl import InlineXbrlParser
from .linkbases import (
    load_labels,
    load_role_definitions,
    load_presentation,
    build_sections,
)
from .narrative import narrative_evidence
from .proxy import parse_proxy
from .subsidiaries import find_exhibit21, parse_exhibit21


def parse_bundle(bundle, *, entity_id, scope="consolidated", accounting_basis="GAAP"):
    if any(not f.admitted or not f.document_id for f in bundle.files):
        raise ParserError(
            "Every parser input must have passed evidence ingestion/cutoff."
        )
    primary = next(f for f in bundle.files if f.role == "primary_inline_xbrl")
    by_role = {f.role: f for f in bundle.files if f.accession == bundle.accession}

    def data(role):
        return by_role[role].content if role in by_role else None

    labels = load_labels(data("label_linkbase"))
    definitions = load_role_definitions(data("schema"))
    presentation = load_presentation(data("presentation_linkbase"))
    parsed = InlineXbrlParser().parse(primary.content, labels=labels)
    sections = build_sections(presentation, definitions, parsed["facts"], labels)
    roles = {}
    for section in sections:
        for row in section["line_items"]:
            roles.setdefault(row["concept"], set()).add(section["role"])
    roles = {concept: sorted(values) for concept, values in roles.items()}
    facts = adapt_facts(
        parsed,
        document_id=primary.document_id,
        entity_id=entity_id,
        cik=bundle.cik,
        accession=bundle.accession,
        scope=scope,
        accounting_basis=accounting_basis,
        presentation_roles=roles,
    )
    narrative = narrative_evidence(
        parsed["document_text"],
        document_id=primary.document_id,
        accession=bundle.accession,
    )
    subsidiaries = []
    for file in bundle.files:
        if file.role == "proxy":
            narrative.extend(parse_proxy(file))
        elif find_exhibit21(file):
            subsidiaries.extend(parse_exhibit21(file))
    diagnostics = {
        "cik": bundle.cik,
        "ticker": bundle.ticker,
        "accession": bundle.accession,
        "form": bundle.form,
        "report_date": bundle.report_date.isoformat(),
        "filing_date": bundle.filing_date.isoformat(),
        "primary_document_id": primary.document_id,
        "files": [f.evidence_metadata() for f in bundle.files],
        "contexts": parsed["contexts"],
        "units": parsed["units"],
        "labels": labels,
        "presentation": sections,
        "observations": parsed["facts"],
        "subsidiaries": subsidiaries,
        "warnings": parsed["warnings"],
        "parser_version": parsed["parser_version"],
        "deferred_linkbases": [
            f.role
            for f in bundle.files
            if f.role in {"definition_linkbase", "calculation_linkbase"}
        ],
    }
    return (
        ParseResult(
            ParserMetadata("sec-inline-xbrl", "1.0.0", ExtractionMethod.INLINE_XBRL),
            facts,
            parsed["warnings"],
        ),
        diagnostics,
        narrative,
    )
