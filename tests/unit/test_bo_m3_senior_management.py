"""Validator 7 senior-management reasoning and completion-status consistency.

Offline only: every case drives :func:`validate_m3_assessment` with controlled,
admitted ``evidence_texts`` (no model is called). The tests prove the governed
distinctions the North Star requires:

* credibility (``disposition``) stays SEPARATE from credit direction
  (``credit_direction``): a credible explanation of an adverse development never
  launders a negative into a positive/neutral one;
* insufficient evidence is reported DISTINCT from a neutral finding;
* a correct financial value with an UNSUPPORTED causal/outcome attribution is
  qualified/downgraded, not accepted;
* a citation id alone is not entailment (numeric / period mismatch downgrades);
* a REJECTED material finding is excluded from the authoritative conclusion but
  retained in the audit trail; and
* completion status is consistent (incomplete / partial / complete).

Nothing forces an equal positive/negative count; direction, verification and
availability stay in separate fields throughout.
"""

from __future__ import annotations

import copy

from app.schemas.bo_m3_structure import (
    CorporateStructureArtifact,
    NO_JSON_MESSAGE,
)
from app.services.bo.m3_artifacts import build_m3_deliverables
from app.services.bo.m3_output import assemble_m3_output
from app.services.bo.m3_validation import validate_m3_assessment


ALLOWED = {"ev-1", "ev-2"}


def _management(**over):
    """A supported, outcome-evidenced CFO observation; override per case."""
    base = {
        "observation_id": "MO1", "management_id": "mgmt-1", "legal_name": "Dana Lee",
        "role": "CFO", "effective_from": None, "effective_to": None,
        "dimension": "competence", "evidence_basis": "outcome_evidence",
        "attribution": "independently_observed", "assurance_scope": None,
        "assurance_date": None,
        "hypothesis": "The CFO executed the stated deleveraging plan this year.",
        "credit_mechanism": "Demonstrated execution supports repayment discipline over time.",
        "supporting_evidence_ids": ["ev-1"], "contradicting_evidence_ids": [],
        "supporting_evidence_investigated": True,
        "contradictory_evidence_investigated": True,
        "disposition": "supported", "admissibility": "established",
        "confidence": "medium", "materiality": "medium",
        "credit_direction": "neutral", "missing_information": [],
    }
    base.update(over)
    return base


def _proposal(*management, **over):
    proposal = {
        "borrower_identification": {"status": "unavailable",
                                    "limitation": "SO confirms the obligor."},
        "legal_entities": [],
        "management_assessments": [copy.deepcopy(m) for m in management],
        "governance_screening": [],
        "information_gaps": ["Completeness not established."],
    }
    proposal.update(over)
    return proposal


def _govern(*management, evidence_texts=None, **over):
    return validate_m3_assessment(
        _proposal(*management, **over), allowed_evidence_ids=ALLOWED,
        validated_borrower_entity_ids=set(),
        evidence_texts=evidence_texts or {"ev-1": "The CFO reduced net leverage as planned."})


# -- senior-management reasoning ---------------------------------------------


def test_supported_claim_is_retained_on_faithful_evidence():
    governed = _govern(_management(
        hypothesis="Net leverage declined during FY2024.",
        credit_mechanism="Lower leverage reduces refinancing and default risk."),
        evidence_texts={"ev-1": "Net leverage declined during FY2024."})
    obs = governed.management_assessments[0]
    assert obs.disposition.value == "supported"
    assert obs.credit_direction.value == "neutral"


def test_contradicted_numeric_value_is_rejected_not_accepted():
    governed = _govern(_management(hypothesis="Revenue grew 51% in FY2024."),
                       evidence_texts={"ev-1": "Revenue grew 48.9% in FY2024."})
    obs = governed.management_assessments[0]
    assert obs.disposition.value == "rejected"
    assert any("deterministic_entailment" in i.rule_id
               for i in governed.validation_issues)


