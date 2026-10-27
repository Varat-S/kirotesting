"""Offline reviewer responses for plumbing tests, not a qualitative performance evaluator."""
import json
from app.services.llm.client import LLMBackend, LLMRawResult


def response(payload,claims):
    allowed={c['claim_id'] for c in claims if c['outcome']!='unsupported'}
    decisions=[]
    for judgment in payload['judgments']:
        refs=judgment['supporting_fact_ids']
        accepted=bool(refs) and set(refs)<=allowed
        decisions.append({'judgment_id':judgment['judgment_id'],
            'outcome':'accepted' if accepted else 'unsupported',
            'reason':'Explicit offline reviewer fixture: declared facts support the bounded test interpretation.' if accepted else 'Fixture declines judgment with missing declared facts.',
            'supporting_claim_ids':refs if accepted else [],
            'reviewed_text':judgment['explanation'] if accepted else '',
            'limitations':[]})
    ids=[d['judgment_id'] for d in decisions if d['outcome']!='unsupported']
    return {'decisions':decisions,'overall':{
        'signal':payload['overall_assessment']['signal'] if ids else 'insufficient_evidence',
        'text':payload['overall_assessment']['explanation'],
        'judgment_ids':payload['overall_assessment']['supporting_judgment_ids'] if ids else [],
        'limitations':[]},
        'principal_executives':list(dict.fromkeys(j['executive'] for j in payload['judgments'] if j['executive'])),
        'scope_limitations':['Offline fixture; qualitative model performance not tested.']}


class FixtureReviewer:
    def review(self,payload,claims,sources):
        return {**response(payload,claims),'mode':'offline_fixture'}


class FixtureReviewerBackend(LLMBackend):
    def generate(self,request):
        assert request.prompt.name=='m3_validator_7'
        inputs=request.inputs
        payload={'judgments':inputs['original_judgments'],
                 'overall_assessment':inputs['original_overall_assessment']}
        return LLMRawResult(json.dumps(response(payload,inputs['claim_validation'])),
                            'offline-validator7-fixture',{'backend':'fake'})
