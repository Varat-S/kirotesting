"""Targeted, bounded recovery of a quotation that failed verification.

When a quote is not found verbatim in the passage the agent cited, one recovery
attempt is made per quote, entirely over the ADMITTED filing corpus and without
calling a model:

1. ``layout_gap`` — the cited passage contains every word of the quote, in
   order, with a short run of source-layout text in between (a page number or a
   running header that the filing's HTML places mid-sentence).
2. ``relocated`` — the quote is verbatim (or verbatim with a layout gap) in a
   different admitted passage of the same filing, or straddles two consecutive
   passages.

A recovered quote is replaced by the SOURCE text of the matched span, never by
invented wording, and the hypothesis text is left untouched. Anything else is
``unresolved``: the quote is discarded and the normal rules then decide whether
the hypothesis can still stand. Every attempt is returned as an audit record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MIN_TOKENS = 6  # tolerant matching needs a quote long enough to be distinctive
MAX_GAP_TOKENS = 14  # per gap: a page number or a short running header
MAX_GAPS = 2
RETRY_LIMIT = 1  # recovery attempts per quote

_TRANSLATE = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ", "�": "'",
})


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").translate(_TRANSLATE)).strip()


def _tokens(text: str) -> list[str]:
    return _normalise(text).split(" ")


@dataclass(frozen=True)
class Match:
    source_text: str  # the passage's own words covering the quote
    gaps: int
    skipped: tuple[str, ...]  # the layout text that sat inside the quote


def tolerant_match(quote: str, passage_text: str) -> Match | None:
    """Find the quote's words in order, allowing a few short layout gaps."""
    wanted = _tokens(quote)
    if len(wanted) < MIN_TOKENS:
        return None
    source = _tokens(passage_text)
    starts = [i for i, token in enumerate(source) if token == wanted[0]]
    for start in starts:
        position, gaps, skipped = start, 0, []
        ok = True
        for token in wanted[1:]:
            position += 1
            run = 0
            while position < len(source) and source[position] != token:
                position += 1
                run += 1
                if run > MAX_GAP_TOKENS:
                    break
            if position >= len(source) or run > MAX_GAP_TOKENS:
                ok = False
                break
            if run:
                gaps += 1
                skipped.append(" ".join(source[position - run:position]))
                if gaps > MAX_GAPS:
                    ok = False
                    break
        if ok:
            return Match(" ".join(source[start:position + 1]), gaps, tuple(skipped))
    return None


def _retrieval_terms(quote: str) -> list[str]:
    """The narrowly scoped request: the quote's most distinctive words."""
    words = re.findall(r"[A-Za-z][A-Za-z-]{4,}|\d+(?:\.\d+)?%?", _normalise(quote))
    seen: list[str] = []
    for word in sorted(words, key=lambda w: (-len(w), w)):
        if word.lower() not in (s.lower() for s in seen):
            seen.append(word)
    return seen[:6]


def recover_quote(
    *, quote: str, cited_evidence_id: str | None,
    corpus: list[dict[str, Any]],
) -> dict[str, Any]:
    """One bounded recovery attempt; returns the audit record with the outcome.

    ``corpus`` is the admitted narrative of the filing in document order, each
    item carrying ``evidence_id``, ``text``, ``section``, ``location`` and
    ``sec_accession``.
    """
    by_id = {p["evidence_id"]: p for p in corpus}
    cited = by_id.get(cited_evidence_id)
    record: dict[str, Any] = {
        "original_quote": _normalise(quote),
        "cited_evidence_id": cited_evidence_id,
        "failure": "quote_not_verbatim_in_cited_passage",
        "retrieval_request": {"scope": "same filing, admitted passages only",
                              "terms": _retrieval_terms(quote)},
        "attempts": RETRY_LIMIT,
        "candidates": [],
        "outcome": "unresolved",
        "replacement_evidence_ids": [],
        "replacement_quote": None,
        "detail": None,
    }
    needle = _normalise(quote)
    if len(needle) < 12:
        record["detail"] = "Quote too short to recover."
        return record

    def found(match: Match | None, passages, how: str) -> bool:
        if match is None:
            return False
        ids = [p["evidence_id"] for p in passages]
        outcome = "layout_gap" if ids == [cited_evidence_id] else "relocated"
        record.update(
            outcome=outcome, replacement_evidence_ids=ids,
            replacement_quote=match.source_text,
            detail=(how + (f"; source-layout text inside the sentence: "
                           f"{list(match.skipped)}" if match.skipped else "")))
        return True

    # 1. same passage, tolerating layout gaps
    if cited is not None and found(
            tolerant_match(quote, cited.get("text") or ""), [cited],
            "All words of the quote occur in order in the cited passage"):
        return record

    # 2. elsewhere in the same filing: rank candidates by shared request terms
    terms = [t.lower() for t in record["retrieval_request"]["terms"]]
    accession = (cited or {}).get("sec_accession")
    pool = [p for p in corpus if accession is None or p.get("sec_accession") == accession]
    scored = []
    for index, passage in enumerate(pool):
        text = _normalise(passage.get("text") or "").lower()
        score = sum(1 for t in terms if t in text)
        if score:
            scored.append((score, index, passage))
    scored.sort(key=lambda item: (-item[0], item[1]))
    record["candidates"] = [
        {"evidence_id": p["evidence_id"], "section": p.get("section"),
         "location": p.get("location"), "terms_matched": score}
        for score, _, p in scored[:5]]
    for _score, index, passage in scored[:5]:
        text = passage.get("text") or ""
        if needle in _normalise(text):
            record.update(outcome="relocated",
                          replacement_evidence_ids=[passage["evidence_id"]],
                          replacement_quote=needle,
                          detail="Quote is verbatim in a different admitted passage.")
            return record
        if found(tolerant_match(quote, text), [passage],
                 "Quote found in a different admitted passage"):
            return record
        # a sentence that straddles two consecutive passages
        if index + 1 < len(pool):
            nxt = pool[index + 1]
            if nxt.get("document_id") == passage.get("document_id"):
                joined = text + " " + (nxt.get("text") or "")
                if needle in _normalise(joined) or tolerant_match(quote, joined):
                    match = tolerant_match(quote, joined) or Match(needle, 0, ())
                    if found(match, [passage, nxt],
                             "Quote straddles two consecutive admitted passages"):
                        return record
    record["detail"] = ("No admitted passage contains the quoted words; the quote "
                        "was discarded and no replacement was invented.")
    return record
