"""Credit-hypothesis & evidence-governance schema tests (Business Overview M1).

Covers the data foundation added in KIRO Task 1: the six :class:`CreditDimension`
dimensions, the five-category :class:`ClaimClassification`, the four
:class:`HypothesisVerificationStatus` states, credit direction, presentation
materiality, the :class:`CreditHypothesis` validation rules, serialisation,
generated JSON Schema, and backward compatibility with the existing
``ClaimKind`` / ``AnalyticalClaim`` / ``AnalysisResponse`` contracts.

These are schema-level tests only -- no hypothesis generation, no pipeline, no
LLM calls. Examples use general corporate borrowers, not exclusively HCA.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.llm import (
    ANALYSIS_JSON_SCHEMA,
    CREDIT_HYPOTHESIS_JSON_SCHEMA,
    AnalysisResponse,
    AnalyticalClaim,
    ClaimClassification,
    ClaimKind,
    CreditDimension,
    CreditDirection,
    CreditHypothesis,
    CreditHypothesisSet,
    HypothesisVerificationStatus,
    Materiality,
    PresentationMateriality,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hypothesis(**overrides) -> CreditHypothesis:
    """A minimal VALID hypothesis; override any field for a specific case."""
    base = dict(
        hypothesis_id="h1",
        dimension=CreditDimension.REVENUE_QUALITY_DEMAND_DEFENSIVENESS,
        business_characteristic="Share of revenue under multi-year contracts.",
        statement="Contracted revenue reduces cash-flow volatility through a downturn.",
        credit_direction=CreditDirection.POSITIVE,
        economic_reasoning="Multi-year contracts dampen demand cyclicality.",
        potential_credit_impact="More stable debt-service capacity in a downturn.",
        classification=ClaimClassification.INFERENCE,
    )
    base.update(overrides)
    return CreditHypothesis(**base)


# ---------------------------------------------------------------------------
# Enum coverage
# ---------------------------------------------------------------------------


def test_all_six_credit_dimensions_present():
    values = {d.value for d in CreditDimension}
    assert values == {
        "revenue_quality_demand_defensiveness",
        "customer_stickiness_concentration",
        "pricing_power_revenue_realisation",
        "operating_model_cost_resilience",
        "competitive_durability",
        "financial_stewardship_capital_commitments",
    }
    assert len(list(CreditDimension)) == 6


def test_all_five_claim_classifications_present():
    values = {c.value for c in ClaimClassification}
    assert values == {
        "reported_fact",
        "calculated_fact",
        "management_claim",
        "inference",
        "assumption",
    }
    assert len(list(ClaimClassification)) == 5


def test_all_four_verification_statuses_present():
    values = {s.value for s in HypothesisVerificationStatus}
    assert values == {"supported", "partially_supported", "rejected", "unverified"}


def test_credit_direction_and_presentation_materiality_values():
    assert {d.value for d in CreditDirection} == {"positive", "negative", "neutral"}
    assert {m.value for m in PresentationMateriality} == {
        "display",
        "on_demand",
        "suppress",
    }


# ---------------------------------------------------------------------------
# Defaults: generation never verifies
# ---------------------------------------------------------------------------


def test_new_hypothesis_defaults_to_unverified():
    """A freshly built hypothesis is UNVERIFIED by default (generation != verification)."""
    h = _hypothesis()
    assert h.verification_status is HypothesisVerificationStatus.UNVERIFIED
    assert h.presentation_materiality is PresentationMateriality.DISPLAY
    assert h.severity is Materiality.MEDIUM
    assert h.human_review_required is False


def test_positive_hypothesis_can_be_unverified():
    h = _hypothesis(
        credit_direction=CreditDirection.POSITIVE,
        verification_status=HypothesisVerificationStatus.UNVERIFIED,
    )
    assert h.credit_direction is CreditDirection.POSITIVE
    assert h.verification_status is HypothesisVerificationStatus.UNVERIFIED


def test_negative_hypothesis_can_be_supported():
    """Direction and verification are independent axes."""
    h = _hypothesis(
        hypothesis_id="h_neg",
        dimension=CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION,
        credit_direction=CreditDirection.NEGATIVE,
        verification_status=HypothesisVerificationStatus.SUPPORTED,
        supporting_evidence_ids=["f_top_customer_share"],
    )
    assert h.credit_direction is CreditDirection.NEGATIVE
    assert h.verification_status is HypothesisVerificationStatus.SUPPORTED


# ---------------------------------------------------------------------------
# Evidence governance invariants
# ---------------------------------------------------------------------------


def test_supported_without_supporting_evidence_is_rejected():
    with pytest.raises(ValidationError):
        _hypothesis(
            verification_status=HypothesisVerificationStatus.SUPPORTED,
            supporting_evidence_ids=[],
        )


def test_partially_supported_without_supporting_evidence_is_rejected():
    with pytest.raises(ValidationError):
        _hypothesis(
            verification_status=HypothesisVerificationStatus.PARTIALLY_SUPPORTED,
            supporting_evidence_ids=[],
        )


def test_rejected_hypothesis_needs_no_supporting_evidence():
    """A rejected hypothesis may legitimately cite only contradictory evidence."""
    h = _hypothesis(
        verification_status=HypothesisVerificationStatus.REJECTED,
        supporting_evidence_ids=[],
        contradictory_evidence_ids=["f_demand_collapse"],
    )
    assert h.verification_status is HypothesisVerificationStatus.REJECTED
    assert h.contradictory_evidence_ids == ["f_demand_collapse"]


def test_reported_fact_requires_source_evidence():
    with pytest.raises(ValidationError):
        _hypothesis(
            classification=ClaimClassification.REPORTED_FACT,
            supporting_evidence_ids=[],
        )


def test_reported_fact_with_source_is_valid():
    h = _hypothesis(
        classification=ClaimClassification.REPORTED_FACT,
        supporting_evidence_ids=["doc_10k_p42"],
    )
    assert h.classification is ClaimClassification.REPORTED_FACT


def test_calculated_fact_requires_inputs_and_calculation_refs():
    # Missing calculation refs.
    with pytest.raises(ValidationError):
        _hypothesis(
            classification=ClaimClassification.CALCULATED_FACT,
            supporting_evidence_ids=["f_revenue", "f_revenue_prior"],
            calculation_refs=[],
        )
    # Missing input evidence.
    with pytest.raises(ValidationError):
        _hypothesis(
            classification=ClaimClassification.CALCULATED_FACT,
            supporting_evidence_ids=[],
            calculation_refs=["metric:revenue_growth"],
        )


def test_calculated_fact_with_inputs_and_refs_is_valid():
    h = _hypothesis(
        classification=ClaimClassification.CALCULATED_FACT,
        supporting_evidence_ids=["f_revenue", "f_revenue_prior"],
        calculation_refs=["metric:revenue_growth@v3"],
    )
    assert h.calculation_refs == ["metric:revenue_growth@v3"]


def test_assumption_cannot_be_supported():
    """An unestablished premise is never auto-promoted to a verified finding."""
    with pytest.raises(ValidationError):
        _hypothesis(
            classification=ClaimClassification.ASSUMPTION,
            verification_status=HypothesisVerificationStatus.SUPPORTED,
            supporting_evidence_ids=["f_something"],
        )


def test_assumption_may_be_unverified_or_rejected():
    h_unverified = _hypothesis(
        classification=ClaimClassification.ASSUMPTION,
        verification_status=HypothesisVerificationStatus.UNVERIFIED,
    )
    h_rejected = _hypothesis(
        classification=ClaimClassification.ASSUMPTION,
        verification_status=HypothesisVerificationStatus.REJECTED,
    )
    assert h_unverified.verification_status is HypothesisVerificationStatus.UNVERIFIED
    assert h_rejected.verification_status is HypothesisVerificationStatus.REJECTED


def test_management_claim_remains_its_own_classification():
    """A management claim is attributable to management, not an established fact."""
    h = _hypothesis(
        classification=ClaimClassification.MANAGEMENT_CLAIM,
        supporting_evidence_ids=["doc_mdna_p3"],
    )
    assert h.classification is ClaimClassification.MANAGEMENT_CLAIM
    # It is NOT reclassified to a reported/calculated fact by the schema.
    assert h.classification not in {
        ClaimClassification.REPORTED_FACT,
        ClaimClassification.CALCULATED_FACT,
    }


# ---------------------------------------------------------------------------
# Missing information and contradictory evidence preservation
# ---------------------------------------------------------------------------


def test_missing_information_stays_explicit_and_is_not_zero():
    h = _hypothesis(
        missing_information=["Customer concentration by named counterparty"],
        additional_evidence_required=["Signed master-services agreements"],
    )
    # Missing info is retained as explicit text, never coerced to a numeric 0.
    assert h.missing_information == ["Customer concentration by named counterparty"]
    assert 0 not in h.missing_information
    assert h.additional_evidence_required == ["Signed master-services agreements"]


def test_contradictory_evidence_preserved_separately():
    h = _hypothesis(
        verification_status=HypothesisVerificationStatus.PARTIALLY_SUPPORTED,
        supporting_evidence_ids=["f_recurring_rev"],
        contradictory_evidence_ids=["f_churned_flagship_customer"],
    )
    # Supporting and contradictory evidence are kept in distinct lists; neither
    # overwrites or merges the other.
    assert h.supporting_evidence_ids == ["f_recurring_rev"]
    assert h.contradictory_evidence_ids == ["f_churned_flagship_customer"]
    assert set(h.supporting_evidence_ids).isdisjoint(h.contradictory_evidence_ids)


# ---------------------------------------------------------------------------
# Confidence / presentation separation
# ---------------------------------------------------------------------------


def test_interpretation_confidence_bounds():
    assert _hypothesis(interpretation_confidence=0.0).interpretation_confidence == 0.0
    assert _hypothesis(interpretation_confidence=1.0).interpretation_confidence == 1.0
    with pytest.raises(ValidationError):
        _hypothesis(interpretation_confidence=1.5)
    with pytest.raises(ValidationError):
        _hypothesis(interpretation_confidence=-0.1)


def test_presentation_materiality_distinct_from_severity():
    h = _hypothesis(
        presentation_materiality=PresentationMateriality.SUPPRESS,
        severity=Materiality.HIGH,
    )
    # A high-severity finding can still be a SUPPRESS presentation decision; the
    # two axes do not collapse into one another.
    assert h.presentation_materiality is PresentationMateriality.SUPPRESS
    assert h.severity is Materiality.HIGH


# ---------------------------------------------------------------------------
# Structure: reasoning is not a single blob
# ---------------------------------------------------------------------------


def test_reasoning_is_structured_across_typed_fields():
    h = _hypothesis(
        offsetting_characteristics=["Diversified end-markets", "Long order backlog"],
    )
    for field in (
        "economic_reasoning",
        "potential_credit_impact",
        "offsetting_characteristics",
    ):
        assert field in CreditHypothesis.model_fields
    assert h.offsetting_characteristics == [
        "Diversified end-markets",
        "Long order backlog",
    ]


def test_extra_fields_are_forbidden():
    with pytest.raises(ValidationError):
        CreditHypothesis(
            hypothesis_id="h1",
            dimension=CreditDimension.COMPETITIVE_DURABILITY,
            business_characteristic="x",
            statement="y",
            credit_direction=CreditDirection.NEUTRAL,
            economic_reasoning="z",
            potential_credit_impact="w",
            classification=ClaimClassification.INFERENCE,
            module_score=7,  # not a field; must be rejected
        )


# ---------------------------------------------------------------------------
# Serialisation / deserialisation + JSON Schema
# ---------------------------------------------------------------------------


def test_round_trip_serialisation():
    h = _hypothesis(
        verification_status=HypothesisVerificationStatus.SUPPORTED,
        supporting_evidence_ids=["f1"],
        contradictory_evidence_ids=["f2"],
        interpretation_confidence=0.6,
    )
    dumped = h.model_dump(mode="json")
    restored = CreditHypothesis.model_validate(dumped)
    assert restored == h
    # Enum values serialise to their string forms.
    assert dumped["dimension"] == "revenue_quality_demand_defensiveness"
    assert dumped["verification_status"] == "supported"
    assert dumped["credit_direction"] == "positive"


def test_hypothesis_set_round_trip_and_dimension_filter():
    hs = CreditHypothesisSet(
        hypotheses=[
            _hypothesis(hypothesis_id="a"),
            _hypothesis(
                hypothesis_id="b",
                dimension=CreditDimension.PRICING_POWER_REVENUE_REALISATION,
            ),
        ]
    )
    restored = CreditHypothesisSet.model_validate(hs.model_dump(mode="json"))
    assert restored == hs
    revenue_quality = hs.for_dimension(
        CreditDimension.REVENUE_QUALITY_DEMAND_DEFENSIVENESS
    )
    assert [h.hypothesis_id for h in revenue_quality] == ["a"]


def test_generated_json_schema_is_well_formed():
    schema = CREDIT_HYPOTHESIS_JSON_SCHEMA
    assert schema["type"] == "object"
    assert "hypotheses" in schema["properties"]
    # The six dimensions appear as an enum somewhere in the generated $defs.
    defs = schema.get("$defs", {})
    dim_enum = defs.get("CreditDimension", {}).get("enum", [])
    assert "competitive_durability" in dim_enum


# ---------------------------------------------------------------------------
# Backward compatibility: existing contracts untouched
# ---------------------------------------------------------------------------


def test_claim_kind_unchanged_two_values():
    """The legacy ClaimKind still has exactly fact/interpretation (not extended)."""
    assert {k.value for k in ClaimKind} == {"fact", "interpretation"}


def test_existing_analytical_claim_still_validates():
    claim = AnalyticalClaim(
        claim_id="c1",
        text="Operates a scheduled passenger airline.",
        kind=ClaimKind.INTERPRETATION,
        evidence_ids=["f1"],
    )
    assert claim.kind is ClaimKind.INTERPRETATION
    # The new classification is NOT required on AnalyticalClaim.
    assert "classification" not in AnalyticalClaim.model_fields


def test_existing_analysis_response_schema_unchanged():
    """Adding Module 1 models must not alter the AnalysisResponse JSON Schema."""
    regenerated = AnalysisResponse.model_json_schema()
    assert regenerated == ANALYSIS_JSON_SCHEMA
    # Sanity: the six analysis sections are still the same shape.
    props = regenerated["properties"]
    for section in (
        "business_overview",
        "repayment_analysis",
        "key_risks",
        "mitigants",
        "data_limitations",
        "questions_for_human",
    ):
        assert section in props


def test_factual_analytical_claim_without_evidence_still_rejected():
    """Pre-existing Req 12.6 invariant is preserved."""
    with pytest.raises(ValidationError):
        AnalyticalClaim(claim_id="x", text="debt is 100", kind="fact", evidence_ids=[])
