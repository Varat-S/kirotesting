"""FO Pre-check: covenants (a breach, a waiver, or an amendment giving covenant relief).

Lenders can block revolver draws or demand repayment after a breach. Same three steps as the
going-concern check (``precheck.py``), so the model never decides the outcome on its own word:

1. ``covenant_passages`` (deterministic) searches the debt note, MD&A liquidity, the auditor's report and
   subsequent events for "waiver", "forbearance", "covenant", "breach", "not in compliance", "event of
   default", "cross-default", "amendment" and "relief", and keeps only the paragraphs where the search hits.
   No hit means in compliance (no model call). ``rule_findings`` is the keyword classification: a cross-check
   shown beside the agent's result, never the decision.
2. The Pre-check Agent (prompt ``fo_precheck_covenants``) ALWAYS reads those paragraphs (no agent, or a rejected
   output, refuses the run): the compliance statement,
   waivers or relief amendments in the year or after year-end, how long any waiver lasts, and any disclosed
   covenant headroom, quoting each finding verbatim.
3. ``validate_agent`` keeps only findings whose quote is verbatim in the cited paragraph and says what the
   classification claims, checks every waiver duration against the quote, and RE-DERIVES the outcome.

Outcomes, first match: (a) breach not waived, or MD&A says a breach is reasonably likely within 12 months:
revolver availability = 0 in every measure and the liquidity-and-maturity score = 10, escalated to an
analyst who can override that score; (b) breach waived for more than 12 months, or a relief amendment: +1 on
the liquidity-and-maturity score after calculation; (c) routine amendment (repricing, extension,
benchmark-rate change): no change; (d) in compliance: no change; (e) status or duration unclear: no
adjustment and no escalation, logged as an information flag (an unclear status is only weak evidence of
stress).
"""

from __future__ import annotations

import re
from datetime import date

from app.prompts.fo_prompts import (
    FO_COVENANTS_PROMPT,
    FO_COVENANTS_SCHEMA,
    FO_COVENANTS_TEMPLATE,
)

from .measures import SENTENCE_SPLIT
from .parsing import AUDIT_REPORT_LINE
from .precheck import (
    AUDIT_REPORT_END,
    DATA_NOTICE,
    PrecheckAgentError,
    _clip,
    _heading,
    _norm,
    call_agent,
    require_agent,
)

COVENANT_VERSION = "fo-covenants-1.1.0"

# The search terms. "waived"/"amended" are the same words in another form.
COV_KEYWORDS = re.compile(r"\bwaive[rsd]?\b|\bwaivers\b|\bforbear\w*|\bcovenants?\b|\bbreach\w*|not in compliance|"
                          r"\bevents? of default\b|\bcross[- ]default\w*|\bamendments?\b|\bamended\b|\brelief\b", re.I)
# Weak terms ("waiver", "amendment", "relief", "breach") count outside the debt note only next to debt words, so a
# Medicaid waiver or a data breach is not read as a covenant matter.
STRONG_KEYWORDS = re.compile(r"\bforbear\w*|\bcovenants?\b|not in compliance|\bevents? of default\b|\bcross[- ]default",
                             re.I)
DEBT_CONTEXT = re.compile(r"credit|facilit|lender|loan|indenture|\bnotes?\b|borrow|\bdebt\b|covenant|revolv|\bbank",
                          re.I)

SECTIONS = [("debt_note", "Debt note"), ("mdna_liquidity", "MD&A: liquidity"),
            ("auditor_report", "Auditor's report"), ("subsequent_events", "Subsequent events")]
SECTION_LABELS = dict(SECTIONS)
ITEMS = ("7", "8", "F", "")
NOTE_HEADING = re.compile(r"^(note\s+\d+|\d{1,2}\s*[.—–:-])\s*\S", re.I)
PAGE_HEADER = re.compile(r"management['’]s discussion and analysis|^and results of operations|"
                         r"^notes to (the )?consolidated financial statements", re.I)
