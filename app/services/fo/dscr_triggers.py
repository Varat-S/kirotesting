"""FO Trigger Agent, DSCR T1: debt service coverage scored 9 or more (below 0.5x: cash flow can't cover debt service).

A trigger agent runs after the measures are scored, only when its measure trips the trigger and the pre-checks allow
it (``payload.gate.agent_triggers`` is "enabled": a going-concern fail skips it). Same three steps as the pre-checks,
so the model never decides the outcome on its own word:

1. ``t1_passages`` (deterministic) collects what the agent checks: the paragraphs and table rows of the debt note and
   MD&A liquidity that mention commercial paper, the revolver, 364-day or securitisation facilities, short-term
   borrowings, maturities, backstop capacity, classification as long-term or refinancing, and every subsequent-events
   paragraph (ids D01…). ``t1_signals`` is a keyword cross-check only.
2. The Trigger Agent (prompt ``fo_trigger_dscr_t1``) ALWAYS reads them (no agent, or a rejected output, refuses the
   run). Every fact is quoted verbatim. Only a filing with none of those passages has nothing to read (no model call).
3. ``validate_t1`` keeps only verbatim evidence whose amount or date is in its quote, and RE-DERIVES the two
   independent adjustments:

   - T1a, rollover debt: commercial paper, revolver borrowings, or a 364-day or securitisation facility in year-1
     principal comes out of principal only if it is shown to be rolled over AND undrawn committed backstop capacity at
     least as large as the amount removed matures more than 12 months after the year-end: an input correction, DSCR
     recomputed. Otherwise no adjustment (an item comes out whole or not at all). Commercial paper is in year-1
     principal only when principal includes short-term borrowings (Debt, Current, without a maturity table).
   - T1b, refinancing after the year-end: first, debt already classified as long-term at the year-end under ASC
     470-10-45-14 gets no adjustment. Executed refinancing of a year-1 maturity: −2 on the DSCR score, not below 1.
     Only a stated plan: no adjustment, because a plan isn't executed (the passage is cited). The result is recorded
     once and shared with Liquidity T2 (``shared_with``).

A trigger escalates its measure only when it is unresolved: the agent's own answer and its verified evidence disagree.
Then the analyst accepts DSCR's score or overrides it. Otherwise the verified outcome stands without an analyst: an
adjustment is applied, and with no adjustment the DSCR score stays as calculated.
"""

from __future__ import annotations

import calendar
import copy
import re
from datetime import date, timedelta

from app.prompts.fo_prompts import FO_DSCR_T1_PROMPT, FO_DSCR_T1_SCHEMA, FO_DSCR_T1_TEMPLATE

from .acquisitions import SCALE, SCALES, _date_in_quote, _verbatim, amount_in, parse_amount
from .covenants import NOTE_HEADING, PAGE_HEADER, _section_after
from .measures import DSCR_BANDS, SENTENCE_SPLIT, risk_score
from .parsing import AUDIT_REPORT_LINE
from .precheck import AUDIT_REPORT_END, DATA_NOTICE, PrecheckAgentError, _clip, _heading, call_agent, require_agent
from .scoring import headline_score

T1_VERSION = "fo-trigger-dscr-t1-1.1.0"
T1_ID = "dscr_t1"
T1_THRESHOLD = 9  # a DSCR score at or above this (below 0.5x) sets the trigger off
T1_REFINANCING_RELIEF = 2  # executed refinancing of year-1 maturities after the year-end: −2 on the score
AGENT = "Trigger Agent"
SHARED_WITH = ["liquidity_t2"]  # the triggers that read T1b's refinancing record instead of re-checking it

ITEMS = ("7", "8", "F", "")
SECTIONS = [("debt_note", "Debt note"), ("mdna_liquidity", "MD&A: liquidity"),
            ("subsequent_events", "Subsequent events")]
LIMITS = {"debt_note": 30, "mdna_liquidity": 15, "subsequent_events": 15}
PASSAGE_MAX_CHARS, TABLE_MAX_CHARS = 1500, 2000
# Debt-note and MD&A paragraphs are read only when they mention one of these; subsequent events are read in full.
T1_TERMS = re.compile(r"commercial paper|revolv\w*|credit (facilit\w*|agreement)|line of credit|short-term "
                      r"(borrowings|debt)|364-day|securiti[sz]ation|back-?stop|back-?up|liquidity support|"
                      r"roll\w*[ -]?over|re-?borrow\w*|refinanc\w*|redeem\w*|redemption|repa(id|y\w*)|"
                      r"current (portion|maturities)|matur\w*|\bdue (in|on|within)\b|undrawn|unused|"
                      r"borrowing capacity|availab\w*|classified as (long-term|non-?current)", re.I)

