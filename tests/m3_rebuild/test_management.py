from copy import deepcopy
import json
from pathlib import Path
import pytest
import jsonschema
from app.services.bo.m3_rebuild.contracts import EXTRACTION_SCHEMA, provider_schema, validate_extraction, validate_management
from app.services.bo.m3_rebuild.sources import FilingSources, sources_from_pages
from app.services.bo.m3_rebuild.management import assess_management as _assess_management, missing_management, date_interval, recency, review_window
from .review_fixtures import FixtureReviewer
from app.services.bo.m3_rebuild.probe import synthetic_sources
from .responses import with_credit_response

EXAMPLES = Path(__file__).resolve().parents[2] / 'examples/m3_rebuild'


def assess_management(payload,sources):
    return _assess_management(payload,sources,reviewer=FixtureReviewer())


def hca():
    payload = json.loads((EXAMPLES/'hca_2024_extraction.json').read_text(encoding="utf-8"))
    return with_credit_response(payload, recent_names=['Jon M. Foster', 'Michael A. Marks', 'Michael R. McAlevey']), FilingSources(**json.loads((EXAMPLES/'hca_2024_sources.json').read_text(encoding="utf-8")))


def synthetic(appointment='2024-05-01', internal=True, external=False):
    s=synthetic_sources()
    quote=s.pages['pdf:p2'].split('Alex Sample was appointed')[1]
    quote='Alex Sample was appointed'+quote
    if external:
        quote=quote.split(' Alex Sample previously')[0]+' Alex Sample joined Example Corp from OtherCo in May 2024.'
        s.pages['pdf:p2']=s.pages['pdf:p2'].split('Alex Sample was appointed')[0]+quote
    p={'name':'Alex Sample','position':'Chief Executive Officer','appointment_date':appointment,'source_id':'pdf:p2','quote':quote,
       'prior_internal_roles':[{'position':'President of the Company','start_date':'2020-01','end_date':'2024-04','source_id':'pdf:p2','quote':quote}] if internal else [],
       'external_recruitment_quote':quote if external else '', 'external_source_id':'pdf:p2' if external else ''}
    return with_credit_response({'roster_date':'2025-02-01','roster_source_id':'pdf:p2','roster_quote':'As of February 1, 2025, our executive officers were as follows:','executives':[p]},recent_names=['Alex Sample'],external=external),s


def test_verified_hca_complete_roster_and_mixed_rules():
    payload,sources=hca();result=assess_management(payload,sources);validate_management(result)
    assert result['completion_status']=='complete'
    assert result['coverage']=={'expected':7,'admitted':7,'missing':[],'verifiable':True}
    assert result['roster_date']=='2025-02-01' and result['filing_period']=='2024-12-31'
    assert result['review_window']=={'start':'2022-02-01','end':'2025-02-01','boundary':'inclusive [start, end]','years':3}
    assert result['overall_assessment']['outcome']=='mixed'
    assert not any('Rossitto' in p['name'] for p in result['profiles'])
    for name in ['Jon M. Foster','Michael A. Marks','Michael R. McAlevey']:
        directions={o['direction'] for o in result['observations'] if o['executive']==name}
        assert directions=={'positive','negative'}
    for name in ['Samuel N. Hazen','Jennifer L. Berres','Michael S. Cuffe, M.D.','Sammie S. Mosier']:
        assert {o['direction'] for o in result['observations'] if o['executive']==name}=={'positive'}
    assert result['profiles'][0]['appointment_precision']=='month'
    assert 'Director' in result['profiles'][0]['roster_position']
    assert len(result['profiles'][4]['prior_internal_roles'])==4


def test_recent_promotion_keeps_both_directions():
    p,s=synthetic();r=assess_management(p,s)
    assert [o['direction'] for o in r['observations']]==['negative','positive']
    assert r['overall_assessment']['outcome']=='mixed'


def test_external_recruitment_needs_affirmative_evidence():
    p,s=synthetic(internal=False,external=True);r=assess_management(p,s)
    assert r['profiles'][0]['succession']=='confirmed_external'
    assert {o['rule'] for o in r['observations']}=={'validator_7_review'}
    assert r['overall_assessment']['outcome']=='negative'


def test_validators_off_keeps_unreviewed_judgments_as_qualified_observations():
    p,s=synthetic()
    assert _assess_management(p,s)['observations']==[]  # no Validator 7 review: nothing survives
    r=_assess_management(p,s,enforce=False);validate_management(r)
    assert len(r['observations'])==len(p['judgments'])
    for observation,judgment in zip(r['observations'],p['judgments']):
        assert observation['rule']=='unenforced_model_judgment' and observation['review_outcome']=='qualified'
        assert observation['text']==judgment['explanation'] and observation['citation_ids']
        assert 'Validators off' in observation['review_reason']


