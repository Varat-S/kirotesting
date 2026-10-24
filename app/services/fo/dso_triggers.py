"""FO Trigger Agent, DSO T1: DSO moved by 5 days or more, which may reflect collections getting worse or better, or
something mechanical that moved receivables.

DSO T1 runs on DSO's headline column (the latest year) after the measures are scored, on the change as the
acquisitions-and-divestitures pre-check corrected it (pro forma or divested revenue: the measures the triggers read are
already adjusted for the year's deals), only when the pre-checks allow it (``payload.gate.agent_triggers`` is
"enabled"), when |DSO this year − DSO last year| ≥ 5 days. Same three steps as the other triggers:

1. ``dso_t1_passages`` (deterministic) collects what the agent checks: the paragraphs and table rows of MD&A and the
   notes that mention receivables, their sale or securitisation (ASC 860), the allowance or write-offs, collections,
   payers, billing or new facilities, and every subsequent-events paragraph (ids S01…).
2. The Trigger Agent (prompt ``fo_trigger_dso_t1``) ALWAYS reads them (no agent, or a rejected output, refuses the
   run). Every fact is quoted verbatim. Only a filing with none of those passages has nothing to read (no model call:
   no cause found, so no adjustment).
3. ``validate_dso_t1`` keeps only verbatim evidence that says what it must and RE-DERIVES the outcome, first match wins:

   - a mechanical cause quantified (receivables sold and derecognised, or an unusual write-off from the allowance
     rollforward): added back to receivables in the year it happened, for both years, and DSO recomputed;
   - DSO up, an operational cause described (payer disputes, billing or system problems, new facilities ramping up)
     and the 10-K shows collections recovered after the year-end: −1 on the DSO score (not below 1);
   - DSO up, a cause described, no evidence of a fix: unresolved;
   - a mechanical cause mentioned but not quantified: unresolved;
   - no cause found: no adjustment (the score reflects what was reported).

   Only receivables sold and derecognised count: a securitisation accounted for as a secured borrowing stays in
   receivables (its quote is held back). Operational causes count only when DSO went up.

DSO is escalated when unresolved, and, as for every trigger, when the agent's own answer and its verified evidence
disagree.
"""

from __future__ import annotations

import copy
import re

from app.prompts.fo_prompts import FO_DSO_T1_PROMPT, FO_DSO_T1_SCHEMA, FO_DSO_T1_TEMPLATE

from .covenants import NOTE_HEADING, SUBSEQUENT_TITLE
from .dscr_t2 import FORECAST
from .dscr_triggers import PLAN_WORDS, _ev, _held, _millions, _quoted, _value, add_trigger_note, collect_passages, stated_amount
from .icr_triggers import MDNA_SKIP
from .precheck import DATA_NOTICE, PrecheckAgentError, call_agent, require_agent
from .scoring import dso_settings, headline_score, score_dso

DSO_T1_VERSION = "fo-trigger-dso-t1-1.0.0"
DSO_T1_ID = "dso_t1"
DSO_T1_DAYS = 5  # |change in DSO| at or above this many days sets the trigger off
DSO_T1_RELIEF = 1  # DSO up, cause described, collections recovered after the year-end
AGENT = "Trigger Agent"

SECTIONS = [("mdna", "MD&A"), ("notes", "Notes to the financial statements"),
            ("subsequent_events", "Subsequent events")]
LIMITS = {"mdna": 25, "notes": 30, "subsequent_events": 15}
DSO_T1_TERMS = re.compile(
    r"receivabl\w*|days sales outstanding|\bDSO\b|factor(ing|ed)\b|securiti[sz]\w*|derecogni\w*|ASC 860|"
    r"allowance for (credit|doubtful|bad)|write-?offs?|written off|charge-?offs?|bad debts?|implicit price "
    r"concession\w*|collect\w*|payers?\b|payors?\b|denials?|disput\w*|billing|claims? (processing|submission)|"
    r"new (facilit|hospital|clinic|location|center)\w*|ramp\w*|start-?up", re.I)