# --- what a quote must say ---------------------------------------------------------------------------------------
COMMERCIAL_PAPER = re.compile(r"commercial paper", re.I)
REVOLVER = re.compile(r"revolv\w*|credit facilit\w*|line of credit|\bABL\b|asset-based", re.I)
SHORT_FACILITY = re.compile(r"364-day|securiti[sz]ation|receivables? (purchase|financing|sale|facilit\w*)|"
                            r"accounts receivable (purchase|financing|securiti[sz]ation)|facilit\w*", re.I)
BALANCE_WORDS = {"commercial_paper": (COMMERCIAL_PAPER, "commercial paper"), "revolver": (REVOLVER, "the revolver"),
                 "364_day_facility": (SHORT_FACILITY, "the facility"),
                 "securitisation_facility": (SHORT_FACILITY, "the facility")}
ROLLOVER = re.compile(r"roll\w*[ -]?over|reissu\w*|re-?borrow\w*|revolving basis|from time to time|refinanc\w*|"
                      r"repaid and (re)?borrowed|replac\w*|renew\w*|continu\w* to issue", re.I)
FACILITY = re.compile(REVOLVER.pattern + r"|" + SHORT_FACILITY.pattern + r"|commitments?|credit agreement|"
                      r"committed term", re.I)
AVAILABLE = re.compile(r"back-?stop|back-?up|support\w*|availab\w*|undrawn|unused|unutilized|capacity|committed",
                       re.I)
# ASC 470-10-45-14: a short-term obligation refinanced (or with a financing agreement) after the balance-sheet date
# can be classified as long-term at it.
RECLASSIFIED = re.compile(r"classified as (long-term|non-?current)|(long-term|non-?current) (classification|basis)|"
                          r"intent and (the )?ability to refinance|ability and intent to refinance|470-10-45-14|"
                          r"excluded from current liabilities|reclassif\w*", re.I)
REFI_WORDS = re.compile(r"refinanc\w*|redeem\w*|redemption|repa(id|y\w*)|prepa(id|y\w*)|extend\w*|extension|"
                        r"issu\w*|new (senior )?(notes|debt|term loan|facility|financing)|replac\w*|retir\w*", re.I)
EXECUTED = re.compile(r"\b(completed|issued|closed|entered into|redeemed|repaid|prepaid|retired|extended|"
                      r"refinanced|exchanged|borrowed|amended|replaced|settled|repurchased|tendered|used)\b", re.I)
PLAN_WORDS = re.compile(r"\b(intends?|intended|plans?|planned|expects?|expected|anticipat\w*|may|might|could|would|"
                        r"seeks?|consider\w*|propos\w*|evaluat\w*|explor\w*)\b|\bwill\b(?! (mature|be due|bear))",
                        re.I)
SIGNALS = [("commercial paper", COMMERCIAL_PAPER, None),
           ("backstop capacity", re.compile(r"back-?stop|back-?up|liquidity support", re.I), None),
           ("refinancing after the year-end", REFI_WORDS, "subsequent_events")]

ROLLOVER_OUTCOMES = {
    "remove_from_principal": "Rolled over, with undrawn committed backstop capacity ≥ the amount, maturing more than "
                             "12 months out: removed from year-1 principal",
    "not_in_principal": "Not part of year-1 principal: nothing to remove",
    "not_rolled_over": "Not shown to be rolled over: no adjustment",
    "no_backstop": "No undrawn committed backstop maturing more than 12 months out: no adjustment",
    "backstop_too_small": "Backstop capacity is less than the amount: no adjustment",
}
REFINANCING_OUTCOMES = {
    "executed": f"Year-1 maturity refinanced after the year-end (executed): −{T1_REFINANCING_RELIEF} on the score",
    "plan": "Only a stated plan to refinance: no adjustment, because a plan isn't executed",
    "reclassified": "Already classified as long-term at the year-end (ASC 470-10-45-14): no adjustment",
    "not_year1": "Refinanced after the year-end, but not shown to be a year-1 maturity: no adjustment",
}
STATUS_TEXT = {
    "not_triggered": f"DSCR scored below {T1_THRESHOLD}: trigger T1 not set off",
    "skipped": "Skipped: agent triggers are skipped after a going-concern fail",
    "plan_only": "Only a stated plan to refinance year-1 maturities: no adjustment, because a plan isn't executed",
    "no_adjustment": "Year-1 principal is not rollover debt with a qualifying backstop, and no year-1 maturity was "
                     "refinanced after the year-end: no adjustment",
}
ESCALATED_NOTE = "Escalated to an analyst, who accepts this score or overrides it."
DECIDED_NOTE = "The analyst has decided this measure's score."
RULES = [("T1a: commercial paper, revolver, 364-day or securitisation borrowings in year-1 principal, rolled over, with "
          "undrawn committed backstop capacity ≥ the amount maturing more than 12 months after the year-end",
          "Input correction: removed from principal, DSCR recomputed"),
         ("T1a: otherwise (no backstop, too small, or maturing within 12 months)", "No adjustment"),
         ("T1b: debt already classified as long-term at the year-end (ASC 470-10-45-14)", "No adjustment"),
         ("T1b: year-1 maturities refinanced after the year-end (executed)",
          f"−{T1_REFINANCING_RELIEF} on the DSCR score"),
         ("T1b: only a stated plan to refinance", "No adjustment: a plan isn't executed (passage cited)"),
         ("Unresolved: the agent's answer and the verified evidence disagree",
          "DSCR escalated to an analyst, who accepts the score or overrides it"),
         ("Resolved, no adjustment", "The DSCR score stays as calculated (no analyst decision)")]


