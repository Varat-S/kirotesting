"""Milestone 8 — deterministic validation layer (Req 8, 3.3, 3.6, 10, 34).

Proves bucket-A deterministic validation rejects: schema-invalid output,
unsupported/unadmitted evidence IDs, uncited factual claims, wrong-entity /
wrong-period references, non-owned parameters, and invented/mismatched quoted
numbers (orchestrator quote-not-calculate). Narrative semantic support is kept
as an advisory bucket B, never a deterministic gate.
"""

from __future__ import annotations

from app.schemas.agentic import EvidencePacket
from app.services.agents.validation import (
    DeterministicValidator,
    ValidationCheck,
)


def _packet(evidence_ids):
    return EvidencePacket(
        case_id="C1",
        snapshot_version=1,
        router_version="router-1",
        analysis_run_id="AR1",
        agent_id="business_orchestrator",
        evidence_ids=list(evidence_ids),
        packet_hash="pkt-1",
    )


def test_schema_invalid_output_rejected():
    v = DeterministicValidator()
    schema = {"type": "object", "required": ["facts"],
              "properties": {"facts": {"type": "array"}}}
    outcome = v.validate({"wrong": 1}, schema=schema)
    assert not outcome.valid
    assert outcome.issues[0].check is ValidationCheck.SCHEMA


def test_none_output_rejected():
    assert not DeterministicValidator().validate(None).valid


def test_unsupported_evidence_id_rejected():
    v = DeterministicValidator.for_packet(_packet(["E1", "E2"]))
    parsed = {"claims": [{"claim_id": "c1", "kind": "fact",
                          "evidence_ids": ["E_GHOST"]}]}
    outcome = v.validate(parsed)
    assert not outcome.valid
    assert any(i.check is ValidationCheck.EVIDENCE_EXISTS for i in outcome.issues)


def test_factual_claim_without_citation_rejected():
    v = DeterministicValidator.for_packet(_packet(["E1"]))
    parsed = {"claims": [{"claim_id": "c1", "kind": "fact", "evidence_ids": []}]}
    outcome = v.validate(parsed)
    assert any(i.check is ValidationCheck.CITATION_VALID for i in outcome.issues)


def test_wrong_entity_rejected():
    v = DeterministicValidator.for_packet(_packet(["E1"]), expected_entity="DAL")
    parsed = {"claims": [{"claim_id": "c1", "kind": "interpretation",
                          "entity_id": "UAL", "evidence_ids": ["E1"]}]}
    outcome = v.validate(parsed)
    assert any(i.check is ValidationCheck.ENTITY_COMPATIBLE for i in outcome.issues)


def test_wrong_period_rejected():
    v = DeterministicValidator.for_packet(_packet(["E1"]), expected_fiscal_year=2025)
    parsed = {"claims": [{"claim_id": "c1", "kind": "interpretation",
                          "fiscal_year": 2019, "evidence_ids": ["E1"]}]}
    outcome = v.validate(parsed)
    assert any(i.check is ValidationCheck.PERIOD_COMPATIBLE for i in outcome.issues)


def test_non_owned_parameter_rejected():
    v = DeterministicValidator(
        admitted_evidence_ids={"E1"},
        owned_parameters={"pricing_power_assessment"},
    )
    parsed = {"parameters": [{"parameter_id": "customer_dependence_assessment",
                              "value_type": "category", "status": "ok",
                              "evidence_ids": ["E1"]}]}
    outcome = v.validate(parsed)
    assert any(i.check is ValidationCheck.PARAMETER_OWNERSHIP for i in outcome.issues)


def test_owned_parameter_accepted():
    v = DeterministicValidator(
        admitted_evidence_ids={"E1"},
        owned_parameters={"pricing_power_assessment"},
    )
    parsed = {"parameters": [{"parameter_id": "pricing_power_assessment",
                              "value": "strong", "value_type": "category",
                              "status": "ok", "evidence_ids": ["E1"]}]}
    assert v.validate(parsed).valid