# --- what a quote must say ---------------------------------------------------------------------------------------
RECEIVABLE = re.compile(r"receivabl\w*", re.I)
SOLD = re.compile(r"\bsold\b|\bsales? of\b|\bsell\w*|factor(ing|ed)\b|derecogni\w*|accounted for as (a )?sales?|"
                  r"true sales?|off-?balance|removed from (the|our) (consolidated )?balance sheets?|ASC 860|"
                  r"transfer\w*", re.I)
# A securitisation accounted for as a secured borrowing: the receivables stay on the balance sheet.
SECURED_BORROWING = re.compile(r"secured (borrowing|financing)s?|accounted for as (a )?(secured )?(borrowing|"
                               r"financing)|collaterali[sz]\w*|remain(s|ed)? on (the|our) (consolidated )?balance "
                               r"sheets?|not (been )?derecogni\w*|do(es)? not (qualify|meet) .{0,30}sale", re.I)
WRITE_OFF = re.compile(r"write-?offs?|written off|wrote off|charge-?offs?|charged off", re.I)
UNUSUAL = re.compile(r"one-?time|unusual|non-?recurring|specific|bankrupt\w*|insolven\w*|exceptional|isolated|"
                     r"significant|large|single (payer|payor|customer)|did not recur|not expected to recur", re.I)
MECHANICAL_WORDS = {
    "receivables_sold": (re.compile(f"(?=.*(?:{RECEIVABLE.pattern}))(?=.*(?:{SOLD.pattern}))", re.I | re.S),
                         "the quote doesn't show receivables sold"),
    "write_off": (WRITE_OFF, "the quote doesn't show a write-off"),
}
COLLECTION = re.compile(r"receivabl\w*|collect\w*|days sales outstanding|\bDSO\b|cash receipts?|billing|claims?|"
                        r"reimburse\w*|payments? from", re.I)
OPERATIONAL_WORDS = {
    "payer_dispute": (re.compile(r"disput\w*|denial\w*|denied|underpay\w*|appeal\w*|payers?\b|payors?\b|"
                                 r"insurers?|negotiat\w*", re.I), "the quote doesn't mention a payer dispute"),
    "billing_system": (re.compile(r"billing|system\w*|claims? (processing|submission)|conversion|implement\w*|"
                                  r"electronic (health|medical) records?|\bEHR\b|cyber\w*|outage|backlog|delay\w*",
                                  re.I), "the quote doesn't mention a billing or system problem"),
    "new_facilities": (re.compile(r"new (\w+ ){0,2}(facilit|hospital|clinic|location|center|centre|site)\w*|"
                                  r"ramp\w*|start-?up|newly (opened|acquired)|opened|de novo|credential\w*|enrol\w*",
                                  re.I), "the quote doesn't mention new facilities ramping up"),
    "other": (re.compile(r"\w"), ""),
}
IMPROVED = re.compile(r"recover\w*|resolv\w*|improv\w*|normali[sz]\w*|returned to|declin\w*|decreas\w*|reduc\w*|"
                      r"collected|caught up|cleared|fixed|remediat\w*|restored", re.I)
AFTER_YEAR_END = re.compile(r"subsequent(ly)? to|since (the )?(year|period|fiscal)[- ]end|after (the )?(year|period|"
                            r"fiscal)[- ]end|after (december|the end of|the balance sheet)|since (december|the end of)|"
                            r"(in|during|through|as of|by) (the )?(first|1st) (fiscal )?quarter|(in|during|through|"
                            r"as of|by) (early )?(january|february|march|april|may)\b", re.I)
# Payers named as plans ("a health plan") aren't plans to do something: dropped before the forecast and plan checks.
PAYER_PLAN = re.compile(r"\b(health|benefit|insurance|medicare advantage|managed care|commercial)\s+plans?\b", re.I)
KIND_TEXT = {"receivables_sold": "Receivables sold and derecognised", "write_off": "Unusual write-off",
             "payer_dispute": "Payer disputes", "billing_system": "Billing or system problems",
             "new_facilities": "New facilities ramping up", "other": "Other operational cause"}