DEBT_TITLE = re.compile(r"\bdebt\b|borrowings?|credit (facilit|agreement)|notes payable|financing arrangements|"
                        r"long-term obligations", re.I)
SUBSEQUENT_TITLE = re.compile(r"subsequent events?", re.I)
LIQUIDITY_TITLE = re.compile(r"\bliquidity\b", re.I)
MDNA_END = re.compile(r"market risk|critical accounting|^results of operations|recent(ly)? (issued|adopted)|"
                      r"accounting (standards|pronouncements)|forward-looking|tax examinations|business strategy", re.I)
# Without numbered notes, a heading like one of these closes the debt note.
OTHER_NOTE_TITLE = re.compile(r"leases?\b|commitments|contingenc|income taxes|stockholders|shareholders|equity\b|"
                              r"fair value|segment|employee benefit|revenue|goodwill|derivative|earnings per share|"
                              r"share-based|accumulated other|related part", re.I)
LIMIT, PASSAGE_MAX_CHARS = 40, 1500

# --- what a sentence says ------------------------------------------------------------------------------
HYPOTHETICAL = re.compile(r"\b(could|may|might|would|if|unless|should)\b|\bin the event\b|\bno assurance", re.I)
WAIVER = re.compile(r"\bwaive[rsd]?\b|\bwaivers\b|\bwaiving\b|\bforbear\w*", re.I)
NOT_WAIVED = re.compile(r"\bnot (been |yet )?waived|\bno waiver|\bwithout (a )?waiver|"
                        r"\b(not|been unable to) (obtain|obtained|received?)\w* (a |any )?waiver", re.I)
BREACH = re.compile(r"not (been )?in compliance|\bnon-?compliance\b|\bbreach(ed|es)?\b|\bviolat\w*|"
                    r"\bfail\w* to (comply|meet|satisfy|maintain)|\bdid not (comply|meet|satisfy)|"
                    r"\bevents? of default\b[^.]{0,40}\b(occurred|exist\w*|declared)|\bin default\b", re.I)
LIKELY = re.compile(r"reasonably likely|\blikely\b|\bprobable\b|\bexpect\w*|\banticipat\w*", re.I)
LIKELY_BREACH = re.compile(r"not be in compliance|not (be able to )?(comply|meet|satisfy|maintain)|\bbreach|\bviolat",
                           re.I)
NOT_LIKELY = re.compile(r"\bnot (reasonably )?likely\b|\bunlikely\b|\bdo(es)? not (expect|anticipate|believe)", re.I)
COMPLIANCE = re.compile(r"\b(in compliance|complied|compliant|comply|complies)\b[^.]{0,80}\bcovenant|"
                        r"\bcovenants?\b[^.]{0,80}\b(in compliance|complied|compliant)\b", re.I)
AMEND = re.compile(r"\bamend\w*", re.I)
RELIEF = re.compile(r"covenant relief|\brelief\b[^.]{0,60}\b(covenant|ratio)|"
                    r"\b(increas|rais|relax|suspen|loosen|reset|eliminat|remov|waiv)\w*[^.]{0,80}"
                    r"\b(covenants?|leverage ratio|coverage ratio|net leverage|interest coverage)\b", re.I)
ROUTINE = re.compile(r"\bextend\w*|\bmaturit\w*|\bre-?pric\w*|\binterest rates?\b|\bmargin\b|\bspread\b|\bSOFR\b|"
                     r"\bLIBOR\b|\bbenchmark|\bcommitments?\b|\bterm of\b", re.I)

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                      "september", "october", "november", "december"], 1)}
DATE = re.compile(r"\b(" + "|".join(MONTHS) + r")\s+(?:(\d{1,2}),?\s+)?(\d{4})\b|\b(\d{4})-(\d{2})-(\d{2})\b", re.I)
WORD_NUMBERS = {"six": 6, "nine": 9, "twelve": 12, "fifteen": 15, "eighteen": 18, "twenty-four": 24,
                "thirty-six": 36}