# --- the passages ------------------------------------------------------------------------------------------------

def _sentences(text):
    return [s.strip() for s in SENTENCE_SPLIT.split(text) if s.strip()]


def t1_passages(data: dict) -> dict:
    """The debt note's and MD&A liquidity's paragraphs and tables (rows joined with " | ", with the scale in force)
    that mention a T1 term, and every subsequent-events paragraph; ids D01… in document order."""
    return collect_passages(data, "D", LIMITS, SECTIONS, T1_TERMS, _section_after, read_whole=("subsequent_events",))


def collect_passages(data: dict, prefix: str, limits: dict, sections: list, terms, section_after,
                     read_whole=()) -> dict:
    """A trigger's passages: the paragraphs and tables (rows joined with " | ", with the scale in force) of the
    sections ``section_after`` opens that mention one of ``terms`` (every paragraph of a ``read_whole`` section), up
    to ``limits`` per section; ids ``prefix``01… in document order."""
    labels = dict(sections)
    passages, seen, found = [], set(), set()
    counts = dict.fromkeys(limits, 0)

    def add(item, section, kind, text, scale):
        key = re.sub(r"\W+", "", text.lower())[:150]
        if not key or key in seen or counts[section] >= limits[section]:
            return
        seen.add(key)
        counts[section] += 1
        passages.append({"item": item, "section": section, "section_label": labels[section], "kind": kind,
                         "text": text, "scale": scale})

    for sec in (s for s in data["document"] if s["item"] in ITEMS):
        item = sec["item"]
        lines = sec["text"].split("\n")
        numbered = any(NOTE_HEADING.match(ln.strip()) and _heading(ln.strip()) for ln in lines)
        state = {"section": None, "scale": None, "cells": [], "in_report": 0}

        def flush():
            cells, state["cells"] = state["cells"], []
            text = " | ".join(cells)
            if state["section"] and len(cells) >= 2 and re.search(r"\d", text) and terms.search(text):
                add(item, state["section"], "table", _clip(text, TABLE_MAX_CHARS), state["scale"])

        for line in lines:
            s = line.strip()
            if item in ("8", "F", "") and AUDIT_REPORT_LINE.match(line):
                flush()
                state["in_report"] = 400  # lines; the auditor's report is not read here
                continue
            if state["in_report"]:
                state["in_report"] = 0 if AUDIT_REPORT_END.search(line) else state["in_report"] - 1
                continue
            if not s or re.fullmatch(r"F-\d{1,3}", s) or (_heading(s) and PAGE_HEADER.search(s)):
                continue
            if _heading(s):
                section = section_after(item, s, state["section"], numbered)
                if section and section == state["section"]:
                    state["cells"].append(s)  # a sub-heading, or a table's header cell
                    continue
                flush()
                state.update(section=section if section in limits else None, scale=None)
                if state["section"]:
                    found.add(state["section"])
                continue
            if not state["section"]:
                continue
            if SCALE.search(s):
                state["scale"] = SCALES[SCALE.search(s).group(1).lower().rstrip("s")]
            if len(s) < 40 or s.count(" | ") > 2:
                state["cells"].append(s)
                continue
            flush()
            whole = state["section"] in read_whole
            if not whole and not terms.search(s):
                continue
            text = s if len(s) <= PASSAGE_MAX_CHARS else " ".join(
                t for t in _sentences(s) if whole or terms.search(t))
            add(item, state["section"], "text", _clip(text, PASSAGE_MAX_CHARS), state["scale"])
        flush()
    for i, p in enumerate(passages, 1):
        p["passage_id"] = f"{prefix}{i:02d}"
    return {"passages": passages,
            "sections": [{"key": k, "label": label, "located": k in found,
                          "passages": sum(1 for p in passages if p["section"] == k)} for k, label in sections]}


