"""FO Trigger Agent, Liquidity T2: liquidity coverage or refinancing coverage (the maturity test) scores 9 or more after
Liquidity T1. The debt driving the score may already have been refinanced, extended or repaid after the year-end.

Liquidity T2 runs on the liquidity-and-maturity measure's headline column (the latest year-end) after Liquidity T1,
only when the pre-checks allow it (``payload.gate.agent_triggers`` is "enabled"), when the coverage score or the
refinancing score is 9 or more. It doesn't fire after a covenant breach not waived (the score is set to 10 whatever
the maturities). Same three steps as the other triggers:

1. ``liquidity_t2_passages`` (deterministic) collects what the agent checks: every subsequent-events paragraph, the
   paragraphs and table rows of the debt note and MD&A liquidity that mention a refinancing, extension, repayment,
   maturity or new financing, and any going-concern disclosure (the note, or the auditor's report) (ids R01…). DSCR
   T1's verified refinancing record is passed in too (``dscr_t1``), and reused: the agent cites its items instead of
   re-checking them.
2. The Trigger Agent (prompt ``fo_trigger_liquidity_t2``) ALWAYS reads them (no agent, or a rejected output, refuses
   the run). Every fact is quoted verbatim. Only a filing with none of those passages (and no DSCR T1 item) has
   nothing to read (no model call: nothing found, so no adjustment).
3. ``validate_liquidity_t2`` keeps only verbatim evidence that says what it must and RE-DERIVES each debt item's
   outcome, first match wins:

   - already classified as long-term because it was refinanced (ASC 470-10-45-14): no further adjustment;
   - refinanced or extended after the year-end, and executed: the debt moves to its new maturity, both tests
     recomputed;
   - repaid after the year-end from cash: the debt and the cash used come out, recomputed;
   - repaid from new borrowing or equity: the debt comes out, the new debt goes in at its maturity, recomputed;
   - only planned: unresolved.

   An executed item that can't be placed (its original maturity, amount, new maturity or source of repayment not
   verified) is unresolved too. The executed items that are verified are applied even when another item is
   unresolved.

Liquidity and debt maturity is escalated when unresolved, and, as for every trigger, when the agent's own answer and
its verified evidence disagree.
"""

from __future__ import annotations

import copy
import re
from datetime import date

from app.prompts.fo_prompts import FO_LIQUIDITY_T2_PROMPT, FO_LIQUIDITY_T2_SCHEMA, FO_LIQUIDITY_T2_TEMPLATE

from .covenants import _section_after
from .dscr_triggers import (
    EXECUTED,
    PLAN_WORDS,
    RECLASSIFIED,
    _dated,
    _ev,
    _held,
    _millions,
    _quoted,
    _span,
    _value,
    add_trigger_note,
    collect_passages,
    stated_amount,
)
from .liquidity_triggers import _times, rescore_liquidity
from .measures import MATURITY_BUCKETS, set_ladder
from .precheck import DATA_NOTICE, GC_MENTION, PrecheckAgentError, call_agent, going_concern_passages, require_agent
from .scoring import headline_score

LIQUIDITY_T2_VERSION = "fo-trigger-liquidity-t2-1.0.0"
LIQUIDITY_T2_ID = "liquidity_t2"
LIQUIDITY_T2_THRESHOLD = 9  # a coverage or refinancing score at or above this sets the trigger off
AGENT = "Trigger Agent"

SECTIONS = [("subsequent_events", "Subsequent events"), ("debt_note", "Debt note"),
            ("mdna_liquidity", "MD&A: liquidity"), ("going_concern", "Going-concern disclosure")]
LIMITS = {"debt_note": 30, "mdna_liquidity": 15, "subsequent_events": 15}
GC_LIMIT = 10
LIQUIDITY_T2_TERMS = re.compile(
    r"refinanc\w*|redeem\w*|redemption|repa(id|y\w*)|prepa(id|y\w*)|extend\w*|extension|amend\w*|matur\w*|"
    r"\bdue (in|on|within)\b|current (portion|maturities)|classified as (long-term|non-?current)|470-10-45-14|"
    r"intent and (the )?ability to refinance|issu(ed|ance)|offering|proceeds|new (senior )?(notes|debt|term loan|"
    r"facility|financing)|retir\w*|repurchas\w*|tender\w*", re.I)

