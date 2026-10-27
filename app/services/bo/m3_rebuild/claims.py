"""M3 claim-level evidence admission; qualitative decisions belong to Validator 7."""
from datetime import date
import re

from .contracts import validate_extraction
from .matching import match_citation, normalized_m3, mapped_text, locate


def key(value):
    return ' '.join(re.findall(r'[a-z0-9]+',normalized_m3(value).casefold()))


class ClaimValidator:
    def __init__(self,sources):
        self.sources=sources
        self.claims=[]
        self.full=''
        self.starts={}
        prior_page=None
        for sid in sources.section_ids:
            page=sources.physical_pages.get(sid)
            if self.full and page!=prior_page:
                self.full+='\n'
            self.starts[sid]=len(self.full)
            self.full+=sources.pages[sid]
            prior_page=page
        text,mapping=mapped_text(self.full,pdf=True)
        beginnings=[]
        for row in sources.expected_roster:
            name=normalized_m3(row['name']).rstrip('.')
            pattern=re.escape(name).replace(r'\ ',r'\s+')+r'\.?\s+(?:was appointed|has served|has been|became)\b'
            matches=list(re.finditer(pattern,text,re.I))
            if len(matches)==1:
                beginnings.append((mapping[matches[0].start()],key(name)))
        beginnings.sort()
        self.biographies={}
        for i,(start,name) in enumerate(beginnings):
            end=beginnings[i+1][0] if i+1<len(beginnings) else len(self.full)
            boundary=re.search(r'^Item\s+\d+[A-Z]?\.',self.full[start:end],re.M|re.I)
            self.biographies[name]=(start,start+boundary.start() if boundary else end)

    def biography(self,name):
        span=self.biographies.get(key(name))
        return normalized_m3(self.full[slice(*span)]) if span else ''

    def citations(self,items,name=''):
        results=[]
        seen=set()
        for item in items:
            signature=(item['source_id'],item['quote'])
            if signature in seen: continue
            seen.add(signature)
            result=match_citation(self.sources,*signature)
            if result['outcome']=='accepted' and name:
                bio=self.biographies.get(key(name))
                start=self.starts.get(item['source_id'],0)+result['span_start']
                end=self.starts.get(item['source_id'],0)+result['span_end']
                if not bio or not (bio[0]<=start and end<=bio[1]):
                    result.update(outcome='unsupported',quotation_check='failed',
                                  reason='Incorrect executive attribution: quotation is outside this executive biography')
            results.append(result)
        return results

    def admit(self,cid,kind,name,statement,items,check=None):
        citations=self.citations(items,name if kind!='identity' else '')
        accepted=[c for c in citations if c['outcome']=='accepted']
        accepted.sort(key=lambda c:(self.starts.get(c['source_id'],0)+c['span_start']))
        text=normalized_m3(''.join(c['original_source_text'] for c in accepted))
        claim={'claim_id':cid,'kind':kind,'executive':name,'statement':statement,
               'outcome':'accepted','reason':'Claim is supported by its declared canonical citations',
               'citation_results':citations,'citations':accepted}
        original=''.join(c['original_source_text'] for c in accepted)
        claim['semantic_matching']={
            'method':'pdf_word_boundaries' if normalized_m3(original)!=mapped_text(original,pdf=False)[0].strip() else 'whitespace_typography',
            'comparison_text':text,'policy':'M3-only; no letter, digit, name, date, title or negation substitutions',
        }
        try:
            if not accepted: raise ValueError('No accurate, correctly attributed declared citation survives')
            if check: check(text,claim)
            if any(c['outcome']=='unsupported' for c in citations):
                claim.update(outcome='qualified',reason='Supported by the remaining declared citations; inaccurate citation(s) excluded')
        except ValueError as exc:
            claim.update(outcome='unsupported',reason=str(exc),citations=[])
        self.claims.append(claim)
        return claim


