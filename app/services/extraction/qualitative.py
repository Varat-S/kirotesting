"""AI qualitative extraction (task 6.3; Req 3.3, 3.4, 3.6).

The LLM extracts qualitative borrower facts -- management, ownership, corporate
structure, competitive position, industry risks and qualitative debt terms --
that are not reliably available via deterministic parsers. This service:

* runs the ``qualitative_extraction`` prompt through the single
  :class:`~app.services.llm.client.LLMClient` and uses only its SCHEMA-VALIDATED
  output (Req 3.6 / 19.4); a rejected run yields NO facts;
* converts each validated :class:`ExtractedQualitativeFact` into a
  :class:`~app.schemas.evidence.CanonicalFact` with
  ``extraction_method=ExtractionMethod.LLM`` and >=1 source ref (Req 3.5);
* NEVER sets ``verified`` -- AI facts enter in a non-verified state and go
  through the SAME reconciliation path as any other fact (Req 3.4). They are
  deliberately NOT shortcut to verified here.

The resulting facts are returned (and optionally persisted) with a
``fact_extracted`` audit event, so they can be reconciled against deterministic
facts downstream (seeded AI-vs-deterministic conflict tests, task 6.6).
"""

from __future__ import annotations

from app.schemas.enums import ExtractionMethod, FactStatus
from app.schemas.evidence import CanonicalFact, SourceRef
from app.schemas.llm import ExtractedQualitativeFact, ExtractionResponse
from app.services.audit.log import ActorType, AuditLog, EventType
from app.services.llm.client import LLMClient, ModelRunResult


class QualitativeExtractionError(ValueError):
    """Raised when extraction cannot proceed (e.g. schema-invalid LLM output)."""


def _to_canonical_fact(ai_fact: ExtractedQualitativeFact) -> CanonicalFact:
    """Map a validated AI fact to a CanonicalFact (never auto-verified)."""
    status = FactStatus(ai_fact.status)
    if status is FactStatus.VERIFIED:  # defensive: schema already forbids this
        raise QualitativeExtractionError(
            f"AI fact {ai_fact.fact_id!r} may not be 'verified' (Req 3.4)."
        )
    refs = [
        SourceRef(
            document_id=r.document_id,
            page=r.page,
            table=r.table,
            row_label=r.row_label,
            cell=r.cell,
        )
        for r in ai_fact.source_refs
    ]
    return CanonicalFact(
        fact_id=ai_fact.fact_id,
        name=ai_fact.name,
        raw_value=ai_fact.statement,
        status=status,
        extraction_method=ExtractionMethod.LLM,
        confidence=ai_fact.confidence,
        period_type=ai_fact.period,
        source_refs=refs,
        created_by="llm:qualitative_extraction",
    )


class QualitativeExtractor:
    """Run AI qualitative extraction and emit never-verified CanonicalFacts."""

    def __init__(
        self,
        client: LLMClient,
        *,
        audit: AuditLog | None = None,
        case_id: str | None = None,
    ) -> None:
        self._client = client
        self._audit = audit
        self._case_id = case_id

    def extract(
        self,
        inputs: dict,
        *,
        evidence_ids: list[str] | None = None,
        case_version: int | None = None,
        key: str | None = None,
    ) -> tuple[list[CanonicalFact], ModelRunResult]:
        """Extract qualitative facts; return (facts, model-run result).

        If the LLM output is schema-invalid the run is rejected and NO facts are
        produced (the rejection is logged in the model-run registry by the
        client). Returns an empty fact list in that case.
        """
        run = self._client.extract(
            inputs,
            evidence_ids=evidence_ids,
            case_id=self._case_id,
            case_version=case_version,
            key=key,
        )
        if not run.is_valid or run.parsed is None:
            return [], run

        response = ExtractionResponse.model_validate(run.parsed)
        facts = [_to_canonical_fact(f) for f in response.facts]

        if self._audit is not None:
            for fact in facts:
                self._audit.record(
                    EventType.FACT_EXTRACTED,
                    case_id=self._case_id,
                    actor_type=ActorType.MODEL,
                    actor_id=run.model_id,
                    after={
                        "fact_id": fact.fact_id,
                        "name": fact.name,
                        "status": fact.status.value,
                        "extraction_method": fact.extraction_method.value,
                    },
                    reason="AI qualitative fact extracted (never auto-verified).",
                    linked_objects=[f"fact:{fact.fact_id}", f"model_run:{run.run_id}"],
                )
        return facts, run