def test_llm_cannot_emit_deterministic_parameter():
    # Item 1: a deterministic id emitted by an LLM is rejected even if "owned".
    v = DeterministicValidator(
        admitted_evidence_ids={"E1"},
        owned_parameters={"segment_hhi"},
        deterministic_parameter_ids={"segment_hhi"},
    )
    parsed = {"parameters": [{"parameter_id": "segment_hhi", "value": 0.3,
                              "value_type": "index", "method": "llm",
                              "status": "ok", "evidence_ids": ["E1"]}]}
    outcome = v.validate(parsed)
    assert any(i.check is ValidationCheck.PARAMETER_OWNERSHIP for i in outcome.issues)


def test_substantive_parameter_without_evidence_rejected():
    v = DeterministicValidator(owned_parameters={"pricing_power_assessment"})
    parsed = {"parameters": [{"parameter_id": "pricing_power_assessment",
                              "value": "strong", "value_type": "category",
                              "status": "ok", "evidence_ids": []}]}
    outcome = v.validate(parsed)
    assert any(i.check is ValidationCheck.CITATION_VALID for i in outcome.issues)


def test_unavailable_parameter_without_evidence_allowed():
    v = DeterministicValidator(owned_parameters={"pricing_power_assessment"})
    parsed = {"parameters": [{"parameter_id": "pricing_power_assessment",
                              "value": None, "value_type": "category",
                              "status": "unavailable", "evidence_ids": []}]}
    assert v.validate(parsed).valid


def test_invented_quoted_number_rejected():
    # Orchestrator quotes a result id that is not an accepted ParameterResult.
    v = DeterministicValidator(known_parameters={"pr_lev": 3.07})
    parsed = {"overall_assessment": {"claim_id": "a", "kind": "assessment",
                                     "quoted_values": [
                                         {"parameter_result_id": "pr_cov", "value": 5.0}]}}
    outcome = v.validate(parsed)
    assert any(i.check is ValidationCheck.NUMERIC_CORRESPONDENCE for i in outcome.issues)


def test_mismatched_quoted_number_rejected():
    v = DeterministicValidator(known_parameters={"pr_lev": 3.07})
    parsed = {"overall_assessment": {"claim_id": "a", "kind": "assessment",
                                     "quoted_values": [
                                         {"parameter_result_id": "pr_lev", "value": 2.6}]}}
    outcome = v.validate(parsed)
    assert any(i.check is ValidationCheck.NUMERIC_CORRESPONDENCE for i in outcome.issues)


def test_exact_quoted_number_accepted():
    v = DeterministicValidator(known_parameters={"pr_lev": 3.07})
    parsed = {"overall_assessment": {"claim_id": "a", "kind": "assessment",
                                     "quoted_values": [
                                         {"parameter_result_id": "pr_lev", "value": 3.07}]}}
    assert v.validate(parsed).valid


def test_legacy_logical_name_quote_is_rejected():
    # Item 6: a logical-name dict mapping must be rejected (ambiguous after reruns).
    v = DeterministicValidator(known_parameters={"pr_lev": 3.07})
    parsed = {"overall_assessment": {"claim_id": "a", "kind": "assessment",
                                     "quoted_values": {"net_leverage": 3.07}}}
    outcome = v.validate(parsed)
    assert any(i.check is ValidationCheck.NUMERIC_CORRESPONDENCE for i in outcome.issues)


def test_semantic_support_is_advisory_not_a_gate():
    v = DeterministicValidator.for_packet(_packet(["E1"]))
    parsed = {"material_risks": [{"claim_id": "r1", "category": "risk",
                                  "evidence_ids": ["E1"]}]}
    outcome = v.validate(parsed)
    assert outcome.valid  # bucket B does not fail storage
    assert outcome.semantic_notes  # but it is flagged for semantic review


def test_clean_output_passes():
    v = DeterministicValidator.for_packet(
        _packet(["E1", "E2"]), owned_parameters={"pricing_power_assessment"},
    )
    parsed = {
        "parameters": [{"parameter_id": "pricing_power_assessment",
                        "value": "strong", "value_type": "category",
                        "status": "ok", "evidence_ids": ["E1"]}],
    }
    assert v.validate(parsed).valid