def assess_claims(payload,sources,reviewer=None):
    from .management import date_interval, date_supported, natural_date, DATE_TEXT, review_window, recency, prior_role_date_pairs, compact_citations
    from .employers import internal_employer

    validate_extraction(payload)
    validator=ClaimValidator(sources)
    expected={key(r['name']):r for r in sources.expected_roster}
    names=[key(p['name']) for p in payload['executives']]
    if len(names)!=len(set(names)): raise ValueError('Duplicate executive in extraction')
    facts_ids=[f['fact_id'] for f in payload['facts']]
    judgment_ids=[j['judgment_id'] for j in payload['judgments']]
    if len(facts_ids)!=len(set(facts_ids)) or len(judgment_ids)!=len(set(judgment_ids)):
        raise ValueError('Duplicate fact or judgment identity')

    def roster_check(text,claim):
        anchor=payload['roster_date']
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',anchor): raise ValueError('Roster date needs disclosed day precision')
        if not date_supported(anchor,text) or not re.search(r'as of.{0,50}(?:executive|officer)',text,re.I):
            raise ValueError('Roster date is not supported by its declared quotation')
        declared=re.findall(r'As\s+of\s+(\w+\s+\d{1,2},?\s+\d{4}),?\s+(?:our\s+)?executive\s+officers',normalized_m3(validator.full),re.I)
        if len(set(declared))!=1 or not date_supported(anchor,declared[0]):
            raise ValueError('Selected section does not establish one consistent roster date')
        if date.fromisoformat(anchor)<date.fromisoformat(sources.filing_period):
            raise ValueError('Roster date precedes selected filing period')
    roster=validator.admit('roster','roster','',payload['roster_quote'],
        [{'source_id':payload['roster_source_id'],'quote':payload['roster_quote']}],roster_check)
    window=review_window(payload['roster_date']) if roster['outcome']!='unsupported' else None
    profiles=[]
    profile_map={}
    for i,raw in enumerate(payload['executives'],1):
        name=raw['name']; prefix=f'executive:{i}'
        def identity_check(text,claim):
            row=expected.get(key(name))
            if row is None: raise ValueError('Executive is not in selected canonical roster')
            if key(name) not in key(text): raise ValueError('Roster quotation does not identify this executive')
        row=expected.get(key(name),{})
        roster_sid=row.get('source_id',payload['roster_source_id'])
        roster_line=next((line for line in sources.pages.get(roster_sid,'').splitlines()
                          if key(name) in key(line) and re.search(r'\b\d{2,3}\b',line)),payload['roster_quote'])
        identity=validator.admit(prefix+':identity','identity',name,name,
            [{'source_id':roster_sid,'quote':roster_line}],identity_check)
        def position_check(text,claim):
            if not raw['position'].strip() or key(raw['position']) not in key(row.get('position','')):
                raise ValueError('Position differs from disclosed roster')
        position=validator.admit(prefix+':position','identity',name,raw['position'],
            [{'source_id':roster_sid,'quote':roster_line}],position_check)
        def appointment_check(text,claim):
            if identity['outcome']=='unsupported': raise ValueError('Executive identity is unsupported')
            scope=re.sub(r'\s+and Director$','',raw['position'],flags=re.I)
            # Director appointment can differ from CEO appointment: never assign
            # the CEO date to both. Preserve submitted title and record date scope.
            clause=re.search(r'(?:was appointed|has served as|became|has been)\b.*?(?=\.\s+[A-Z]|$)',text,re.I)
            if not clause or key(scope) not in key(clause[0]): raise ValueError('Current title is not supported by its appointment clause')
            if not raw['appointment_date']: raise ValueError('Current appointment date is unavailable')
            if not date_supported(raw['appointment_date'],clause[0]): raise ValueError('Appointment date/role is not supported')
            disclosed=re.search(r'\b(?:since|effective|in)\s+('+DATE_TEXT+r')',clause[0],re.I)
            if disclosed and natural_date(disclosed[1])!=raw['appointment_date']:
                raise ValueError('Appointment date changes disclosed value or precision')
            if window: recency(raw['appointment_date'],window)
            claim['appointment_scope']=scope
        appointment=validator.admit(prefix+':appointment','appointment',name,
            raw['position']+'; '+raw['appointment_date'],
            [{'source_id':raw['source_id'],'quote':raw['quote']}]+raw.get('supporting_citations',[]),appointment_check)
        admitted_roles=[]
        for ri,role in enumerate(raw['prior_internal_roles'],1):
            def role_check(text,claim,role=role):
                if identity['outcome']=='unsupported': raise ValueError('Executive identity is unsupported')
                if key(role['position']) not in key(text): raise ValueError('Prior role title differs from quotation')
                for field in ('start_date','end_date'):
                    if not role[field] or not date_supported(role[field],text): raise ValueError('Prior role date lacks support')
                pairs=prior_role_date_pairs(normalized_m3(role['position']),text)
                if pairs and (role['start_date'],role['end_date']) not in pairs:
                    raise ValueError('Prior role dates are not bound to that title at disclosed precision')
                lo,hi=date_interval(role['start_date']),date_interval(role['end_date'])
                if lo[0]>hi[1]: raise ValueError('Prior role dates are reversed')
                if window and hi[0]>date.fromisoformat(window['end']): raise ValueError('Prior role ends after roster date')
                # Employer checks receive only canonical biography context and
                # previously admitted roles, never unvalidated sibling assertions.
                biography=validator.biography(name)
                try:
                    internal_employer({**role,'position':normalized_m3(role['position']),'quote':text},
                        {'quote':biography,'prior_internal_roles':admitted_roles},sources.company)
                except ValueError as exc:
                    if 'ambiguous' not in str(exc): raise
                    from .employers import company_link
                    first=re.split(r'(?<![A-Z])(?<!Mr)(?<!Dr)(?<!Ms)\.\s+(?=[A-Z])',biography,maxsplit=1)[0]
                    boundary=re.search(r'Prior to joining',biography,re.I)
                    location=biography.find(text)
                    entities=re.findall(r'([A-Z][a-z]+ Group) of the Company',biography)
                    internal_entity=any(key(entity) in key(role['position']) for entity in entities)
                    explicit_scope=(company_link(first,sources.company) and boundary and
                                    location>=0 and location<boundary.start())
                    if not (internal_entity or explicit_scope): raise
                    claim['employer_basis']='Explicit same-biography company/entity scope; not inferred from tenure'
                claim['context_evidence_ids']=[sid for sid in sources.section_ids
                    if (validator.starts[sid]<validator.biographies[key(name)][1]
                        and validator.starts[sid]+len(sources.pages[sid])>validator.biographies[key(name)][0])]
            claim=validator.admit(prefix+f':role:{ri}','prior_internal_role',name,
                role['position']+'; '+role['start_date']+' to '+role['end_date'],
                [{'source_id':role['source_id'],'quote':role['quote']}]+role.get('supporting_citations',[]),role_check)
            if claim['outcome']!='unsupported':
                start,end=date_interval(role['start_date']),date_interval(role['end_date'])
                admitted_roles.append({'position':role['position'],'start_date':role['start_date'],
                    'end_date':role['end_date'],'start_precision':start[2],'end_precision':end[2],
                    'source_id':role['source_id'],'quote':role['quote'],
                    'validated_citations':claim['citations']})
        # A concurrently held explicitly attributed company office can validate
        # another role with the same disclosed start date. Recheck only employer
        # ambiguity after independent sibling evidence has itself been admitted.
        for ri,role in enumerate(raw['prior_internal_roles'],1):
            cid=prefix+f':role:{ri}'
            old=next(c for c in validator.claims if c['claim_id']==cid)
            if old['outcome']=='unsupported' and 'employer attribution is ambiguous' in old['reason']:
                try:
                    text=normalized_m3(''.join(c['original_source_text'] for c in old['citation_results'] if c['outcome']=='accepted'))
                    internal_employer({**role,'position':normalized_m3(role['position']),'quote':text},
                        {'quote':validator.biography(name),'prior_internal_roles':admitted_roles},sources.company)
                except ValueError:
                    from .employers import company_link
                    concurrent=any(other['start_date']==role['start_date'] and
                        re.search(r'\balso served\b',normalized_m3(other['quote']),re.I) and
                        company_link(other['quote'],sources.company) for other in admitted_roles)
                    if not concurrent:continue
                old.update(outcome='accepted',reason='Supported role and dates; concurrently held company office establishes employer scope',
                           citations=[c for c in old['citation_results'] if c['outcome']=='accepted'])
                start,end=date_interval(role['start_date']),date_interval(role['end_date'])
                admitted_roles.append({'position':role['position'],'start_date':role['start_date'],'end_date':role['end_date'],
                    'start_precision':start[2],'end_precision':end[2],
                    'source_id':role['source_id'],'quote':role['quote'],'validated_citations':old['citations']})
        external=None
        if raw['external_recruitment_quote'] or raw['external_source_id']:
            def external_check(text,claim):
                if not re.search(r'(?:joined|recruited|hired).{0,120}\bfrom\b',text,re.I):
                    raise ValueError('External recruitment needs an affirmative source statement')
            external=validator.admit(prefix+':external','external_recruitment',name,
                raw['external_recruitment_quote'],[{'source_id':raw['external_source_id'],
                    'quote':raw['external_recruitment_quote']}],external_check)
        if identity['outcome']!='unsupported':
            interval=date_interval(raw['appointment_date']) if appointment['outcome']!='unsupported' else None
            anchor=date.fromisoformat(window['end']) if window else None
            profile={'name':name,'position':raw['position'] if position['outcome']!='unsupported' else expected[key(name)]['position'],
                'roster_position':expected[key(name)]['position'],
                'appointment_date':raw['appointment_date'] if interval else '',
                'appointment_precision':interval[2] if interval else 'unavailable',
                'recency':recency(raw['appointment_date'],window) if interval and window else 'unavailable',
                'prior_internal_roles':admitted_roles,
                'succession':'internal' if admitted_roles else 'confirmed_external' if external and external['outcome']!='unsupported' else 'unavailable',
                'citations':identity['citations']+appointment['citations']+
                    [c for role in admitted_roles for c in role['validated_citations']],
                'semantic_support_check':'passed_bounded_rules',
                'tenure':{'as_of':window['end'] if window else None,
                    'minimum_days':max(0,(anchor-interval[1]).days) if anchor and interval else None,
                    'maximum_days':(anchor-interval[0]).days if anchor and interval else None,
                    'basis':'Calculated from accepted appointment claim at disclosed precision'}}
            profiles.append(profile);profile_map[key(name)]=profile
    facts=[]
    for fact in payload['facts']:
        def fact_check(text,claim,fact=fact):
            if fact['executive'] and key(fact['executive']) not in expected:
                raise ValueError('Fact executive identity is unsupported')
            if not fact['statement'].strip() or locate(text,fact['statement']) is None:
                raise ValueError('Fact statement changes or exceeds its surviving quotations')
        claim=validator.admit(fact['fact_id'],'fact',fact['executive'],fact['statement'],fact['citations'],fact_check)
        if claim['outcome']!='unsupported':
            facts.append({**fact,'citations':claim['citations']})

    ids=[c['claim_id'] for c in validator.claims]
    if len(ids)!=len(set(ids)):raise ValueError('Fact identity collides with a reserved evidence claim identity')
    accepted={c['claim_id']:c for c in validator.claims if c['outcome']!='unsupported'}
    candidates=[]
    for judgment in payload['judgments']:
        candidates.append({**judgment,'remaining_fact_ids':[f for f in judgment['supporting_fact_ids'] if f in accepted],
                           'rejected_fact_ids':[f for f in judgment['supporting_fact_ids'] if f not in accepted]})
    unavailable_review={
        'mode':'not_attempted','decisions':[],
        'overall':{'signal':'insufficient_evidence','text':'Validator 7 qualitative review has not been performed.',
                   'judgment_ids':[],'limitations':['Qualitative reasoning remains unreviewed.']}}
    try:
        review=reviewer.review(payload,validator.claims,sources) if reviewer is not None else unavailable_review
    except (ValueError,KeyError,RuntimeError) as exc:
        review={**unavailable_review,'mode':'failed','error':str(exc)}
    decisions={d['judgment_id']:d for d in review.get('decisions',[])}
    observations=[];judgment_results=[]
    directions={'credit_positive':'positive','credit_negative':'negative','neutral':'neutral','insufficient_evidence':'unavailable'}
    for judgment in candidates:
        decision=decisions.get(judgment['judgment_id'])
        outcome=decision['outcome'] if decision else 'unsupported'
        reason=decision['reason'] if decision else 'Validator 7 has not reviewed this judgment'
        refs=decision['supporting_claim_ids'] if decision else []
        if not set(refs)<=accepted.keys() or not refs:
            outcome='unsupported';reason='No sufficient accepted claim basis supplied by Validator 7'
        if judgment['executive'] and any(key(accepted[r]['executive']) not in ('',key(judgment['executive'])) for r in refs if r in accepted):
            outcome='unsupported';reason='Reviewer attributed another executive evidence to this individual'
        if judgment['executive'] and key(judgment['executive']) not in profile_map:
            outcome='unsupported';reason='Individual judgment lacks an admitted executive identity'
        if decision and outcome=='accepted' and (judgment['rejected_fact_ids'] or decision['reviewed_text']!=judgment['explanation']):
            outcome='qualified';reason+='; original basis/text changed and is explicitly qualified'
        if re.search(r'\btenure\b.{0,45}\b(?:proves?|establishes?|demonstrates?)\b.{0,25}\bcompeten',decision['reviewed_text'] if decision else judgment['explanation'],re.I):
            outcome='unsupported';reason='Tenure alone cannot establish competence'
        result={'judgment_id':judgment['judgment_id'],'outcome':outcome,'reason':reason,
                'original_text':judgment['explanation'],'original_fact_ids':judgment['supporting_fact_ids'],
                'removed_fact_ids':judgment['rejected_fact_ids'],'supporting_claim_ids':refs,
                'reviewed_text':decision['reviewed_text'] if decision else None}
        judgment_results.append(result)
        if outcome!='unsupported':
            observations.append({'observation_id':judgment['judgment_id'],'executive':judgment['executive'] or None,
                'rule':('explicit_review_correction' if any(
                    change['judgment_id']==judgment['judgment_id']
                    for change in review.get('explicit_corrections',{}).get('changes',[]))
                    else 'validator_7_review'),'direction':directions[judgment['signal']],
                'signal':judgment['signal'],'category':judgment['category'],
                'text':decision['reviewed_text'],'supporting_fact_ids':refs,
                'uncertainties':judgment['uncertainties']+decision['limitations'],
                'review_outcome':outcome,'review_reason':reason,
                'citations':[c for ref in refs for c in accepted[ref]['citations']]})
    surviving={o['observation_id'] for o in observations}
    overall=review['overall']
    overall_valid=bool(overall['judgment_ids']) and set(overall['judgment_ids'])<=surviving
    limitations=list(sources.limitations)+payload['overall_assessment']['uncertainties']+payload['overall_assessment']['missing_information']+overall['limitations']+review.get('scope_limitations',[])
    failed=[c for c in validator.claims if c['outcome']=='unsupported']
    if failed:limitations.append(f'{len(failed)} individual evidence claims are unsupported; other supported claims remain usable.')
    if any(j['outcome']!='accepted' for j in judgment_results):limitations.append('Qualified or withheld judgments are identified individually in the review record.')
    if review.get('mode')=='offline_fixture':limitations.append('Validator 7 used a saved/offline reviewer response; this execution does not verify live reviewer model performance.')
    # Evidence completeness is separate from qualitative scope: no seven-person
    # credit-judgment quota. The review reports disclosed/principal/team coverage.
    missing=[r['name'] for k,r in expected.items() if k not in profile_map]
    complete=not failed and not missing and overall_valid and all(j['outcome']=='accepted' for j in judgment_results) and sources.coverage_verifiable
    result={'schema_version':'m3-management-assessment-3.0','artifact_type':'senior_management_assessment',
        'company':sources.company,'filing_period':sources.filing_period,
        'source':{'path':sources.source_path,'sha256':sources.sha256,'section_ids':sources.section_ids,'physical_pages':sources.physical_pages},
        'roster_date':payload['roster_date'] if window else None,'roster_citation':roster['citations'][0] if roster['citations'] else None,
        'review_window':window,'profiles':profiles,'observations':observations,'facts':facts,
        'model_assessment':payload['overall_assessment'],
        'validation':{'claim_results':validator.claims,'judgment_results':judgment_results,
            'rejected_facts':[{'fact_id':c['claim_id'],'detail':c['reason']} for c in failed if c['kind']=='fact'],
            'rejected_judgments':[{'judgment_id':j['judgment_id'],'detail':j['reason']} for j in judgment_results if j['outcome']=='unsupported'],
            'overall_references_valid':overall_valid,'validator_7':review.get('original_review',review),
            'explicit_review_corrections':review.get('explicit_corrections')},
        'coverage':{'expected':len(expected) if sources.coverage_verifiable else None,'admitted':len(profiles),'missing':missing,'verifiable':sources.coverage_verifiable},
        'rejected_profiles':[], 'limitations':limitations,'completion_status':'complete' if complete else 'partial',
        'overall_assessment':{'outcome':directions.get(overall['signal'],overall['signal']) if overall_valid else 'unavailable',
            'information_gaps':bool(limitations),'policy':overall['text'] if overall_valid else 'No overall assessment with valid surviving judgment references.',
            'information_direction':'unavailable' if limitations else 'neutral'}}
    return compact_citations(result)