STATUS_TEXT = {"skipped": "Skipped: agent triggers are skipped after a going-concern fail"}
RULES = [("A mechanical cause quantified (receivables sold and derecognised, or an unusual write-off from the "
          "allowance rollforward)",
          "Added back (sold receivables) or reversed (the write-off) in both years; DSO recomputed"),
         ("DSO up, cause described, and the 10-K says collections recovered after the year-end",
          f"−{DSO_T1_RELIEF} on the DSO score (not below 1)"),
         ("DSO up, cause described, no evidence it's been fixed",
          "Unresolved: DSO escalated to an analyst, who accepts the score or overrides it"),
         ("A mechanical cause mentioned but not quantified",
          "Unresolved: DSO escalated to an analyst, who accepts the score or overrides it"),
         ("No cause found", "No adjustment: the score reflects what was reported"),
         ("A securitisation accounted for as a secured borrowing", "Not a mechanical cause: stays in receivables"),
         ("The agent's answer and the verified evidence disagree",
          "Unresolved: DSO escalated to an analyst, who accepts the score or overrides it")]


# --- the passages ------------------------------------------------------------------------------------------------

def _section_after(item: str, heading: str, current: str | None, numbered: bool) -> str | None:
    """The searched section a heading opens, keeps or closes: MD&A (Item 7, without its boilerplate), every note,
    and subsequent events (in MD&A or a note)."""
    if item == "7":
        if SUBSEQUENT_TITLE.search(heading):
            return "subsequent_events"
        return None if MDNA_SKIP.search(heading) else "mdna"
    if numbered and not NOTE_HEADING.match(heading):
        return current
    return "subsequent_events" if SUBSEQUENT_TITLE.search(heading) else "notes"


def dso_t1_passages(data: dict) -> dict:
    """MD&A's and the notes' paragraphs and tables that mention receivables, their sale, write-offs, collections,
    payers, billing or new facilities, and every subsequent-events paragraph; ids S01… in document order."""
    return collect_passages(data, "S", LIMITS, SECTIONS, DSO_T1_TERMS, _section_after,
                            read_whole=("subsequent_events",))


def _headline_index(m: dict) -> int | None:
    return next((i for i, c in enumerate(m["columns"]) if c.get("score") is not None), None)


def dso_facts(m: dict, i: int) -> dict:
    """What the engine counted for the triggering column and the year before."""
    col = m["columns"][i]
    prior = m["columns"][i + 1] if i + 1 < len(m["columns"]) else {}
    change = col.get("change")
    return {"heading": col["heading"], "period": col["period"], "period_end": col["period"].split("--")[1],
            "prior_heading": prior.get("heading"), "prior_period_end": prior.get("as_at"),
            "receivables": col.get("receivables"), "revenue": col.get("revenue"), "dso": col.get("dso"),
            "prior_receivables": prior.get("receivables"), "prior_revenue": prior.get("revenue"),
            "prior_dso": col.get("prior_dso"), "change": change,
            "direction": "up" if change is not None and change > 0 else "down",
            "deal_adjusted": bool(col.get("deal_adjustment")), "score": col.get("score")}


def _days(v):
    return None if v is None else round(v, 1)


def dso_t1_inputs(t: dict, meta: dict, fx: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": fx["period_end"], "prior_period_end": fx["prior_period_end"]},
        "dso": {"column": fx["heading"], "receivables_millions": _millions(fx["receivables"]),
                "revenue_millions": _millions(fx["revenue"]), "dso_days": _days(fx["dso"]),
                "prior_receivables_millions": _millions(fx["prior_receivables"]),
                "prior_revenue_millions": _millions(fx["prior_revenue"]), "prior_dso_days": _days(fx["prior_dso"]),
                "change_days": _days(fx["change"]), "direction": fx["direction"],
                "revenue_adjusted_for_deals": fx["deal_adjusted"], "score": fx["score"],
                "trigger": f"|change in DSO| of {DSO_T1_DAYS} days or more"},
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t["passages"]],
    }