def test_mismatched_reporting_period_is_unsupported_not_contradicted():
    # A citation covering a DIFFERENT fiscal period (FY2024) does not disprove a
    # claim about another period (FY2023). Under the central rule a different
    # period is not, by itself, a conflicting assertion -> UNSUPPORTED, not
    # rejected. (Corrects an earlier test that treated a period mismatch as a
    # contradiction.)
    governed = _govern(_management(hypothesis="Leverage fell in FY2023."),
                       evidence_texts={"ev-1": "Leverage fell in FY2024."})
    obs = governed.management_assessments[0]
    assert obs.disposition.value == "unverified"
    assert obs.disposition.value != "rejected"


def test_numerical_discrepancy_downgrades_even_with_valid_citation():
    # The evidence id resolves, but no numeric token in the claim is present in
    # the cited text -> citation-id existence is not entailment.
    governed = _govern(_management(hypothesis="EBITDA margin was 30%."),
                       evidence_texts={"ev-1": "The company remained profitable."})
    obs = governed.management_assessments[0]
    assert obs.disposition.value in {"rejected", "unverified"}
    assert obs.disposition.value != "supported"


def test_absent_numeric_value_is_unsupported_not_contradicted():
    # The cited evidence contains NO number, so a claimed figure is UNSUPPORTED
    # (absence of support), NOT contradicted. It is downgraded to 'unverified',
    # never 'rejected' — absence is not conflict.
    governed = _govern(_management(hypothesis="Revenue grew 77% in FY2024."),
                       evidence_texts={"ev-1": "The CFO reduced net leverage as planned."})
    obs = governed.management_assessments[0]
    assert obs.disposition.value == "unverified"
    assert obs.disposition.value != "rejected"
    assert any("unsupported" in r.casefold() for r in obs.validation_reasons)


def test_conflicting_numeric_value_is_contradicted_and_rejected():
    # The cited evidence asserts a DIFFERENT figure for the same class, so the
    # claim is CONTRADICTED -> rejected (distinct from the unsupported case).
    governed = _govern(_management(hypothesis="Revenue grew 77% in FY2024."),
                       evidence_texts={"ev-1": "Revenue grew 12% in FY2024."})
    obs = governed.management_assessments[0]
    assert obs.disposition.value == "rejected"
    assert any("contradicted" in r.casefold() for r in obs.validation_reasons)


def test_neutral_finding_is_distinct_from_insufficient_evidence():
    # A genuinely assessed neutral finding is 'assessed'; an empty assessment is
    # 'insufficient_evidence'. The two are reported in separate states.
    assessed = _govern(_management(credit_direction="neutral"))
    output_assessed = assemble_m3_output(
        structure=_unavailable_structure(), governed_m3=assessed.model_dump(mode="json"))
    assert output_assessed["section_b_management_commentary"][
        "assessment_status"] == "assessed"

    empty = _govern()  # no management or governance findings
    output_empty = assemble_m3_output(
        structure=_unavailable_structure(), governed_m3=empty.model_dump(mode="json"))
    assert output_empty["section_b_management_commentary"][
        "assessment_status"] == "insufficient_evidence"


def test_correct_value_with_unsupported_causal_attribution_is_not_accepted():
    # The reported fact (tenure) is faithful, but the credit inference (reduced
    # key-person risk) is unproven background -> qualified, never 'supported'.
    governed = _govern(_management(
        evidence_basis="credentials_or_tenure", attribution="company_reported",
        hypothesis="The CEO has long tenure, which has reduced key-person risk.",
        credit_mechanism="Long reported tenure is treated as lower leadership risk."),
        evidence_texts={"ev-1": "The CEO has served the company for 12 years."})
    obs = governed.management_assessments[0]
    assert obs.disposition.value != "supported"
    assert obs.disposition.value in {"qualified", "unverified"}


