"""Draft/assembly boundary regressions. All inputs are authored or replayed."""
from copy import deepcopy
import json
from pathlib import Path

import jsonschema
import pytest

from app.core.hashing import content_hash
from app.prompts.bo_prompts import BO_M3_JSON_SCHEMA
from app.services.bo.m3_contract import (assemble_draft, audit_required_gaps, generation_schema,
    provider_generation_schema, PROFILE_DERIVED, FACT_DERIVED, STABILITY_DERIVED, FINDINGS, STATUS_FIELDS)
from app.services.llm.providers_vertex_m3 import vertex_m3_response_schema
from examples.bo_m3.vertex400_diagnostics.compare_offline import sdk_wire_check


def authored_draft(response):
    """Author a complete *test* draft from a grounded legacy fixture.

    These explicit empty/unknown fixture inputs are not production defaults and
    must never be used on a recorded model reply to manufacture acceptance.
    """
    data = deepcopy(response)
    schema = generation_schema()
    for h in data['hypotheses']:
        h.setdefault('missing_information', [])
        h.setdefault('evidence_basis', 'inference')
        h.setdefault('credit_mechanism', 'Conditional scenario credit mechanism; actual legal terms remain unknown.')
    for p in data['parameters']:
        p.setdefault('value', None)
    m3 = data['m3_assessment']
    for bucket in FINDINGS:
        for item in m3[bucket]:
            item['proposed_disposition'] = item.pop('disposition')
            item.pop('admissibility')
    for bucket, field in STATUS_FIELDS.items():
        for item in m3[bucket]: item['proposed_'+field] = item.pop(field)
    stability = m3['management_stability']
    for key in ('as_of_date', 'as_of_source', 'history_start', 'history_end', 'history_source'):
        stability.setdefault(key, None)
    stability.setdefault('limitations', [])
    for profile in stability['executives']:
        for fact in profile['facts']:
            fact.setdefault('date', None)
            fact.setdefault('end_date', None)
    def project(value, node):
        if isinstance(value, dict):
            return {k: project(v,node['properties'][k]) for k,v in value.items() if k in node.get('properties',{})}
        if isinstance(value, list): return [project(v,node['items']) for v in value]
        return value
    return project(data, schema)


def sample():
    fixture=json.loads(Path('examples/bo_m3/management_stability/fictional_negative_fixture.json').read_text())
    return authored_draft(fixture['response'])


def test_v15_audit_enumerates_all_nested_required_gaps_and_ownership():
    gaps=audit_required_gaps(BO_M3_JSON_SCHEMA,vertex_m3_response_schema(BO_M3_JSON_SCHEMA))
    assert len(gaps)==68
    for section,count in [('structural_credit_findings',10),('management_assessments',16)]:
        assert len([g for g in gaps if g['path'].startswith('/m3_assessment/'+section+'/[]/')])==count
    assert {g['ownership'] for g in gaps}=={'A','C'}
    assert all(g['resolution'] for g in gaps)


def test_assembly_adds_only_actual_configured_window_and_preserves_input():
    draft=sample();before=deepcopy(draft)
    assembled,audit=assemble_draft(draft,window_years=4)
    assert draft==before
    assert assembled['m3_assessment']['management_stability'].pop('recent_change_window_years')==4
    assert assembled==before
    assert not audit['validator_outcomes_assigned'] and not audit['model_owned_defaults_inserted']
    assert len(audit['operations'])==1
    assert audit['original_draft_sha256']==content_hash(draft)


@pytest.mark.parametrize('field',['supporting_evidence_ids','contradicting_evidence_ids','dimension','evidence_basis',
    'attribution','assurance_scope','confidence','materiality','supporting_evidence_investigated',
    'contradictory_evidence_investigated','effective_from','missing_information','fact_ids'])
def test_missing_model_owned_fields_are_not_backfilled(field):
    draft=sample();del draft['m3_assessment']['management_assessments'][0][field]
    before=deepcopy(draft)
    with pytest.raises(jsonschema.ValidationError):assemble_draft(draft,window_years=3)
    assert draft==before