# --- what a quote must say ---------------------------------------------------------------------------------------
KIND_WORDS = {
    "refinanced": (re.compile(r"refinanc\w*|exchang\w*|replac\w*|issu\w*", re.I),
                   "the quote doesn't show a refinancing"),
    "extended": (re.compile(r"extend\w*|extension|amend\w*", re.I), "the quote doesn't show an extension"),
    "repaid": (re.compile(r"repa(id|y\w*)|redeem\w*|redemption|prepa(id|y\w*)|retir\w*|repurchas\w*|settl\w*|"
                          r"tender\w*|paid (off|down|in full)", re.I), "the quote doesn't show a repayment"),
}
PLANNED_WORDS = re.compile(r"refinanc\w*|extend\w*|extension|repa(id|y\w*)|redeem\w*|redemption|retir\w*|issu\w*|"
                           r"amend\w*", re.I)
SOURCE_WORDS = {
    "cash": (re.compile(r"cash (on hand|and cash equivalents)|available cash|existing cash|\bcash\b", re.I),
             "the quote doesn't show it was repaid from cash"),
    "new_debt": (re.compile(r"proceeds (from|of)|net proceeds|borrow\w*|new (senior )?(notes|debt|term loan|"
                            r"facility)|issu\w*|offering|draw\w* (on|under)", re.I),
                 "the quote doesn't show it was repaid from new borrowing"),
    "equity": (re.compile(r"equity|(common|preferred) (stock|shares)|share (issu\w*|offering)|stock offering|"
                          r"rights issue", re.I), "the quote doesn't show it was repaid from equity"),
}
BUCKET_TEXT = {key: label for key, label, _ in MATURITY_BUCKETS}
ROUTE_TEXT = {"already_long_term": "already classified as long-term (ASC 470-10-45-14): no further adjustment",
              "moved": "moved to its new maturity", "repaid_cash": "repaid from cash: debt and cash used removed",
              "repaid_new_debt": "repaid from new borrowing: debt removed, new debt added at its maturity",
              "repaid_equity": "repaid from equity: debt removed", "planned": "only planned: unresolved",
              "incomplete": "executed, but can't be placed: unresolved"}
APPLIED = ("moved", "repaid_cash", "repaid_new_debt", "repaid_equity")
KIND_TEXT = {"refinanced": "Refinanced", "extended": "Extended", "repaid": "Repaid"}

STATUS_TEXT = {"skipped": "Skipped: agent triggers are skipped after a going-concern fail"}
RULES = [("Debt already classified as long-term because it was refinanced (ASC 470-10-45-14)",
          "No further adjustment"),
         ("Refinanced or extended after the year-end, and executed",
          "The debt moves to its new maturity; both tests recomputed"),
         ("Repaid after the year-end from cash", "The debt and the cash used come out; both tests recomputed"),
         ("Repaid from new borrowing or equity",
          "The debt comes out, the new debt goes in at its maturity; both tests recomputed"),
         ("Only planned", "Unresolved: liquidity and debt maturity escalated to an analyst, who accepts the score or "
                          "overrides it"),
         ("Executed, but its original maturity, amount, new maturity or source isn't verified",
          "Unresolved: liquidity and debt maturity escalated"),
         ("Nothing refinanced, extended or repaid found", "No adjustment: the score reflects what was reported"),
         ("The agent's answer and the verified evidence disagree",
          "Unresolved: liquidity and debt maturity escalated to an analyst, who accepts the score or overrides it")]


# --- the passages ------------------------------------------------------------------------------------------------

