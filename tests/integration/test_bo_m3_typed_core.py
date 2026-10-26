"""Actual SDK + production gates, entirely in memory with authored responses.

Mock handlers validate the outgoing schema AND supplied response, so these
tests cannot imply that a mock server enforced an unconstrained wire contract.
They are not Gemini-generated analyses or remote acceptance tests.
"""
from copy import deepcopy
import json

import httpx
import jsonschema
import pytest

from app.prompts.bo_prompts import BO_M3_JSON_SCHEMA
from app.services.bo.m3_standalone import run_m3
from app.services.llm.providers_vertex import VertexProviderBackend
from app.services.llm.providers_vertex_m3 import vertex_m3_response_schema
from tests import bo_helpers as h
from tests.integration.test_bo_m3_phase3_replay import recorded
from tests.integration.test_bo_m3_v14_correction import scenario_setup, scenario_response
from tests.unit.test_bo_m3_v14_contract_fix import hca_example
from tests.unit.test_vertex_m3_schema import _settings


def mock_backend(response):
    from google import genai
    from google.oauth2.credentials import Credentials
    requests = []

    def handle(request):
        wire = json.loads(request.content)
        requests.append(wire)
        schema = wire["generationConfig"]["responseJsonSchema"]
        assert schema == vertex_m3_response_schema(BO_M3_JSON_SCHEMA)
        jsonschema.validate(response, schema)
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [
            {"text": json.dumps(response)}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 100, "totalTokenCount": 200}})

    client = genai.Client(vertexai=True, project="offline-diagnostic-project", location="global",
        credentials=Credentials(token="offline-synthetic-token"),
        http_options={"client_args": {"transport": httpx.MockTransport(handle)}})
    return VertexProviderBackend(_settings(), client_factory=lambda: client, m3_draft_contract=False), client, requests


@pytest.mark.parametrize("failure", [None, "unknown_node", "unqualified_legal_claim", "unknown_evidence"])
def test_sdk_to_real_specialist_gate_and_validator7_admits_supported_scenario_and_rejects_bad_claims(db_session, failure):
    prep, eid, doc = scenario_setup(db_session)
    response = scenario_response(prep, eid)
    first = response["m3_assessment"]["structural_credit_findings"][0]
    if failure == "unknown_node": first["referenced_node_ids"].append("nonexistent")
    if failure == "unqualified_legal_claim": first["credit_hypothesis"] = "The guarantee is legally binding and enforceable."
    if failure == "unknown_evidence": response["hypotheses"][0]["supporting_evidence_ids"] = ["nonexistent"]
    before = deepcopy(response)
    backend, client, requests = mock_backend(response)
    try:
        result = run_m3(db_session, case_id=h.CASE, evidence=h.evidence_snapshot(), metrics=h.metrics(),
            live_backend=backend, corporate_structure_document=doc,
            required_assessment_version="bo-m3-corporate-assessment-1.2")
    finally: client.close()
    assert len(requests) == 1
    assert response == before
    assert result.validation_stages["canonical_schema_validation"] == "valid"
    if failure == "unknown_evidence":
        assert result.validation_stages["specialist_validation"] == "rejected"
        assert result.validation_stages["admitted_analytical_findings"]["structural_credit"] == 0
        return
    assert result.specialist_validated, result.specialist_failure
    assert result.validation_stages["assessment_governance"] == "performed"
    count = 5 if failure is None else 4
    assert result.validation_stages["evidence_supported_findings"]["structural_credit"] == count
    if failure:
        rejected = result.governed_assessment["structural_credit_findings"][0]
        assert rejected["disposition"] in {"unverified", "rejected"}
        assert rejected["admissibility"] == "audit_only"
    assert result.m3_analysis["scenario_assumptions"]["real_world_verification"] == "not_established"


@pytest.mark.parametrize("failure", [None, "unsupported_competence"])
def test_authored_management_objects_reach_real_gates_and_only_supported_continuity_is_admitted(recorded, db_session, failure):
    response, _ = hca_example()
    if failure:
        response["m3_assessment"]["management_assessments"][0]["hypothesis"] = "Long service proves superior competence."
    before = deepcopy(response)
    backend, client, requests = mock_backend(response)
    try:
        result = run_m3(db_session, case_id=recorded["evidence"].case_id, evidence=recorded["evidence"], metrics={},
            live_backend=backend, required_assessment_version="bo-m3-corporate-assessment-1.2")
    finally: client.close()
    assert len(requests) == 1
    assert result.validation_stages["canonical_schema_validation"] == "valid"
    assert result.specialist_validated, result.specialist_failure
    assert result.validation_stages["assessment_governance"] == "performed"
    assert result.validation_stages["evidence_supported_findings"]["management"] == (2 if failure else 3)
    profiles = result.governed_assessment["management_stability"]["executives"]
    assert len(profiles) == 7
    assert all(f["verification_status"] == "verified" for p in profiles for f in p["facts"])
    if failure:
        item = result.governed_assessment["management_assessments"][0]
        assert item["disposition"] == "unverified" and item["admissibility"] == "audit_only"
    assert response == before
