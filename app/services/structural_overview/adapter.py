"""Deterministic adapter from AcceptedBusinessOverview to the SO thin slice."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from pydantic import ValidationError

from app.core.hashing import content_hash
from app.schemas.bo import AcceptedBusinessOverview
from app.schemas.bo_m3 import M3CorporateAssessment
from app.schemas.structural_overview import (
    ASSESSMENT_SCHEMA_VERSION,
    TRANSFORMATION_VERSION,
    AssessmentAuditMetadata,
    BorrowerContext,
    BusinessAssessment,
    CompoundRisk,
    CorporateStructureAssessment,
    CorporateStructureAudit,
    CuratedClaim,
    DownstreamDependencies,
    DownstreamSection,
    EvidenceReference,
    FindingProvenance,
    GovernanceState,
    MitigantClaim,
    NormalizedFinding,
    StructuralOverviewAssessment,
)
from app.services.bo.reporting import handoff_content_hash


SUPPORTED_HANDOFF_VERSION = "bo-handoff-1.2"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
_MODULE_BUCKETS = {
    "M1": "business_model_and_revenue_drivers",
    "M2": "industry_and_competitive_risks",
    "M3": "management_and_operational_findings",
    "M4": "strategy_and_capital_allocation_findings",
}


class StructuralOverviewInputError(ValueError):
    """The BO handoff cannot safely cross the integration boundary."""


def _valid_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise StructuralOverviewInputError(f"Malformed {label}: {value!r}.")
    return value


def assessment_content_hash(payload: dict[str, Any] | StructuralOverviewAssessment) -> str:
    """Hash every assessment field except the self-referential content_hash."""
    data = payload.model_dump(mode="json") if isinstance(payload, StructuralOverviewAssessment) else dict(payload)
    data.pop("content_hash", None)
    return content_hash(data)


def _claim(record: dict[str, Any], *, qualified: bool) -> CuratedClaim:
    return CuratedClaim(
        claim_id=record["claim_id"], title=record["title"], text=record["text"],
        section=record["section"], materiality=record["materiality"],
        hypothesis_ids=record.get("hypothesis_ids", []),
        evidence_ids=record.get("evidence_ids", []),
        qualification="qualified" if qualified else "accepted",
    )


def _corporate_structure(
    module_data: dict[str, Any],
) -> tuple[CorporateStructureAssessment, str | None, str | None, list[str], str | None]:
    """Map only validator-admitted M3 records into consumable SO fields."""
    status = module_data.get("m3_assessment_status", "unavailable")
    raw = module_data.get("m3_assessment")
    if status != "validated":
        if raw is not None:
            raise StructuralOverviewInputError(
                "M3 assessment is present but is not marked validated; refusing ambiguous structural input."
            )
        limitation = (
            "BO supplied no validated M3 corporate assessment; SO must establish "
            "the transaction borrower and legal structure independently."
        )
        unavailable = CorporateStructureAssessment(
            source_status="unavailable",
            borrower_identification={"status": "unavailable", "limitation": limitation},
            information_gaps=[limitation],
            downstream_dependencies=[{
                "module": "SO",
                "question": "Confirm the proposed facility obligor and legal borrower from transaction-specific evidence.",
            }],
        )
        return unavailable, None, None, [], limitation
    if not isinstance(raw, dict):
        raise StructuralOverviewInputError(
            "M3 is marked validated but module_conclusions.M3.m3_assessment is missing."
        )
    try:
        governed = M3CorporateAssessment.model_validate(raw)
    except ValidationError as exc:
        raise StructuralOverviewInputError(f"Invalid governed M3 assessment: {exc}") from exc

    borrower = governed.borrower_identification
    by_id: dict[str, Any] = {}
    for entity in governed.legal_entities:
        _valid_id(entity.entity_id, "M3 legal entity ID")
        if entity.entity_id in by_id:
            raise StructuralOverviewInputError(
                f"Duplicate governed M3 legal entity ID {entity.entity_id!r}."
            )
        if entity.identity_type != "legal_entity":
            raise StructuralOverviewInputError(
                f"Governed M3 graph contains non-legal identity {entity.entity_id!r}."
            )
        by_id[entity.entity_id] = entity
    for relationship in governed.ownership_relationships:
        _valid_id(relationship.relationship_id, "M3 ownership relationship ID")
        if (relationship.parent_entity_id not in by_id
                or relationship.subsidiary_entity_id not in by_id):
            raise StructuralOverviewInputError(
                f"Governed ownership relationship {relationship.relationship_id!r} references an unadmitted entity."
            )
    for relationship in governed.cash_transfer_relationships:
        _valid_id(relationship.transfer_id, "M3 cash-transfer relationship ID")
        if (relationship.source_entity_id not in by_id
                or relationship.destination_entity_id not in by_id):
            raise StructuralOverviewInputError(
                f"Governed cash-transfer relationship {relationship.transfer_id!r} references an unadmitted entity."
            )
    for location in governed.cash_flow_locations:
        _valid_id(location.cash_flow_id, "M3 cash-flow location ID")
        if location.entity_id not in by_id:
            raise StructuralOverviewInputError(
                f"Governed cash-flow location {location.cash_flow_id!r} references an unadmitted entity."
            )
    for records, attribute, label in (
        (governed.ownership_relationships, "relationship_id", "ownership relationship"),
        (governed.cash_transfer_relationships, "transfer_id", "cash-transfer relationship"),
        (governed.cash_flow_locations, "cash_flow_id", "cash-flow location"),
    ):
        identifiers = [getattr(record, attribute) for record in records]
        if len(identifiers) != len(set(identifiers)):
            raise StructuralOverviewInputError(
                f"Governed M3 graph contains a duplicate {label} ID."
            )
    borrower_name = None
    if borrower.status == "identified":
        entity = by_id.get(borrower.entity_id or "")
        if entity is None or entity.identity_type != "legal_entity":
            raise StructuralOverviewInputError(
                "Governed M3 borrower does not resolve to an admitted legal entity."
            )
        borrower_name = entity.legal_name

    mapped = CorporateStructureAssessment(
        source_status="validated",
        source_schema_version=governed.schema_version,
        validator_id=governed.validator_id,
        validator_version=governed.validator_version,
        borrower_identification=borrower,
        legal_entities=governed.legal_entities,
        ownership_relationships=governed.ownership_relationships,
        cash_flow_locations=governed.cash_flow_locations,
        cash_transfer_relationships=governed.cash_transfer_relationships,
        structural_credit_findings=governed.structural_credit_findings,
        management_assessments=governed.management_assessments,
        governance_screening=governed.governance_screening,
        information_gaps=governed.information_gaps,
        downstream_dependencies=governed.downstream_dependencies,
        audit=CorporateStructureAudit(
            validation_issues=governed.validation_issues,
            rejected_items=governed.rejected_items,
            model_proposal=governed.model_proposal,
        ),
    )
    return (
        mapped,
        borrower_name,
        borrower.entity_id if borrower.status == "identified" else None,
        borrower.evidence_ids if borrower.status == "identified" else [],
        borrower.limitation,
    )


def adapt_business_overview(
    payload: dict[str, Any] | AcceptedBusinessOverview,
    *,
    expected_case_id: str | None = None,
) -> StructuralOverviewAssessment:
    """Validate and map one BO handoff without rewriting analytical language."""
    try:
        handoff = payload if isinstance(payload, AcceptedBusinessOverview) else AcceptedBusinessOverview.model_validate(payload)
    except ValidationError as exc:
        raise StructuralOverviewInputError(f"Invalid BO handoff: {exc}") from exc
    source = handoff.model_dump(mode="json")

    if handoff.schema_version != SUPPORTED_HANDOFF_VERSION:
        raise StructuralOverviewInputError(
            f"Unsupported BO handoff schema {handoff.schema_version!r}; expected {SUPPORTED_HANDOFF_VERSION!r}."
        )
    for value, label in ((handoff.handoff_id, "handoff ID"), (handoff.analysis_run_id, "analysis run ID"), (handoff.case_id, "case ID")):
        _valid_id(value, label)
    if expected_case_id is not None and handoff.case_id != expected_case_id:
        raise StructuralOverviewInputError(
            f"Case identity mismatch: handoff is {handoff.case_id!r}, expected {expected_case_id!r}."
        )
    if not handoff.borrower_name or not handoff.borrower_name.strip():
        raise StructuralOverviewInputError("The BO handoff has no borrower legal name.")
    if handoff.conclusion_status not in {"accepted", "accepted_with_qualification"}:
        raise StructuralOverviewInputError(
            f"BO conclusion is not accepted for downstream use: {handoff.conclusion_status!r}."
        )
    if handoff.content_hash is None or handoff_content_hash(source) != handoff.content_hash:
        raise StructuralOverviewInputError("BO handoff content hash is missing or invalid.")
    if handoff.canonical_references.get("analysis_run_id") != handoff.analysis_run_id:
        raise StructuralOverviewInputError("Canonical analysis-run reference does not match the handoff run.")

    findings: dict[str, dict[str, Any]] = {}
    evidence_to_hypotheses: dict[str, set[str]] = defaultdict(set)
    evidence_to_sections: dict[str, set[str]] = defaultdict(set)
    buckets: dict[str, list[NormalizedFinding]] = {name: [] for name in _MODULE_BUCKETS.values()}
    qualified_ids: list[str] = []

    for module, target in _MODULE_BUCKETS.items():
        module_data = handoff.module_conclusions.get(module)
        if not module_data:
            raise StructuralOverviewInputError(f"Missing required BO module conclusion {module}.")
        for finding in module_data.get("findings", []):
            hypothesis_id = _valid_id(finding.get("hypothesis_id"), "source hypothesis ID")
            if hypothesis_id in findings:
                raise StructuralOverviewInputError(f"Duplicate source hypothesis ID {hypothesis_id!r}.")
            findings[hypothesis_id] = finding
            evidence_ids = [*finding.get("supporting_evidence_ids", []), *finding.get("contradicting_evidence_ids", [])]
            for evidence_id in evidence_ids:
                _valid_id(evidence_id, "evidence ID")
                evidence_to_hypotheses[evidence_id].add(hypothesis_id)
                evidence_to_sections[evidence_id].add(finding.get("section", module))
            qualified = finding.get("disposition") == "qualified" or finding.get("admissibility") == "qualified_narrative"
            if qualified:
                qualified_ids.append(hypothesis_id)
            buckets[target].append(NormalizedFinding(
                claim=finding["claim"], claim_type=finding["claim_type"],
                direction=finding["direction"], disposition=finding["disposition"],
                admissibility=finding["admissibility"], confidence=finding["confidence"],
                materiality=finding["materiality"],
                supporting_evidence_ids=finding.get("supporting_evidence_ids", []),
                contradicting_evidence_ids=finding.get("contradicting_evidence_ids", []),
                missing_information=finding.get("missing_information", []),
                validation_notes=finding.get("validation_notes", []),
                review_required=qualified or bool(finding.get("missing_information")),
                accepted_for_handoff=hypothesis_id in handoff.accepted_finding_ids,
                provenance=FindingProvenance(
                    source_handoff_id=handoff.handoff_id, source_run_id=handoff.analysis_run_id,
                    source_module=module, source_section=finding.get("section", module),
                    source_hypothesis_id=hypothesis_id,
                ),
            ))

    corporate_structure, borrower_name, borrower_entity_id, borrower_evidence_ids, borrower_limitation = (
        _corporate_structure(handoff.module_conclusions["M3"])
    )

    unknown_accepted = set(handoff.accepted_finding_ids) - set(findings)
    if unknown_accepted:
        raise StructuralOverviewInputError(f"Accepted finding IDs do not resolve: {sorted(unknown_accepted)!r}.")

    curated_records = [
        *handoff.business_strengths, *handoff.operational_vulnerabilities,
        *handoff.compound_business_risks,
    ]
    claims_by_id: dict[str, dict[str, Any]] = {}
    evidence_to_claims: dict[str, set[str]] = defaultdict(set)
    hypothesis_to_claims: dict[str, set[str]] = defaultdict(set)
    for record in curated_records:
        claim_id = _valid_id(record.get("claim_id"), "source claim ID")
        previous = claims_by_id.get(claim_id)
        if previous is not None and previous != record:
            raise StructuralOverviewInputError(f"Conflicting source claim ID {claim_id!r}.")
        claims_by_id[claim_id] = record
        broken_hypotheses = set(record.get("hypothesis_ids", [])) - set(findings)
        if broken_hypotheses:
            raise StructuralOverviewInputError(
                f"Claim {claim_id!r} has broken hypothesis links: {sorted(broken_hypotheses)!r}."
            )
        for hypothesis_id in record.get("hypothesis_ids", []):
            hypothesis_to_claims[hypothesis_id].add(claim_id)
        broken_evidence = set(record.get("evidence_ids", [])) - set(evidence_to_hypotheses)
        if broken_evidence:
            raise StructuralOverviewInputError(
                f"Claim {claim_id!r} has broken evidence links: {sorted(broken_evidence)!r}."
            )
        for evidence_id in record.get("evidence_ids", []):
            evidence_to_claims[evidence_id].add(claim_id)

    for record in [*handoff.validated_mitigants, *handoff.unproven_mitigants]:
        hypothesis_id = _valid_id(record.get("hypothesis_id"), "mitigant hypothesis ID")
        if hypothesis_id not in findings:
            raise StructuralOverviewInputError(
                f"Mitigant has a broken hypothesis link: {hypothesis_id!r}."
            )
        broken_evidence = set(record.get("evidence_ids", [])) - set(evidence_to_hypotheses)
        if broken_evidence:
            raise StructuralOverviewInputError(
                f"Mitigant {hypothesis_id!r} has broken evidence links: {sorted(broken_evidence)!r}."
            )
        for evidence_id in record.get("evidence_ids", []):
            evidence_to_claims[evidence_id].add(hypothesis_id)

    for bucket, normalized_findings in buckets.items():
        buckets[bucket] = [
            finding.model_copy(update={
                "provenance": finding.provenance.model_copy(update={
                    "source_claim_ids": sorted(
                        hypothesis_to_claims[finding.provenance.source_hypothesis_id]
                    )
                })
            })
            for finding in normalized_findings
        ]

    qualification = handoff.conclusion_status == "accepted_with_qualification"
    strengths = [_claim(x, qualified=qualification) for x in handoff.business_strengths]
    vulnerabilities = [_claim(x, qualified=qualification) for x in handoff.operational_vulnerabilities]
    mitigants = [
        MitigantClaim(
            capability=x["capability"], existence=x["existence"],
            effectiveness=effectiveness, source_hypothesis_id=x["hypothesis_id"],
            evidence_ids=x.get("evidence_ids", []),
            qualification="qualified" if qualification or effectiveness == "unproven" else "accepted",
        )
        for records, effectiveness in ((handoff.validated_mitigants, "validated"), (handoff.unproven_mitigants, "unproven"))
        for x in records
    ]
    compound = [
        CompoundRisk(
            **_claim(x, qualified=qualification).model_dump(),
            component_hypothesis_ids=x.get("hypothesis_ids", []),
        ) for x in handoff.compound_business_risks
    ]

    for entity in corporate_structure.legal_entities:
        for evidence_id in entity.evidence_ids:
            evidence_to_sections[evidence_id].add("M3.corporate_structure")
    for relationship in corporate_structure.ownership_relationships:
        for evidence_id in relationship.evidence_ids:
            evidence_to_sections[evidence_id].add("M3.corporate_structure")
    for location in corporate_structure.cash_flow_locations:
        for evidence_id in location.evidence_ids:
            evidence_to_sections[evidence_id].add("M3.corporate_structure")
    for relationship in corporate_structure.cash_transfer_relationships:
        for evidence_id in relationship.evidence_ids:
            evidence_to_sections[evidence_id].add("M3.corporate_structure")
    for finding in [
        *corporate_structure.structural_credit_findings,
        *corporate_structure.governance_screening,
    ]:
        for evidence_id in [
            *finding.supporting_evidence_ids,
            *finding.contradicting_evidence_ids,
        ]:
            evidence_to_sections[evidence_id].add("M3.corporate_structure")
            if finding.hypothesis_id:
                evidence_to_hypotheses[evidence_id].add(finding.hypothesis_id)
    for observation in corporate_structure.management_assessments:
        for evidence_id in [
            *observation.supporting_evidence_ids,
            *observation.contradicting_evidence_ids,
        ]:
            evidence_to_sections[evidence_id].add("M3.corporate_structure")

    evidence_references = [
        EvidenceReference(
            evidence_id=evidence_id,
            source_hypothesis_ids=sorted(hypothesis_ids),
            source_claim_ids=sorted(evidence_to_claims[evidence_id]),
            source_sections=sorted(evidence_to_sections[evidence_id]),
        )
        for evidence_id, hypothesis_ids in sorted(
            (evidence_id, evidence_to_hypotheses[evidence_id])
            for evidence_id in evidence_to_sections
        )
    ]
    structural_topics = sorted({
        "Legal entity and obligor structure",
        "Facility terms, collateral, guarantees, and covenant protections",
    })
    structural_topics.extend(
        dependency["question"]
        for dependency in corporate_structure.downstream_dependencies
        if dependency.get("module") == "SO" and dependency.get("question")
        and dependency["question"] not in structural_topics
    )
    if corporate_structure.source_status == "unavailable":
        structural_status = "unavailable"
        structural_reason = (
            "BO supplied no validated M3 corporate assessment; obligor and legal structure are unavailable."
        )
    elif corporate_structure.borrower_identification.status != "identified":
        structural_status = "partial"
        structural_reason = (
            "A validator-admitted corporate graph is available, but the proposed facility obligor is unavailable; SO must confirm it from transaction evidence."
        )
    else:
        structural_status = "partial"
        structural_reason = (
            "The governed M3 graph is available; SO must independently verify obligor identity, ranking, restrictions, and facility terms."
        )
    sections = {
        "business_overview": DownstreamSection(
            status="partial" if qualification or handoff.human_review_status != "accepted" else "complete",
            reason="BO is accepted with qualification and awaits human review." if qualification else "BO handoff is available.",
            source_ids=[handoff.handoff_id],
        ),
        "financial_overview": DownstreamSection(
            status="unavailable", reason="Financial Overview has not been executed; an empty BO-to-FO handoff list does not imply completion."
        ),
        "structural_overview": DownstreamSection(
            status=structural_status, reason=structural_reason,
            source_ids=[handoff.handoff_id] if corporate_structure.source_status == "validated" else [],
        ),
        "final_credit_synthesis": DownstreamSection(
            status="blocked", reason="Final synthesis requires accepted BO, FO, Structural Overview, and applicable facility inputs."
        ),
    }

    assessment_seed = {
        "schema_version": ASSESSMENT_SCHEMA_VERSION,
        "source_handoff_id": handoff.handoff_id,
        "source_bo_run_id": handoff.analysis_run_id,
        "transformation_version": TRANSFORMATION_VERSION,
    }
    draft = StructuralOverviewAssessment(
        assessment_id="soa_" + content_hash(assessment_seed)[:16],
        status="partial",
        source_handoff_id=handoff.handoff_id,
        source_bo_run_id=handoff.analysis_run_id,
        borrower=BorrowerContext(
            reporting_entity_name=handoff.borrower_name,
            legal_name=borrower_name, entity_id=borrower_entity_id,
            identification_status=corporate_structure.borrower_identification.status,
            identification_evidence_ids=borrower_evidence_ids,
            identification_limitation=borrower_limitation,
            case_id=handoff.case_id, as_of_date=handoff.as_of_date,
            evidence_cutoff_timestamp=handoff.evidence_cutoff_timestamp,
            evidence_snapshot_version=handoff.evidence_snapshot_version,
            source_bo_run_id=handoff.analysis_run_id,
        ),
        business_assessment=BusinessAssessment(
            **buckets,
            material_business_vulnerabilities=vulnerabilities,
            business_strengths=strengths, mitigants=mitigants, compound_risks=compound,
        ),
        corporate_structure=corporate_structure,
        evidence_references=evidence_references,
        governance=GovernanceState(
            conclusion_status=handoff.conclusion_status,
            accepted_conclusion_ids=[x for x in [handoff.business_topic_conclusion_id] if x],
            qualified_conclusion_ids=sorted(qualified_ids),
            unresolved_challenges=handoff.unresolved_challenges,
            open_questions=handoff.open_questions,
            missing_information=handoff.evidence_limitations,
            human_review_status=handoff.human_review_status,
            provisional_business_risk_band=handoff.business_risk_band,
            business_risk_score_status=handoff.business_risk_score_status,
            scoring_limitations=handoff.score_limitations,
        ),
        downstream_dependencies=DownstreamDependencies(
            bo_to_fo_handoffs=handoff.fo_handoffs,
            additional_financial_questions=handoff.open_questions,
            structural_or_facility_topics=structural_topics,
            sections=sections,
        ),
        audit_metadata=AssessmentAuditMetadata(
            transformation_version=TRANSFORMATION_VERSION,
            source_schema_version=handoff.schema_version,
            source_handoff_content_hash=handoff.content_hash,
            source_execution_mode=handoff.execution_mode,
            canonical_hash_scope="All assessment fields except content_hash; canonical UTF-8 JSON with sorted keys and no insignificant whitespace.",
            canonical_references=handoff.canonical_references,
        ),
        content_hash="pending",
    )
    return draft.model_copy(update={"content_hash": assessment_content_hash(draft)})
