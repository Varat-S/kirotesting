"""Replacement acceptance: defects, default execution and actual partial-memo use."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from .test_management import synthetic, hca
from .test_structure import fixture
from .test_management import assess_management
from app.services.bo.m3_rebuild.sources import sources_from_pages
from app.services.bo.m3_rebuild.adapters import unavailable_block


def with_prior(position, history, *, start='2020-01', end='2024-04'):
    p,s=synthetic()
    q='Alex Sample was appointed Chief Executive Officer effective May 1, 2024. '+history
    s.pages['pdf:p2']=s.pages['pdf:p2'].split('Alex Sample was appointed')[0]+q
    p['executives'][0].update(quote=q,prior_internal_roles=[dict(position=position,start_date=start,end_date=end,source_id='pdf:p2',quote=q)])
    p['facts'][0].update(statement=q,citations=[dict(source_id='pdf:p2',quote=q)])
    return p,s


@pytest.mark.parametrize('role,history,valid',[
 ('Chief Financial Officer of OtherCo','Alex Sample previously served as Chief Financial Officer of OtherCo from January 2020 through April 2024.',False),
 ('President','Alex Sample previously served as President from January 2020 through April 2024.',False),
 ('President of the Company','Alex Sample previously served as President of the Company from January 2020 through April 2024.',True),
 ('CFO at OtherCo','Alex Sample joined Example Corp in 2010. Alex Sample previously served as CFO at OtherCo from January 2020 through April 2024.',False),
 ('President of the Company','Alex Sample joined Example Corp from OtherCo in 2010. Alex Sample previously served as President of the Company from January 2020 through April 2024.',True),
])
def test_employer_attribution_defect_and_historical_external_promotion(role,history,valid):
    p,s=with_prior(role,history);r=assess_management(p,s)
    if valid:
        assert r['profiles'][0]['succession']=='internal'
        assert {o['direction'] for o in r['observations']}=={'positive','negative'}
        assert not any(o['rule']=='B' for o in r['observations'])
    else:
        assert r['completion_status']=='complete' and r['profiles']
        assert r['coverage']['missing']==[]
        assert r['profiles'][0]['prior_internal_roles']==[]
        rejected=[c for c in r['validation']['claim_results'] if c['kind']=='prior_internal_role' and c['outcome']=='unsupported']
        assert rejected and 'employer' in rejected[0]['reason']


def test_mixed_roster_is_not_one_person_complete():
    p,s=synthetic()
    s2=sources_from_pages([s.pages['pdf:p1'],s.pages['pdf:p2'].replace('Alex Sample was appointed','Jamie Example 45 Chief Financial Officer\nAlex Sample was appointed')],s.company,s.filing_period)
    assert len(s2.expected_roster)==2
    assert not s2.coverage_verifiable  # Jamie has no reconciled biography.
    r=assess_management(p,s2)
    assert len(r['profiles'])==1 and r['completion_status']=='partial'
    assert r['coverage']['missing']==['Jamie Example'] and r['limitations']


@pytest.mark.parametrize('extra',['Unfamiliar/possibly missing executive content','Jamie Example -- Chief Financial Officer','Alex Sample........ 50 Chief Executive Officer'])
def test_unresolved_or_duplicate_roster_content_requires_review(extra):
    p,s=synthetic();text=s.pages['pdf:p2'].replace('Alex Sample was appointed',extra+'\nAlex Sample was appointed')
    s2=sources_from_pages([s.pages['pdf:p1'],text],s.company,s.filing_period)
    assert not s2.coverage_verifiable and s2.limitations
    assert assess_management(p,s2)['completion_status']=='partial'


def test_wrapped_roster_position_is_reconciled():
    p,s=synthetic();text=s.pages['pdf:p2'].replace('50 Chief Executive Officer','50 Chief Executive\nOfficer')
    s2=sources_from_pages([s.pages['pdf:p1'],text],s.company,s.filing_period)
    assert s2.coverage_verifiable and s2.expected_roster[0]['position']=='Chief Executive Officer'


def test_registry_cannot_invoke_old_specialist():
    from app.services.agents.registry import default_registry
    for roster in ['v1','v2','v3']:
        assert 'm3_corporate_management' not in {a.agent_id for a in default_registry(roster).all()}


def test_default_orchestration_has_two_missing_statuses_without_old_call(db_session,tmp_path):
    from app.services.bo.runner import BusinessOverviewRunner
    from app.services.bo.offline import ScriptedBoBackend
    from tests.integration.test_bo_v3_complete import _script
    from tests import bo_helpers as h
    backend=ScriptedBoBackend(_script())
    result=BusinessOverviewRunner(db_session,backend=backend,bo_roster='v3',data_root=tmp_path/'data',output_root=tmp_path/'out').run_prepared(h.CASE,h.evidence_snapshot(),h.metrics(),write_outputs=False)
    block=result.handoff['module_conclusions']['M3']
    assert {c['status'] for c in block['m3_rebuild'].values()}=={'not_attempted'}
    assert block['status']=='incomplete' and backend.calls('m3_corporate_management')==0


def test_private_m3_inputs_cannot_bypass_shared_admission(db_session,tmp_path):
    from app.services.bo.runner import BusinessOverviewRunner
    p,s=hca()
    with pytest.raises(ValueError,match='private inputs are unsupported'):
        BusinessOverviewRunner(
            db_session,data_root=tmp_path/'data',output_root=tmp_path/'out',
            m3_structure=fixture(),m3_filing='selected.pdf',m3_company=s.company,
            m3_filing_period=s.filing_period,m3_extraction=p,
        )


def partial_memo_with_new_observations():
    from tests.integration.test_structural_overview_thin_slice import _handoff,_rehash
    from tests.integration.test_credit_memo_vertical_slice import fo_for
    from app.services.structural_overview.credit_memo import build_partial_assessment
    from app.services.bo.m3_rebuild.structure import convert_structure
    p,s=synthetic(internal=True)
    q='Jamie Example was appointed Chief Financial Officer effective June 1, 2024.'
    text=s.pages['pdf:p2'].replace('Alex Sample was appointed','Jamie Example 45 Chief Financial Officer\nAlex Sample was appointed')+'\n'+q
    s=sources_from_pages([s.pages['pdf:p1'],text],s.company,s.filing_period)
    p['executives'].append(dict(name='Jamie Example',position='Chief Financial Officer',appointment_date='2024-06-01',source_id='pdf:p2',quote=q,prior_internal_roles=[],external_recruitment_quote='',external_source_id=''))
    from .responses import with_credit_response
    p=with_credit_response(p,recent_names=['Alex Sample','Jamie Example'])
    r=assess_management(p,s)
    b=_handoff(True);b.update(borrower_name=s.company,as_of_date=s.filing_period)
    corp=fixture();corp['company_name']=s.company
    block=unavailable_block(b['case_id'])
    block['m3_rebuild']={'structure':dict(status='complete',validation_status='passed',artifact=convert_structure(corp),error=None),
                        'management':dict(status='complete',validation_status='passed',artifact=r,error=None)}
    block['status']='complete';b['module_conclusions']['M3']=block
    fo=fo_for(b)
    for i,period in enumerate(fo['periods']):period['fiscal_year']=2023+i
    return build_partial_assessment(_rehash(b),fo)


def test_actual_partial_memo_expresses_transition_mitigation_and_information_gap():
    from app.services.structural_overview.credit_memo import render_partial_html,validate_assessment
    a=partial_memo_with_new_observations();validate_assessment(a)
    html=render_partial_html(a)
    assert 'potential transition uncertainty' in html
    assert 'internal experience mitigates' in html
    assert 'omitted history does not establish external recruitment' in html
    assert 'SIMULATED EDUCATIONAL EXAMPLE' in html and 'pdf:p2' in html
    assert {c.category for c in a.management_claims}=={'transition','continuity','information_gap'}
    assert all(c.citation_ids for c in a.management_claims)
    assert a.status=='partial' and a.final_credit_score is None


@pytest.mark.parametrize('mutation',['text','citation','observation'])
def test_final_memo_rejects_unsupported_management_even_after_rehash(mutation):
    from app.services.structural_overview.adapter import assessment_content_hash
    from app.services.structural_overview.credit_memo import validate_assessment
    a=partial_memo_with_new_observations().model_dump(mode='json')
    if mutation=='text':a['management_claims'][0]['text']='Management is incompetent and turnover will destroy earnings.'
    if mutation=='citation':a['management_claims'][0]['citation_ids']=['invented']
    if mutation=='observation':a['management_claims'][0]['observation_id']='invented'
    a['content_hash']=assessment_content_hash(a)
    with pytest.raises(ValueError,match='Unsupported management'):validate_assessment(a)


def test_single_request_budget_is_persistent_and_historical_batch_is_retired(tmp_path):
    from types import SimpleNamespace
    from app.services.bo.m3_rebuild.credit_test import OneRequestBackend, MODEL
    from app.services.bo.m3_rebuild.provider import AGENT_ID
    from app.services.llm.client import LLMRawResult
    calls=[]
    class Backend:
        def model_for_tier(self,tier): return MODEL
        def generate(self,request):
            calls.append(request)
            return LLMRawResult('{}',MODEL,{'input_tokens':100,'output_tokens':50,'thinking_tokens':10})
    guard=OneRequestBackend(Backend(),tmp_path/'ledger.json',{MODEL:{'input':.3,'output':2.5}})
    request=SimpleNamespace(prompt=SimpleNamespace(name=AGENT_ID),model_tier='narrow')
    guard.generate(request)
    with pytest.raises(RuntimeError,match='exhausted'):guard.generate(request)
    assert len(calls)==1
    ledger=json.loads((tmp_path/'ledger.json').read_text(encoding="utf-8"))
    assert ledger['requests']==1 and ledger['estimated_cost_usd']==pytest.approx(.00018)
    from app.services.bo.m3_rebuild.authorized_tests import run_authorized_batch
    with pytest.raises(ValueError,match='retired'):run_authorized_batch()


def test_failed_management_request_consumes_budget_and_prevents_retry(tmp_path):
    from types import SimpleNamespace
    from app.services.bo.m3_rebuild.credit_test import OneRequestBackend, MODEL
    from app.services.bo.m3_rebuild.provider import AGENT_ID
    calls=[]
    class Backend:
        def model_for_tier(self,tier): return MODEL
        def generate(self,request):
            calls.append(request)
            raise RuntimeError('Simulated provider rejection')
    guard=OneRequestBackend(Backend(),tmp_path/'ledger.json',{})
    request=SimpleNamespace(prompt=SimpleNamespace(name=AGENT_ID),model_tier='narrow')
    with pytest.raises(RuntimeError,match='Simulated'):guard.generate(request)
    with pytest.raises(RuntimeError,match='exhausted'):guard.generate(request)
    assert len(calls)==1
    assert json.loads((tmp_path/'ledger.json').read_text(encoding="utf-8"))['state']=='failed'