MONTH_SPAN = re.compile(r"\b(\d{1,3}|" + "|".join(WORD_NUMBERS) + r")[- ]months?\b", re.I)
WAIVER_MONTHS = 12

STATUS_FOR = {  # the outcome a classification leads to; the first outcome in OUTCOME_ORDER that applies wins
    "breach_not_waived": "breach", "breach_reasonably_likely": "breach",
    "waiver_over_12_months": "relief", "relief_amendment": "relief",
    "routine_amendment": "routine", "in_compliance": "compliant",
    "short_waiver": "unclear", "unclear_status": "unclear",
}
OUTCOME_ORDER = ["breach", "relief", "routine", "compliant", "unclear"]
OUTCOME_LETTER = {"breach": "a", "relief": "b", "routine": "c", "compliant": "d", "unclear": "e"}
STATUS_REASONS = {
    "breach": "A covenant breach not waived, or MD&A says a breach is reasonably likely within 12 months",
    "relief": "A breach waived for more than 12 months, or an amendment giving covenant relief",
    "routine": "A routine amendment only (repricing, extension, change of benchmark rate): resolved",
    "compliant": "In compliance: no covenant breach, waiver or relief amendment disclosed",
    "unclear": "A waiver or breach is mentioned, but its status or duration isn't clear",
}
EFFECTS = {
    "breach": "Revolver availability is set to 0 in every measure and the liquidity-and-maturity score to 10. "
              "Escalated to an analyst, who can override the liquidity-and-maturity score.",
    "relief": "+1 on the liquidity-and-maturity score after calculation.",
    "routine": "None: no change (resolved).",
    "compliant": "None: no change.",
    "unclear": "None: no adjustment and no escalation; the measures run as normal, because an unclear status is "
               "only weak evidence of stress. Logged as an information flag.",
}
NEEDS = {  # what a classification's quote must say: (pattern, why it is held back otherwise)
    "breach_not_waived": (BREACH, "quote states no breach, non-compliance or default"),
    "breach_reasonably_likely": (LIKELY_BREACH, "quote states no likely breach"),
    "waiver_over_12_months": (WAIVER, "quote mentions no waiver or forbearance"),
    "short_waiver": (WAIVER, "quote mentions no waiver or forbearance"),
    "relief_amendment": (RELIEF, "quote shows no covenant relief"),
    "routine_amendment": (AMEND, "quote mentions no amendment"),
    "in_compliance": (COMPLIANCE, "quote states no covenant compliance"),
    "unclear_status": (re.compile(WAIVER.pattern + "|" + BREACH.pattern, re.I), "quote mentions no waiver or breach"),
}
MIN_QUOTE = 20


def _sentences(text):
    return [s.strip() for s in SENTENCE_SPLIT.split(text) if s.strip()]


def _hit(sentence: str, section: str) -> list[str]:
    """The search terms a sentence hits, in this section."""
    found = [m.group(0).lower() for m in COV_KEYWORDS.finditer(sentence)]
    if not found:
        return []
    if section != "debt_note" and not STRONG_KEYWORDS.search(sentence) and not DEBT_CONTEXT.search(sentence):
        return []
    return list(dict.fromkeys(found))


