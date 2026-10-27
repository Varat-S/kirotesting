"""Explicit M3 review corrections; never modify factual admission or raw model output."""
from copy import deepcopy
import jsonschema
from app.core.hashing import content_hash


def review_content(review):
    return {k: review[k] for k in ('decisions', 'overall', 'principal_executives', 'scope_limitations')}


def apply_corrections(review, payload, claims, correction):
    from .validator7 import REVIEW_SCHEMA
    if correction['original_judgments_sha256'] != content_hash(payload['judgments']):
        raise ValueError('Review correction does not match the original judgments')
    if correction['original_review_sha256'] != content_hash(review_content(review)):
        raise ValueError('Review correction does not match the original reviewer response')
    if not correction['author'].strip() or not correction['reason'].strip():
        raise ValueError('Explicit review correction needs author and reason')
    effective = deepcopy(review)
    admitted = {c['claim_id'] for c in claims if c['outcome'] in ('accepted', 'qualified') and c['citations']}
    decisions = {d['judgment_id']: d for d in effective['decisions']}
    originals = {j['judgment_id']: j for j in payload['judgments']}
    seen = set()
    changes = []
    for replacement in correction['decisions']:
        jid = replacement['judgment_id']
        if jid in seen or jid not in decisions:
            raise ValueError('Unknown or duplicate corrected judgment')
        seen.add(jid)
        if replacement['outcome'] not in ('qualified', 'unsupported'):
            raise ValueError('Corrections may qualify or withhold judgments, not declare acceptance')
        if not replacement['reason'].strip():
            raise ValueError('Correction needs a specific reason')
        refs = replacement['supporting_claim_ids']
        if not set(refs) <= admitted or (replacement['outcome'] == 'qualified' and not refs):
            raise ValueError('Correction cannot rehabilitate unsupported evidence claims')
        if replacement['outcome'] == 'qualified' and (not replacement['reviewed_text'].strip() or
                replacement['reviewed_text'] == originals[jid]['explanation']):
            raise ValueError('Qualification must supply separate narrower wording')
        changes.append({'judgment_id': jid, 'original_specialist_text': originals[jid]['explanation'],
                        'original_reviewer_decision': deepcopy(decisions[jid]),
                        'correction': deepcopy(replacement)})
        decisions[jid] = deepcopy(replacement)
        decisions[jid]['reason'] = 'Explicit review correction: ' + replacement['reason']
    if not changes:
        raise ValueError('Empty review correction')
    effective['decisions'] = [decisions[d['judgment_id']] for d in review['decisions']]
    effective['overall'] = deepcopy(correction['overall'])
    surviving = {d['judgment_id'] for d in effective['decisions'] if d['outcome'] != 'unsupported'}
    if not effective['overall']['judgment_ids'] or not set(effective['overall']['judgment_ids']) <= surviving:
        raise ValueError('Corrected overall assessment must use surviving judgments')
    try:
        jsonschema.Draft202012Validator(REVIEW_SCHEMA).validate(review_content(effective))
    except jsonschema.ValidationError as exc:
        raise ValueError('Invalid explicit review correction contract') from exc
    effective['original_review'] = deepcopy(review)
    effective['explicit_corrections'] = {
        'origin': 'explicit_review_correction', 'author': correction['author'],
        'reason': correction['reason'], 'correction_sha256': content_hash(correction),
        'source_sha256': correction['source_sha256'],
        'original_judgments_sha256': correction['original_judgments_sha256'],
        'original_review_sha256': correction['original_review_sha256'],
        'changes': changes, 'original_overall': deepcopy(review['overall']),
        'corrected_overall': deepcopy(effective['overall']),
        'limitation': 'Authored corrections, not live reviewer decisions; factual admission is unchanged.'}
    return effective
