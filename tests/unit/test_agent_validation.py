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
    v = DeterministicValidator(owned_parameters={"net_leverage"})
    parsed = {"parameters": [{"parameter_id": "customer_hhi"}]}
    outcome = v.validate(parsed)
    assert any(i.check is ValidationCheck.PARAMETER_OWNERSHIP for i in outcome.issues)


def test_owned_parameter_accepted():
    v = DeterministicValidator(owned_parameters={"net_leverage"})
    parsed = {"parameters": [{"parameter_id": "net_leverage"}]}
    assert v.validate(parsed).valid


def test_invented_quoted_number_rejected():
    # Orchestrator quotes a number for a parameter that has no validated value.
    v = DeterministicValidator(known_parameters={"net_leverage": 3.07})
    parsed = {"overall_assessment": {"claim_id": "a", "kind": "assessment",
                                     "quoted_values": {"interest_coverage": 5.0}}}
    outcome = v.validate(parsed)
    assert any(i.check is ValidationCheck.NUMERIC_CORRESPONDENCE for i in outcome.issues)


def test_mismatched_quoted_number_rejected():
    v = DeterministicValidator(known_parameters={"net_leverage": 3.07})
    parsed = {"overall_assessment": {"claim_id": "a", "kind": "assessment",
                                     "quoted_values": {"net_leverage": 2.6}}}
    outcome = v.validate(parsed)
    assert any(i.check is ValidationCheck.NUMERIC_CORRESPONDENCE for i in outcome.issues)


def test_exact_quoted_number_accepted():
    v = DeterministicValidator(known_parameters={"net_leverage": 3.07})
    parsed = {"overall_assessment": {"claim_id": "a", "kind": "assessment",
                                     "quoted_values": {"net_leverage": 3.07}}}
    assert v.validate(parsed).valid


def test_semantic_support_is_advisory_not_a_gate():
    v = DeterministicValidator.for_packet(_packet(["E1"]))
    parsed = {"material_risks": [{"claim_id": "r1", "category": "risk",
                                  "evidence_ids": ["E1"]}]}
    outcome = v.validate(parsed)
    assert outcome.valid  # bucket B does not fail storage
    assert outcome.semantic_notes  # but it is flagged for semantic review


def test_clean_output_passes():
    v = DeterministicValidator.for_packet(
        _packet(["E1", "E2"]), owned_parameters={"net_leverage"},
        known_parameters={"net_leverage": 3.07},
    )
    parsed = {
        "parameters": [{"parameter_id": "net_leverage"}],
        "overall_assessment": {"claim_id": "a", "kind": "assessment",
                               "evidence_ids": ["E1"],
                               "quoted_values": {"net_leverage": 3.07}},
    }
    assert v.validate(parsed).valid