def covenant_passages(data: dict) -> dict:
    """The paragraphs of the debt note, MD&A liquidity, the auditor's report and subsequent events where the search
    hits (ids C01… in document order), and which of those sections were located."""
    passages, seen, found = [], {}, set()
    for sec in (s for s in data["document"] if s["item"] in ITEMS):
        item = sec["item"]
        lines = sec["text"].split("\n")
        numbered = any(NOTE_HEADING.match(ln.strip()) and _heading(ln.strip()) for ln in lines)
        note, section, in_report = None, None, 0
        for line in lines:
            s = line.strip()
            if item in ("8", "F", "") and AUDIT_REPORT_LINE.match(line):
                in_report = 400  # lines; the report ends with the auditor-tenure sentence
                found.add("auditor_report")
                continue
            if in_report:
                in_report = 0 if AUDIT_REPORT_END.search(line) else in_report - 1
                where = "auditor_report"
            elif line.count(" | ") > 2:  # table rows aren't sentences
                continue
            elif _heading(s):
                if PAGE_HEADER.search(s):
                    continue
                section = _section_after(item, s, section, numbered)
                if section:
                    found.add(section)
                continue
            else:
                where = section
            if where is None or len(s) < 40:
                continue
            hit_sents = [(t, _hit(t, where)) for t in _sentences(s)]
            hits = [k for _, ks in hit_sents for k in ks]
            if not hits:
                continue
            text = s if len(s) <= PASSAGE_MAX_CHARS else " ".join(t for t, ks in hit_sents if ks)
            key = re.sub(r"\W+", "", text.lower())[:150]
            if key in seen:  # the same paragraph in MD&A and the debt note is sent once
                first = seen[key]
                if where != first["section"] and SECTION_LABELS[where] not in first["also_in"]:
                    first["also_in"].append(SECTION_LABELS[where])
                continue
            if len(passages) >= LIMIT:
                continue
            seen[key] = {"item": item, "section": where, "section_label": SECTION_LABELS[where],
                         "text": _clip(text, PASSAGE_MAX_CHARS), "hits": list(dict.fromkeys(hits)), "also_in": []}
            passages.append(seen[key])
    for i, p in enumerate(passages, 1):
        p["passage_id"] = f"C{i:02d}"
    counts = {}
    for p in passages:
        for label in (p["section_label"], *p["also_in"]):
            counts[label] = counts.get(label, 0) + 1
    return {"passages": passages,
            "sections": [{"key": k, "label": label, "located": k in found, "hits": counts.get(label, 0)}
                         for k, label in SECTIONS]}


def _section_after(item: str, heading: str, current: str | None, numbered: bool) -> str | None:
    """The searched section a heading opens, keeps or closes."""
    if item == "7":
        if SUBSEQUENT_TITLE.search(heading):
            return "subsequent_events"
        if LIQUIDITY_TITLE.search(heading):
            return "mdna_liquidity"
        if current == "mdna_liquidity" and not MDNA_END.search(heading):
            return current  # a sub-heading of MD&A liquidity (financing activities, capital resources, debt)
        return None
    if numbered and not NOTE_HEADING.match(heading):
        return current  # a sub-heading inside the current note
    if SUBSEQUENT_TITLE.search(heading):
        return "subsequent_events"
    if DEBT_TITLE.search(heading) and len(heading) <= 80:
        return "debt_note"
    if numbered or OTHER_NOTE_TITLE.search(heading) or current == "subsequent_events":
        return None
    return current


# --- dates and waiver length ---------------------------------------------------------------------------

def _period_end(meta: dict) -> date | None:
    try:
        return date.fromisoformat(str(meta.get("period_end"))[:10])
    except ValueError:
        return None


def _dates(text: str) -> list[date]:
    out = []
    for m in DATE.finditer(text):
        try:
            if m.group(1):
                month, year = MONTHS[m.group(1).lower()], int(m.group(3))
                day = int(m.group(2)) if m.group(2) else 28
                out.append(date(year, month, day))
            else:
                out.append(date(int(m.group(4)), int(m.group(5)), int(m.group(6))))
        except ValueError:
            continue
    return out


def _spans(text: str) -> list[int]:
    return [int(n) if n.isdigit() else WORD_NUMBERS[n.lower()] for n in MONTH_SPAN.findall(text)]


def months_after(end: date | None, until: date) -> float | None:
    return None if end is None else (until - end).days / 30.44


def waiver_length(text: str, end: date | None) -> float | None:
    """How many months after the year-end a waiver stated in ``text`` lasts: to the latest date after the year-end,
    else a stated span ("for 18 months"); None when the text gives neither."""
    later = [d for d in _dates(text) if end is not None and d > end]
    if later:
        return months_after(end, max(later))
    spans = _spans(text)
    return float(max(spans)) if spans else None


# --- the keyword rules ---------------------------------------------------------------------------------

