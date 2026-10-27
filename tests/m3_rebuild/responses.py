"""Explicit offline model judgments for contract tests; never production policy."""
from copy import deepcopy


def with_credit_response(payload, *, recent_names=(), external=False):
    result = deepcopy(payload)
    result['facts'], result['judgments'] = [], []
    for index, executive in enumerate(result['executives'], 1):
        name = executive['name']
        fid = f'fact:{index}'
        result['facts'].append({
            'fact_id': fid, 'executive': name, 'statement': executive['quote'],
            'citations': [{'source_id': executive['source_id'], 'quote': executive['quote']}],
        })
        signals = []
        if name in recent_names:
            signals.append(('credit_negative', 'transition', 'A',
                'Recent significant appointment creates potential transition uncertainty; it does not establish a departure, repeated turnover or disruption.'))
        if external:
            signals.append(('credit_negative', 'integration', 'B',
                'Confirmed external recruitment entails potential company-specific integration risk.'))
        if executive['prior_internal_roles']:
            signals.append(('credit_positive', 'continuity', 'E',
                'Supported internal experience mitigates potential transition uncertainty.'))
        elif not external:
            signals.append(('insufficient_evidence', 'information_gap', 'history_gap',
                'Prior internal experience is unavailable; omitted history does not establish external recruitment.'))
        for signal, category, rule, text in signals:
            result['judgments'].append({
                'judgment_id': f'{rule}:{index}', 'executive': name,
                'signal': signal, 'category': category, 'supporting_fact_ids': [fid],
                'explanation': text, 'uncertainties': ['Biography does not demonstrate realized financial effects.'],
            })
    directions = {j['signal'] for j in result['judgments']}
    result['overall_assessment'] = {
        'signal': 'mixed' if {'credit_positive', 'credit_negative'} <= directions
                  else 'credit_positive' if 'credit_positive' in directions
                  else 'credit_negative' if 'credit_negative' in directions
                  else 'insufficient_evidence',
        'explanation': 'Offline authored assessment: continuity benefits and potential transition uncertainty require outcome evidence.',
        'supporting_judgment_ids': [j['judgment_id'] for j in result['judgments']],
        'uncertainties': [], 'missing_information': ['Prior internal experience'] if 'insufficient_evidence' in directions else [],
    }
    return result
