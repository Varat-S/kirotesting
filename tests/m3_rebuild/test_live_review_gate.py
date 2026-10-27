"""Mocked one-call live-review gate; production preparation/runtime, no network."""
import json
from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services.bo.m3_rebuild.contracts import provider_schema
from app.services.bo.m3_rebuild.credit_test import OneRequestBackend, MODEL
from app.services.llm.providers_vertex import VertexProviderBackend
from .review_fixtures import response
from .test_validator7_claims import run, ResponseBackend


@pytest.mark.parametrize('fail',[False,True])
def test_only_reviewer_can_spend_single_budget_through_bo(db_session,tmp_path,fail):
    calls=[]
    class Models:
        def generate_content(self,**kwargs):
            calls.append(kwargs)
            if fail:raise RuntimeError('Mock provider failure')
            data=json.loads(kwargs['contents'])
            assert set(data)=={'canonical_packet','claim_validation','original_judgments','original_overall_assessment','assessment_scope'}
            assert 'decisions' not in data and 'review_fixture' not in data
            parsed=response({'judgments':data['original_judgments'],
                'overall_assessment':data['original_overall_assessment']},data['claim_validation'])
            return SimpleNamespace(text=json.dumps(parsed),model_version=MODEL,
                usage_metadata=SimpleNamespace(prompt_token_count=100,candidates_token_count=50,total_token_count=150))
    vertex=VertexProviderBackend(Settings(_env_file=None,llm_provider='vertex',
        google_cloud_project='test',google_cloud_location='global',vertex_model_narrow=MODEL),
        client_factory=lambda:SimpleNamespace(models=Models()),schema_transform=provider_schema)
    guarded=OneRequestBackend(vertex,tmp_path/'budget/request_ledger.json',
        {MODEL:{'input':.3,'output':2.5}},agent_id='m3_validator_7',authorization='Mocked one-review test')
    unauthorized=SimpleNamespace(prompt=SimpleNamespace(name='m3_management_assessment'),model_tier='narrow')
    with pytest.raises(RuntimeError,match='specifically authorized'):guarded.generate(unauthorized)
    assert calls==[] and not guarded.path.exists()
    _,_,assessment=run(db_session,tmp_path,ResponseBackend(lambda p,packet:None),guarded)
    assert len(calls)==1 and guarded.attempt_count==1
    assert calls[0]['model']==MODEL
    assert calls[0]['config']['http_options']['retry_options']['attempts']==1
    exact=json.loads((guarded.path.parent/'vertex_exact_request.json').read_text(encoding="utf-8"))
    assert {k:exact[k] for k in ('model','contents','config')}==calls[0]
    ledger=json.loads(guarded.path.read_text(encoding="utf-8"))
    assert ledger['agent_id']=='m3_validator_7' and ledger['requests']==1
    if fail:
        assert ledger['state']=='failed' and ledger['estimated_cost_usd'] is None
        assert assessment['validation']['validator_7']['mode']=='failed'
        assert assessment['facts'] and assessment['observations']==[]
    else:
        assert ledger['state']=='answer_returned'
        assert ledger['estimated_cost_usd']==pytest.approx(.000155)
        assert assessment['validation']['validator_7']['mode']=='model_review'
    request=SimpleNamespace(prompt=SimpleNamespace(name='m3_validator_7'),model_tier='narrow')
    with pytest.raises(RuntimeError,match='exhausted'):guarded.generate(request)
    assert len(calls)==1