def t1_signals(passages: list[dict]) -> list[dict]:
    """The keyword cross-check: sentences naming commercial paper, backstop capacity or (in subsequent events) a
    refinancing. Shown beside the agent's result; never decides."""
    out = []
    for p in passages:
        for name, pattern, section in SIGNALS:
            if section and p["section"] != section:
                continue
            hit = next((t for t in _sentences(p["text"]) if pattern.search(t)), None)
            if hit:
                out.append({"signal": name, "passage_id": p["passage_id"], "quote": hit})
    return out


# --- year-1 principal (from the scored DSCR) ---------------------------------------------------------------------

def _headline_index(m: dict) -> int | None:
    return next((i for i, c in enumerate(m["columns"]) if c.get("score") is not None), None)


def principal_facts(col: dict) -> dict:
    """What the engine counted for the triggering column."""
    concept = col.get("principal_concept") or ""
    return {"heading": col["heading"], "period": col["period"], "basis": col.get("basis"),
            "period_end": col["period"].split("--")[1], "as_at": col.get("principal_as_at"),
            "principal": col.get("principal"), "concept": concept or None,
            "principal_basis": col.get("principal_basis"),
            # The maturity table's scheduled amortisation has no short-term borrowings; of the current-portion tags
            # only Debt, Current counts them (commercial paper) in.
            "includes_short_term_borrowings": col.get("principal_includes_short_term",
                                                      "us-gaap:DebtCurrent" in concept),
            "interest": col.get("interest"), "rent": col.get("rent"), "cash_flow": col.get("cash_flow"),
            "debt_service": col.get("debt_service"), "ratio": col.get("ratio"), "score": col.get("score")}


def _millions(v):
    return None if v is None else round(v / 1e6, 1)


