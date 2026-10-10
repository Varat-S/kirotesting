"""Tests for the `hypothesis_generation` prompt (Business Overview Module 1).

Two clearly separated kinds of test live here:

* **Executable validation** -- the prompt registers (versioned + hashed), the
  existing prompts are unaffected, a schema-valid CreditHypothesisSet routed
  through the real ``LLMClient`` + ``FakeLLMBackend`` is accepted, schema-invalid
  output is rejected, and the model-run provenance (prompt id/version/hash) is
  recorded. These exercise the real registry/client/schema code paths.
* **Prompt-wording inspection** -- assertions about the template STRING only
  (that it mentions the six dimensions, offsets, governance, UNVERIFIED). These
  verify intent/wording, NOT model behaviour.

NOTE ON SCOPE: ``FakeLLMBackend`` returns scripted canned JSON. These tests
prove the plumbing (registration, routing, schema validation, provenance) and
that well-formed hypotheses satisfy the governance invariants. They do NOT and
cannot establish that a real LLM would produce good credit reasoning -- that is
out of scope for an offline task and requires a live provider and evaluation.

Scenarios are fictional and drawn from different industries (a precision-parts
manufacturer and a B2B software provider); no HCA/hospital/nursing specifics.
"""

from __future__ import annotations

from app.models.orm import ModelRun
from app.prompts.registry import PromptRegistry
from app.prompts.templates import HYPOTHESIS_GENERATION_TEMPLATE, PROMPT_CATALOGUE
from app.schemas.llm import (
    CREDIT_HYPOTHESIS_JSON_SCHEMA,
    CreditDimension,
    CreditHypothesisSet,
    HypothesisVerificationStatus,
)
from app.services.audit.log import AuditLog
from app.services.llm.client import FakeLLMBackend, LLMClient

PROMPT_NAME = "hypothesis_generation"


# ---------------------------------------------------------------------------
# Fictional, cross-industry scenario fixtures (schema-valid canned outputs)
# ---------------------------------------------------------------------------


def _manufacturer_hypotheses() -> dict:
    """A precision-parts manufacturer: input-cost vulnerability + an offset.

    Demonstrates a negative operating-model hypothesis (specialty-alloy
    dependence) alongside a company-specific offsetting capability (a long-term
    supply agreement), with the offset treated as a management_claim, not a
    demonstrated outcome.
    """
    return {
        "hypotheses": [
            {
                "hypothesis_id": "mfg-1",
                "dimension": CreditDimension.OPERATING_MODEL_COST_RESILIENCE.value,
                "business_characteristic": "Dependence on a single specialty-alloy input.",
                "statement": (
                    "Concentration in one specialty alloy exposes margins to "
                    "shortage and price inflation in that input."
                ),
                "credit_direction": "negative",
                "economic_reasoning": (
                    "A supply shock raises unit costs the firm may not fully "
                    "pass through, compressing operating cash flow available "
                    "for debt service."
                ),
                "potential_credit_impact": "Margin and CFO volatility under input shocks.",
                "offsetting_characteristics": [
                    "Disclosed 5-year fixed-volume supply agreement with two mills."
                ],
                "supporting_evidence_ids": ["doc_10k_p31_inputs"],
                "contradictory_evidence_ids": ["doc_10k_p33_dual_sourcing"],
                "additional_evidence_required": [
                    "Share of COGS represented by the alloy",
                    "Hedging or price-adjustment clauses",
                ],
                "missing_information": ["Spot vs contracted volume split"],
                "calculation_refs": [],
                "classification": "inference",
                "verification_status": "unverified",
                "interpretation_confidence": 0.45,
                "presentation_materiality": "display",
                "severity": "high",
                "human_review_required": True,
            },
            {
                "hypothesis_id": "mfg-2",
                "dimension": CreditDimension.PRICING_POWER_REVENUE_REALISATION.value,
                "business_characteristic": "Long-dated contracts with price-adjustment clauses.",
                "statement": (
                    "Contractual price-adjustment clauses may let the firm pass "
                    "through input inflation."
                ),
                "credit_direction": "positive",
                "economic_reasoning": (
                    "Pass-through clauses transfer input-cost risk to customers, "
                    "stabilising realised margins."
                ),
                "potential_credit_impact": "More stable gross margin through cost cycles.",
                "offsetting_characteristics": [],
                "supporting_evidence_ids": ["doc_mdna_p12_pricing"],
                "contradictory_evidence_ids": [],
                "additional_evidence_required": [
                    "Proportion of revenue under such clauses"
                ],
                "missing_information": ["Clause lag before pass-through takes effect"],
                "calculation_refs": [],
                "classification": "management_claim",
                "verification_status": "unverified",
                "interpretation_confidence": 0.4,
                "presentation_materiality": "display",
                "severity": "medium",
            },
        ]
    }