# --- validation --------------------------------------------------------------------------------------------------

def _mechanical(o, by_id, held):
    """One verified, quantified mechanical cause, or None (held back). Receivables sold must be derecognised (a
    secured borrowing keeps them on the balance sheet); a write-off must be shown to be unusual."""
    kind = o.get("kind")
    item = {"item_id": o.get("item_id"), "kind": kind, "year": o.get("year"), "description": o.get("description")}
    if item["year"] not in ("current", "prior"):
        held.append(_held(item, "amount", o.get("evidence"), "the year isn't 'current' or 'prior'"))
        return None
    words, why = MECHANICAL_WORDS.get(kind, MECHANICAL_WORDS["write_off"])
    e = o.get("evidence")
    if kind == "receivables_sold" and isinstance(e, dict) and SECURED_BORROWING.search(e.get("quote") or ""):
        held.append(_held(item, "amount", e, "a securitisation accounted for as a secured borrowing stays in "
                                             "receivables"))
        return None
    p, reason = _quoted(e, by_id, words, why)
    if not reason and FORECAST.search(PAYER_PLAN.sub("", e["quote"])):
        reason = "the quote reads as a forecast, not the year's figure"
    value = None
    if not reason:
        value, reason = _value(e, p)
    if reason:
        held.append(_held(item, "amount", e, reason))
        return None
    entry = {**item, "amount": value, "evidence": _ev(e, p), "unusual_evidence": None}
    if kind == "write_off":
        u = o.get("unusual_evidence")
        p, why = _quoted(u, by_id, UNUSUAL, "the quote doesn't show the write-off is unusual")
        if why:
            held.append(_held(item, "unusual", u, why))
            return None
        entry["unusual_evidence"] = _ev(u, p)
    return entry


def _cited(o, by_id, words, held, fact, also=None, also_why=None):
    """One verified cited cause (no amount), or None (held back)."""
    kind = o.get("kind")
    item = {"item_id": o.get("item_id"), "kind": kind, "description": o.get("description")}
    need, why = words.get(kind, next(iter(words.values())))
    p, reason = _quoted(o.get("evidence"), by_id, need, why)
    if not reason and also is not None and not also.search(o["evidence"]["quote"]):
        reason = also_why
    if not reason and kind == "receivables_sold" and SECURED_BORROWING.search(o["evidence"]["quote"]):
        reason = "a securitisation accounted for as a secured borrowing stays in receivables"
    if reason:
        held.append(_held(item, fact, o.get("evidence"), reason))
        return None
    return {**item, "evidence": _ev(o["evidence"], p)}


def _recovery(r, by_id, held):
    """The verified evidence that collections recovered after the year-end, or None."""
    if r is None:
        return None
    item = {"item_id": "recovery", "kind": "recovery", "description": "Collections recovered after the year-end"}
    p, why = _quoted(r, by_id, IMPROVED, "the quote doesn't show collections recovered")
    if not why and not COLLECTION.search(r["quote"]):
        why = "the quote doesn't mention receivables or collections"
    if not why and not (p["section"] == "subsequent_events" or AFTER_YEAR_END.search(r["quote"])):
        why = "the quote doesn't show the recovery came after the year-end"
    if not why and PLAN_WORDS.search(PAYER_PLAN.sub("", r["quote"])):
        why = "the quote reads as a plan or expectation, not a recovery that happened"
    if why:
        held.append(_held(item, "recovery", r, why))
        return None
    return _ev(r, p)