def test_validators_off_keeps_a_judgment_validator_7_withholds():
    p,s=synthetic();p['judgments'][0]['explanation']='Long tenure proves management competence.'
    held=assess_management(p,s)
    kept=_assess_management(p,s,reviewer=FixtureReviewer(),enforce=False);validate_management(kept)
    jid=p['judgments'][0]['judgment_id']
    assert jid not in {o['observation_id'] for o in held['observations']}
    observation=next(o for o in kept['observations'] if o['observation_id']==jid)
    assert observation['text']=='Long tenure proves management competence.'
    assert 'Tenure alone cannot establish competence' in observation['review_reason']


def test_omitted_history_does_not_prove_external():
    p,s=synthetic(internal=False);r=assess_management(p,s)
    assert r['profiles'][0]['succession']=='unavailable'
    assert 'B' not in [o['rule'] for o in r['observations']]
    assert any(o['direction']=='unavailable' for o in r['observations'])
    assert r['overall_assessment']['information_gaps']


def test_missing_or_incorporated_biographies_are_information_negative():
    pages=['Form 10-K\nExample Corp\nFor the fiscal year ended December 31, 2024',
           'Directors and Executive Officers\nExecutive information is incorporated by reference to our proxy statement.']
    s=sources_from_pages(pages,'Example Corp','2024-12-31');r=missing_management(s);validate_management(r)
    assert s.section_ids==[] and r['profiles']==[]
    assert r['overall_assessment']['outcome']=='unavailable'
    assert r['overall_assessment']['information_direction']=='unavailable'
    assert r['observations'][0]['category']=='information_gap'


@pytest.mark.parametrize('value,expected', [('2022-02-01','recent'),('2022-01-31','stable'),('2025-02-01','recent'),('2022','boundary_uncertain'),('2022-02','recent'),('2025','boundary_uncertain'),('2025-02','boundary_uncertain'),('2021','stable'),('','unavailable')])
def test_three_year_window_and_partial_dates(value,expected):
    assert recency(value,review_window('2025-02-01'))==expected


@pytest.mark.parametrize('value',['2025-02-02','2026','2024-02-30','May 2024','2024-13','2024-1',None,True])
def test_invalid_or_future_dates(value):
    with pytest.raises((ValueError,TypeError)):recency(value,review_window('2025-02-01'))


def test_leap_year_boundary_and_precision():
    assert review_window('2024-02-29')['start']=='2021-02-28'
    assert date_interval('2024-02')[1].isoformat()=='2024-02-29'


@pytest.mark.parametrize('mutation',['wrong_type','missing_field','extra_field','string_roles','invalid_date'])
def test_strict_schema_does_not_coerce_facts(mutation):
    p,s=synthetic()
    if mutation=='wrong_type':p['executives'][0]['name']=7
    if mutation=='missing_field':del p['executives'][0]['quote']
    if mutation=='extra_field':p['validator_approved']=True
    if mutation=='string_roles':p['executives'][0]['prior_internal_roles']=['President']
    if mutation=='invalid_date':p['executives'][0]['appointment_date']='yesterday'
    if mutation=='invalid_date':
        r=assess_management(p,s)
        assert next(c for c in r['validation']['claim_results'] if c['kind']=='appointment')['outcome']=='unsupported'
    else:
        with pytest.raises(jsonschema.ValidationError):assess_management(p,s)


@pytest.mark.parametrize('mutation',['bad_source','wrong_quote','wrong_role','wrong_date','generic_risk','external_without_evidence','another_executive','external_prior_role'])
def test_citation_match_is_separate_from_supported_claim(mutation):
    p,s=synthetic();profile=p['executives'][0]
    if mutation=='bad_source':profile['source_id']='pdf:p999'
    if mutation=='wrong_quote':profile['quote']='Invented biography'
    if mutation=='wrong_role':profile['position']='Chief Financial Officer'
    if mutation=='wrong_date':profile['appointment_date']='2023-05-01'
    if mutation=='generic_risk':
        s.pages['pdf:p2']+=' The loss of executives could harm operations.'
        profile['quote']='The loss of executives could harm operations.'
    if mutation=='external_without_evidence':profile.update(prior_internal_roles=[],external_recruitment_quote=profile['quote'],external_source_id='pdf:p2')
    if mutation=='another_executive':profile['name']='Wrong Person'
    if mutation=='external_prior_role':
        q='Alex Sample previously served OtherCo as Vice President from January 2020 through April 2024.'
        s.pages['pdf:p2']+=' '+q
        profile['prior_internal_roles']=[dict(position='Vice President',start_date='2020-01',end_date='2024-04',source_id='pdf:p2',quote=q)]
    r=assess_management(p,s)
    # A rejected claim is excluded on its own; only a lost executive is partial.
    assert r['completion_status']==('partial' if mutation=='another_executive' else 'complete')
    rejected=[c for c in r['validation']['claim_results'] if c['outcome']=='unsupported']
    assert rejected
    assert r['rejected_profiles']==[]  # Failures are scoped to evidence claims.
    if mutation!='another_executive': assert r['profiles']
    assert r['facts']  # The independently correct saved fact is not discarded.
    appointment=next(c for c in r['validation']['claim_results'] if c['kind']=='appointment')
    if mutation=='wrong_date':assert appointment['citation_results'][0]['quotation_check']=='passed'
    if mutation=='wrong_quote':assert appointment['citation_results'][0]['quotation_check']=='failed'