def _software_hypotheses() -> dict:
    """A B2B software provider: concentration risk + a stickiness offset."""
    return {
        "hypotheses": [
            {
                "hypothesis_id": "sw-1",
                "dimension": CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION.value,
                "business_characteristic": "Top customer is a large share of ARR.",
                "statement": (
                    "Revenue concentration in one customer threatens cash flow "
                    "if that customer churns or renegotiates."
                ),
                "credit_direction": "negative",
                "economic_reasoning": (
                    "Loss of a dominant customer removes a large, near-fixed "
                    "revenue stream, impairing debt-service coverage."
                ),
                "potential_credit_impact": "Step-down in recurring revenue on a single churn.",
                "offsetting_characteristics": [
                    "High disclosed gross retention and deep product integration raising switching costs."
                ],
                "supporting_evidence_ids": ["doc_s1_p58_concentration"],
                "contradictory_evidence_ids": ["doc_s1_p60_retention"],
                "additional_evidence_required": ["Remaining contract term for the top account"],
                "missing_information": ["Named-customer renewal date"],
                "calculation_refs": [],
                "classification": "inference",
                "verification_status": "unverified",
                "interpretation_confidence": 0.5,
                "presentation_materiality": "display",
                "severity": "high",
                "human_review_required": True,
            },
            {
                "hypothesis_id": "sw-2",
                "dimension": CreditDimension.REVENUE_QUALITY_DEMAND_DEFENSIVENESS.value,
                "business_characteristic": "Subscription revenue recognised rateably.",
                "statement": "Recurring subscription revenue improves cash-flow visibility.",
                "credit_direction": "positive",
                "economic_reasoning": (
                    "Rateable, pre-committed revenue reduces period-to-period "
                    "volatility relevant to debt service."
                ),
                "potential_credit_impact": "More predictable CFO supporting repayment.",
                "offsetting_characteristics": [],
                "supporting_evidence_ids": ["metric_arr", "metric_arr_prior"],
                "contradictory_evidence_ids": [],
                "additional_evidence_required": ["Net revenue retention trend"],
                "missing_information": [],
                "calculation_refs": ["metric:arr_growth@v1"],
                "classification": "calculated_fact",
                "verification_status": "unverified",
                "interpretation_confidence": 0.55,
                "presentation_materiality": "display",
                "severity": "medium",
            },
        ]
    }


def _client(db_session, backend=None):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    backend = backend or FakeLLMBackend()
    audit = AuditLog(db_session)
    client = LLMClient(backend, prompts=registry, session=db_session, audit=audit)
    return client, backend, registry


# ===========================================================================
# EXECUTABLE VALIDATION TESTS (real registry / client / schema code paths)
# ===========================================================================


def test_prompt_registers_versioned_and_hashed(db_session):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()

    prompt = registry.latest(PROMPT_NAME)
    assert prompt is not None
    assert prompt.prompt_id == "hypothesis_generation_v1.0"
    assert prompt.version == 1
    assert prompt.content_hash  # stable hash over template + schema
    assert prompt.role == "analyze"
    # The registered schema is the Task-1 credit-hypothesis-set schema.
    assert prompt.response_schema == CREDIT_HYPOTHESIS_JSON_SCHEMA


def test_prompt_in_catalogue_once(db_session):
    names = [e["name"] for e in PROMPT_CATALOGUE]
    assert names.count(PROMPT_NAME) == 1


def test_registration_is_idempotent_and_bumps_on_change(db_session):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    again = registry.register_catalogue()  # identical content -> no new version
    assert registry.latest(PROMPT_NAME).version == 1
    assert all(p.version == 1 for p in again if p.name == PROMPT_NAME)

    bumped = registry.register(
        PROMPT_NAME,
        template=HYPOTHESIS_GENERATION_TEMPLATE + "\nAddendum.",
        response_schema=CREDIT_HYPOTHESIS_JSON_SCHEMA,
        role="analyze",
    )
    assert bumped.version == 2
    # Historical v1 preserved intact.
    assert registry.get(PROMPT_NAME, 1).template == HYPOTHESIS_GENERATION_TEMPLATE


def test_existing_prompts_unaffected(db_session):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    for name, pid in (
        ("qualitative_extraction", "qualitative_extraction_v1.0"),
        ("business_analysis", "business_analysis_v1.0"),
        ("challenge", "challenge_v1.0"),
    ):
        prompt = registry.latest(name)
        assert prompt is not None
        assert prompt.prompt_id == pid
        assert prompt.version == 1  # still v1; not re-versioned by the new prompt


