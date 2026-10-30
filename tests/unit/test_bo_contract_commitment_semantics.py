"""Company-agnostic semantic regression tests for contractual commitments."""
import re
import pytest
from app.services.bo import inference_rules
from app.services.bo.hypotheses import HypothesisValidator
from app.services.bo.indicators import IndicatorSet
from app.services.bo.plans import BO_SPEC_BY_CODE

RULE='rate_agreement_to_volume_guarantee'

def flagged(text):
    return RULE in {f['rule_id'] for f in inference_rules.evaluate(
        claim=text,mechanism=None,claim_type='inference',evidence_basis='inference')}

@pytest.mark.parametrize('industry,text',[
    ('healthcare','The reimbursement agreement guarantees patient volumes.'),
    ('manufacturing','The supply contract guarantees minimum purchase volumes.'),
    ('saas','Subscription revenue is contractually guaranteed.'),
    ('retail','The distribution agreement ensures minimum purchases.'),
    ('manufacturing','The contract guarantees no fewer than the committed volumes.'),
    ('saas','The contract not only guarantees revenue but also ensures demand.'),
    ('retail','There is no stock shortage; the retail agreement guarantees revenue.'),
    ('manufacturing','The agreement does not guarantee volumes, but a separate contract guarantees revenue.'),
    ('saas','The contract does not guarantee volumes and guarantees revenue.'),
    ('retail','The agreement guarantees revenue but does not guarantee profits.'),
])
def test_unsupported_affirmative_guarantees_remain_flagged(industry,text):
    assert flagged(text)

@pytest.mark.parametrize('industry,text',[
    ('healthcare','Because payer contracts lack volume commitments, revenue realization remains exposed to changing customer channels.'),
    ('manufacturing','The supply agreement does not guarantee minimum purchase volumes.'),
    ('saas','The subscription contracts do not guarantee renewal revenue.'),
    ('retail','The pricing agreement provides no guaranteed minimum purchases.'),
    ('manufacturing','No contract guarantees volumes or revenue.'),
    ('saas',"The subscription agreement doesn't guarantee renewal revenue."),
    ('retail','Contracts without any guaranteed revenue leave the retailer exposed to weak demand.'),
    ('manufacturing','The contract lacks guaranteed minimum purchases, so idle capacity could reduce margins.'),
    ('saas','The absence of contractually guaranteed revenue could expose fixed support costs.'),
    ('retail','The contract guarantees no minimum purchase volumes.'),
])
def test_explicit_denials_and_qualified_absence_inferences_are_not_guarantees(industry,text):
    assert not flagged(text)

@pytest.mark.parametrize('industry,text',[
    ('healthcare','It is unclear whether the payer agreement guarantees volumes.'),
    ('manufacturing','The evidence does not establish that the supply contract guarantees minimum purchases.'),
    ('saas','The subscription agreement may guarantee revenue, but the terms are unverified.'),
    ('retail','Whether the contract guarantees minimum purchases is not established.'),
])
def test_uncertainty_does_not_become_an_affirmative_guarantee(industry,text):
    assert not flagged(text)

@pytest.mark.parametrize('text',[
    'Contracts guarantee volumes and the company cannot service its debt.',
    'Revenue per unit increases demonstrate pricing power.',
    'Narrow networks establish high switching costs.',
    'The loss of senior management creates high key-person dependency.',
    'Essential services are recession-proof.',
    'An acquisition plan demonstrates disciplined capital allocation.',
    'An implicit price concession means a cash loss.',
    'No contract guarantees volumes; revenues are exposed to demand changes.',
])
def test_unrelated_rule_predicates_are_unchanged(text):
    for rule in inference_rules.RULES:
        if rule.rule_id==RULE:continue
        expected=all(re.search(t,text,re.I|re.S) for t in rule.triggers) and not any(re.search(e,text,re.I|re.S) for e in rule.exempt)
        assert bool(rule.hit(text))==bool(expected)

@pytest.mark.parametrize('claim',[
    'The contracts guarantee minimum revenue.',
    'The contracts do not guarantee minimum revenue.',
])
def test_facts_without_verified_quotes_still_fail_evidence_requirements(claim):
    result=HypothesisValidator(BO_SPEC_BY_CODE['BO1'],passages={},indicators=IndicatorSet()).validate(
        {'parameters':[],'hypotheses':[{'hypothesis_id':'BO1-H001','claim':claim,'claim_type':'fact',
          'direction':'neutral','disposition':'supported','supporting_evidence_ids':[],'evidence_quotes':[]}]},
        analysis_run_id='test',agent_run_id='test')
    assert result.records[0].disposition.value=='unverified'
    assert result.records[0].admissibility.value=='audit_only'


@pytest.mark.parametrize('claim',[
    'Service agreements do not guarantee customer volumes.',
    'Supply contracts do not guarantee minimum purchase volumes.',
    'Subscription contracts do not guarantee renewal revenue.',
    'Distribution agreements provide no guaranteed minimum purchases.',
])
def test_sourced_negated_facts_retain_their_evidence_classification(claim):
    result=HypothesisValidator(BO_SPEC_BY_CODE['BO1'],passages={'e':claim},indicators=IndicatorSet()).validate(
        {'parameters':[],'hypotheses':[{'hypothesis_id':'BO1-H001','claim':claim,'claim_type':'fact',
          'direction':'neutral','disposition':'supported','supporting_evidence_ids':['e'],
          'evidence_quotes':[{'evidence_id':'e','quote':claim}]}]},analysis_run_id='test',agent_run_id='test')
    assert result.records[0].disposition.value=='supported'
    assert result.records[0].admissibility.value=='established'


def test_qualified_absence_inference_remains_qualified_when_sourced():
    quote='The supply contracts do not guarantee minimum purchase volumes.'
    result=HypothesisValidator(BO_SPEC_BY_CODE['BO1'],passages={'e':quote},indicators=IndicatorSet()).validate(
        {'parameters':[],'hypotheses':[{'hypothesis_id':'BO1-H001',
          'claim':'Contracts lack volume commitments, revenue could fall while production costs remain fixed.',
          'claim_type':'inference','direction':'negative','disposition':'qualified',
          'supporting_evidence_ids':['e'],'evidence_quotes':[{'evidence_id':'e','quote':quote}]}]},
        analysis_run_id='test',agent_run_id='test')
    assert result.records[0].disposition.value=='qualified'
    assert result.records[0].admissibility.value=='qualified_narrative'
