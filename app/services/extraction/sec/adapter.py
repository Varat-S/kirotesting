"""Convert already transformed reported values to canonical units exactly once."""

from datetime import date
from app.core.hashing import content_hash
from app.schemas.enums import ExtractionMethod, FactStatus
from app.schemas.evidence import CanonicalFact, SourceRef
from .inline_xbrl import InlineXbrlParser


def adapt_facts(
    parsed,
    *,
    document_id,
    entity_id,
    cik,
    accession,
    accounting_basis="GAAP",
    scope="consolidated",
    presentation_roles=None,
):
    observations = parsed["facts"]
    focus = next(
        (
            f["value"]
            for f in observations
            if f["concept"] == "dei:DocumentFiscalYearFocus"
        ),
        None,
    )
    report = next(
        (
            f["value"]
            for f in observations
            if f["concept"] == "dei:DocumentPeriodEndDate"
        ),
        None,
    )
    try:
        report_date = date.fromisoformat(report)
        fiscal_focus = int(focus)
    except (ValueError, TypeError):
        report_date, fiscal_focus = None, None
    result = []
    for observation in observations:
        period = observation["period"]
        end = date.fromisoformat(period.get("date") or period["end"])
        start = (
            date.fromisoformat(period["start"])
            if period["type"] == "duration"
            else None
        )
        year = fiscal_focus + end.year - report_date.year if report_date else end.year
        period_type = (
            "FY" if start and 330 <= (end - start).days <= 400 else period["type"]
        )
        unit, value = observation["unit"], observation["value"]
        numeric = observation["type"] == "numeric"
        currency = unit if unit and len(unit) == 3 and unit.isupper() else None
        normalized = value if numeric else None
        normalized_unit = unit
        scale = "units"
        if currency:
            normalized = value / 1_000_000 if value is not None else None
            normalized_unit, scale = f"{currency}_million", "millions"
        elif unit == "pure":
            normalized_unit = "ratio"
        observed_entity = observation["entity"]
        try:
            entity = entity_id if int(observed_entity) == int(cik) else observed_entity
        except (ValueError, TypeError):
            entity = entity_id if observed_entity == cik else observed_entity
        refs = [
            SourceRef(
                document_id=document_id,
                taxonomy_concept=observation["concept"],
                xbrl_context_id=observation["context_id"],
                inline_element_id=observation["inline_element_id"],
                sec_accession=accession,
                presentation_role=role,
            )
            for role in (presentation_roles or {}).get(observation["concept"], [None])
        ]
        fact = CanonicalFact(
            fact_id=content_hash(
                {"document": document_id, "observation": observation["observation_id"]}
            ),
            name=observation["concept"],
            original_name=observation["concept"],
            taxonomy_concept=observation["concept"],
            raw_value=None if observation["nil"] else observation["raw_text"],
            raw_unit=unit,
            normalized_value=normalized,
            normalized_unit=normalized_unit if numeric else None,
            currency=currency,
            scale=scale if numeric else None,
            period_start=start,
            period_end=end,
            period_type=period_type,
            fiscal_year=year,
            entity_id=entity,
            accounting_basis=accounting_basis,
            consolidation_scope=scope,
            dimensions=observation["dimensions"],
            xbrl_context_id=observation["context_id"],
            inline_element_id=observation["inline_element_id"],
            sec_accession=accession,
            source_label=observation["label"],
            source_refs=refs,
            status=FactStatus.MISSING if observation["nil"] else FactStatus.UNVERIFIED,
            extraction_method=ExtractionMethod.INLINE_XBRL,
            created_by=InlineXbrlParser.version,
            normalization_method=f"ix-transform={observation['format']};scale={observation['scale']};sign={observation['sign']};target={scale}",
        )
        result.append(fact)
    return result
