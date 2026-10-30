"""Agent 6 request eligibility; ranking/admission remain the shared retriever's job."""

import re
import unicodedata

from app.services.bo.plans import PURPOSES


def _term_key(term):
    # Query terms support regexes: never case-fold escapes such as \S into \s.
    return term.strip() if "\\" in term else " ".join(term.split()).casefold()


def eligible_requests(requests, hypothesis_ids, budget):
    """Bound and deduplicate searches without merging opposing purposes or claims.

    Case/whitespace variations and term sets with >=80% overlap are duplicates.
    Every original request gets an audit outcome, including requests over budget.
    """
    accepted, outcomes = [], []
    for request in requests:
        reason = None
        if not isinstance(request, dict):
            outcomes.append({"request": request, "outcome": "invalid_structure"})
            continue
        purpose, hid = request.get("purpose"), request.get("hypothesis_id")
        terms = request.get("terms")
        if purpose not in PURPOSES or not isinstance(terms, list) or not terms or any(
                not isinstance(t, str) or not t.strip() for t in terms):
            reason = "invalid_structure"
        elif hid is not None and (not isinstance(hid, str) or hid not in hypothesis_ids):
            reason = "unknown_hypothesis"
        else:
            unique = {}
            for term in terms:
                unique.setdefault(_term_key(term), term.strip())
            terms = list(unique.values())
            terms = terms[:max(0, budget.max_followup_terms)]
            normalized = {"purpose": purpose, "hypothesis_id": hid, "terms": terms}
            if not terms:
                reason = "term_budget_exhausted"
            elif any(
                a["purpose"] == purpose and a["hypothesis_id"] == hid
                and len({_term_key(t) for t in a["terms"]} & {_term_key(t) for t in terms})
                / len({_term_key(t) for t in a["terms"]} | {_term_key(t) for t in terms}) >= .8
                for a in accepted
            ):
                reason = "duplicate"
            elif len(accepted) >= max(0, budget.max_followup_requests):
                reason = "request_budget_exhausted"
            else:
                accepted.append(normalized)
        outcomes.append({"request": request, "outcome": reason or "eligible"})
    return accepted, outcomes


# Generic question wording only. Query vocabulary comes exclusively from the
# gap; these rules never add synonyms, company names or financial assertions.
_STOP = set("""a an the of to for and or in on from by via with is are be has have
would could that this those their its our information detail details specific
specifically direct quantitative quantifiable attribution attributable proportion
percentage next within months month years year whether needed please provide
disclosed reported additional evidence missing about existing major new more
regarding particular any data figures metrics as at which what how when will
specify precisely including achieved""".split())
_VAGUE = set("company business risk risks performance financial operational credit interaction assessment outcome outcomes result results summary initiative program capability existence".split())
_WORD = re.compile(r"[^\W_]+(?:[-’'][^\W_]+)*", re.UNICODE)
_ENTITY = re.compile(r"\b[A-Z][\w&’'.-]*(?:\s+(?:(?:of|the|and|for)\s+)?[A-Z][\w&’'.-]*)+")
_UNAVAILABLE = re.compile(
    r"\b(?:confidential|undisclosed|non[- ]public|not publicly available|"
    r"not separately disclosed|not disclosed|predict(?:ion|ions)?|actual future)\b|"
    r"\bcompetitors?'?\b.{0,50}\b(?:internal|private)\b", re.I)
_EFFECT = re.compile(r"effectiv|outcome|saving|performance|benefit|impact|attribut|result|achiev|realis|realiz", re.I)
_NUMERIC = re.compile(r"\d|\b(?:dollars?|percent|million|billion)\b", re.I)
_NEEDS_NUMBER = re.compile(r"quantit|quantif|percentage|proportion|how much|amount|revenue|saving|schedule|timetable|volume", re.I)
_CAUSAL = re.compile(r"attribut|causal|specifically.*(?:due|achiev)|separately", re.I)
_NEGATED = re.compile(r"\b(?:no|not|cannot|unavailable|uncertain|unproven)\b", re.I)
_PRIORITY = set("cost costs savings renewal renewals schedule revenue volumes volume outcomes performance effectiveness contract contracts results".split())


def _normal(text):
    return " ".join(unicodedata.normalize("NFKC", text).split())


def _stem(word):
    word = word.casefold()
    if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "is")):
        return word[:-1]
    return word


def _concepts(text):
    return {_stem(m.group()) for m in _WORD.finditer(text)
            if m.group().casefold() not in _STOP and not m.group().isdigit()}