def classify_sentence(sentence: str, paragraph: str, item: str, end: date | None) -> tuple[str, float | None]:
    """(classification, waiver months) for one sentence, in the outcome order."""
    if LIKELY.search(sentence) and LIKELY_BREACH.search(sentence) and not NOT_LIKELY.search(sentence) and item == "7":
        return "breach_reasonably_likely", None
    if WAIVER.search(sentence) and not NOT_WAIVED.search(sentence) and not HYPOTHETICAL.search(sentence):
        months = waiver_length(sentence, end)
        if months is None:
            return "unclear_status", None
        return ("waiver_over_12_months" if months > WAIVER_MONTHS else "short_waiver"), round(months, 1)
    if BREACH.search(sentence) and not HYPOTHETICAL.search(sentence):
        waived = any(WAIVER.search(t) and not NOT_WAIVED.search(t) and not HYPOTHETICAL.search(t)
                     for t in _sentences(paragraph))
        return ("not_a_conclusion" if waived else "breach_not_waived"), None  # a waiver sentence decides
    if RELIEF.search(sentence) and (AMEND.search(sentence) or "relief" in sentence.lower()):
        return "relief_amendment", None
    if AMEND.search(sentence) and ROUTINE.search(sentence) and DEBT_CONTEXT.search(sentence):
        return "routine_amendment", None
    if COMPLIANCE.search(sentence) and not BREACH.search(sentence):
        return "in_compliance", None
    return "not_a_conclusion", None


def rule_findings(passages: list[dict], meta: dict) -> list[dict]:
    """Every sentence the keyword rules classify as bearing on covenants."""
    end = _period_end(meta)
    out = []
    for p in passages:
        for s in _sentences(p["text"]):
            if not COV_KEYWORDS.search(s):
                continue
            cls, months = classify_sentence(s, p["text"], p["item"], end)
            if cls != "not_a_conclusion":
                out.append({"passage_id": p["passage_id"], "classification": cls, "quote": s,
                            "waiver_months": months, "section": p["section_label"], "item": p["item"]})
    return out


def derive_status(findings: list[dict]) -> str:
    """The first outcome (a) to (e) the findings match; compliant when none does."""
    found = {STATUS_FOR[f["classification"]] for f in findings if f["classification"] in STATUS_FOR}
    return next((s for s in OUTCOME_ORDER if s in found), "compliant")


# --- the agent -----------------------------------------------------------------------------------------

