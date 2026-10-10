"""HypothesisGenerationService tests (Business Overview Module 1, Task 3).

Exercises the real LLMClient + PromptRegistry + FakeLLMBackend path and the
service's layered validation gate (client JSON Schema -> Pydantic governance ->
service generation governance). All offline; no live provider.

Scope honesty: FakeLLMBackend returns scripted JSON, so these tests prove the
VALIDATION/REJECTION plumbing and provenance -- not that a live model reasons
well. Reference validation here is membership-in-allow-list only; it is NOT
semantic verification that evidence supports a hypothesis.

Fixtures use fictional companies from different industries (a logistics carrier
and a consumer-hardware maker); no HCA-specific details.
"""

from __future__ import annotations

import pytest

from app.models.orm import ModelRun
from app.prompts.registry import PromptRegistry
from app.schemas.llm import CreditDimension, HypothesisVerificationStatus
from app.services.analysis.hypothesis_generation import (
    HYPOTHESIS_GENERATION_REJECTED_EVENT,
    HypothesisGenerationInputs,
    HypothesisGenerationRejectedError,
    HypothesisGenerationService,
)
from app.services.audit.log import AuditLog
from app.services.llm.client import FakeLLMBackend, LLMClient


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _service(db_session, case_id="C1"):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    backend = FakeLLMBackend()
    audit = AuditLog(db_session)
    client = LLMClient(backend, prompts=registry, session=db_session, audit=audit)
    service = HypothesisGenerationService(client, audit=audit, case_id=case_id)
    return service, backend, audit


# Allow-lists the fixtures draw their references from.
EVIDENCE_IDS = [
    "doc_10k_p22_fleet",
    "doc_10k_p24_fuel",
    "doc_mdna_p9_hedging",
    "metric_fuel_cost_ratio",
]
CALC_REFS = ["metric:fuel_cost_ratio@v2"]


def _inputs(**overrides) -> HypothesisGenerationInputs:
    base = dict(
        evidence_ids=list(EVIDENCE_IDS),
        calculation_refs=list(CALC_REFS),
        canonical_facts=[{"fact_id": e} for e in EVIDENCE_IDS],
    )
    base.update(overrides)
    return HypothesisGenerationInputs(**base)


def _hypothesis(**overrides) -> dict:
    """A valid, UNVERIFIED hypothesis citing only allow-listed references."""
    base = {
        "hypothesis_id": "h1",
        "dimension": CreditDimension.OPERATING_MODEL_COST_RESILIENCE.value,
        "business_characteristic": "Fuel is a large share of operating cost.",
        "statement": "Fuel-price spikes could compress operating margins.",
        "credit_direction": "negative",
        "economic_reasoning": "Fuel inflation raises unit cost faster than fares adjust.",
        "potential_credit_impact": "CFO volatility reduces debt-service headroom.",
        "offsetting_characteristics": ["Disclosed rolling fuel-hedging programme."],
        "supporting_evidence_ids": ["doc_10k_p24_fuel"],
        "contradictory_evidence_ids": ["doc_mdna_p9_hedging"],
        "additional_evidence_required": ["Hedge coverage ratio by quarter"],
        "missing_information": ["Unhedged volume for next fiscal year"],
        "calculation_refs": ["metric:fuel_cost_ratio@v2"],
        "classification": "inference",
        "verification_status": "unverified",
        "interpretation_confidence": 0.5,
        "presentation_materiality": "display",
        "severity": "high",
    }
    base.update(overrides)
    return base


def _set(*hypotheses: dict) -> dict:
    return {"hypotheses": list(hypotheses)}


# ---------------------------------------------------------------------------
# 1. Success
# ---------------------------------------------------------------------------


def test_valid_generation_is_accepted(db_session):
    service, backend, _ = _service(db_session)
    backend.register("analyze", _set(_hypothesis()), key="ok")

    result = service.generate(_inputs(), case_version=1, key="ok")

    assert len(result.hypotheses.hypotheses) == 1
    h = result.hypotheses.hypotheses[0]
    assert h.verification_status is HypothesisVerificationStatus.UNVERIFIED
    assert result.run.is_valid


# ---------------------------------------------------------------------------
# 2-3. Malformed / structurally invalid JSON (client JSON-Schema gate)
# ---------------------------------------------------------------------------


def test_malformed_json_is_rejected(db_session):
    service, backend, _ = _service(db_session)
    backend.register("analyze", "this is not json", key="bad")
    with pytest.raises(HypothesisGenerationRejectedError) as excinfo:
        service.generate(_inputs(), key="bad")
    assert excinfo.value.stage == "json_schema"


def test_structurally_invalid_json_is_rejected(db_session):
    """Missing a required field -> rejected by the client's JSON Schema."""
    service, backend, _ = _service(db_session)
    bad = _hypothesis()
    del bad["dimension"]  # required
    backend.register("analyze", _set(bad), key="bad")
    with pytest.raises(HypothesisGenerationRejectedError) as excinfo:
        service.generate(_inputs(), key="bad")
    assert excinfo.value.stage == "json_schema"