def t1_inputs(t1: dict, meta: dict, pr: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": pr["period_end"]},
        "dscr": {"column": pr["heading"], "ratio": round(pr["ratio"], 2) if pr["ratio"] is not None else None,
                 "score": pr["score"], "trigger": f"score {T1_THRESHOLD} or more"},
        "year1_principal": {"amount_millions": _millions(pr["principal"]), "as_at": pr["as_at"],
                            "source": pr["concept"],
                            "includes_short_term_borrowings": pr["includes_short_term_borrowings"]},
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t1["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t1["passages"]],
    }


# --- validation --------------------------------------------------------------------------------------------------

def _span(stated: tuple) -> tuple[date, date] | None:
    """(earliest, latest) day a date of the precision stated can be."""
    y, mo, d = stated
    try:
        if d:
            return date(y, mo, d), date(y, mo, d)
        if mo:
            return date(y, mo, 1), date(y, mo, calendar.monthrange(y, mo)[1])
        return date(y, 1, 1), date(y, 12, 31)
    except ValueError:
        return None


def _a_year_after(end: date) -> date:
    try:
        return end.replace(year=end.year + 1)
    except ValueError:  # 29 February
        return end + timedelta(days=365)


def _quoted(e, by_id, need=None, why=None):
    """(passage, why held back): the evidence must cite a supplied passage, quote it verbatim and say ``need``."""
    if not isinstance(e, dict):
        return None, "not given"
    p = by_id.get(e.get("passage_id"))
    if p is None:
        return None, "cites a passage that was not supplied"
    if not _verbatim(e.get("quote") or "", p["text"]):
        return None, "quote is not verbatim in the cited passage"
    if need is not None and not need.search(e["quote"]):
        return None, why
    return p, None


def _value(e, p):
    if not (e.get("amount") or "").strip():
        return None, "no amount given"
    if not amount_in(e["amount"], e["quote"]):
        return None, f"the amount {e['amount']} is not in the quote"
    return parse_amount(e["amount"], p.get("scale"))


def _dated(e):
    """((earliest, latest), why not): the stated date, if the quote contains it."""
    if not (e.get("date") or "").strip():
        return None, "no date given"
    stated, why = _date_in_quote(e["date"], e["quote"])
    if why:
        return None, why
    span = _span(stated)
    return (span, None) if span else (None, f"date {e['date']!r} isn't a date")


def _ev(e, p):
    return {**{k: e[k] for k in ("passage_id", "quote", "amount", "date") if e.get(k) is not None},
            "section": p["section_label"]}


def _held(item, fact, e, reason):
    return {**item, "fact": fact, **{k: e[k] for k in ("passage_id", "quote", "amount", "date")
                                     if isinstance(e, dict) and e.get(k) is not None}, "reason": reason}


def _rollover(r, by_id, pr, end, held, notes):
    """One verified rollover balance, or None (held back)."""
    kind = r.get("kind")
    item = {"item_id": r.get("item_id"), "kind": kind, "description": r.get("description")}
    words, name = BALANCE_WORDS.get(kind, BALANCE_WORDS["commercial_paper"])
    p, why = _quoted(r.get("balance"), by_id, words, f"the balance quote doesn't name {name}")
    balance = None
    if not why:
        balance, why = _value(r["balance"], p)
    if why:
        held.append(_held(item, "balance", r.get("balance"), why))
        return None
    entry = {**item, "balance": balance, "balance_evidence": _ev(r["balance"], p), "rollover_evidence": None,
             "backstop": None, "backstop_matures": None, "backstop_evidence": None, "removed": 0.0,
             "agent_in_principal": bool(r.get("in_year1_principal"))}
    if r.get("rollover"):
        rp, why = _quoted(r["rollover"], by_id, ROLLOVER, "the quote doesn't say it is rolled over, reissued, "
                                                          "re-borrowed, renewed or issued from time to time")
        if why:
            held.append(_held(item, "rollover", r["rollover"], why))
        else:
            entry["rollover_evidence"] = _ev(r["rollover"], rp)
    b = r.get("backstop")
    if b:  # undrawn committed capacity and its maturity, in one quote
        bp, why = _quoted(b, by_id, FACILITY, "the quote names no committed facility")
        if not why and not AVAILABLE.search(b["quote"]):
            why = "the quote shows no undrawn committed capacity"
        value = span = None
        if not why:
            value, why = _value(b, bp)
            why = "the backstop's undrawn committed capacity is not given" if why == "no amount given" else why
        if not why:
            span, why = _dated(b)
            why = "the backstop's maturity date is not given" if why == "no date given" else why
        if not why and span[0] <= _a_year_after(end):
            why = f"the backstop matures {b['date']}, not more than 12 months after the year-end"
        if why:
            held.append(_held(item, "backstop", b, why))
        else:
            entry.update(backstop=value, backstop_matures=b["date"], backstop_evidence=_ev(b, bp))
    if not entry["agent_in_principal"]:
        outcome = "not_in_principal"
    elif kind == "commercial_paper" and not pr["includes_short_term_borrowings"]:
        outcome = "not_in_principal"
        notes.append(f"{item['item_id']}: year-1 principal is {pr['concept'] or 'not tagged'}, which doesn't include "
                     "commercial paper or other short-term borrowings; nothing to remove.")
    elif not entry["rollover_evidence"]:
        outcome = "not_rolled_over"
    elif entry["backstop"] is None:
        outcome = "no_backstop"
        if kind in ("364_day_facility", "securitisation_facility"):
            notes.append(f"{item['item_id']}: a {kind.replace('_', ' ').replace('364 day', '364-day')} maturing within "
                         "12 months stays in principal: no committed term beyond 12 months is shown.")
    elif entry["backstop"] < balance:
        outcome = "backstop_too_small"
        notes.append(f"{item['item_id']}: backstop capacity {entry['backstop'] / 1e6:,.1f} million is less than the "
                     f"{balance / 1e6:,.1f} million to remove; no adjustment.")
    else:
        outcome = "remove_from_principal"  # removed in validate_t1, once the shared backstop is counted
    entry.update(outcome=outcome, outcome_text=ROLLOVER_OUTCOMES[outcome])
    return entry


def _refinancing(f, by_id, pr, end, held, notes):
    """One verified refinancing, or None (held back)."""
    item = {"item_id": f.get("item_id"), "kind": f.get("kind"), "description": f.get("description")}
    ev = f.get("event")
    p, why = _quoted(ev, by_id, REFI_WORDS, "the quote mentions no refinancing, redemption, repayment or new financing")
    if why:
        held.append(_held(item, "event", ev, why))
        return None
    span, date_why = _dated(ev) if ev.get("date") else (None, None)
    kind = f.get("kind")
    rc, reclassified = f.get("reclassified"), None
    if rc:  # first: debt already classified as long-term at the year-end (ASC 470-10-45-14) gets no adjustment
        rp, why = _quoted(rc, by_id, RECLASSIFIED, "the quote doesn't say the debt was classified as long-term")
        if why:
            held.append(_held(item, "reclassified", rc, why))
            notes.append(f"{item['item_id']}: the agent said this debt was already classified as long-term, but its "
                         "quote doesn't hold up; checked as a refinancing.")
        else:
            reclassified = _ev(rc, rp)
    if reclassified:
        entry = {**item, "kind": kind, "stated_kind": f.get("kind"), "date": ev.get("date") if span else None,
                 "event_evidence": _ev(ev, p), "reclassified_evidence": reclassified, "maturity": None,
                 "maturity_evidence": None, "amount": None, "amount_evidence": None, "share_of_principal": None,
                 "outcome": "reclassified", "outcome_text": REFINANCING_OUTCOMES["reclassified"]}
        return entry
    if kind == "executed":
        if PLAN_WORDS.search(ev["quote"]) or not EXECUTED.search(ev["quote"]):
            notes.append(f"{item['item_id']}: the agent called this refinancing executed, but its quote reads as a "
                         "plan (nothing completed); no adjustment.")
            kind = "plan"
        elif span is None:
            held.append(_held(item, "event", ev, date_why or "no date in the quote shows it was after the year-end"))
            return None
        elif span[0] <= end:
            held.append(_held(item, "event", ev, "dated on or before the year-end: already in the year-end figures"))
            return None
    entry = {**item, "kind": kind, "stated_kind": f.get("kind"), "date": ev.get("date") if span else None,
             "event_evidence": _ev(ev, p), "reclassified_evidence": None, "maturity": None, "maturity_evidence": None,
             "amount": None, "amount_evidence": None, "share_of_principal": None}
    m = f.get("maturity")
    if m:
        mp, why = _quoted(m, by_id)
        mspan = None
        if not why:
            mspan, why = _dated(m)
        if not why and not (mspan[0] > end and mspan[1] <= _a_year_after(end)):
            why = f"the maturity {m['date']} is not within the 12 months after the year-end"
        if why:
            held.append(_held(item, "maturity", m, why))
        else:
            entry.update(maturity=m["date"], maturity_evidence=_ev(m, mp))
    a = f.get("amount")
    if a:
        ap, why = _quoted(a, by_id)
        value = None
        if not why:
            value, why = _value(a, ap)
        if why:
            held.append(_held(item, "amount", a, why))
        else:
            entry.update(amount=value, amount_evidence=_ev(a, ap),
                         share_of_principal=value / pr["principal"] if pr["principal"] else None)
    outcome = "plan" if kind == "plan" else "executed" if entry["maturity"] else "not_year1"
    if outcome == "not_year1":
        notes.append(f"{item['item_id']}: refinanced after the year-end, but no verified quote shows it refinances a "
                     "year-1 maturity; no adjustment.")
    entry.update(outcome=outcome, outcome_text=REFINANCING_OUTCOMES[outcome])
    return entry


def validate_t1(parsed: dict, t1: dict, pr: dict) -> dict:
    """Keep the evidence that holds up and re-derive the two independent adjustments: T1a, the principal removed
    (rollover debt with a qualifying backstop) and T1b, the refinancing relief (−2 for an executed refinancing of a
    year-1 maturity not already classified as long-term)."""
    by_id = {p["passage_id"]: p for p in t1["passages"]}
    end = date.fromisoformat(pr["period_end"])
    held, notes = [], []
    rollover = [e for r in parsed.get("rollover") or [] for e in [_rollover(r, by_id, pr, end, held, notes)] if e]
    refinancings = [e for f in parsed.get("refinancings") or []
                    for e in [_refinancing(f, by_id, pr, end, held, notes)] if e]
    # One backstop can stand behind several items (commercial paper and revolver drawings on the same facility): the
    # items removed must fit, together, within the largest backstop capacity shown. Each comes out whole or not at all.
    qualifying = [e for e in rollover if e["outcome"] == "remove_from_principal"]
    pool, removed = max((e["backstop"] for e in qualifying), default=0.0), 0.0
    for e in qualifying:
        if removed + e["balance"] > pool:
            e.update(outcome="backstop_too_small", outcome_text=ROLLOVER_OUTCOMES["backstop_too_small"])
            notes.append(f"{e['item_id']}: the backstop capacity shown ({pool / 1e6:,.1f} million) is already used by "
                         "the items before it; no adjustment.")
        else:
            e["removed"] = e["balance"]
            removed += e["balance"]
    if removed and pr["principal"] is None:
        notes.append("Year-1 principal isn't tagged, so nothing can be removed from it.")
        removed = 0.0
    elif removed and removed > pr["principal"]:
        notes.append(f"The rollover debt shown ({removed / 1e6:,.1f} million) is more than year-1 principal "
                     f"({pr['principal'] / 1e6:,.1f} million): all of year-1 principal is removed.")
        removed = pr["principal"]
    relief = T1_REFINANCING_RELIEF if any(e["outcome"] == "executed" for e in refinancings) else 0
    if removed or relief:
        status = "adjusted"
    elif any(e["outcome"] == "plan" for e in refinancings):
        status = "plan_only"
    else:
        status = "no_adjustment"
    return {"status": status, "rollover": rollover, "refinancings": refinancings, "principal_removed": removed,
            "refinancing_relief": relief, "held_back": held, "stated_status": parsed.get("status"),
            "status_consistent": parsed.get("status") == status, "validation_notes": notes}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_DSCR_T1_PROMPT, template=FO_DSCR_T1_TEMPLATE, response_schema=FO_DSCR_T1_SCHEMA,
                      role="analyze", label="Financial Overview Trigger Agent: DSCR T1")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