def agent_inputs(cov: dict, meta: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "period_end": meta.get("period_end"),
                    "fiscal_year": meta.get("fiscal_year")},
        "sections_searched": [{"section": s["label"], "located": s["located"], "paragraphs_with_hits": s["hits"]}
                              for s in cov["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "search_hits": p["hits"], "text": p["text"]} for p in cov["passages"]],
    }


def _verbatim(quote: str, text: str) -> bool:
    q = _norm(quote).rstrip("…")
    return len(q) >= MIN_QUOTE and q in _norm(text)


def _checked_length(f: dict, end: date | None) -> tuple[float | None, str | None]:
    """The waiver's length in months as the quote supports it, or (None, why not)."""
    quote = f.get("quote") or ""
    until, months = f.get("waiver_until"), f.get("waiver_months")
    if until:
        try:
            stated = date.fromisoformat(str(until)[:10])
        except ValueError:
            return None, f"waiver_until {until!r} is not a date"
        if not any((d.year, d.month) == (stated.year, stated.month) for d in _dates(quote)):
            return None, f"the waiver end date {until} is not in the quote"
        length = months_after(end, stated)
        return (length, None) if length is not None else (None, "the year-end is unknown")
    if months is not None:
        if int(months) not in _spans(quote):
            return None, f"a {months:g}-month waiver is not stated in the quote"
        return float(months), None
    return None, "the waiver's end date or length is not given"


def validate_agent(parsed: dict, cov: dict, meta: dict) -> dict:
    """Keep the findings that hold up, check each waiver's length against its quote, and re-derive the outcome.

    A finding is held back when it cites an unknown passage, its quote is not verbatim (or too short), or the quote
    does not say what the classification claims: a breach must be stated (not hypothetical, and not waived in the
    same quote); a likely breach must be in MD&A and not negated; a compliance statement must not admit a breach.
    A waiver whose length the quote does not support is kept as "unclear status" (outcome e), and one of 12 months
    or less as a short waiver (also e)."""
    by_id = {p["passage_id"]: p for p in cov["passages"]}
    end = _period_end(meta)
    accepted, held_back, notes = [], [], []
    for f in parsed.get("findings") or []:
        pid, cls, quote = f.get("passage_id"), f.get("classification"), f.get("quote") or ""
        p = by_id.get(pid)
        problem, months = None, None
        if p is None:
            problem = "cites a passage that was not supplied"
        elif not _verbatim(quote, p["text"]):
            problem = "quote is not verbatim in the cited passage"
        elif cls in NEEDS and not NEEDS[cls][0].search(quote):
            problem = NEEDS[cls][1]
        elif cls == "breach_not_waived" and WAIVER.search(quote) and not NOT_WAIVED.search(quote):
            problem = "quote says the breach was waived"
        elif cls == "breach_not_waived" and HYPOTHETICAL.search(quote) and not re.search(
                r"\b(was|were|is|are|has been|have been|had been)\b", quote, re.I):
            problem = "quote is hypothetical (what could happen), not a breach"
        elif cls == "breach_reasonably_likely" and (p["item"] != "7" or NOT_LIKELY.search(quote)
                                                    or not LIKELY.search(quote)):
            problem = ("a likely breach must be stated in MD&A (Item 7)" if p["item"] != "7"
                       else "quote does not say a breach is reasonably likely")
        elif cls == "in_compliance" and BREACH.search(quote):
            problem = "quote admits non-compliance"
        elif cls in ("waiver_over_12_months", "short_waiver"):
            months, why = _checked_length(f, end)
            if months is None:
                notes.append(f"{pid}: {why}; read as an unclear status (outcome e).")
                cls = "unclear_status"
            elif months <= WAIVER_MONTHS and cls == "waiver_over_12_months":
                notes.append(f"{pid}: the waiver runs {months:.0f} months after the year-end, not more than "
                             f"{WAIVER_MONTHS}; read as a short waiver (outcome e).")
                cls = "short_waiver"
            elif months > WAIVER_MONTHS and cls == "short_waiver":
                cls = "waiver_over_12_months"
        entry = {"passage_id": pid, "classification": cls, "quote": quote}
        if problem:
            held_back.append({**entry, "classification": f.get("classification"), "reason": problem})
        else:
            accepted.append({**entry, "waiver_months": round(months, 1) if months is not None else None,
                             "item": p["item"], "section": p["section_label"],
                             **({"stated_classification": f["classification"]}
                                if cls != f.get("classification") else {})})
    headroom, headroom_held = [], []
    for h in parsed.get("headroom") or []:
        p = by_id.get(h.get("passage_id"))
        numbers = [x for x in (h.get("required"), h.get("actual")) if x]
        if p is None or not _verbatim(h.get("quote") or "", p["text"]):
            headroom_held.append({**h, "reason": "quote is not verbatim in the cited passage"})
        elif not all(_norm(x) in _norm(h["quote"]) for x in numbers):
            headroom_held.append({**h, "reason": "the levels given are not in the quote"})
        else:
            headroom.append({**h, "section": p["section_label"]})
    derived = derive_status(accepted)
    return {"accepted": accepted, "held_back": held_back, "headroom": headroom, "headroom_held_back": headroom_held,
            "derived_status": derived, "stated_status": parsed.get("status"),
            "status_consistent": parsed.get("status") == derived, "validation_notes": notes}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_COVENANTS_PROMPT, template=FO_COVENANTS_TEMPLATE, response_schema=FO_COVENANTS_SCHEMA,
                      role="analyze", label="Financial Overview Pre-check Agent: covenants")
    return registry


# --- the pre-check -------------------------------------------------------------------------------------