# ---------------------------------------------------------------------------
# 4. Cross-field governance that JSON Schema alone misses (Pydantic layer)
# ---------------------------------------------------------------------------


def test_cross_field_governance_violation_is_rejected(db_session):
    """calculated_fact without calculation_refs: structurally fine, governance-invalid."""
    service, backend, _ = _service(db_session)
    bad = _hypothesis(
        classification="calculated_fact",
        calculation_refs=[],  # structurally valid list, but governance requires >=1
    )
    backend.register("analyze", _set(bad), key="xfield")
    with pytest.raises(HypothesisGenerationRejectedError) as excinfo:
        service.generate(_inputs(), key="xfield")
    assert excinfo.value.stage == "pydantic_governance"
    # Provenance still available on the rejection.
    assert excinfo.value.run is not None
    assert excinfo.value.run.prompt_id == "hypothesis_generation_v1.0"


# ---------------------------------------------------------------------------
# 5. Self-verified hypotheses rejected (service governance)
# ---------------------------------------------------------------------------


def test_supported_with_evidence_is_rejected_at_generation(db_session):
    """A SUPPORTED hypothesis is rejected even WITH supporting evidence present.

    This passes JSON Schema and Pydantic (supported + evidence is schema-valid),
    so the service layer must catch the generation-stage rule.
    """
    service, backend, audit = _service(db_session)
    bad = _hypothesis(
        verification_status="supported",
        supporting_evidence_ids=["doc_10k_p24_fuel"],
    )
    backend.register("analyze", _set(bad), key="selfver")
    with pytest.raises(HypothesisGenerationRejectedError) as excinfo:
        service.generate(_inputs(), key="selfver")
    assert excinfo.value.stage == "service_governance"
    # A distinct service-level rejection event was recorded.
    types = [e.event_type for e in audit.events_for_case("C1")]
    assert HYPOTHESIS_GENERATION_REJECTED_EVENT in types


def test_partially_supported_and_rejected_are_also_refused(db_session):
    service, backend, _ = _service(db_session)
    for status, key in (("partially_supported", "ps"), ("rejected", "rj")):
        bad = _hypothesis(
            hypothesis_id=f"h_{key}",
            verification_status=status,
            supporting_evidence_ids=["doc_10k_p24_fuel"],
        )
        backend.register("analyze", _set(bad), key=key)
        with pytest.raises(HypothesisGenerationRejectedError) as excinfo:
            service.generate(_inputs(), key=key)
        assert excinfo.value.stage == "service_governance"


# ---------------------------------------------------------------------------
# 6-7. Unknown / fabricated references
# ---------------------------------------------------------------------------


def test_unknown_supporting_evidence_id_is_rejected(db_session):
    service, backend, _ = _service(db_session)
    bad = _hypothesis(supporting_evidence_ids=["doc_fabricated_p1"])
    backend.register("analyze", _set(bad), key="fab")
    with pytest.raises(HypothesisGenerationRejectedError) as excinfo:
        service.generate(_inputs(), key="fab")
    assert excinfo.value.stage == "service_governance"


def test_unknown_contradictory_evidence_id_is_rejected(db_session):
    service, backend, _ = _service(db_session)
    bad = _hypothesis(contradictory_evidence_ids=["doc_not_in_set"])
    backend.register("analyze", _set(bad), key="fabc")
    with pytest.raises(HypothesisGenerationRejectedError) as excinfo:
        service.generate(_inputs(), key="fabc")
    assert excinfo.value.stage == "service_governance"


def test_unknown_calculation_ref_is_rejected(db_session):
    service, backend, _ = _service(db_session)
    bad = _hypothesis(calculation_refs=["metric:made_up@v9"])
    backend.register("analyze", _set(bad), key="fabcalc")
    with pytest.raises(HypothesisGenerationRejectedError) as excinfo:
        service.generate(_inputs(), key="fabcalc")
    assert excinfo.value.stage == "service_governance"


# ---------------------------------------------------------------------------
# 8-9. Contradictory evidence preserved; unverified-with-missing-info accepted
# ---------------------------------------------------------------------------


def test_contradictory_evidence_is_preserved_on_accept(db_session):
    service, backend, _ = _service(db_session)
    backend.register("analyze", _set(_hypothesis()), key="ok")
    result = service.generate(_inputs(), key="ok")
    h = result.hypotheses.hypotheses[0]
    assert h.supporting_evidence_ids == ["doc_10k_p24_fuel"]
    assert h.contradictory_evidence_ids == ["doc_mdna_p9_hedging"]
    assert set(h.supporting_evidence_ids).isdisjoint(h.contradictory_evidence_ids)


