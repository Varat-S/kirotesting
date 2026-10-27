"""M3-only reversible text matching. Never search another passage to repair a citation."""
import re
import unicodedata

# Closed vocabulary of ordinary role/operating words; no names, digits or negations.
# Only boundaries are normalized, never letters, dates, numbers or title content.
PDF_WORDS = ('Human','Resources','Physician','Services','Finance','Financial',
             'President','Western','Operations','Administrative','Clinical',
             'Nursing','Executive','Officer','Operating','Division','Group')
TYPOGRAPHY = str.maketrans({'’':"'",'‘':"'",'“':'"','”':'"','–':'-','—':'-',
                          '\u00a0':' ','\u00ad':''})


def mapped_text(text, *, pdf=False):
    chars, locations = [], []
    for index, char in enumerate(text):
        normalized=char if char.isdigit() else unicodedata.normalize('NFKC', char)
        for value in normalized.translate(TYPOGRAPHY):
            chars.append(value); locations.append(index)
    value = ''.join(chars)

    def substitute(pattern, replacement):
        nonlocal value, locations
        out, mapping, cursor = [], [], 0
        for match in re.finditer(pattern, value):
            out.append(value[cursor:match.start()]); mapping.extend(locations[cursor:match.start()])
            replacement_value, indexes = replacement(match)
            out.append(replacement_value); mapping.extend(locations[i] for i in indexes)
            cursor = match.end()
        out.append(value[cursor:]); mapping.extend(locations[cursor:])
        value, locations = ''.join(out), mapping

    if pdf:
        # Delete only a line-end hyphen inside an alphabetic word, with an
        # otherwise exact character sequence. Dates/numeric ranges are excluded.
        safe_hyphen_words={w.lower() for w in PDF_WORDS}|{'financial','operating','management','information','responsibilities'}
        def dehyphenate(match):
            if (match[1]+match[2]).lower() not in safe_hyphen_words:
                return match[0],list(range(match.start(),match.end()))
            return match[1]+match[2],list(range(match.start(1),match.end(1)))+list(range(match.start(2),match.end(2)))
        substitute(r'\b([A-Za-z]+)-[ \t]*\n[ \t]*([a-z]+)\b',dehyphenate)
        for word in PDF_WORDS:
            pattern = r'\b'+r'\s*'.join(map(re.escape, word))+r'(?=\b|[A-Z]|from\b)'
            substitute(pattern, lambda m: (''.join(c for c in m[0] if not c.isspace()),
                [m.start()+i for i,c in enumerate(m[0]) if not c.isspace()]))
        for left, right in [('Physician','Services'),('Finance','from')]:
            substitute(r'\b'+left+right+r'\b', lambda m, left=left: (
                m[0][:len(left)]+' '+m[0][len(left):],
                list(range(m.start(),m.start()+len(left)))+[m.start()+len(left)]+
                list(range(m.start()+len(left),m.end()))))
    substitute(r'\s+', lambda m: (' ',[m.start()]))
    return value, locations


def normalized_m3(text):
    return mapped_text(text, pdf=True)[0].strip()


def locate(text, quote):
    if not quote.strip():
        return None
    for method, pdf in [('whitespace_typography',False),('pdf_word_boundaries',True)]:
        source, mapping=mapped_text(text,pdf=pdf)
        query=mapped_text(quote,pdf=pdf)[0].strip()
        start=source.find(query)
        if start < 0 or not query:
            continue
        if ((query[0].isalnum() and start and source[start-1].isalnum()) or
            (query[-1].isalnum() and start+len(query)<len(source) and source[start+len(query)].isalnum())):
            continue
        lo,hi=mapping[start],mapping[start+len(query)-1]+1
        return {'matching_method':'exact' if text[lo:hi]==quote else method,
                'span_start':lo,'span_end':hi,'original_source_text':text[lo:hi],
                'normalized_quote':query}
    return None


def match_citation(sources, source_id, quote):
    result={'source_id':source_id,'quote':quote,'document_id':sources.document_id,
            'source_sha256':sources.sha256,
            'physical_page':sources.physical_pages.get(source_id,
                int(source_id[5:]) if source_id.startswith('pdf:p') else 1),
            'passage_offset':sources.passage_offsets.get(source_id,0)}
    match=locate(sources.pages.get(source_id,''),quote) if source_id in sources.section_ids else None
    if match:
        result.update(match,quotation_check='passed',outcome='accepted')
        result['page_char_start']=result['passage_offset']+match['span_start']
        result['page_char_end']=result['passage_offset']+match['span_end']
    else:
        alternatives=[sid for sid in sources.section_ids
                      if sid!=source_id and locate(sources.pages[sid],quote)]
        result.update(outcome='unsupported',quotation_check='failed',
                      reason='Wrong passage reference' if alternatives else 'Quotation absent from declared passage',
                      found_elsewhere_ids=alternatives)
    return result