def _reason(status: str, removed: float, relief: int, refinancings: list[dict] = ()) -> str:
    plans = ", ".join(dict.fromkeys(e["event_evidence"]["passage_id"] for e in refinancings if e["outcome"] == "plan"))
    if status == "plan_only":
        return f"Only a stated plan to refinance year-1 maturities ({plans}): no adjustment, because a plan isn't executed"
    if status != "adjusted":
        return STATUS_TEXT[status]
    parts = ([f"T1a: rollover debt with a qualifying backstop removed from year-1 principal ({removed / 1e6:,.1f} "
              "million), DSCR recomputed"] if removed else []) + \
            ([f"T1b: year-1 maturities refinanced after the year-end (executed): −{relief} on the score"]
             if relief else [])
    text = "; ".join(parts)
    return text[:1].upper() + text[1:]


def dscr_t1_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None, mode: str = "live",
                    case_id: str | None = None) -> dict:
    """The DSCR T1 trigger result recorded in the run payload (``payload.triggers.dscr_t1``). ``metrics`` are the
    scored measures; the trigger reads DSCR's headline column (the forward column first)."""
    dscr = next((m for m in metrics if m["key"] == "dscr"), None)
    score, heading = headline_score(dscr) if dscr else (None, None)
    result = {"trigger": T1_ID, "measure": "dscr", "version": T1_VERSION, "threshold": T1_THRESHOLD,
              "score": score, "column": heading, "fired": False, "mode": mode if backend is not None else "none",
              "agent": None, "notes": [], "review_required": False}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", reason=STATUS_TEXT["skipped"],
                      effect="None: the measures are superseded by the going-concern fail.")
        return result
    if score is None or score < T1_THRESHOLD:
        result.update(status="not_triggered", decided_by="score",
                      reason=(f"DSCR scored {score:g}, below {T1_THRESHOLD}: trigger T1 not set off"
                              if score is not None else "DSCR has no score: trigger T1 not set off"),
                      effect="None.")
        return result
    pr = principal_facts(dscr["columns"][_headline_index(dscr)])
    t1 = t1_passages(data)
    signals = t1_signals(t1["passages"])
    result.update(fired=True, principal=pr, passages=t1["passages"], sections=t1["sections"],
                  rules={"signals": signals})
    if not t1["passages"]:
        checked = {"status": "no_adjustment", "rollover": [], "refinancings": [], "principal_removed": 0.0,
                   "refinancing_relief": 0, "status_consistent": True}
        decided_by = "no_text"
        result["notes"].append("No debt note, MD&A liquidity or subsequent-events passage to read: the agent was not "
                               "called, and the score stays as calculated.")
    else:
        require_agent(backend, T1_ID, AGENT)
        checked, decided_by = _run_agent(result, t1, data, pr, session, backend, case_id)
        used = {e[k]["passage_id"] for e in checked["rollover"] + checked["refinancings"]
                for k in ("balance_evidence", "rollover_evidence", "backstop_evidence", "event_evidence",
                          "reclassified_evidence", "maturity_evidence", "amount_evidence") if e.get(k)}
        result["notes"] += [f"The keyword rules found {s['signal']} in {s['passage_id']} that no verified item uses: "
                            f"\"{_clip(s['quote'], 300)}\". Read it to confirm." for s in signals
                            if s["passage_id"] not in used]
    status = checked["status"]
    removed, relief = checked["principal_removed"], checked["refinancing_relief"]
    # Unresolved: the agent's own answer and its verified evidence disagree. Only then does an analyst decide;
    # otherwise the verified outcome stands (no adjustment: the score stays as calculated).
    unresolved = not checked["status_consistent"]
    result.update(status=status, decided_by=decided_by, rollover=checked["rollover"],
                  refinancings=checked["refinancings"], principal_removed=removed, refinancing_relief=relief,
                  reason=_reason(status, removed, relief, checked["refinancings"]), shared_with=SHARED_WITH,
                  unresolved=unresolved, review_required=unresolved,
                  effect=("DSCR recomputed and adjusted as above" if status == "adjusted"
                          else "None: the DSCR score stays as calculated")
                  + ("; unresolved (the agent's answer and the verified evidence disagree), so DSCR is escalated to "
                     "an analyst, who accepts the score or overrides it." if unresolved else
                     "; resolved (the agent and the verified evidence agree), so no analyst decision is needed."))
    return result