def validate_dso_t1(parsed: dict, t: dict, fx: dict) -> dict:
    """Keep the evidence that holds up and re-derive the outcome, first match wins: recomputed (a mechanical cause
    quantified), recovered (DSO up, an operational cause, collections recovered after the year-end: −1), unresolved
    (DSO up with a cause but no fix, or a mechanical cause not quantified), else no cause (no adjustment)."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    held, notes = [], []
    mechanical = [e for o in parsed.get("mechanical") or [] for e in [_mechanical(o, by_id, held)] if e]
    unquantified = [e for o in parsed.get("mechanical_unquantified") or []
                    for e in [_cited(o, by_id, MECHANICAL_WORDS, held, "mechanical cause mentioned")] if e]
    up = fx["direction"] == "up"
    operational = [e for o in parsed.get("operational") or []
                   for e in [_cited(o, by_id, OPERATIONAL_WORDS, held, "operational cause", COLLECTION,
                                    "the quote doesn't tie the cause to receivables or collections")] if e]
    if operational and not up:
        notes.append(f"{', '.join(e['item_id'] for e in operational)}: operational causes count only when DSO went "
                     "up; DSO went down, so they are not used.")
        operational = []
    recovery = _recovery(parsed.get("recovery"), by_id, held) if operational else None
    added = sum(e["amount"] for e in mechanical if e["year"] == "current")
    prior_added = sum(e["amount"] for e in mechanical if e["year"] == "prior")
    why = None
    if mechanical:
        status = "recomputed"
        if operational or unquantified:
            notes.append("Other causes also found: not used (a quantified mechanical cause comes first).")
    elif operational and recovery:
        status = "recovered"
    elif operational:
        status = "unresolved"
        why = (f"DSO went up with a cause described ({', '.join(e['item_id'] for e in operational)}) and no evidence "
               "that collections recovered after the year-end")
    elif unquantified:
        status = "unresolved"
        why = (f"a mechanical cause is mentioned but not quantified "
               f"({', '.join(e['item_id'] for e in unquantified)})")
    else:
        status = "no_cause"
    return {"status": status, "mechanical": mechanical, "unquantified": unquantified, "operational": operational,
            "recovery": recovery, "added": added if mechanical else 0.0,
            "prior_added": prior_added if mechanical else 0.0, "unresolved_why": why, "held_back": held,
            "stated_status": parsed.get("status"), "status_consistent": parsed.get("status") == status,
            "validation_notes": notes}


def agent_reading(parsed: dict, t: dict) -> dict:
    """The agent's answer as it states it (not verified, not applied), for an analyst to compare with the verified
    outcome: "recomputed" adds its mechanical amounts back to receivables (this year's and the
    prior year's); "recovered" takes the relief off the score. Any other answer changes nothing."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    stated, notes, added, prior = parsed.get("status"), [], 0.0, 0.0
    if stated == "recomputed":
        for o in parsed.get("mechanical") or []:
            v = stated_amount(o.get("evidence"), by_id, notes, o.get("item_id")) or 0.0
            if o.get("year") == "current":
                added += v
            elif o.get("year") == "prior":
                prior += v
    return {"status": stated, "notes": notes,
            "applies": {"status": stated, "added": added, "prior_added": prior,
                        "relief": DSO_T1_RELIEF if stated == "recovered" else 0}}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_DSO_T1_PROMPT, template=FO_DSO_T1_TEMPLATE, response_schema=FO_DSO_T1_SCHEMA,
                      role="analyze", label="Financial Overview Trigger Agent: DSO T1")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

def _reason(status: str, checked: dict) -> str:
    if status == "recomputed":
        items = "; ".join(f"{KIND_TEXT[e['kind']].lower()} of {e['amount'] / 1e6:,.1f} million in the "
                          f"{e['year']} year ({e['evidence']['passage_id']})" for e in checked["mechanical"])
        return f"Mechanical cause quantified ({items}): added back to receivables, DSO recomputed"
    if status == "recovered":
        causes = ", ".join(f"{KIND_TEXT[e['kind']].lower()} ({e['evidence']['passage_id']})"
                           for e in checked["operational"])
        return (f"DSO up, cause described ({causes}), collections recovered after the year-end "
                f"({checked['recovery']['passage_id']}): −{DSO_T1_RELIEF} on the score")
    if status == "no_cause":
        return "No cause found: no adjustment, the score reflects what was reported"
    return "Unresolved: " + checked["unresolved_why"]


