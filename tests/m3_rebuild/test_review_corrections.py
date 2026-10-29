"""Explicit overstatement corrections retain source gates and original reviewer audit."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from app.core.hashing import content_hash
from app.services.bo.m3_rebuild.corrections import review_content
from app.services.bo.offline import ScriptedBoBackend
from app.services.bo.runner import BusinessOverviewRunner
from tests.integration.test_bo_v3_complete import _script
from .test_shared_input_integration import _package
from .test_validator7_claims import ResponseBackend, MixedReview


def mutate(payload, packet):
    payload['facts'].append({'fact_id':'bad_fact','executive':'Alex Sample',
        'statement':'Invented performance','citations':[{'source_id':'unadmitted','quote':'Invented performance'}]})
    j=payload['judgments'][0]
    payload['judgments']=[{**j,'judgment_id':'valid','explanation':'Long tenure proves management competence.'},
        {**j,'judgment_id':'mixed','supporting_fact_ids':['fact:1','bad_fact']},
        {**j,'judgment_id':'unsupported','supporting_fact_ids':['bad_fact']}]


def execute(session, path, package, corrections=None):
    result=BusinessOverviewRunner(session,backend=ScriptedBoBackend(_script()),bo_roster='v3',
        data_root=path/'data',output_root=path/'out',m3_backend=ResponseBackend(mutate),
        m3_reviewer_backend=MixedReview(),m3_review_corrections=corrections).run_case(
        'CORRECT_M3',package=package,write_outputs=False)
    block=result.handoff['module_conclusions']['M3']
    return block['m3_rebuild']['management']['artifact'],Path(block['source_manifest']).parent


@pytest.mark.parametrize('failure',[None,'unsupported_reference','judgment_hash','review_hash','source_hash','invalid_contract'])
def test_explicit_correction_through_bo_preserves_claim_gates_and_raw_audit(db_session,tmp_path,failure):
    package=_package()
    before, first=execute(db_session,tmp_path/'first',package)
    payload=json.loads((first/'management_extraction.json').read_text(encoding="utf-8"))
    review=json.loads((first/'validator7_response.txt').read_text(encoding="utf-8"))
    corrected={'judgment_id':'valid','outcome':'qualified',
        'reason':'Tenure establishes appointment context, not demonstrated competence.',
        'supporting_claim_ids':['fact:1'],
        'reviewed_text':'The appointment supports neutral transition monitoring; competence is not established.',
        'limitations':['No performance outcome is established.']}
    correction={'author':'Test explicit reviewer','reason':'Narrow an analytical overstatement.',
        'source_sha256':before['source']['sha256'],
        'original_judgments_sha256':content_hash(payload['judgments']),
        'original_review_sha256':content_hash(review_content(review)),
        'decisions':[corrected], 'overall':deepcopy(review['overall'])}
    correction['overall']['text']='Only bounded neutral transition monitoring survives; performance is unestablished.'
    if failure=='unsupported_reference':corrected['supporting_claim_ids']=['bad_fact']
    if failure=='judgment_hash':correction['original_judgments_sha256']='wrong'
    if failure=='review_hash':correction['original_review_sha256']='wrong'
    if failure=='source_hash':correction['source_sha256']='wrong'
    if failure=='invalid_contract':corrected['limitations']=123
    after, folder=execute(db_session,tmp_path/'second',package,correction)
    assert (folder/'management_response.txt').read_bytes()==(first/'management_response.txt').read_bytes()
    assert (folder/'validator7_response.txt').read_bytes()==(first/'validator7_response.txt').read_bytes()
    assert after['profiles']==before['profiles'] and after['facts']==before['facts']
    assert after['validation']['claim_results']==before['validation']['claim_results']
    assert next(c for c in after['validation']['claim_results'] if c['claim_id']=='bad_fact')['outcome']=='unsupported'
    if failure:
        assert after['observations']==[]
        assert after['validation']['validator_7']['mode']=='failed'
        return
    assert review_content(after['validation']['validator_7'])==review_content(before['validation']['validator_7'])
    results={j['judgment_id']:j for j in after['validation']['judgment_results']}
    assert results['valid']['outcome']=='qualified'
    assert results['valid']['original_text']=='Long tenure proves management competence.'
    assert results['unsupported']['outcome']=='unsupported'
    observations={o['observation_id']:o for o in after['observations']}
    assert observations['valid']['text']==corrected['reviewed_text']
    assert observations['mixed']==next(o for o in before['observations'] if o['observation_id']=='mixed')
    assert after['overall_assessment']['policy']==correction['overall']['text']
    audit=json.loads((folder/'validator7_explicit_corrections.json').read_text(encoding="utf-8"))
    assert audit['origin']=='explicit_review_correction'
    assert audit['changes'][0]['original_reviewer_decision']==review['decisions'][0]
    table={c['citation_id']:c for c in after['citations']}
    for claim in after['validation']['claim_results']:
        if claim['outcome']=='unsupported':
            assert claim['citation_ids']==[]
        else:
            assert claim['citation_ids']
            for cid in claim['citation_ids']:
                citation=table[cid]
                assert citation['document_id'] and citation['physical_page']>0
                assert citation['source_id'] and citation['original_source_text']
                assert citation['page_char_end']>citation['page_char_start']