def test_unverified_hypothesis_with_no_evidence_but_missing_info_is_accepted(db_session):
    """A genuinely unverified hypothesis may cite no evidence if it names the gaps."""
    service, backend, _ = _service(db_session)
    h = _hypothesis(
        hypothesis_id="h_open",
        classification="assumption",
        supporting_evidence_ids=[],
        contradictory_evidence_ids=[],
        calculation_refs=[],
        additional_evidence_required=["Signed customer contracts"],
        missing_information=["Named-counterparty concentration"],
        verification_status="unverified",
    )
    backend.register("analyze", _set(h), key="open")
    result = service.generate(_inputs(), key="open")
    accepted = result.hypotheses.hypotheses[0]
    assert accepted.verification_status is HypothesisVerificationStatus.UNVERIFIED
    assert accepted.supporting_evidence_ids == []
    assert accepted.missing_information  # the gap is explicit, not zero


# ---------------------------------------------------------------------------
# 10. Duplicate hypothesis IDs
# ---------------------------------------------------------------------------


def test_duplicate_hypothesis_ids_are_rejected(db_session):
    service, backend, _ = _service(db_session)
    dup_a = _hypothesis(hypothesis_id="dup")
    dup_b = _hypothesis(
        hypothesis_id="dup",
        dimension=CreditDimension.PRICING_POWER_REVENUE_REALISATION.value,
        credit_direction="positive",
    )
    backend.register("analyze", _set(dup_a, dup_b), key="dup")
    with pytest.raises(HypothesisGenerationRejectedError) as excinfo:
        service.generate(_inputs(), key="dup")
    assert excinfo.value.stage == "service_governance"


# ---------------------------------------------------------------------------
# 11. Provenance preserved
# ---------------------------------------------------------------------------


def test_model_run_provenance_preserved_on_success(db_session):
    service, backend, _ = _service(db_session)
    backend.register("analyze", _set(_hypothesis()), key="ok")
    result = service.generate(_inputs(), case_version=7, key="ok")

    run = result.run
    assert run.prompt_id == "hypothesis_generation_v1.0"
    row = db_session.get(ModelRun, run.run_id)
    assert row is not None
    assert row.prompt_name == "hypothesis_generation"
    assert row.prompt_version == 1
    assert row.prompt_hash
    assert row.case_version == 7
    assert row.evidence_ids == EVIDENCE_IDS
    assert row.validation_outcome == "valid"


def test_rejection_preserves_run_and_llm_run_event(db_session):
    """Even a service-level rejection keeps the client's run + llm_run event."""
    service, backend, audit = _service(db_session)
    backend.register(
        "analyze", _set(_hypothesis(supporting_evidence_ids=["ghost"])), key="r"
    )
    with pytest.raises(HypothesisGenerationRejectedError) as excinfo:
        service.generate(_inputs(), key="r")

    run = excinfo.value.run
    assert run is not None
    # Client-level run row persists (append-only); llm_run emitted.
    assert db_session.get(ModelRun, run.run_id) is not None
    types = [e.event_type for e in audit.events_for_case("C1")]
    assert "llm_run" in types
    assert HYPOTHESIS_GENERATION_REJECTED_EVENT in types


# ---------------------------------------------------------------------------
# Second industry scenario: multiple valid hypotheses in one set
# ---------------------------------------------------------------------------


def test_second_industry_multi_hypothesis_set_accepted(db_session):
    """Consumer-hardware maker: concentration risk + recurring-revenue positive."""
    service, backend, _ = _service(db_session)
    evidence = ["doc_s1_p40_retailer", "doc_s1_p42_subscription", "metric_nrr"]
    calc = ["metric:nrr@v1"]
    hw_concentration = _hypothesis(
        hypothesis_id="hw-1",
        dimension=CreditDimension.CUSTOMER_STICKINESS_CONCENTRATION.value,
        business_characteristic="One retailer drives most unit sales.",
        statement="Retailer concentration risks a demand cliff on delisting.",
        credit_direction="negative",
        offsetting_characteristics=["Growing direct-to-consumer channel disclosed."],
        supporting_evidence_ids=["doc_s1_p40_retailer"],
        contradictory_evidence_ids=["doc_s1_p42_subscription"],
        calculation_refs=[],
        severity="high",
    )
    hw_recurring = _hypothesis(
        hypothesis_id="hw-2",
        dimension=CreditDimension.REVENUE_QUALITY_DEMAND_DEFENSIVENESS.value,
        business_characteristic="Attached subscription service.",
        statement="Subscription attach adds recurring, higher-visibility revenue.",
        credit_direction="positive",
        classification="calculated_fact",
        supporting_evidence_ids=["metric_nrr"],
        contradictory_evidence_ids=[],
        calculation_refs=["metric:nrr@v1"],
        severity="medium",
    )
    backend.register("analyze", _set(hw_concentration, hw_recurring), key="hw")

    result = service.generate(
        _inputs(
            evidence_ids=evidence,
            calculation_refs=calc,
            canonical_facts=[{"fact_id": e} for e in evidence],
        ),
        key="hw",
    )
    dirs = {h.credit_direction.value for h in result.hypotheses.hypotheses}
    assert {"positive", "negative"} <= dirs
    assert all(
        h.verification_status is HypothesisVerificationStatus.UNVERIFIED
        for h in result.hypotheses.hypotheses
    )