def _terms(text, limit):
    """Literal phrases: named entities, useful adjacent pairs, then subjects."""
    if limit <= 0:
        return []
    words = list(_WORD.finditer(text))
    entities = [m for m in _ENTITY.finditer(text)
                if len(_concepts(m.group())) >= 2 and len(m.group()) <= 120]
    candidates = [m.group() for m in entities]
    pairs = []
    for left, right in zip(words, words[1:]):
        if (left.group().casefold() in _STOP or right.group().casefold() in _STOP
                or left.group().isdigit() or right.group().isdigit()):
            continue
        if any(e.start() <= left.start() and e.end() >= right.end() for e in entities):
            continue
        phrase = text[left.start():right.end()]
        if len(phrase) <= 120:
            score = sum(w.group().casefold() in _PRIORITY for w in (left, right))
            pairs.append((-score, left.start(), phrase))
    candidates += [p for _, _, p in sorted(pairs)]
    if not candidates:
        candidates = [m.group() for m in words if m.group().casefold() not in _STOP
                      and not m.group().isdigit() and len(m.group()) >= 4]
    # Escaping is mandatory: gap text is data, never a model-authored regex.
    return list(dict.fromkeys(re.escape(c) for c in candidates))[:limit]


def _record_dict(record):
    return record if isinstance(record, dict) else record.model_dump(mode="json")


def _material(record):
    return (record.get("materiality") in {"medium", "high"}
            and record.get("admissibility") != "audit_only"
            and record.get("disposition") not in {"rejected", "unverified"})


def _purpose(text, record):
    kind = record.get("interaction_kind")
    if kind == "mitigating_capability":
        return "mitigant_challenge" if _EFFECT.search(text) else "mitigant"
    if kind == "compound_risk":
        return "contradict" if re.search(r"counterevidence|contradict|refut|disprov|challeng", text, re.I) else "support"
    return None


def _same_need(left, right):
    """Conservative lexical overlap, not a claim of semantic equivalence."""
    shared = left & right
    return bool(left and right) and (
        left == right or (len(shared) >= 3 and len(shared) / min(len(left), len(right)) >= .6))