def test_complete_roster_enforced_and_partial_rejections_preserved():
    p,s=hca();p['executives'][1]['appointment_date']='2024-01-01'
    r=assess_management(p,s)
    assert len(r['profiles'])==7 and r['completion_status']=='complete'
    assert r['coverage']['missing']==[]
    assert r['profiles'][1]['appointment_date']==''
    assert any(c['claim_id']=='executive:2:appointment' and c['outcome']=='unsupported' for c in r['validation']['claim_results'])
    p,s=hca();p['executives']=p['executives'][:1]
    r=assess_management(p,s)
    assert len(r['coverage']['missing'])==6
    assert r['completion_status']=='partial'


def test_period_isolation_and_dynamic_location():
    p,s=hca();pages=['front matter']*11+[s.pages['pdf:p5']]+['padding']*5+[s.pages['pdf:p35'],s.pages['pdf:p36']+'\nItem 1A. Risk Factors']
    relocated=sources_from_pages(pages,'HCA Healthcare','2024-12-31')
    assert relocated.section_ids==['pdf:p18','pdf:p19'] and len(relocated.expected_roster)==7
    with pytest.raises(ValueError,match='period'):sources_from_pages(pages,'HCA Healthcare','2025-12-31')
    p['roster_date']='2026-02-01'
    r=assess_management(p,s)
    assert r['validation']['claim_results'][0]['outcome']=='unsupported'
    assert r['roster_date'] is None


def test_provider_schema_is_one_explicit_compatibility_transform():
    p,_=synthetic()
    schema=provider_schema()
    assert schema['required']==EXTRACTION_SCHEMA['required']
    assert 'additionalProperties' not in json.dumps(schema)
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(p,schema);validate_extraction(p)
    p['executives'][0]['prior_internal_roles']=['bad']
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate(p,schema)
    with pytest.raises(jsonschema.ValidationError):validate_extraction(p)


def test_invalid_observation_enum_rejected():
    p,s=synthetic();r=assess_management(p,s);r['observations'][0]['direction']='mixed'
    with pytest.raises(jsonschema.ValidationError):validate_management(r)


def test_date_from_different_prior_role_is_not_admitted():
    p,s=hca()
    # Marks' 2023 finance promotion is not the start of his National Group CFO role.
    p['executives'][4]['prior_internal_roles'][2]['start_date']='2023-01'
    r=assess_management(p,s)
    assert r['completion_status']=='complete'
    assert any(x['executive']=='Michael A. Marks' and 'bound' in x['reason'] for x in r['validation']['claim_results'])
    assert len(r['profiles'])==7


def test_appointment_precision_must_match_disclosure():
    p,s=synthetic();p['executives'][0]['appointment_date']='2024'
    r=assess_management(p,s)
    assert any(c['outcome']=='unsupported' and 'precision' in c['reason'] for c in r['validation']['claim_results'])
    assert r['profiles'][0]['appointment_date']==''


def test_roster_and_biographies_can_begin_on_different_pages():
    p,s=synthetic()
    full=s.pages['pdf:p2']
    roster,bio=full.split('Alex Sample was appointed')
    relocated=sources_from_pages([s.pages['pdf:p1'],roster,'Alex Sample was appointed'+bio+'\nItem 1A. Risk Factors'],s.company,s.filing_period)
    assert relocated.section_ids==['pdf:p2','pdf:p3']
    assert len(relocated.expected_roster)==1


def test_dated_roster_without_biographies_is_information_insufficiency():
    s=synthetic_sources();s2=sources_from_pages([s.pages['pdf:p1'],s.pages['pdf:p2'].split('Alex Sample was appointed')[0]],s.company,s.filing_period)
    assert not s2.section_ids
    assert missing_management(s2)['overall_assessment']['information_direction']=='unavailable'


def test_duplicate_roster_executive_rejected():
    p,s=synthetic();p['executives'].append(deepcopy(p['executives'][0]))
    with pytest.raises(ValueError,match='Duplicate'):assess_management(p,s)


def test_compact_citations_are_resolvable_and_not_repeated():
    p,s=hca();r=assess_management(p,s);validate_management(r)
    ids={c['citation_id'] for c in r['citations']}
    assert len(r['citations'])==15  # roster date, seven identity rows, seven biographies
    assert len(ids)==len(r['citations'])  # Canonical matches are deduplicated.
    for profile in r['profiles']:
        assert set(profile['citation_ids'])<=ids
        for role in profile['prior_internal_roles']:
            assert set(role['citation_ids'])<=ids
    r['observations'][0]['citation_ids']=['missing']
    with pytest.raises(ValueError,match='reference'):validate_management(r)