def liquidity_t2_passages(data: dict) -> dict:
    """Every subsequent-events paragraph, the debt note's and MD&A liquidity's paragraphs and tables that mention a
    refinancing, extension, repayment, maturity or new financing, then the going-concern disclosure (the auditor's
    report paragraphs and the filing's sentences that mention going concern or substantial doubt, outside the risk
    factors); ids R01…, the going-concern passages last."""
    t = collect_passages(data, "R", LIMITS, SECTIONS[:3], LIQUIDITY_T2_TERMS, _section_after,
                         read_whole=("subsequent_events",))
    gc = going_concern_passages(data)["passages"]
    found = [p for p in gc["auditor"] if GC_MENTION.search(p["text"])] + [
        p for p in gc["management"] if p.get("section") != "risk_factors"]
    seen = {re.sub(r"\W+", "", p["text"].lower())[:150] for p in t["passages"]}
    extra = []
    for p in found:
        key = re.sub(r"\W+", "", p["text"].lower())[:150]
        if key not in seen and len(extra) < GC_LIMIT:
            seen.add(key)
            extra.append({"item": p["item"], "section": "going_concern", "section_label": "Going-concern disclosure",
                          "kind": "text", "text": p["text"], "scale": None})
    passages = t["passages"] + extra
    for i, p in enumerate(passages, 1):
        p["passage_id"] = f"R{i:02d}"
    return {"passages": passages,
            "sections": t["sections"] + [{"key": "going_concern", "label": "Going-concern disclosure",
                                          "located": bool(found), "passages": len(extra)}]}


def _headline_index(m: dict) -> int | None:
    return next((i for i, c in enumerate(m["columns"]) if c.get("score") is not None), None)


def ladder_of(col: dict) -> dict:
    """{bucket: principal} on a liquidity column, the buckets it has."""
    return {key: col[key] for key, _, _ in MATURITY_BUCKETS if col.get(key) is not None}


def maturity_facts(m: dict, i: int) -> dict:
    """What the engine counted for the triggering column."""
    col = m["columns"][i]
    return {"heading": col["heading"], "as_at": col.get("as_at"), "cash": col.get("cash"), "y1": col.get("y1"),
            "ladder": ladder_of(col), "ladder_source": col.get("ladder_source"),
            "weighted_maturities": col.get("weighted_maturities"), "coverage": col.get("coverage"),
            "coverage_score": col.get("coverage_score"), "refinancing": col.get("refinancing"),
            "refinancing_score": col.get("refinancing_score"), "score": col.get("score")}


def dscr_t1_record(t1: dict | None) -> list[dict]:
    """DSCR T1's verified refinancings (T1b), as Liquidity T2 reuses them: empty when T1 didn't fire."""
    if not (t1 and t1.get("fired")):
        return []
    return [{"item_id": e["item_id"], "outcome": e["outcome"], "date": e.get("date"), "maturity": e.get("maturity"),
             "amount": e.get("amount"), "event_evidence": e.get("event_evidence"),
             "reclassified_evidence": e.get("reclassified_evidence"), "maturity_evidence": e.get("maturity_evidence"),
             "amount_evidence": e.get("amount_evidence")} for e in t1.get("refinancings") or []]


def liquidity_t2_inputs(t: dict, meta: dict, fx: dict, reused: list[dict]) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": fx["as_at"]},
        "liquidity": {"column": fx["heading"], "cash_millions": _millions(fx["cash"]),
                      "maturities_millions": {BUCKET_TEXT[k]: _millions(v) for k, v in fx["ladder"].items()},
                      "maturity_table_tagged": bool(fx["ladder_source"]),
                      "liquidity_coverage": _ratio(fx["coverage"]), "coverage_score": fx["coverage_score"],
                      "refinancing_coverage": _ratio(fx["refinancing"]), "refinancing_score": fx["refinancing_score"],
                      "trigger": f"coverage score or refinancing score {LIQUIDITY_T2_THRESHOLD} or more"},
        "dscr_t1": [{"item_id": e["item_id"], "outcome": e["outcome"], "date": e["date"],
                     "year1_maturity": e["maturity"], "amount_millions": _millions(e["amount"]),
                     "quote": (e["event_evidence"] or {}).get("quote")} for e in reused],
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t["passages"]],
    }


def _ratio(v):
    return None if v is None or v == float("inf") else round(v, 2)


# --- validation --------------------------------------------------------------------------------------------------

def _years_after(end: date, n: int) -> date:
    try:
        return end.replace(year=end.year + n)
    except ValueError:  # 29 February
        return date(end.year + n, 2, 28)


def bucket_of(day: date, end: date) -> str | None:
    """The maturity-table bucket a date after the year-end falls in (year 1 is the 12 months after it); None on or
    before the year-end."""
    if day <= end:
        return None
    return next((f"y{n}" for n in range(1, 6) if day <= _years_after(end, n)), "after")