def test_unsupported_execution_claim_intention_is_not_outcome():
    # An announced intention stated as an achieved outcome, with weak basis.
    governed = _govern(_management(
        evidence_basis="reported_experience", attribution="company_reported",
        hypothesis="Management announced a cost programme that has improved margins.",
        credit_mechanism="The announced plan is treated as a realised margin gain."),
        evidence_texts={"ev-1": "Management announced a cost-reduction programme."})
    assert governed.management_assessments[0].disposition.value != "supported"


def test_unsupported_or_fabricated_citation_is_downgraded():
    governed = _govern(_management(supporting_evidence_ids=["ev-unknown"]),
                       evidence_texts={"ev-1": "irrelevant"})
    obs = governed.management_assessments[0]
    assert obs.disposition.value == "unverified"
    assert any("evidence_integrity" in i.rule_id for i in governed.validation_issues)


def test_credible_adverse_statement_stays_credit_negative():
    """FEAT-001 item 9: a credible explanation of an adverse development keeps
    the negative credit direction; credibility and direction are separate."""
    governed = _govern(_management(
        credit_direction="negative",
        hypothesis="Management credibly explains that staffing shortages raised labour costs.",
        credit_mechanism="Higher labour costs compress margins and weaken debt service."),
        evidence_texts={"ev-1": "Staffing shortages increased our labour costs."})
    obs = governed.management_assessments[0]
    # Credibility (disposition) is preserved supported/qualified ...
    assert obs.disposition.value in {"supported", "qualified"}
    # ... while the credit direction stays negative and is NOT laundered.
    assert obs.credit_direction.value == "negative"
    assert obs.credibility_vs_direction_note
    assert any(i.rule_id == "m3.credibility_vs_direction"
               for i in governed.validation_issues)


def test_direction_verification_and_availability_are_separate_fields():
    governed = _govern(_management(credit_direction="negative"),
                       evidence_texts={"ev-1": "The CFO reduced net leverage as planned."})
    obs = governed.management_assessments[0].model_dump(mode="json")
    # Three independent fields; none is derived from another.
    assert {"disposition", "credit_direction", "admissibility"} <= set(obs)
    assert obs["credit_direction"] == "negative"


def test_no_equal_positive_negative_count_is_forced():
    governed = _govern(
        _management(observation_id="MO1", credit_direction="negative"),
        _management(observation_id="MO2", management_id="mgmt-2",
                    credit_direction="negative"),
        evidence_texts={"ev-1": "The CFO reduced net leverage as planned."})
    directions = [m.credit_direction.value for m in governed.management_assessments]
    assert directions == ["negative", "negative"]  # not rebalanced to one each


# -- Validator 7 outcomes -----------------------------------------------------


def test_validator_outcomes_accepted_qualified_rejected_human_review():
    governed = _govern(
        _management(observation_id="A", management_id="m-a"),  # accepted/supported
        _management(observation_id="B", management_id="m-b",
                    evidence_basis="credentials_or_tenure",
                    hypothesis="The CEO is well qualified.",
                    credit_mechanism="Reported qualifications over a long career."),  # qualified
        _management(observation_id="C", management_id="m-c",
                    hypothesis="Revenue grew 99% in FY2024."),  # rejected (contradicted)
        _management(observation_id="D", management_id="m-d",
                    supporting_evidence_ids=["ev-unknown"]),  # unverified / invalid ref
        # The cited evidence asserts a DIFFERENT value for C on a COMPARABLE basis
        # (same metric 'revenue growth', same FY2024 period: 12% vs the claimed
        # 99%), so C is a genuine contradiction -> rejected; A/B/D unchanged.
        evidence_texts={"ev-1": "Revenue grew 12% in FY2024; the CFO reduced net "
                                "leverage as planned."})
    by_id = {m.observation_id: m.disposition.value
             for m in governed.management_assessments}
    assert by_id["A"] == "supported"
    assert by_id["B"] == "qualified"
    assert by_id["C"] == "rejected"
    assert by_id["D"] == "unverified"
    # The analytical record marks the authoritative conclusion for human review.
    analysis = _analysis(governed)
    assert analysis["consolidated_conclusion"]["disposition"] == "human_review_required"
    assert analysis["requires_human_review"] is True
    assert analysis["forced_outcome"] is False