@pytest.mark.parametrize('error',['string_fact','missing_source','invented_kind','extra_admissibility','derived_profile','missing_date','wrong_source_type'])
def test_malformed_fact_or_forged_validator_output_cannot_cross_draft_boundary(error):
    draft=sample();profile=draft['m3_assessment']['management_stability']['executives'][0];fact=profile['facts'][0]
    if error=='string_fact':profile['facts']=['fact_001']
    elif error=='missing_source':del fact['source']
    elif error=='invented_kind':fact['kind']='competent'
    elif error=='extra_admissibility':draft['m3_assessment']['management_assessments'][0]['admissibility']='established'
    elif error=='derived_profile':profile['tenure']={'min_completed_months':99}
    elif error=='missing_date':del fact['date']
    elif error=='wrong_source_type':fact['source']='citation'
    with pytest.raises(jsonschema.ValidationError):assemble_draft(draft,window_years=3)


def test_draft_is_closed_and_canonical_unchanged_with_actual_sdk_serialization():
    before=deepcopy(BO_M3_JSON_SCHEMA)
    draft=generation_schema();jsonschema.Draft202012Validator.check_schema(draft)
    assert 'admissibility' not in draft['properties']['m3_assessment']['properties']['management_assessments']['items']['properties']
    assert 'analytical_dimension' in draft['properties']['m3_assessment']['properties']['structural_credit_findings']['items']['required']
    assert 'analytical_dimension' not in draft['properties']['m3_assessment']['properties']['governance_screening']['items']['properties']
    result=sdk_wire_check(draft);assert result['raw_json_schema_preserved'] and not result['remote_acceptance_tested']
    assert before==BO_M3_JSON_SCHEMA


def test_smaller_decoding_grammar_preserves_every_required_field_and_type():
    local=generation_schema();wire=provider_generation_schema(local)
    def shape(node):
        if isinstance(node,dict):return {k:shape(v) for k,v in node.items() if k not in {'$id','enum','description'}}
        if isinstance(node,list):return [shape(v) for v in node]
        return node
    assert shape(local)==shape(wire)
    draft=sample()
    draft['m3_assessment']['management_assessments'][0]['confidence']='invented-confidence'
    jsonschema.validate(draft,wire)  # Provider-only acceptance is insufficient.
    with pytest.raises(jsonschema.ValidationError):assemble_draft(draft,window_years=3)
    assert sdk_wire_check(wire)['raw_json_schema_preserved']


def test_original_v15_response_is_not_repaired_into_the_new_draft():
    import hashlib
    from app.models.base import create_engine_and_session
    from app.services.bo.m3_diagnostics import load_recorded_m3_run
    path=Path('data/m3_v15_hca_live_20261026_01.db')
    if not path.exists():pytest.skip('Private original v1.5 recording absent; no paid replacement.')
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    engine,sessions=create_engine_and_session(f'sqlite:///file:{path.resolve()}?mode=ro&uri=true')
    with sessions() as session:bundle=load_recorded_m3_run(session,'72e6431a-b465-4682-a68d-96573dbef460')
    engine.dispose();original=json.loads(bundle['raw_response']);snapshot=deepcopy(original)
    with pytest.raises(jsonschema.ValidationError):assemble_draft(original,window_years=3)
    assert original==snapshot
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before


def test_compact_transport_is_a_lossless_key_mapping_with_identical_types():
    from app.services.bo.m3_contract import compact_generation_schema,encode_transport,decode_transport,transport_aliases
    original=sample();encoded=encode_transport(original)
    jsonschema.validate(encoded,compact_generation_schema())
    assert decode_transport(encoded)==original
    assert content_hash(decode_transport(encoded))==content_hash(original)
    assembled,audit=assemble_draft(encoded,window_years=3,compact=True)
    assembled['m3_assessment']['management_stability'].pop('recent_change_window_years')
    assert assembled==original
    assert audit['transport_decode']['alias_map_sha256']==content_hash(transport_aliases())
    wire=provider_generation_schema(compact_generation_schema())
    assert sdk_wire_check(wire)['raw_json_schema_preserved']
    def required_shapes(node):
        out={'type':node.get('type'),'required_count':len(node.get('required',[]))}
        out['fields']=[required_shapes(n) for n in node.get('properties',{}).values()]
        if 'items' in node:out['items']=required_shapes(node['items'])
        return out
    assert required_shapes(compact_generation_schema())==required_shapes(generation_schema())
    from unittest.mock import patch
    registered=compact_generation_schema()
    with patch('app.services.bo.m3_contract.generation_schema',side_effect=RuntimeError('Future contract changed')):
        assert decode_transport(encoded,registered)==original