def _maturity(e, by_id, end, item, fact, held):
    """(bucket, date, evidence) for a verified maturity after the year-end, or None (held back). A date given only as
    a year or a month that spans two buckets goes in the earlier one (more weight: the conservative side)."""
    if e is None:
        return None
    p, why = _quoted(e, by_id, re.compile(r"\d{4}"), "the quote gives no date")
    span = None
    if not why:
        span, why = _dated(e)
    if not why and span[1] <= end:
        why = f"the maturity {e['date']} is not after the year-end"
    if why:
        held.append(_held(item, fact, e, why))
        return None
    return bucket_of(span[0] if span[0] > end else span[1], end), e["date"], _ev(e, p)


def _event(o, reused, by_id, end, item, held, notes):
    """The item's event: ("executed" | "planned", date, evidence), its own quote first, else DSCR T1's; or None."""
    kind = o.get("kind")
    words, why_kind = KIND_WORDS.get(kind, (PLANNED_WORDS, "the quote doesn't show a refinancing, extension or "
                                                           "repayment"))
    e = o.get("event")
    if e is not None:
        p, why = _quoted(e, by_id, words if kind in KIND_WORDS else PLANNED_WORDS, why_kind)
        if not why:
            planned = kind == "planned" or bool(PLAN_WORDS.search(e["quote"])) or not EXECUTED.search(e["quote"])
            if planned:
                if kind != "planned":
                    notes.append(f"{item['item_id']}: the agent called this {kind}, but its quote reads as a plan "
                                 "(nothing completed).")
                return "planned", e.get("date"), _ev(e, p)
            span, why = _dated(e)
            if not why and span[0] <= end:
                why = "dated on or before the year-end: already in the year-end figures"
            if not why:
                return "executed", e["date"], _ev(e, p)
        held.append(_held(item, "event", e, why))
    if reused:  # DSCR T1 verified the event (executed after the year-end, or only a plan)
        if reused["outcome"] == "plan":
            return "planned", reused["date"], {**reused["event_evidence"], "reused": "DSCR T1"}
        if reused["outcome"] in ("executed", "not_year1") and kind in KIND_WORDS:
            if words.search(reused["event_evidence"]["quote"]):
                return "executed", reused["date"], {**reused["event_evidence"], "reused": "DSCR T1"}
            notes.append(f"{item['item_id']}: DSCR T1's event quote doesn't show it was {kind}; not reused.")
    return None


