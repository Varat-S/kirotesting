"""Milestone 4 acceptance through real preparation, routing and BO execution."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path

import pytest
from sqlalchemy import select
from reportlab.pdfgen.canvas import Canvas

from app.models.orm import AgentRunRow, EvidencePacketRow
from app.services.bo.runner import BusinessOverviewRunner, M3_PACKET_AGENT_ID
from app.services.bo.offline import ScriptedBoBackend
from app.services.llm.client import LLMBackend, LLMRawResult
from app.services.bo.m3_rebuild.matching import locate
from tests.integration.test_bo_v3_complete import _script
from .test_shared_input_integration import _package
from .test_credit_specialist import response_for
from .review_fixtures import FixtureReviewerBackend


class ResponseBackend(LLMBackend):
    def __init__(self,mutate):self.mutate=mutate;self.raw=None
    def generate(self,request):
        payload=response_for(request.inputs)
        self.mutate(payload,request.inputs)
        self.raw=json.dumps(payload)
        return LLMRawResult(self.raw,'unchanged-offline-specialist-response',{'backend':'fake'})


class MixedReview(LLMBackend):
    def generate(self,request):
        assert request.prompt.name=='m3_validator_7'
        claims={c['claim_id']:c for c in request.inputs['claim_validation']}
        assert claims['bad_fact']['outcome']=='unsupported'
        assert claims['fact:1']['outcome']=='accepted'
        decisions=[
            {'judgment_id':'valid','outcome':'accepted','reason':'Accepted appointment fact supports this bounded neutral inference.',
             'supporting_claim_ids':['fact:1'],'reviewed_text':request.inputs['original_judgments'][0]['explanation'],'limitations':[]},
            {'judgment_id':'mixed','outcome':'qualified','reason':'Invented performance fact is rejected. Appointment alone supports only neutral monitoring.',
             'supporting_claim_ids':['fact:1'],'reviewed_text':'The disclosed appointment supports neutral transition monitoring; no performance result is established.',
             'limitations':['Unsupported performance assertion excluded.']},
            {'judgment_id':'unsupported','outcome':'unsupported','reason':'No accepted fact supports the claimed performance conclusion.',
             'supporting_claim_ids':[],'reviewed_text':'','limitations':['Performance fact was fabricated.']},
        ]
        return LLMRawResult(json.dumps({'decisions':decisions,'overall':{
            'signal':'neutral','text':'Only neutral monitoring survives; performance is unestablished.',
            'judgment_ids':['valid','mixed'],'limitations':['No outcome evidence.']},
            'principal_executives':['Alex Sample'],'scope_limitations':['Only one executive is disclosed in this fixture.']}),
            'offline-validator7-fixture',{'backend':'fake'})


def run(db_session,tmp_path,backend,reviewer=None,package=None):
    result=BusinessOverviewRunner(db_session,backend=ScriptedBoBackend(_script()),
        bo_roster='v3', m3_version='v2',data_root=tmp_path/'data',output_root=tmp_path/'out',
        m3_backend=backend,m3_reviewer_backend=reviewer or FixtureReviewerBackend()).run_case(
        'M4_CLAIMS',package=package or _package(),write_outputs=False)
    block=result.handoff['module_conclusions']['M3']
    return result,block,block['m3_rebuild']['management']['artifact']


def test_mixed_claims_dependencies_and_review_are_integrated(db_session,tmp_path,monkeypatch):
    def mutate(p,packet):
        p['executives'][0]['prior_internal_roles'].append({
            'position':'Chief Financial Officer','start_date':'2010','end_date':'2020',
            'source_id':'not-admitted','quote':'Invented prior role'})
        p['facts'].append({'fact_id':'bad_fact','executive':'Alex Sample',
                          'statement':'Invented performance','citations':[{'source_id':'not-admitted','quote':'Invented performance'}]})
        j=p['judgments'][0]
        p['judgments']=[{**j,'judgment_id':'valid'},
            {**j,'judgment_id':'mixed','supporting_fact_ids':['fact:1','bad_fact']},
            {**j,'judgment_id':'unsupported','supporting_fact_ids':['bad_fact']}]
    from app.services.bo.m3_rebuild import workflow
    monkeypatch.setattr(workflow,'locate_sources',lambda *a:pytest.fail('Execution reopened PDF'))
    backend=ResponseBackend(mutate)
    result,block,a=run(db_session,tmp_path,backend,MixedReview())
    assert block['m3_rebuild']['structure']['status']=='complete'
    assert len(a['profiles'])==1 and a['profiles'][0]['appointment_date']=='2024-05-01'
    assert len(a['profiles'][0]['prior_internal_roles'])==1
    assert [f['fact_id'] for f in a['facts']]==['fact:1']
    claims={c['claim_id']:c for c in a['validation']['claim_results']}
    assert claims['executive:1:role:2']['outcome']=='unsupported'
    assert claims['executive:1:role:1']['outcome']=='accepted'
    outcomes={j['judgment_id']:j for j in a['validation']['judgment_results']}
    assert {k:v['outcome'] for k,v in outcomes.items()}=={'valid':'accepted','mixed':'qualified','unsupported':'unsupported'}
    assert outcomes['mixed']['removed_fact_ids']==['bad_fact']
    assert {o['observation_id'] for o in a['observations']}=={'valid','mixed'}
    assert a['overall_assessment']['outcome']=='neutral'
    assert a['validation']['validator_7']['mode']=='offline_fixture'
    assert (Path(block['source_manifest']).parent/'management_response.txt').read_text(encoding="utf-8")==backend.raw
    agents=db_session.scalars(select(AgentRunRow).where(AgentRunRow.analysis_run_id==result.analysis_run_id)).all()
    assert {'m3_management_assessment','m3_validator_7'}<={a.agent_id for a in agents}
    assert (Path(block['source_manifest']).parent/'validator7_response.txt').is_file()


def package_with_sentence(sentence):
    package=_package()
    stream=BytesIO();canvas=Canvas(stream)
    for lines in [
        ['Form 10-K','Delta Synthetic Fixture','For the fiscal year ended December 31, 2024'],
        ['Executive Officers','As of January 10, 2025, our executive officers were as follows:',
         'Alex Sample........ 50 Chief Executive Officer',
         'Alex Sample was appointed Chief Executive Officer effective May 1, 2024.',
         'Alex Sample previously served as Presi dent of the Company from January 2020 through April 2024.',
         sentence,'Item 2.'],
    ]:
        y=760
        for line in lines:canvas.drawString(72,y,line);y-=22
        canvas.showPage()
    canvas.save()
    next(s for s in package.sources if s.filename=='management-10k.pdf').data=stream.getvalue()
    return package


@pytest.mark.parametrize('mutation',['formatting','name','date','number','title','negation','passage'])
def test_traceable_formatting_and_real_citation_changes_through_bo(db_session,tmp_path,mutation):
    source='Alex Sample has not changed the Chief Executive Officer title since May 1, 2024; the disclosed count is 1.'
    quote=source
    if mutation=='formatting':quote=quote.replace('Chief Executive Officer','Chief\nExecutive Officer').replace(';',';  ')
    if mutation=='name':quote=quote.replace('Alex Sample','Jamie Example')
    if mutation=='date':quote=quote.replace('May 1','May 2')
    if mutation=='number':quote=quote.replace('count is 1','count is 2')
    if mutation=='title':quote=quote.replace('Chief Executive Officer','Chief Financial Officer')
    if mutation=='negation':quote=quote.replace('has not changed','has changed')
    def mutate(p,packet):
        sid=p['executives'][0]['source_id']
        if mutation=='passage':sid=next(x['evidence_id'] for x in packet['narrative_evidence'] if x['page']==1)
        p['facts'].append({'fact_id':'test_fact','executive':'Alex Sample','statement':quote,
                          'citations':[{'source_id':sid,'quote':quote}]})
    result,block,a=run(db_session,tmp_path,ResponseBackend(mutate),package=package_with_sentence(source))
    claims={c['claim_id']:c for c in a['validation']['claim_results']}
    assert claims['test_fact']['outcome']==('accepted' if mutation=='formatting' else 'unsupported')
    assert claims['fact:1']['outcome']=='accepted'
    assert claims['executive:1:role:1']['outcome']=='accepted'  # PDF Presi dent
    packet=db_session.scalars(select(EvidencePacketRow).where(
        EvidencePacketRow.analysis_run_id==result.analysis_run_id,
        EvidencePacketRow.agent_id==M3_PACKET_AGENT_ID)).one().payload
    passages={p['evidence_id']:p for p in packet['narrative_evidence']}
    for c in a['citations']:
        p=passages[c['source_id']]
        assert p['text'][c['span_start']:c['span_end']]==c['original_source_text']
        assert c['page_char_start']==p['location']+c['span_start']
        assert c['document_id']==packet['context']['management_document_id']
    assert any(c['matching_method']=='pdf_word_boundaries' for c in a['citations'])


def test_matching_does_not_change_names_numbers_or_negations():
    assert locate('Vice President — H uman Resources', 'Vice President - Human Resources')
    assert locate('finan-\ncial', 'financial')
    assert locate('Mary Ann has not changed 2024', 'Maryann has not changed 2024') is None
    assert locate('May 1, 20240','May 1, 2024') is None
    assert locate('has not changed','has changed') is None
    assert locate('CEO','CFO') is None
    assert locate('re-\nsign','resign') is None
    assert locate('Ann-\nmarie','Annmarie') is None


def test_quote_from_another_executive_fails_even_on_the_same_canonical_page(db_session,tmp_path):
    package=_package()
    source=next(s for s in package.sources if s.filename=='management-10k.pdf')
    stream=BytesIO();canvas=Canvas(stream)
    lines=['Form 10-K','Delta Synthetic Fixture','For the fiscal year ended December 31, 2024',
        'Executive Officers','As of January 10, 2025, our executive officers were as follows:',
        'Alex Sample........ 50 Chief Executive Officer','Jamie Example........ 45 Chief Financial Officer',
        'Alex Sample was appointed Chief Executive Officer effective May 1, 2024.',
        'Alex Sample previously served as President of the Company from January 2020 through April 2024.',
        'Jamie Example was appointed Chief Financial Officer effective June 1, 2024.',
        'Jamie Example previously served as Vice President of the Company from January 2020 through May 2024.',
        'Item 2.']
    y=760
    for line in lines:canvas.drawString(72,y,line);y-=22
    canvas.save();source.data=stream.getvalue()
    def mutate(p,packet):
        p['facts'].append({'fact_id':'wrong_person','executive':'Alex Sample',
            'statement':'Jamie Example was appointed Chief Financial Officer effective June 1, 2024.',
            'citations':[{'source_id':p['executives'][0]['source_id'],
              'quote':'Jamie Example was appointed Chief Financial Officer effective June 1, 2024.'}]})
    _,_,a=run(db_session,tmp_path,ResponseBackend(mutate),package=package)
    claim=next(c for c in a['validation']['claim_results'] if c['claim_id']=='wrong_person')
    assert claim['outcome']=='unsupported'
    assert 'Incorrect executive attribution' in claim['citation_results'][0]['reason']
    assert a['facts'][0]['fact_id']=='fact:1'


def test_no_reviewer_does_not_invent_qualitative_acceptance():
    from .test_management import synthetic
    from app.services.bo.m3_rebuild.management import assess_management
    p,s=synthetic();a=assess_management(p,s)
    assert a['facts'] and a['profiles']
    assert a['observations']==[] and a['overall_assessment']['outcome']=='unavailable'
    assert a['validation']['validator_7']['mode']=='not_attempted'


def test_invalid_offline_reviewer_preserves_facts_but_withholds_review(db_session,tmp_path):
    from app.services.llm.client import FakeLLMBackend
    review=FakeLLMBackend('offline-invalid-review')
    review.register('analyze','not JSON')
    _,_,a=run(db_session,tmp_path,ResponseBackend(lambda p,packet:None),review)
    assert a['profiles'] and a['facts']
    assert a['observations']==[]
    assert a['validation']['validator_7']['mode']=='failed'
    assert a['overall_assessment']['outcome']=='unavailable'
    reviewer_run=db_session.scalars(select(AgentRunRow).where(AgentRunRow.agent_id=='m3_validator_7')).one()
    assert reviewer_run.validation_status=='rejected'