def test_valid_hypothesis_output_is_accepted_and_logged(db_session):
    """A schema-valid set routed through analyze(prompt_name=...) is accepted."""
    client, backend, _ = _client(db_session)
    backend.register("analyze", _manufacturer_hypotheses(), key="mfg")

    result = client.analyze(
        {"canonical_facts": [{"fact_id": "doc_10k_p31_inputs"}]},
        evidence_ids=["doc_10k_p31_inputs"],
        case_id="C_MFG",
        case_version=1,
        prompt_name=PROMPT_NAME,
        key="mfg",
    )

    assert result.is_valid
    assert result.parsed is not None
    parsed = CreditHypothesisSet.model_validate(result.parsed)
    assert len(parsed.hypotheses) == 2

    # Provenance recorded against the hypothesis_generation prompt (not analyze).
    row = db_session.get(ModelRun, result.run_id)
    assert row.prompt_name == PROMPT_NAME
    assert row.prompt_id == "hypothesis_generation_v1.0"
    assert row.prompt_version == 1
    assert row.prompt_hash
    assert row.validation_outcome == "valid"


def test_software_scenario_is_accepted(db_session):
    client, backend, _ = _client(db_session)
    backend.register("analyze", _software_hypotheses(), key="sw")
    result = client.analyze(
        {}, case_id="C_SW", prompt_name=PROMPT_NAME, key="sw"
    )
    assert result.is_valid
    parsed = CreditHypothesisSet.model_validate(result.parsed)
    dims = {h.dimension for h in parsed.hypotheses}
    assert CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION in dims
    assert CreditDimension.REVENUE_QUALITY_DEMAND_DEFENSIVENESS in dims


def test_structurally_invalid_output_is_rejected_by_schema(db_session):
    """A missing required field is caught by JSON-Schema validation in the client."""
    client, backend, _ = _client(db_session)
    backend.register(
        "analyze",
        {
            "hypotheses": [
                {
                    "hypothesis_id": "bad-1",
                    # 'dimension' (required) omitted -> structural schema failure.
                    "business_characteristic": "x",
                    "statement": "y",
                    "credit_direction": "positive",
                    "economic_reasoning": "z",
                    "potential_credit_impact": "w",
                    "classification": "inference",
                }
            ]
        },
        key="bad",
    )
    result = client.analyze({}, case_id="C_BAD", prompt_name=PROMPT_NAME, key="bad")
    assert not result.is_valid
    assert result.validation_outcome == "rejected"
    assert result.parsed is None


def test_unknown_field_output_is_rejected_by_schema(db_session):
    """extra='forbid' emits additionalProperties:false, so a module score is rejected."""
    client, backend, _ = _client(db_session)
    backend.register(
        "analyze",
        {
            "hypotheses": [
                {
                    "hypothesis_id": "score-1",
                    "dimension": CreditDimension.COMPETITIVE_DURABILITY.value,
                    "business_characteristic": "x",
                    "statement": "y",
                    "credit_direction": "neutral",
                    "economic_reasoning": "z",
                    "potential_credit_impact": "w",
                    "classification": "inference",
                    "module_score": 7,  # not in schema -> rejected by the client
                }
            ]
        },
        key="score",
    )
    result = client.analyze({}, case_id="C_SCORE", prompt_name=PROMPT_NAME, key="score")
    assert not result.is_valid


def test_governance_invariant_is_model_level_not_json_schema(db_session):
    """IMPORTANT distinction: 'supported with no evidence' is a cross-field
    governance rule enforced by the Pydantic ``model_validator`` (Task 1), NOT by
    the generated JSON Schema. The client's ``jsonschema`` check passes such a
    payload; the invariant bites only when the typed model is constructed.

    This documents a real limitation: structural schema validation at the client
    boundary does not enforce the evidence-governance invariants. A later task
    should validate generated hypotheses through the typed model (or add the
    cross-field rules to the fixed schema) before they are trusted.
    """
    import pytest
    from pydantic import ValidationError

    client, backend, _ = _client(db_session)
    offending = {
        "hypotheses": [
            {
                "hypothesis_id": "bad-1",
                "dimension": CreditDimension.COMPETITIVE_DURABILITY.value,
                "business_characteristic": "x",
                "statement": "y",
                "credit_direction": "positive",
                "economic_reasoning": "z",
                "potential_credit_impact": "w",
                "classification": "inference",
                "verification_status": "supported",  # but no supporting evidence
                "supporting_evidence_ids": [],
            }
        ]
    }
    backend.register("analyze", offending, key="gov")
    result = client.analyze({}, case_id="C_GOV", prompt_name=PROMPT_NAME, key="gov")
    # Structural JSON-Schema validation at the client boundary PASSES ...
    assert result.is_valid
    # ... but constructing the typed model enforces the governance invariant.
    with pytest.raises(ValidationError):
        CreditHypothesisSet.model_validate(result.parsed)