def _item(o, reused_by_id, by_id, end, ladder, held, notes):
    """One verified debt item with its outcome re-derived (first match), or None (held back)."""
    item = {"item_id": o.get("item_id"), "kind": o.get("kind"), "description": o.get("description")}
    reused = reused_by_id.get(o.get("dscr_t1_item")) if o.get("dscr_t1_item") else None
    if o.get("dscr_t1_item") and reused is None:
        notes.append(f"{item['item_id']}: cites DSCR T1 item {o['dscr_t1_item']}, which T1 didn't verify; not reused.")
    entry = {**item, "dscr_t1_item": reused["item_id"] if reused else None, "route": None, "unresolved_why": None,
             "date": None, "from": None, "to": None, "amount": None, "cash_used": None, "evidence": {},
             "source": None}
    # 1. Already classified as long-term at the year-end (ASC 470-10-45-14): no further adjustment.
    rc = o.get("reclassified")
    if rc is not None:
        p, why = _quoted(rc, by_id, RECLASSIFIED, "the quote doesn't say the debt was classified as long-term")
        if why:
            held.append(_held(item, "reclassified", rc, why))
        else:
            entry["evidence"]["reclassified"] = _ev(rc, p)
    if not entry["evidence"].get("reclassified") and reused and reused["outcome"] == "reclassified":
        entry["evidence"]["reclassified"] = {**reused["reclassified_evidence"], "reused": "DSCR T1"}
    if entry["evidence"].get("reclassified"):
        return {**entry, "route": "already_long_term"}
    event = _event(o, reused, by_id, end, item, held, notes)
    if event is None:
        held.append(_held(item, "item", None, "no verified refinancing, extension or repayment"))
        return None
    state, entry["date"], entry["evidence"]["event"] = event
    if state == "planned":
        return {**entry, "route": "planned", "unresolved_why": "only planned"}
    missing = []
    # The debt's original maturity and the amount: its own quotes first, else DSCR T1's (a year-1 maturity).
    got = _maturity(o.get("original_maturity"), by_id, end, item, "original_maturity", held)
    if got:
        entry["from"], _, entry["evidence"]["original_maturity"] = got
    elif reused and reused.get("maturity"):
        entry["from"], entry["evidence"]["original_maturity"] = "y1", {**reused["maturity_evidence"],
                                                                       "reused": "DSCR T1"}
    else:
        missing.append("the original maturity")
    a = o.get("amount")
    if a is not None:
        p, why = _quoted(a, by_id)
        value = None
        if not why:
            value, why = _value(a, p)
        if why:
            held.append(_held(item, "amount", a, why))
        else:
            entry["amount"], entry["evidence"]["amount"] = value, _ev(a, p)
    if entry["amount"] is None and reused and reused.get("amount") is not None:
        entry["amount"], entry["evidence"]["amount"] = reused["amount"], {**reused["amount_evidence"],
                                                                          "reused": "DSCR T1"}
    if entry["amount"] is None:
        missing.append("the amount")
    kind = o.get("kind")
    if kind == "repaid":
        source = o.get("source")
        se = o.get("source_evidence")
        if source not in SOURCE_WORDS:
            missing.append("the source of repayment")
        else:
            p, why = _quoted(se, by_id, *SOURCE_WORDS[source])
            if why:
                held.append(_held(item, "source", se, why))
                missing.append("the source of repayment")
            else:
                entry["source"], entry["evidence"]["source"] = source, _ev(se, p)
    needs_new = kind in ("refinanced", "extended") or entry["source"] == "new_debt"
    if needs_new:
        got = _maturity(o.get("new_maturity"), by_id, end, item, "new_maturity", held)
        if got:
            entry["to"], _, entry["evidence"]["new_maturity"] = got
        else:
            missing.append("the new maturity")
    if missing:
        return {**entry, "route": "incomplete", "unresolved_why": "executed, but " + ", ".join(missing)
                + " not verified"}
    if entry["from"] not in ladder:
        notes.append(f"{item['item_id']}: the maturity table has no {BUCKET_TEXT[entry['from']].lower()} amount to "
                     "take it from; nothing moved.")
        return {**entry, "route": "incomplete", "unresolved_why": f"no {BUCKET_TEXT[entry['from']].lower()} "
                                                                  "maturities to take it from"}
    if kind in ("refinanced", "extended"):
        route = "moved"
    else:
        route = f"repaid_{entry['source']}"
        if entry["source"] == "cash":
            entry["cash_used"] = entry["amount"]
            c = o.get("cash_used")
            if c is not None:
                p, why = _quoted(c, by_id)
                value = None
                if not why:
                    value, why = _value(c, p)
                if why:
                    held.append(_held(item, "cash_used", c, why))
                else:
                    entry["cash_used"], entry["evidence"]["cash_used"] = value, _ev(c, p)
    return {**entry, "route": route}


def adjustments_for(items: list[dict]) -> list[dict]:
    """The ladder and cash changes the applied items make: {item_id, from, to, amount, cash_used}."""
    return [{"item_id": e["item_id"], "from": e["from"], "to": e["to"] if e["route"] in ("moved", "repaid_new_debt")
             else None, "amount": e["amount"], "cash_used": e["cash_used"] or 0.0}
            for e in items if e["route"] in APPLIED]