def bridge_requests(parsed, records, packet_passages, budget):
    """Merge explicit requests and explainable gap-derived needs, explicit first.

    Returns accepted requests, request audits, and an audit for EVERY original
    gap entry. Global gaps remain global; matched records justify materiality
    and purpose only. Neither lexical matches nor omitted final gaps prove
    resolution, so the deterministic bridge always retains unresolved status.
    """
    records = [_record_dict(r) for r in records]
    by_id = {r["hypothesis_id"]: r for r in records}
    accepted, audits = eligible_requests(parsed.get("retrieval_requests") or [], set(by_id), budget)
    query_index = 0
    for audit in audits:
        audit["request_origin"] = "agent_explicit"
        if audit["outcome"] == "eligible":
            audit["query_index"] = query_index
            query_index += 1
    signatures = [_concepts(" ".join(r["terms"]).replace("\\", "")) for r in accepted]
    gaps = []
    sources = [("hypothesis_level", r.get("hypothesis_id"), r.get("missing_information"), r) for r in records]
    sources.append(("response_level", None, parsed.get("missing_information"), None))
    for source, hid, entries, owner in sources:
        if entries is None:
            continue
        malformed_container = not isinstance(entries, list)
        for original in entries if isinstance(entries, list) else [entries]:
            gap = {"gap_id": f"a6_gap_{len(gaps) + 1}", "original_gap": original,
                   "source": source, "hypothesis_id": hid, "eligible": False,
                   "outcome": "invalid_gap", "reason": "Expected a nonblank string in a gap list.",
                   "terms": [], "purpose": None, "deduplication": "not_applicable",
                   "query_index": None, "attempt_ids": [], "retrieved_evidence_ids": [],
                   "newly_admitted_evidence_ids": [], "new_evidence_admitted": False,
                   "reconsideration_occurred": False, "unresolved": True,
                   "resolution": "not_deterministically_established"}
            gaps.append(gap)
            if malformed_container or not isinstance(original, str) or not original.strip():
                continue
            text = _normal(original)
            gap["normalized_gap"] = text
            concepts = _concepts(text)
            def reject(outcome, reason):
                gap.update(outcome=outcome, reason=reason)
            if len(text) > 2000 or len(concepts) < 3 or len(concepts - _VAGUE) < 2:
                reject("not_specific", "Too broad, too few subject concepts, or excessive text length.")
                continue
            if _UNAVAILABLE.search(text):
                reject("unavailable", "Explicitly confidential, undisclosed, non-public or predictive information.")
                continue
            relevant = [owner] if owner is not None and _material(owner) else []
            if owner is None:
                scored = []
                for record in records:
                    context = " ".join([record.get("claim") or "", record.get("transmission_mechanism") or "",
                        (record.get("mitigant") or {}).get("capability") or "",
                        *[x for x in record.get("missing_information") or [] if isinstance(x, str)]])
                    overlap = len(concepts & _concepts(context))
                    if _material(record) and overlap >= 2:
                        scored.append((overlap, record))
                # A weak two-word co-mention must not obscure a substantially
                # more specific material context. Ties keep all roles and can
                # still reject an ambiguous purpose; global IDs stay null.
                strongest = max((score for score, _ in scored), default=0)
                relevant = [r for score, r in scored if score == strongest]
            if not relevant:
                reject("no_material_relationship", "No sufficiently material validated interaction matches this gap.")
                continue
            gap["materiality_basis_ids"] = [r["hypothesis_id"] for r in relevant]
            purposes = {_purpose(text, r) for r in relevant}
            if len(purposes) != 1 or None in purposes:
                reject("ambiguous_purpose", "No single supported retrieval purpose can be assigned safely.")
                continue
            gap["purpose"] = purpose = purposes.pop()
            gap["terms"] = terms = _terms(text, max(0, budget.max_followup_terms))
            if not terms or len(_concepts(" ".join(terms).replace("\\", ""))) < 2:
                reject("not_searchable", "No bounded meaningful lexical query could be constructed.")
                continue
            # Avoid redundant searches only with a strong same-passage match.
            # Causal attribution is deliberately NEVER inferred from co-mentions.
            represented = []
            if not _CAUSAL.search(text):
                for passage in packet_passages:
                    body = passage.get("text") or ""
                    if (concepts <= _concepts(body) and not _NEGATED.search(body)
                            and (not _NEEDS_NUMBER.search(text) or _NUMERIC.search(body))):
                        represented.append(passage["evidence_id"])
            if represented:
                gap["already_admitted_evidence_ids"] = sorted(represented)
                gap["deduplication"] = "initial_packet"
                reject("already_in_packet", "All gap subjects and required numeric marker are already co-located; resolution remains unverified.")
                continue
            duplicate = next((n for n, r in enumerate(accepted)
                              if r["purpose"] == purpose and _same_need(concepts, signatures[n])), None)
            request = {"purpose": purpose, "hypothesis_id": hid, "terms": terms}
            origin = "missing_information_bridge"
            if duplicate is not None:
                gap.update(outcome="duplicate", reason="Overlapping lexical need is already queued at higher priority.",
                           deduplication="queued_request", query_index=duplicate)
                audits.append({"request": request, "outcome": "duplicate", "request_origin": origin,
                               "gap_id": gap["gap_id"], "duplicate_of_query_index": duplicate})
                continue
            combined, decision = eligible_requests([*accepted, request], set(by_id), budget)
            outcome = decision[-1]["outcome"]
            gap.update(outcome=outcome, reason=("Specific material need; a bounded admitted-corpus search is justified."
                       if outcome == "eligible" else f"Existing request eligibility gate: {outcome}."))
            audit = {**decision[-1], "request_origin": origin, "gap_id": gap["gap_id"]}
            if outcome == "eligible":
                gap.update(eligible=True, deduplication="unique", query_index=len(accepted))
                audit["query_index"] = len(accepted)
                accepted = combined
                signatures.append(concepts)
            audits.append(audit)
    return accepted, audits, gaps


def annotate_gap_searches(gaps, searches, new_ids, *, reconsideration_occurred):
    """Attach execution outcomes without declaring a lexical hit to be an answer."""
    for gap in gaps:
        index = gap["query_index"]
        attempts = [searches[index]] if index is not None and index < len(searches) else []
        ids = {r["evidence_id"] for a in attempts for r in a["results"]}
        gap.update(attempt_ids=[a["attempt_id"] for a in attempts],
                   retrieved_evidence_ids=sorted(ids), newly_admitted_evidence_ids=sorted(ids & set(new_ids)),
                   new_evidence_admitted=bool(ids & set(new_ids)),
                   reconsideration_occurred=reconsideration_occurred)