def test_generated_hypotheses_default_to_unverified(db_session):
    """Every generated hypothesis in both scenarios stays unverified."""
    client, backend, _ = _client(db_session)
    backend.register("analyze", _manufacturer_hypotheses(), key="mfg")
    backend.register("analyze", _software_hypotheses(), key="sw")

    for key in ("mfg", "sw"):
        result = client.analyze({}, case_id="C", prompt_name=PROMPT_NAME, key=key)
        parsed = CreditHypothesisSet.model_validate(result.parsed)
        assert parsed.hypotheses
        for h in parsed.hypotheses:
            assert h.verification_status is HypothesisVerificationStatus.UNVERIFIED


def test_positive_and_negative_directions_independent_of_verification(db_session):
    client, backend, _ = _client(db_session)
    backend.register("analyze", _manufacturer_hypotheses(), key="mfg")
    result = client.analyze({}, case_id="C", prompt_name=PROMPT_NAME, key="mfg")
    parsed = CreditHypothesisSet.model_validate(result.parsed)

    directions = {h.credit_direction.value for h in parsed.hypotheses}
    assert {"positive", "negative"} <= directions
    # Both directions present, yet all remain unverified -> axes are independent.
    assert all(
        h.verification_status is HypothesisVerificationStatus.UNVERIFIED
        for h in parsed.hypotheses
    )


def test_unresolved_hypothesis_can_specify_missing_evidence(db_session):
    client, backend, _ = _client(db_session)
    backend.register("analyze", _manufacturer_hypotheses(), key="mfg")
    result = client.analyze({}, case_id="C", prompt_name=PROMPT_NAME, key="mfg")
    parsed = CreditHypothesisSet.model_validate(result.parsed)

    mfg1 = next(h for h in parsed.hypotheses if h.hypothesis_id == "mfg-1")
    assert mfg1.missing_information  # explicit, not zero
    assert mfg1.additional_evidence_required
    # Contradictory evidence is preserved alongside supporting evidence.
    assert mfg1.contradictory_evidence_ids
    assert set(mfg1.supporting_evidence_ids).isdisjoint(mfg1.contradictory_evidence_ids)


def test_offset_is_recorded_but_not_a_demonstrated_outcome(db_session):
    """An offsetting initiative is captured without being promoted to a fact."""
    client, backend, _ = _client(db_session)
    backend.register("analyze", _manufacturer_hypotheses(), key="mfg")
    result = client.analyze({}, case_id="C", prompt_name=PROMPT_NAME, key="mfg")
    parsed = CreditHypothesisSet.model_validate(result.parsed)

    mfg1 = next(h for h in parsed.hypotheses if h.hypothesis_id == "mfg-1")
    assert mfg1.offsetting_characteristics  # offset discovered for the vulnerability
    # The offset-bearing hypothesis is an inference/management_claim, never a
    # reported/calculated fact, and remains unverified.
    assert mfg1.classification.value in {"inference", "management_claim", "assumption"}
    assert mfg1.verification_status is HypothesisVerificationStatus.UNVERIFIED


# ===========================================================================
# PROMPT-WORDING INSPECTION TESTS (string only; NOT model behaviour)
# ===========================================================================


def test_prompt_text_names_all_six_dimensions():
    text = HYPOTHESIS_GENERATION_TEMPLATE.lower()
    for token in (
        "revenue_quality_demand_defensiveness",
        "customer_stickiness_concentration",
        "pricing_power_revenue_realisation",
        "operating_model_cost_resilience",
        "competitive_durability",
        "financial_stewardship_capital_commitments",
    ):
        assert token in text


def test_prompt_text_requires_unverified_and_forbids_self_verification():
    text = HYPOTHESIS_GENERATION_TEMPLATE.lower()
    assert "unverified" in text
    assert "do not mark your own hypothesis supported" in text


def test_prompt_text_requires_offset_discovery_and_governance():
    text = HYPOTHESIS_GENERATION_TEMPLATE.lower()
    assert "offsetting_characteristics" in text
    assert "never fabricate" in text
    assert "never treat non-disclosure as zero" in text
    # Fact vs management-claim vs inference separation is instructed.
    for token in ("reported_fact", "management_claim", "inference", "assumption"):
        assert token in text


def test_prompt_text_is_industry_general_not_hca_specific():
    text = HYPOTHESIS_GENERATION_TEMPLATE.lower()
    for banned in ("hca", "galen", "hospital", "nursing"):
        assert banned not in text