def validate_liquidity_t2(parsed: dict, t: dict, fx: dict, reused: list[dict]) -> dict:
    """Keep the evidence that holds up and re-derive the outcome: unresolved (an item only planned, or executed but
    not placed; the verified executed items are still applied); recomputed (an item moved, or repaid); already long
    term (only ASC 470-10-45-14 items); else nothing found (no adjustment)."""
    end = date.fromisoformat(fx["as_at"])
    by_id = {p["passage_id"]: p for p in t["passages"]}
    reused_by_id = {e["item_id"]: e for e in reused}
    held, notes = [], []
    items = [e for o in parsed.get("items") or []
             for e in [_item(o, reused_by_id, by_id, end, fx["ladder"], held, notes)] if e]
    unresolved = [e for e in items if e["route"] in ("planned", "incomplete")]
    applied = [e for e in items if e["route"] in APPLIED]
    why = None
    if unresolved:
        status = "unresolved"
        why = "; ".join(f"{e['item_id']}: {e['unresolved_why']}" for e in unresolved)
        if applied:
            notes.append(f"The verified executed items ({', '.join(e['item_id'] for e in applied)}) are applied; "
                         "the rest is unresolved.")
    elif applied:
        status = "recomputed"
    elif items:
        status = "already_long_term"
    else:
        status = "none_found"
    return {"status": status, "items": items, "adjustments": adjustments_for(items), "unresolved_why": why,
            "held_back": held, "stated_status": parsed.get("status"),
            "status_consistent": parsed.get("status") == status, "validation_notes": notes}


def _stated_bucket(e, end):
    """The bucket of a date as the agent states it (not checked against the filing), or None."""
    m = re.fullmatch(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", ((e or {}).get("date") or "").strip())
    span = m and _span((int(m.group(1)), int(m.group(2)) if m.group(2) else None,
                        int(m.group(3)) if m.group(3) else None))
    return bucket_of(span[0] if span[0] > end else span[1], end) if span else None


def agent_reading(parsed: dict, t: dict, fx: dict) -> dict:
    """The agent's answer as it states it (not verified, not applied), for an analyst to compare with the verified
    outcome: "recomputed" or "unresolved" applies every item it calls refinanced, extended or repaid, at its stated
    amounts and dates (DSCR T1's item reused as a year-1 maturity when it gives no date). Any other answer changes
    nothing."""
    end = date.fromisoformat(fx["as_at"])
    by_id = {p["passage_id"]: p for p in t["passages"]}
    stated, notes, out = parsed.get("status"), [], []
    if stated in ("recomputed", "unresolved"):
        for o in parsed.get("items") or []:
            kind, source = o.get("kind"), o.get("source")
            if kind not in KIND_WORDS or o.get("reclassified") is not None:
                continue
            amount = stated_amount(o.get("amount"), by_id, notes, o.get("item_id"))
            frm = _stated_bucket(o.get("original_maturity"), end) or ("y1" if o.get("dscr_t1_item") else None)
            to = _stated_bucket(o.get("new_maturity"), end) if kind != "repaid" or source == "new_debt" else None
            if amount is None or frm not in fx["ladder"] or (to is None and (kind != "repaid" or source == "new_debt")):
                continue
            cash = (stated_amount(o.get("cash_used"), by_id, notes, o.get("item_id")) or amount) \
                if kind == "repaid" and source == "cash" else 0.0
            out.append({"item_id": o.get("item_id"), "from": frm, "to": to, "amount": amount, "cash_used": cash})
    return {"status": stated, "notes": notes, "applies": {"status": stated, "adjustments": out}}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_LIQUIDITY_T2_PROMPT, template=FO_LIQUIDITY_T2_TEMPLATE,
                      response_schema=FO_LIQUIDITY_T2_SCHEMA, role="analyze",
                      label="Financial Overview Trigger Agent: Liquidity T2")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

def _describe(e: dict) -> str:
    amount = f"{e['amount'] / 1e6:,.1f} million"
    if e["route"] == "moved":
        return (f"{e['item_id']}: {amount} {e['kind']}, {BUCKET_TEXT[e['from']].lower()} → "
                f"{BUCKET_TEXT[e['to']].lower()}")
    if e["route"] == "repaid_cash":
        return (f"{e['item_id']}: {amount} repaid from cash ({BUCKET_TEXT[e['from']].lower()} removed, "
                f"{e['cash_used'] / 1e6:,.1f} million cash used)")
    if e["route"] == "repaid_new_debt":
        return (f"{e['item_id']}: {amount} repaid from new borrowing ({BUCKET_TEXT[e['from']].lower()} → new debt in "
                f"{BUCKET_TEXT[e['to']].lower()})")
    return f"{e['item_id']}: {amount} repaid from equity ({BUCKET_TEXT[e['from']].lower()} removed)"