def dso_t1_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None,
                   mode: str = "live", case_id: str | None = None) -> dict:
    """The DSO T1 trigger result recorded in the run payload (``payload.triggers.dso_t1``). ``metrics`` are the scored
    measures (DSO as the acquisitions-and-divestitures pre-check corrected it); the trigger reads DSO's headline
    column (the latest year)."""
    m = next((x for x in metrics if x["key"] == "dso"), None)
    score, heading = headline_score(m) if m else (None, None)
    result = {"trigger": DSO_T1_ID, "measure": "dso", "version": DSO_T1_VERSION, "threshold": DSO_T1_DAYS,
              "score": score, "column": heading, "fired": False, "mode": mode if backend is not None else "none",
              "agent": None, "notes": [], "review_required": False}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", reason=STATUS_TEXT["skipped"],
                      effect="None: the measures are superseded by the going-concern fail.")
        return result
    if score is None:
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason="DSO has no score: trigger DSO T1 not set off")
        return result
    fx = dso_facts(m, _headline_index(m))
    result["inputs"] = fx
    if abs(fx["change"]) < DSO_T1_DAYS:
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason=f"DSO changed by {fx['change']:+.1f} days, less than {DSO_T1_DAYS} either way: trigger "
                             "DSO T1 not set off")
        return result
    t = dso_t1_passages(data)
    result.update(fired=True, passages=t["passages"], sections=t["sections"],
                  fired_by=f"DSO {fx['direction']} {abs(fx['change']):.1f} days ({DSO_T1_DAYS} or more)"
                  + (" after the acquisitions-and-divestitures pre-check" if fx["deal_adjusted"] else ""))
    if not t["passages"]:
        checked = {"status": "no_cause", "mechanical": [], "unquantified": [], "operational": [], "recovery": None,
                   "added": 0.0, "prior_added": 0.0, "unresolved_why": None, "status_consistent": True}
        decided_by = "no_text"
        result["notes"].append("No MD&A, note or subsequent-events passage mentions receivables, collections or "
                               "their causes: the agent was not called. No cause found, so no adjustment.")
    else:
        require_agent(backend, DSO_T1_ID, AGENT)
        checked, decided_by = _run_agent(result, t, data, fx, session, backend, case_id)
    status = checked["status"]
    disagree = not checked["status_consistent"]
    unresolved = status == "unresolved" or disagree
    why = ("the agent's answer and the verified evidence disagree" if disagree else checked["unresolved_why"]) \
        if unresolved else None
    result.update(status=status, decided_by=decided_by, mechanical=checked["mechanical"],
                  unquantified=checked["unquantified"], operational=checked["operational"],
                  recovery=checked["recovery"], added=checked["added"], prior_added=checked["prior_added"],
                  relief=DSO_T1_RELIEF if status == "recovered" else 0,
                  reason=_reason(status, checked), unresolved=unresolved, disagreement=disagree,
                  review_required=unresolved, unresolved_why=why,
                  effect=({"recomputed": "DSO recomputed with the mechanical items added back in both years",
                           "recovered": f"−{DSO_T1_RELIEF} on the DSO score"}
                          .get(status, "None: the DSO score stays as calculated"))
                  + (f"; unresolved ({why}), so DSO is escalated to an analyst, who accepts the score or overrides it."
                     if unresolved else
                     "; resolved (the agent and the verified evidence agree), so no analyst decision is needed."))
    return result


def _run_agent(result, t, data, fx, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_DSO_T1_PROMPT,
                            dso_t1_inputs(t, data["meta"], fx))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's DSO T1 output was rejected "
                                 f"({agent['validation_detail']}); the run was not recorded. Run it again.")
    checked = validate_dso_t1(run.parsed, t, fx)
    result["agent_reading"] = agent_reading(run.parsed, t)
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