def _run_agent(result, t1, data, pr, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_DSCR_T1_PROMPT,
                            t1_inputs(t1, data["meta"], pr))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's DSCR T1 output was rejected ({agent['validation_detail']}); "
                                 "the run was not recorded. Run it again.")
    checked = validate_t1(run.parsed, t1, pr)
    agent.update(rationale=run.parsed.get("rationale"), missing_information=run.parsed.get("missing_information") or [],
                 held_back=checked["held_back"], derived_status=checked["status"],
                 stated_status=checked["stated_status"], status_consistent=checked["status_consistent"])
    result["notes"] += checked["validation_notes"]
    if checked["held_back"]:
        result["notes"].append(f"{len(checked['held_back'])} agent item(s) held back by validation.")
    if not checked["status_consistent"]:
        result["notes"].append(f"The agent stated {str(checked['stated_status']).upper()} but its verified evidence "
                               f"supports {checked['status'].upper()}; the verified evidence decides.")
    return checked, "agent"


# --- the measure -------------------------------------------------------------------------------------------------

def apply_t1(fo: list[dict], t1: dict | None) -> list[dict]:
    """DSCR with the trigger's outcome on its headline column (a copy; the inputs stay untouched): rollover debt with
    backstop capacity removed from principal and the ratio and score recomputed, then −2 for an executed refinancing
    (not below 1). The column keeps the figures as reported. The measure is marked for escalation only when the
    trigger is unresolved; with no adjustment and nothing unresolved, the score stays as calculated."""
    if not t1 or not t1.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "dscr":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        col = m["columns"][_headline_index(m)]
        adj = {"trigger": "T1", "calculated": col["score"], "ratio_calculated": col["ratio"],
               "principal_removed": t1["principal_removed"], "recomputed": None,
               "refinancing": -t1["refinancing_relief"] if t1["refinancing_relief"] else 0}
        done = []
        if t1["principal_removed"]:
            col["principal_reported"], col["debt_service_reported"] = col["principal"], col["debt_service"]
            col["principal"] -= t1["principal_removed"]
            col["debt_service"] = col["interest"] + col["principal"] + col["rent"]
            col["ratio"] = col["cash_flow"] / col["debt_service"] if col["debt_service"] else None
            col["score"] = adj["recomputed"] = risk_score(col["ratio"], DSCR_BANDS)
            col["reason"] = None if col["ratio"] is not None else "No debt service to cover"
            done.append(f"T1a: {t1['principal_removed'] / 1e6:,.1f} million of rollover debt with a qualifying backstop "
                        f"removed from principal: DSCR {adj['ratio_calculated']:.2f}x → "
                        + (f"{col['ratio']:.2f}x" if col["ratio"] is not None else "no debt service") + ", score "
                        f"{adj['calculated']:g} → " + (f"{col['score']:g}" if col["score"] is not None else "none"))
        if t1["refinancing_relief"] and col["score"] is not None:
            before = col["score"]
            col["score"] = max(1, col["score"] - t1["refinancing_relief"])
            done.append(f"T1b: year-1 maturities refinanced after the year-end (executed): {before:g} → "
                        f"{col['score']:g}")
        adj["score"] = col["score"]
        col["trigger_adjustment"] = adj
        unresolved = t1.get("unresolved", True)  # runs recorded before 2026-10-22 always escalated
        add_trigger_note(m, (f"DSCR trigger T1 (score {adj['calculated']:g} ≥ {T1_THRESHOLD}, {col['heading']}): "
                             + ("; ".join(done) if done else t1["reason"][:1].lower() + t1["reason"][1:])),
                         "trigger T1: " + ("adjusted" if done else "no adjustment"), unresolved)
        out.append(m)
    return out


def add_trigger_note(m: dict, note: str, summary: str, unresolved: bool,
                     why: str = "the agent's answer and the verified evidence disagree") -> None:
    """Add one trigger's note to its measure, after any earlier trigger's (DSCR: T2, T3, then T1). The measure is
    escalated when any of its triggers is unresolved; ``why`` says why this one is."""
    note += (f". Unresolved: {why}." if unresolved else ". Resolved: the agent and the verified evidence agree.")
    summary += ", unresolved, escalated" if unresolved else ""
    if m.get("trigger_note"):
        note = f"{m['trigger_note'].replace(f' {ESCALATED_NOTE}', '')} {note}"
        summary = f"{m['trigger_summary']}; {summary}"
    m["trigger_escalated"] = bool(m.get("trigger_escalated")) or unresolved
    m["trigger_note"] = f"{note} {ESCALATED_NOTE}" if m["trigger_escalated"] else note  # once, at the end
    m["trigger_summary"] = summary
