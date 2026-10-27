"""Readable claim/citation comparison of the untouched M3 answer and M4 replay."""
import argparse
from collections import Counter
from hashlib import sha256
from html import escape
import json
from pathlib import Path

from .workflow import write_json


def compare(before_path,after_path,raw_path,output):
    before=json.loads(Path(before_path).read_text())
    after=json.loads(Path(after_path).read_text())
    replay=Path(after_path).parent/'management_response.txt'
    if replay.read_bytes()!=Path(raw_path).read_bytes():
        raise ValueError('Comparison refuses a changed specialist response')
    prior={p['name']:p for p in before.get('rejected_profiles',[])}
    facts={f['fact_id']:f for f in before['validation'].get('rejected_facts',[])}
    rows=[]
    judgments=after['validation']['judgment_results']
    for claim in after['validation']['claim_results']:
        old=prior.get(claim['executive'])
        before_status='Not individually checked; whole profile withheld' if old else 'Source/claim not separately reported'
        before_reason=old['detail'] if old else 'Milestone 3 had no independent claim outcome'
        if claim['claim_id'] in facts:
            before_status='Withheld by profile dependency';before_reason=facts[claim['claim_id']]['detail']
        bad=[c for c in claim['citation_results'] if c['outcome']=='unsupported']
        if bad and claim['outcome']=='unsupported':
            classification='Real citation/attribution failure remains rejected'
        elif bad:
            classification='Bad citation remains rejected; remaining explicit citations support a qualified claim'
        elif claim['outcome']=='unsupported':
            classification='Factual/role support failure remains rejected'
        elif old and claim['semantic_matching']['method']=='pdf_word_boundaries':
            classification='Previous profile/formatting false rejection corrected with recorded normalization'
        elif old:
            classification='Independently supported claim recovered from previous whole-profile cascade'
        else:
            classification='Independently validated source claim'
        dependents=[j['judgment_id'] for j in judgments
            if claim['claim_id'] in j['original_fact_ids'] or claim['claim_id'] in j['supporting_claim_ids']]
        rows.append({**claim,'before_outcome':before_status,'before_reason':before_reason,
                     'classification':classification,'dependent_judgments':dependents})
    report={'raw_sha256':sha256(Path(raw_path).read_bytes()).hexdigest(),'raw_unchanged':True,
            'before_admitted_profiles':len(before['profiles']),'after_admitted_profiles':len(after['profiles']),
            'claims':rows,'outcomes':dict(Counter(c['outcome'] for c in rows)),
            'judgments':judgments,'overall_assessment':after['overall_assessment'],
            'limitations':after['limitations'],'review_mode':after['validation']['validator_7']['mode'],
            'live_validator7_performance_verified':False}
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    write_json(output/'comparison.json',report)
    def e(value):return escape(str(value))
    html=['<!doctype html><html><head><meta charset="utf-8"><title>M3 Milestone 4 comparison</title>',
          '<style>body{font:15px system-ui;max-width:1500px;margin:30px auto;padding:20px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:8px;vertical-align:top}th{background:#eef}pre{white-space:pre-wrap;overflow-wrap:anywhere}details{margin:8px 0}.unsupported{background:#fff0f0}.qualified{background:#fff9e6}.accepted{background:#effaef}</style></head><body>',
          '<h1>Unchanged HCA FY2024 response: claim-level evidence and Validator 7</h1>',
          f'<p>Before: {len(before["profiles"])} profiles admitted. After: {len(after["profiles"])} roster identities/current facts preserved. Claim outcomes: {e(report["outcomes"])}.</p>',
          f'<p>Original PDF document SHA-256: <code>{e(after["source"]["sha256"])}</code>. Raw response SHA-256: <code>{e(report["raw_sha256"])}</code>; replay bytes unchanged.</p>',
          '<p>Validator 7 used an explicitly authored offline fixture. No paid calls; live reviewer performance is not verified. Character spans refer to the captured original page text, not a rewritten quotation.</p>',
          '<h2>Resulting management assessment</h2><p>'+e(after['overall_assessment']['policy'])+'</p>',
          '<h2>Every evidence claim</h2><table><tr><th>Claim / original statement</th><th>Before</th><th>After / reason</th><th>Citations and original locations</th><th>Effect on judgments</th></tr>']
    for row in rows:
        citations=[]
        for c in row['citation_results']:
            location=f"Document {c.get('document_id')}; PDF page {c['physical_page']}; passage {c['source_id']}; passage offset {c.get('passage_offset')}; page characters [{c.get('page_char_start')}, {c.get('page_char_end')})"
            citations.append('<details><summary>'+e(c['outcome']+' — '+c.get('matching_method',c.get('reason','unmatched')))+'</summary><p>'+e(location)+'</p><b>Submitted quote</b><pre>'+e(c['quote'])+'</pre><b>Original matched text</b><pre>'+e(c.get('original_source_text','No match in the declared passage.'))+'</pre></details>')
        html.append('<tr class="'+row['outcome']+'"><td><b>'+e(row['claim_id'])+'</b><br>'+e(row['executive'])+'<br>'+e(row['statement'])+'</td><td>'+e(row['before_outcome'])+'<br>'+e(row['before_reason'])+'</td><td><b>'+e(row['outcome'])+'</b><br>'+e(row['reason'])+'<br>'+e(row['classification'])+'<br>Semantic matching: '+e(row['semantic_matching']['method'])+'</td><td>'+''.join(citations)+'</td><td>'+e(', '.join(row['dependent_judgments']) or 'No direct declared/reviewed dependency; see qualitative review scope.')+'</td></tr>')
    html.append('</table><h2>Every qualitative judgment</h2>')
    for judgment in judgments:
        html.append('<h3>'+e(judgment['judgment_id'])+' — '+e(judgment['outcome'])+'</h3><b>Original</b><p>'+e(judgment['original_text'])+'</p><b>Reviewed wording (separate from original answer)</b><p>'+e(judgment['reviewed_text'])+'</p><p>'+e(judgment['reason'])+'</p><p>Accepted basis: '+e(judgment['supporting_claim_ids'])+'; removed original facts: '+e(judgment['removed_fact_ids'])+'</p>')
    html.append('<h2>Limitations</h2><ul>'+''.join('<li>'+e(x)+'</li>' for x in after['limitations'])+'</ul></body></html>')
    (output/'comparison.html').write_text('\n'.join(html),encoding='utf-8')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('before','after','raw','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();r=compare(a.before,a.after,a.raw,a.output)
    print(json.dumps({k:v for k,v in r.items() if k not in ('claims','judgments','limitations')},indent=2))


if __name__=='__main__':main()