def _recompute(col, added):
    col["receivables_before_trigger"] = col["receivables"]
    col["receivables"] += added
    if col.get("revenue") and col["revenue"] > 0:
        col["dso"] = col["receivables"] / (col["revenue"] / 365)


def apply_dso_t1(fo: list[dict], t: dict | None) -> list[dict]:
    """DSO with the trigger's outcome on its headline column (a copy; the inputs stay untouched): the mechanical items
    added back to receivables in the year they happened (the headline year and the year before), both DSOs and the
    change recomputed and rescored with the settings the measure was scored with; or −1 on the score (not below 1)
    when collections recovered after the year-end. The column keeps the figures as calculated. The measure is marked
    for escalation when the trigger is unresolved."""
    if not t or not t.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "dso":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        i = _headline_index(m)
        col = m["columns"][i]
        prior = m["columns"][i + 1] if i + 1 < len(m["columns"]) else None
        adj = {"trigger": "DSO T1", "outcome": t["status"], "calculated": col["score"], "dso_calculated": col["dso"],
               "prior_dso_calculated": col["prior_dso"], "change_calculated": col["change"],
               "added": t.get("added") or 0.0, "prior_added": t.get("prior_added") or 0.0,
               "relief": t.get("relief") or 0, "recomputed": None}
        done = None
        if t["status"] == "recomputed" and col.get("receivables") is not None:
            settings = dso_settings(m)
            if adj["added"]:
                _recompute(col, adj["added"])
            if adj["prior_added"] and prior is not None and prior.get("receivables") is not None:
                _recompute(prior, adj["prior_added"])
                col["prior_dso"] = prior["dso"]
                if prior.get("change") is not None and prior.get("prior_dso") is not None:  # its own change moves too
                    prior["change"] = prior["dso"] - prior["prior_dso"]
                    score_dso(prior, settings)
            missing = bool(adj["prior_added"]) and (prior is None or prior.get("receivables") is None)
            if missing:
                adj["prior_added"] = 0.0
            col["change"] = col["dso"] - col["prior_dso"]
            score_dso(col, settings)
            adj["recomputed"] = col["score"]
            done = (f"DSO {adj['dso_calculated']:.1f} → {col['dso']:.1f} days, prior-year DSO "
                    f"{adj['prior_dso_calculated']:.1f} → {col['prior_dso']:.1f} days, change "
                    f"{adj['change_calculated']:+.1f} → {col['change']:+.1f} days (mechanical items added back), "
                    "score " + (f"{adj['calculated']:g} → " if adj["calculated"] is not None else "")
                    + (f"{col['score']:g}" if col["score"] is not None else "none")
                    + ("; no prior-year receivables, so the prior year's items weren't added back" if missing else ""))
        elif t["status"] == "recovered" and col["score"] is not None:
            col["score"] = max(1.0, round(col["score"] - DSO_T1_RELIEF, 1))
            adj["recomputed"] = col["score"]
            done = (f"score {adj['calculated']:g} → {col['score']:g} (−{DSO_T1_RELIEF}: collections recovered after "
                    "the year-end)")
        adj["dso"], adj["change"], adj["score"] = col["dso"], col["change"], col["score"]
        col["dso_adjustment"] = adj
        add_trigger_note(m, (f"DSO trigger T1 ({t.get('fired_by')}, {col['heading']}): "
                             + (done or "no adjustment" if t["status"] == "unresolved"
                                else done or t["reason"][:1].lower() + t["reason"][1:])),
                         "trigger DSO T1: " + {"recomputed": "mechanical items added back",
                                               "recovered": f"−{DSO_T1_RELIEF}, collections recovered",
                                               "no_cause": "no cause found"}.get(t["status"], "unresolved"),
                         t.get("unresolved", True), t.get("unresolved_why") or "cause not resolved")
        out.append(m)
    return out
