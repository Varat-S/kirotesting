"""Actual production draft -> assembly -> evidence gate -> Validator 7 offline."""
from copy import deepcopy
import json
from pathlib import Path

import httpx
import jsonschema
import pytest

from app.services.bo.m3_contract import generation_schema, compact_generation_schema, provider_generation_schema, encode_transport, _label
from app.services.bo.m3_diagnostics import load_recorded_m3_run
from app.services.bo.m3_standalone import run_m3
from app.services.llm.client import LLMBackend, LLMRawResult
from app.services.llm.providers_vertex import VertexProviderBackend
from tests import bo_helpers as h
from tests.integration.test_bo_m3_phase3_replay import recorded
from tests.integration.test_bo_m3_v14_correction import scenario_setup, scenario_response
from tests.unit.test_bo_m3_v14_contract_fix import hca_example
from tests.unit.test_m3_contract_alignment import authored_draft
from tests.unit.test_vertex_m3_schema import _settings


class DraftBackend(LLMBackend):
    model_id='fake-authored-draft'
    m3_draft_contract=True
    def __init__(self,response):self.response=response;self.calls=0
    def generate(self,request):
        assert request.prompt.response_schema==compact_generation_schema()
        self.calls+=1
        # Encode malformed authored fixtures too, so the real client (rather
        # than this test backend) must reject missing model-owned fields.
        def encode(value,node):
            if isinstance(value,dict):
                names={key:_label(i) for i,key in enumerate(node['properties'])}
                return {names[k]:encode(v,node['properties'][k]) for k,v in value.items()}
            if isinstance(value,list):return [encode(v,node['items']) for v in value]
            return value
        return LLMRawResult(json.dumps(encode(self.response,generation_schema())),self.model_id,{'backend':'fake'})


@pytest.mark.parametrize('failure',[None,'unknown_node','unsupported_guarantee','false_review_flag','uncited_finding','unknown_citation','missing_model_field'])
def test_portable_structural_findings_pass_every_real_gate_or_remain_rejected(db_session,failure):
    prep,eid,doc=scenario_setup(db_session)
    response=authored_draft(scenario_response(prep,eid))
    finding=response['m3_assessment']['structural_credit_findings'][0]
    if failure=='unknown_node':finding['referenced_node_ids'].append('invented')
    if failure=='unsupported_guarantee':finding['credit_hypothesis']='The guarantee is legally binding and enforceable.'
    if failure=='false_review_flag':finding['contradictory_evidence_investigated']=False
    if failure=='uncited_finding':finding['supporting_evidence_ids']=[]
    if failure=='unknown_citation':response['hypotheses'][0]['supporting_evidence_ids']=['invented']
    if failure=='missing_model_field':del finding['materiality']
    before=deepcopy(response);backend=DraftBackend(response)
    result=run_m3(db_session,case_id=h.CASE,evidence=h.evidence_snapshot(),metrics=h.metrics(),
        live_backend=backend,corporate_structure_document=doc,required_assessment_version='bo-m3-corporate-assessment-1.2')
    assert backend.calls==1 and response==before
    stages=result.validation_stages
    if failure=='missing_model_field':
        assert stages['generation_draft_validation']=='rejected'
        assert stages['specialist_validation']==stages['assessment_governance']=='not_run'
        assert stages['module_completion']=='failed_generation_contract'
        return
    assert stages['canonical_input_validation']=='valid'
    if failure=='unknown_citation':
        assert stages['specialist_validation']=='rejected'
        assert stages['assessment_governance']=='not_run'
        assert result.governed_assessment is None
        return
    assert result.specialist_validated,result.specialist_failure
    assert stages['assessment_governance']=='performed'
    assert stages['canonical_schema_validation']=='valid'
    assert stages['evidence_supported_findings']['structural_credit']==(5 if failure is None else 4)
    if failure:
        item=result.governed_assessment['structural_credit_findings'][0]
        assert item['disposition'] in {'unverified','rejected'} and item['admissibility']=='audit_only'
    assert 'admissibility' not in response['m3_assessment']['structural_credit_findings'][0]
    bundle=load_recorded_m3_run(db_session,stages['model_run_id'])
    assert json.loads(bundle['raw_response'])==encode_transport(before)
    assert result.m3_analysis['scenario_assumptions']['real_world_verification']=='not_established'


@pytest.mark.parametrize('failure',[None,'unsupported_competence'])
def test_actual_sdk_wire_and_full_management_pipeline_use_the_same_draft_contract(recorded,db_session,failure):
    from google import genai
    from google.oauth2.credentials import Credentials
    response,_=hca_example();response=authored_draft(response)
    if failure:response['m3_assessment']['management_assessments'][0]['hypothesis']='Long service proves superior competence.'
    requests=[]
    def handle(request):
        wire=json.loads(request.content);requests.append(wire)
        assert wire['generationConfig']['responseJsonSchema']==provider_generation_schema(compact_generation_schema())
        jsonschema.validate(encode_transport(response),wire['generationConfig']['responseJsonSchema'])
        return httpx.Response(200,json={'candidates':[{'content':{'role':'model','parts':[{'text':json.dumps(encode_transport(response))}]},'finishReason':'STOP'}],
            'usageMetadata':{'promptTokenCount':100,'candidatesTokenCount':100,'totalTokenCount':200}})
    client=genai.Client(vertexai=True,project='offline-project',location='global',credentials=Credentials(token='offline-synthetic'),
        http_options={'client_args':{'transport':httpx.MockTransport(handle)}})
    backend=VertexProviderBackend(_settings(),client_factory=lambda:client)
    try:
        result=run_m3(db_session,case_id=recorded['evidence'].case_id,evidence=recorded['evidence'],metrics={},
            live_backend=backend,required_assessment_version='bo-m3-corporate-assessment-1.2')
    finally:client.close()
    assert len(requests)==1
    assert result.specialist_validated,result.specialist_failure
    assert result.validation_stages['assessment_governance']=='performed'
    assert result.validation_stages['canonical_schema_validation']=='valid'
    assert result.validation_stages['evidence_supported_findings']['management']==(2 if failure else 3)
    profiles=result.governed_assessment['management_stability']['executives']
    assert len(profiles)==7
    assert all(f['verification_status']=='verified' for p in profiles for f in p['facts'])
    if failure:assert result.governed_assessment['management_assessments'][0]['admissibility']=='audit_only'


def test_a_governed_canonical_failure_is_recorded_and_never_admitted(db_session):
    from unittest.mock import patch
    prep,eid,doc=scenario_setup(db_session)
    backend=DraftBackend(authored_draft(scenario_response(prep,eid)))
    with patch('app.services.bo.m3_contract.validate_governed_response',side_effect=jsonschema.ValidationError('Injected output contract defect')):
        result=run_m3(db_session,case_id=h.CASE,evidence=h.evidence_snapshot(),metrics=h.metrics(),
            live_backend=backend,corporate_structure_document=doc,required_assessment_version='bo-m3-corporate-assessment-1.2')
    assert result.governed_assessment is None
    assert result.validation_stages['canonical_schema_validation']=='rejected'
    assert result.validation_stages['canonical_validation_occurred'] is True
    assert result.validation_stages['assessment_governance']=='rejected'
    assert result.validation_stages['admitted_analytical_findings']['structural_credit']==0
