"""Canonical-derived generation constraints; no remote calls or JSON repair."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

from app.core.hashing import content_hash
from app.prompts.bo_prompts import BO_M3_JSON_SCHEMA
from app.schemas.bo_m3 import M3CorporateAssessment
from app.services.llm.providers_vertex_m3 import vertex_m3_response_schema
from examples.bo_m3.vertex_strict_json.compare_offline import BASELINE_HASH, compare


def sample():
    return json.loads(Path("examples/bo_m3/management_stability/fictional_negative_fixture.json").read_text())["response"]


def management(response):
    return response["m3_assessment"]["management_stability"]


def test_current_candidate_and_historical_baseline_are_explicit_and_independent():
    before = deepcopy(BO_M3_JSON_SCHEMA)
    baseline = vertex_m3_response_schema(before, variant="compact")
    assert content_hash(baseline) == BASELINE_HASH
    current = vertex_m3_response_schema(before)
    assert current != baseline
    # Only the version, new management requirement, and three critical subtrees change.
    for key in ("management_stability", "management_assessments", "structural_credit_findings"):
        current["properties"]["m3_assessment"]["properties"][key] = baseline["properties"]["m3_assessment"]["properties"][key]
    current["properties"]["m3_assessment"]["required"].remove("management_stability")
    current["properties"]["m3_assessment"]["properties"]["schema_version"] = baseline["properties"]["m3_assessment"]["properties"]["schema_version"]
    assert current == baseline
    assert before == BO_M3_JSON_SCHEMA


def test_projected_fields_keep_their_canonical_types_and_property_order():
    original = BO_M3_JSON_SCHEMA["properties"]["m3_assessment"]["properties"]
    current = vertex_m3_response_schema(BO_M3_JSON_SCHEMA)["properties"]["m3_assessment"]["properties"]
    for name in ("management_assessments", "structural_credit_findings"):
        selected = current[name]["items"]["properties"]
        assert list(selected) == [k for k in original[name]["items"]["properties"] if k in selected]
        assert all(value == original[name]["items"]["properties"][key] for key, value in selected.items())
    original_fact = original["management_stability"]["properties"]["executives"]["items"]["properties"]["facts"]["items"]
    current_fact = current["management_stability"]["properties"]["executives"]["items"]["properties"]["facts"]["items"]
    for key in ("fact_id", "kind", "value", "date", "end_date"):
        assert current_fact["properties"][key] == original_fact["properties"][key]


def test_supported_nonempty_profile_and_facts_pass_both_schemas_and_typed_contract():
    response = sample()
    assert management(response)["executives"][0]["facts"]
    for schema in (vertex_m3_response_schema(BO_M3_JSON_SCHEMA), BO_M3_JSON_SCHEMA):
        jsonschema.validate(response, schema)
    M3CorporateAssessment.model_validate(response["m3_assessment"])


@pytest.mark.parametrize("case", ["string", "object_fragments", "missing_fact_id", "missing_kind", "missing_value", "missing_source",
    "number_value", "unknown_kind", "string_source", "source_without_quote", "source_id_array", "numeric_date",
    "facts_reference_array", "numeric_executive_id", "missing_role", "missing_facts", "fact_ids_objects", "executive_ids_string",
    "structural_item_string", "node_ids_string", "link_ids_objects", "missing_management", "wrong_version"])
def test_critical_malformed_shapes_fail_provider_schema_without_repairs(case):
    response = sample()
    stability = management(response)
    executive = stability["executives"][0]
    fact = executive["facts"][0]
    observation = response["m3_assessment"]["management_assessments"][0]
    if case == "string": executive["facts"] = ["fact_01"]
    elif case == "object_fragments": executive["facts"] = ["fact_id: f01", "value: CEO", "source: {evidence_id: ev}"]
    elif case.startswith("missing_") and case[8:] in fact: del fact[case[8:]]
    elif case == "number_value": fact["value"] = 123
    elif case == "unknown_kind": fact["kind"] = "competent"
    elif case == "string_source": fact["source"] = "ev"
    elif case == "source_without_quote": del fact["source"]["quote"]
    elif case == "source_id_array": fact["source"]["evidence_id"] = ["ev"]
    elif case == "numeric_date": fact["date"] = 2025
    elif case == "facts_reference_array": executive["facts"] = observation["fact_ids"]
    elif case == "numeric_executive_id": executive["executive_id"] = 123
    elif case == "missing_role": del executive["current_role"]
    elif case == "missing_facts": del executive["facts"]
    elif case == "fact_ids_objects": observation["fact_ids"] = [deepcopy(fact)]
    elif case == "executive_ids_string": observation["executive_ids"] = executive["executive_id"]
    elif case == "missing_management": del response["m3_assessment"]["management_stability"]
    elif case == "wrong_version": response["m3_assessment"]["schema_version"] = "bo-m3-corporate-assessment-1.0"
    else:
        finding = {"finding_id": "f", "hypothesis_id": None, "credit_hypothesis": "Test", "causal_mechanism": "Test",
                   "referenced_node_ids": [], "referenced_link_ids": []}
        if case == "structural_item_string": finding = "f"
        elif case == "node_ids_string": finding["referenced_node_ids"] = "node"
        elif case == "link_ids_objects": finding["referenced_link_ids"] = [{"link": "node"}]
        else: raise AssertionError(case)
        response["m3_assessment"]["structural_credit_findings"] = [finding]
    before = deepcopy(response)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(response, vertex_m3_response_schema(BO_M3_JSON_SCHEMA))
    assert response == before


def test_unavailable_and_incomplete_dates_do_not_require_fabrication():
    response = sample()
    stability = management(response)
    for fact in stability["executives"][0]["facts"]:
        fact["date"] = None
        fact.pop("end_date", None)
    jsonschema.validate(response, vertex_m3_response_schema(BO_M3_JSON_SCHEMA))
    stability.update(executives=[], as_of_date=None, as_of_source=None)
    response["hypotheses"] = []
    response["m3_assessment"]["management_assessments"] = []
    for schema in (vertex_m3_response_schema(BO_M3_JSON_SCHEMA), BO_M3_JSON_SCHEMA):
        jsonschema.validate(response, schema)
    M3CorporateAssessment.model_validate(response["m3_assessment"])


def test_remaining_open_fields_still_fail_full_canonical_not_silently_repaired():
    response = sample()
    response["m3_assessment"]["legal_entities"] = [{}]
    jsonschema.validate(response, vertex_m3_response_schema(BO_M3_JSON_SCHEMA))
    with pytest.raises(jsonschema.ValidationError): jsonschema.validate(response, BO_M3_JSON_SCHEMA)


def test_real_sdk_conversion_has_no_schema_loss_and_no_live_acceptance_claim():
    result = compare()
    for details in result["variants"].values():
        assert details["mock_http_requests"] == 1
        assert details["raw_json_schema_preserved"] is True
        assert details["remote_acceptance_tested"] is False
        assert not details["unsupported_keyword_paths"]
        assert not details["required_not_in_properties"]


def test_prepared_probe_requires_real_nested_objects_not_vacuous_empty_arrays():
    from examples.bo_m3.vertex_strict_json.prepared_probe import HERE, inspect_probe_response, prepared_request
    request = prepared_request()
    assert request["config"]["max_output_tokens"] == 1024
    assert request["config"]["http_options"]["retry_options"]["attempts"] == 1
    expected = json.loads((HERE / "synthetic_probe_expected.json").read_text())
    assert inspect_probe_response(json.dumps(expected))["fact_objects"] == 1
    empty = deepcopy(expected)
    empty["m3_assessment"]["management_stability"]["executives"] = []
    # Valid unavailable JSON remains legal generally but cannot prove this diagnostic.
    jsonschema.validate(empty, request["config"]["response_json_schema"])
    with pytest.raises(ValueError, match="Inconclusive"): inspect_probe_response(json.dumps(empty))
    wrong = deepcopy(expected)
    wrong["m3_assessment"]["management_stability"]["executives"][0]["facts"][0]["source"]["quote"] = "Invented quote"
    with pytest.raises(ValueError, match="source reference"): inspect_probe_response(json.dumps(wrong))


@pytest.mark.parametrize("database,run_id,count", [
    ("data/m3_v14_hca_live_20261026_01.db", "23d91a53-dc5e-4a74-8f03-593f97c94840", 1),
    ("data/m3_v14_hca_corrected_20261026_02.db", "ee2a9951-5df6-4067-88e4-c12fffee939b", 4),
])
def test_original_private_responses_expose_the_old_gap_and_fail_the_new_constraint(database, run_id, count):
    from app.models.base import create_engine_and_session
    from app.services.bo.m3_diagnostics import load_recorded_m3_run
    path = Path(database)
    if not path.exists(): pytest.skip("Private live recording absent; no paid replacement.")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    engine, sessions = create_engine_and_session(f"sqlite:///file:{path.resolve()}?mode=ro&uri=true")
    try:
        with sessions() as session: bundle = load_recorded_m3_run(session, run_id)
    finally: engine.dispose()
    original = json.loads(bundle["raw_response"])
    sent = bundle["provider_metadata"]["provider_request"]["config"]["response_json_schema"]
    assert content_hash(sent) == BASELINE_HASH
    jsonschema.validate(original, sent)
    assert len(list(jsonschema.Draft202012Validator(BO_M3_JSON_SCHEMA).iter_errors(original))) == count
    candidate_errors = list(jsonschema.Draft202012Validator(vertex_m3_response_schema(BO_M3_JSON_SCHEMA)).iter_errors(original))
    fact_errors = [e for e in candidate_errors if list(e.absolute_path)[:5] == ["m3_assessment", "management_stability", "executives", 0, "facts"]]
    assert len(fact_errors) == count
    assert all(e.validator == "type" for e in fact_errors)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