def _reason(status: str, checked: dict) -> str:
    applied = "; ".join(_describe(e) for e in checked["items"] if e["route"] in APPLIED)
    if status == "recomputed":
        return f"Refinanced, extended or repaid after the year-end ({applied}): both tests recomputed"
    if status == "already_long_term":
        ids = ", ".join(e["item_id"] for e in checked["items"])
        return f"Debt already classified as long-term (ASC 470-10-45-14) ({ids}): no further adjustment"
    if status == "none_found":
        return "Nothing refinanced, extended or repaid after the year-end found: no adjustment"
    return "Unresolved: " + checked["unresolved_why"] + (f" (applied: {applied})" if applied else "")


def liquidity_t2_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None,
                         mode: str = "live", case_id: str | None = None, dscr_t1: dict | None = None) -> dict:
    """The Liquidity T2 trigger result recorded in the run payload (``payload.triggers.liquidity_t2``). ``metrics``
    are the scored measures after Liquidity T1; ``dscr_t1`` is DSCR T1's result, whose verified refinancings are
    reused."""
    m = next((x for x in metrics if x["key"] == "liquidity"), None)
    score, heading = headline_score(m) if m else (None, None)
    result = {"trigger": LIQUIDITY_T2_ID, "measure": "liquidity", "version": LIQUIDITY_T2_VERSION,
              "threshold": LIQUIDITY_T2_THRESHOLD, "score": score, "column": heading, "fired": False,
              "mode": mode if backend is not None else "none", "agent": None, "notes": [], "review_required": False}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", reason=STATUS_TEXT["skipped"],
                      effect="None: the measures are superseded by the going-concern fail.")
        return result
    if score is None:
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason="Liquidity and debt maturity has no score: trigger Liquidity T2 not set off")
        return result
    i = _headline_index(m)
    col = m["columns"][i]
    fx = maturity_facts(m, i)
    result["inputs"] = fx
    if (col.get("covenant_adjustment") or {}).get("status") == "breach":
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason="The score is set to 10 after the covenant breach (the covenant pre-check), whatever the "
                             "maturities: trigger Liquidity T2 not set off")
        return result
    hits = [f"{name} score {s:g}" for name, s in (("coverage", fx["coverage_score"]),
                                                  ("refinancing", fx["refinancing_score"]))
            if s is not None and s >= LIQUIDITY_T2_THRESHOLD]
    if not hits:
        scores = ", ".join(f"{name} {s:g}" for name, s in (("coverage", fx["coverage_score"]),
                                                           ("refinancing", fx["refinancing_score"])) if s is not None)
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason=f"Scores below {LIQUIDITY_T2_THRESHOLD} ({scores}): trigger Liquidity T2 not set off")
        return result
    reused = dscr_t1_record(dscr_t1)
    t = liquidity_t2_passages(data)
    result.update(fired=True, passages=t["passages"], sections=t["sections"], dscr_t1=reused,
                  fired_by=" and ".join(hits) + f" ({LIQUIDITY_T2_THRESHOLD} or more)")
    if not t["passages"] and not reused:
        checked = {"status": "none_found", "items": [], "adjustments": [], "unresolved_why": None,
                   "status_consistent": True}
        decided_by = "no_text"
        result["notes"].append("No subsequent-events, debt-note, MD&A liquidity or going-concern passage to read: the "
                               "agent was not called. Nothing found, so no adjustment.")
    else:
        require_agent(backend, LIQUIDITY_T2_ID, AGENT)
        checked, decided_by = _run_agent(result, t, data, fx, reused, session, backend, case_id)
    status = checked["status"]
    disagree = not checked["status_consistent"]
    unresolved = status == "unresolved" or disagree
    why = ("the agent's answer and the verified evidence disagree" if disagree else checked["unresolved_why"]) \
        if unresolved else None
    adjusted = bool(checked["adjustments"])
    result.update(status=status, decided_by=decided_by, items=checked["items"], adjustments=checked["adjustments"],
                  reason=_reason(status, checked), unresolved=unresolved, disagreement=disagree,
                  review_required=unresolved, unresolved_why=why,
                  effect=("The maturities (and cash) adjusted for the verified items; both tests recomputed"
                          if adjusted else "None: the liquidity-and-maturity score stays as calculated")
                  + (f"; unresolved ({why}), so liquidity and debt maturity is escalated to an analyst, who accepts "
                     "the score or overrides it." if unresolved else
                     "; resolved (the agent and the verified evidence agree), so no analyst decision is needed."))
    return result


