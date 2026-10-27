"""Dedicated observation/citation references for BO and the available partial memo.

No legacy hypotheses, scores or verified corporate relationships are fabricated.
The current renderer uses a deterministic supported statement, not free-form
paraphrases that could introduce unsupported severity or competence claims.
"""
from .contracts import validate_management


def management_claims(components):
    item = (components or {}).get('management') or {}
    artifact = item.get('artifact')
    if not artifact or item.get('validation_status') not in ('passed', 'partial'):
        return []
    validate_management(artifact)
    return [{'observation_id': o['observation_id'], 'executive': o['executive'],
             'direction': o['direction'], 'category': o['category'], 'text': o['text'],
             'citation_ids': list(o['citation_ids'])} for o in artifact['observations']]


def validate_claims(claims, components):
    allowed = {c['observation_id']: c for c in management_claims(components)}
    seen = set()
    for claim in claims:
        if claim['observation_id'] in seen or allowed.get(claim['observation_id']) != claim:
            raise ValueError('Unsupported management narrative or unresolved observation/citation reference')
        seen.add(claim['observation_id'])
    return claims