def _basis(findings: list[dict], status: str) -> list[dict]:
    return [f for f in findings if STATUS_FOR.get(f["classification"]) == status]


def covenant_precheck(data: dict, *, session=None, backend=None, mode: str = "rules",
                      case_id: str | None = None) -> dict:
    """The covenant pre-check result recorded in the run payload.

    No search hit: in compliance, no model call. A search hit is always interpreted by the agent; the keyword rules
    run alongside as a cross-check only. Without an agent, or when its output is rejected, ``PrecheckAgentError``."""
    cov = covenant_passages(data)
    rules = rule_findings(cov["passages"], data["meta"])
    r_status = derive_status(rules)
    result = {"check": "covenants", "version": COVENANT_VERSION, "mode": mode if backend is not None else "none",
              "rules": {"status": r_status, "reason": STATUS_REASONS[r_status], "findings": rules},
              "agent": None, "notes": [], "info_flags": [], "headroom": [],
              "passages": cov["passages"], "sections": cov["sections"]}
    missing = [s["label"] for s in cov["sections"] if not s["located"]]
    if missing:
        result["notes"].append("No heading found for: " + ", ".join(missing) + ". That is usual when there is "
                               "nothing to report there (a 10-K without a subsequent-events note, for example).")

    if not cov["passages"]:
        status, decided_by, findings = "compliant", "no_hits", []
        result["notes"].append("No search term hit in the sections searched: in compliance. The agent was not "
                               "called (nothing to interpret).")
    else:  # a search hit: the agent must interpret it
        require_agent(backend, "covenants")
        status, decided_by, findings = _run_agent(result, cov, data, session, backend, case_id)
    if decided_by == "agent" and status != r_status:
        result["notes"].append(f"The keyword rules read this filing as {r_status.upper()} "
                               f"({STATUS_REASONS[r_status].lower()}); the agent's verified findings give "
                               f"{status.upper()}. Read the passages to confirm.")
    if status == "compliant" and cov["passages"] and not _basis(findings, "compliant"):
        result["info_flags"].append("No covenant compliance statement found; no breach, waiver or relief "
                                    "amendment either.")
    if status == "unclear":
        result["info_flags"] += [f"{f['passage_id']} ({f['section']}): "
                                 + ("a waiver of 12 months or less" if f["classification"] == "short_waiver"
                                    else "a waiver or breach whose status or duration isn't clear")
                                 + f": \"{f['quote']}\"" for f in _basis(findings, "unclear")]
    waivers = [f for f in findings if f["classification"] in ("waiver_over_12_months", "short_waiver")]
    result.update(status=status, outcome=OUTCOME_LETTER[status], decided_by=decided_by,
                  reason=STATUS_REASONS[status], effect=EFFECTS[status], basis=_basis(findings, status),
                  waivers=[{"passage_id": f["passage_id"], "months": f.get("waiver_months"), "quote": f["quote"]}
                           for f in waivers],
                  review_required=status == "breach")
    return result


def _run_agent(result, cov, data, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_COVENANTS_PROMPT,
                            agent_inputs(cov, data["meta"]))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Pre-check Agent's covenant output was rejected "
                                 f"({agent['validation_detail']}); the run was not recorded. Run it again.")
    checked = validate_agent(run.parsed, cov, data["meta"])
    agent.update(rationale=run.parsed.get("rationale"),
                 missing_information=run.parsed.get("missing_information") or [], **checked)
    result["headroom"] = checked["headroom"]
    result["notes"] += checked["validation_notes"]
    if checked["held_back"] or checked["headroom_held_back"]:
        result["notes"].append(f"{len(checked['held_back']) + len(checked['headroom_held_back'])} agent finding(s) "
                               "held back by validation.")
    if not checked["status_consistent"]:
        result["notes"].append(f"The agent stated {str(checked['stated_status']).upper()} but its verified findings "
                               f"support {checked['derived_status'].upper()}; the verified findings decide.")
    return checked["derived_status"], "agent", checked["accepted"]