def _run_agent(result, t, data, fx, reused, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_LIQUIDITY_T2_PROMPT,
                            liquidity_t2_inputs(t, data["meta"], fx, reused))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's Liquidity T2 output was rejected "
                                 f"({agent['validation_detail']}); the run was not recorded. Run it again.")
    checked = validate_liquidity_t2(run.parsed, t, fx, reused)
    result["agent_reading"] = agent_reading(run.parsed, t, fx)
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

def _score(v):
    return "none" if v is None else f"{v:g}"


def apply_liquidity_t2(fo: list[dict], t: dict | None) -> list[dict]:
    """Liquidity and debt maturity with the trigger's outcome on its headline column (a copy; the inputs stay
    untouched): each item's amount taken out of its original maturity bucket (never below 0) and, when refinanced,
    extended or repaid from new borrowing, put in at its new maturity; the cash used for a repayment from cash taken
    out of cash (never below 0). Without a tagged maturity table only year 1 changes (a new maturity isn't placed).
    Both tests are recomputed and rescored as the column was scored. The measure is marked for escalation when the
    trigger is unresolved."""
    if not t or not t.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "liquidity":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        col = m["columns"][_headline_index(m)]
        adj = {"trigger": "Liquidity T2", "outcome": t["status"], "calculated": col["score"],
               "coverage_calculated": col.get("coverage"), "coverage_score_calculated": col.get("coverage_score"),
               "refinancing_calculated": col.get("refinancing"),
               "refinancing_score_calculated": col.get("refinancing_score"),
               "ladder_calculated": ladder_of(col), "cash_calculated": col.get("cash"), "recomputed": None}
        notes, done = [], None
        moves = t.get("adjustments") or []
        if moves:
            ladder, table = ladder_of(col), bool(col.get("ladder_source"))
            for a in moves:
                taken = min(a["amount"], ladder.get(a["from"]) or 0.0)
                if taken < a["amount"]:
                    notes.append(f"{a['item_id']}: only {taken / 1e6:,.1f} million is due in "
                                 f"{BUCKET_TEXT[a['from']].lower()}, so that much is taken out")
                if a["from"] in ladder:
                    ladder[a["from"]] -= taken
                if a["to"] and table:
                    ladder[a["to"]] = (ladder.get(a["to"]) or 0.0) + taken
                elif a["to"]:
                    notes.append(f"{a['item_id']}: no maturity table tagged, so the new maturity isn't placed")
                if a["cash_used"] and col.get("cash") is not None:
                    col["cash"] = max(0.0, col["cash"] - a["cash_used"])
            set_ladder(col, ladder, col.get("ladder_source"), col.get("y1_source"))
            rescore_liquidity(col)
            adj["recomputed"] = col["score"]
            done = (f"{len(moves)} item(s) moved or removed: debt due in 12 months "
                    f"{adj['ladder_calculated'].get('y1', 0) / 1e6:,.1f} → {(col.get('y1') or 0) / 1e6:,.1f} "
                    f"million, liquidity coverage {_times(adj['coverage_calculated'])} → {_times(col['coverage'])}, "
                    f"refinancing "
                    f"coverage {_times(adj['refinancing_calculated'])} → {_times(col['refinancing'])}, score "
                    f"{_score(adj['calculated'])} → {_score(col['score'])}" + "".join(f"; {n}" for n in notes))
        adj.update(score=col["score"], notes=notes)
        col["maturity_adjustment"] = adj
        add_trigger_note(m, (f"Liquidity trigger T2 ({t.get('fired_by')}, {col['heading']}): "
                             + (done or t["reason"][:1].lower() + t["reason"][1:])),
                         "trigger Liquidity T2: " + {"recomputed": "maturities recomputed",
                                                     "already_long_term": "already long-term",
                                                     "none_found": "nothing found"}.get(t["status"], "unresolved"),
                         t.get("unresolved", True), t.get("unresolved_why") or "maturities not resolved")
        out.append(m)
    return out