def test_rejected_material_finding_excluded_from_conclusion_but_kept_in_audit():
    """FEAT-001 item 7: rejected material findings never inform the authoritative
    conclusion, but are fully retained under evidence_verification."""
    governed = _govern(
        _management(observation_id="GOOD", management_id="m-good",
                    materiality="high", credit_direction="neutral"),
        _management(observation_id="BAD", management_id="m-bad", materiality="high",
                    hypothesis="Revenue grew 77% in FY2024."),  # rejected (contradicted)
        # The cited evidence asserts a conflicting figure (12%), so BAD is a
        # genuine contradiction rather than mere absence of support.
        evidence_texts={"ev-1": "The CFO reduced net leverage; revenue grew 12% "
                                "in FY2024."})
    bad = next(m for m in governed.management_assessments
               if m.observation_id == "BAD")
    assert bad.disposition.value == "rejected"
    analysis = _analysis(governed)
    admitted = analysis["consolidated_conclusion"]["admitted_management_signal_ids"]
    assert "BAD" not in admitted
    assert "GOOD" in admitted
    # Retained in the audit trail.
    assert "BAD" in analysis["evidence_verification"]["excluded_from_conclusion_ids"]
    assert any("deterministic_entailment" in i["rule_id"]
               for i in analysis["evidence_verification"]["validation_issues"])


def test_invalid_evidence_refs_are_recorded_and_downgraded():
    governed = _govern(
        _management(contradicting_evidence_ids=["ev-unknown"]),
        evidence_texts={"ev-1": "The CFO reduced net leverage as planned."})
    obs = governed.management_assessments[0]
    assert obs.disposition.value == "unverified"
    assert any("contradictory_evidence_integrity" in i.rule_id
               for i in governed.validation_issues)


# -- completion-status consistency (FEAT-001 item 6) --------------------------


def test_completion_status_consistency_incomplete_partial_complete():
    governed = _govern(_management())
    governed_json = governed.model_dump(mode="json")

    # A unavailable + B unavailable -> incomplete.
    both_absent = assemble_m3_output(
        structure=_unavailable_structure(), governed_m3=None)
    assert both_absent["completion"]["status"] == "incomplete"

    # A unavailable + B assessed -> partial.
    partial = assemble_m3_output(
        structure=_unavailable_structure(), governed_m3=governed_json)
    assert partial["completion"]["status"] == "partial"

    # A available + B assessed -> complete.
    complete = assemble_m3_output(
        structure=_available_structure(), governed_m3=governed_json)
    assert complete["completion"]["status"] == "complete"

    # A available + B unavailable -> incomplete (never silently complete).
    a_only = assemble_m3_output(
        structure=_available_structure(), governed_m3=None)
    assert a_only["completion"]["status"] == "incomplete"


# -- helpers ------------------------------------------------------------------


def _analysis(governed) -> dict:
    governed_json = governed.model_dump(mode="json")
    structure = _unavailable_structure()
    output = assemble_m3_output(structure=structure, governed_m3=governed_json)
    return build_m3_deliverables(structure, governed_json, output)["m3_analysis"]


def _unavailable_structure() -> CorporateStructureArtifact:
    return CorporateStructureArtifact(status="unavailable", message=NO_JSON_MESSAGE)


def _available_structure() -> CorporateStructureArtifact:
    """A minimal available structure to exercise completion = complete.

    Only the ``available`` status drives the completion gate; node/link content
    is irrelevant here, so the render-ready collections stay empty.
    """
    return CorporateStructureArtifact(
        status="available", is_simulated=True,
        refined_structure={"schema_version": "2.0", "artifact_type": "x",
                           "companies": {}})
