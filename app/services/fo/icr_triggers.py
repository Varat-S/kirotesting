"""FO Trigger Agent, ICR T1: interest coverage scored 8.5 or more (below 1.5x: weak cover).

ICR = (EBIT + rent) / (interest incurred + rent). EBIT is pre-tax income plus interest expense, so one-off charges or
gains in pre-tax income move it, and an undesignated swap's mark-to-market recorded in interest expense moves the
denominator. ICR T1 runs after the measures are scored (and after the DSCR triggers), only when ICR's headline score
is 8.5 or more and the pre-checks allow it (``payload.gate.agent_triggers`` is "enabled"). Same three steps as the
DSCR triggers:

1. ``icr_t1_passages`` (deterministic) collects what the agent checks: the paragraphs and table rows of MD&A and the
   notes that mention one-off items (impairments, restructuring, litigation, gains or losses on sales, transaction
   costs, write-downs, insurance recoveries), interest rate swaps or derivatives, or capitalised interest (ids I01…).
2. The Trigger Agent (prompt ``fo_trigger_icr_t1``) ALWAYS reads them (no agent, or a rejected output, refuses the
   run). Every fact is quoted verbatim. Only a filing with none of those passages has nothing to read (no model call:
   nothing found).
3. ``validate_icr_t1`` keeps only verbatim evidence that says what it must and RE-DERIVES the two adjustments, which
   apply together:

   - quantified one-offs in EBIT: input correction, charges added back and gains removed, ICR recomputed;
   - an undesignated swap's mark-to-market in interest expense: removed from interest incurred, ICR recomputed (EBIT
     already excludes it: interest expense is added back to pre-tax income);
   - nothing found: unresolved, so ICR is escalated to an analyst.

   Capitalised interest is a cross-check only: a disclosed amount the tagged figures missed is noted, not added.

ICR is escalated when nothing is found, and, as for every trigger, when the agent's own answer and its verified
evidence disagree.
"""

from __future__ import annotations

import copy
import re

from app.prompts.fo_prompts import FO_ICR_T1_PROMPT, FO_ICR_T1_SCHEMA, FO_ICR_T1_TEMPLATE

from .covenants import NOTE_HEADING
from .dscr_t2 import FORECAST
from .dscr_triggers import _ev, _held, _millions, _quoted, _value, add_trigger_note, collect_passages
from .measures import ICR_BANDS, risk_score
from .precheck import DATA_NOTICE, PrecheckAgentError, call_agent, require_agent
from .scoring import headline_score

ICR_T1_VERSION = "fo-trigger-icr-t1-1.0.0"
ICR_T1_ID = "icr_t1"
ICR_T1_THRESHOLD = 8.5  # an ICR score at or above this (below 1.5x) sets the trigger off
AGENT = "Trigger Agent"
NOTHING_FOUND = "nothing found to explain the weak cover"

SECTIONS = [("mdna", "MD&A"), ("notes", "Notes to the financial statements")]
LIMITS = {"mdna": 20, "notes": 30}
# MD&A headings that close the search (boilerplate, not the year's results).
MDNA_SKIP = re.compile(r"critical accounting|recent(ly)? (issued|adopted)|accounting (standards|pronouncements)|"
                       r"forward-looking", re.I)
ONE_OFF = re.compile(r"impair\w*|restructur\w*|severance|litigation|legal (settlement|charge|expense)s?|"
                     r"settlement (charge|gain|loss)s?|(gain|loss)\w* on (the )?(sale|disposal|divestiture|"
                     r"disposition|extinguishment)|transaction (costs?|expenses?)|acquisition-related|merger|"
                     r"integration (costs?|expenses?)|write-?(down|off)s?|one-?time|non-?recurring|unusual|"
                     r"special (items?|charges?)|insurance (recover\w*|proceeds)|casualty|hurricane", re.I)
SWAP = re.compile(r"\bswaps?\b|derivative\w*", re.I)
CAPITALISED = re.compile(r"capitali[sz]\w*", re.I)
ICR_TERMS = re.compile(ONE_OFF.pattern + "|" + SWAP.pattern + r"|mark-to-market|not designated|undesignated|"
                       r"economic hedges?|capitali[sz]ed interest|interest capitali[sz]ed|capitali[sz]ation of "
                       r"interest", re.I)

# --- what a quote must say ---------------------------------------------------------------------------------------
KIND_WORDS = {
    "charge": (re.compile(r"impair\w*|restructur\w*|severance|litigation|legal|settlement|loss\w*|write-?(down|off)s?|"
                          r"charges?|costs?|expenses?", re.I), "the quote doesn't show a charge or loss"),
    "gain": (re.compile(r"gains?|recover\w*|proceeds|income|benefit", re.I), "the quote doesn't show a gain"),
}
UNDESIGNATED = re.compile(r"not designated|undesignated|non-?designated|economic hedges?|not (been )?(accounted for|"
                          r"treated|qualif\w*) as (a )?hedg\w*|do(es)? not (apply|elect) hedge accounting|without "
                          r"hedge accounting|not (in|part of) a hedg\w* relationship", re.I)
INTEREST_EXPENSE = re.compile(r"interest expense", re.I)
FAIR_VALUE = re.compile(r"fair value|mark-to-market|gains?|loss\w*|chang\w*", re.I)

STATUS_TEXT = {
    "skipped": "Skipped: agent triggers are skipped after a going-concern fail",
    "nothing_found": "Nothing found: no quantified one-off in EBIT and no undesignated swap mark-to-market in interest "
                     "expense",
}
RULES = [("Quantified one-offs in EBIT", "Input correction: charges added back, gains removed, ICR recomputed"),
         ("An undesignated swap's mark-to-market in interest expense", "Removed from interest incurred, ICR "
                                                                         "recomputed"),
         ("Nothing found", "Unresolved: ICR escalated to an analyst, who accepts the score or overrides it"),
         ("Capitalised interest", "Cross-check only: a disclosed amount the tagged figures missed is noted"),
         ("The agent's answer and the verified evidence disagree",
          "Unresolved: ICR escalated to an analyst, who accepts the score or overrides it")]


# --- the passages ------------------------------------------------------------------------------------------------

def _section_after(item: str, heading: str, current: str | None, numbered: bool) -> str | None:
    """The searched section a heading opens, keeps or closes: MD&A (Item 7, without its boilerplate) and every note."""
    if item == "7":
        return None if MDNA_SKIP.search(heading) else "mdna"
    if numbered and not NOTE_HEADING.match(heading):
        return current
    return "notes"


def icr_t1_passages(data: dict) -> dict:
    """MD&A's and the notes' paragraphs and tables that mention an ICR T1 term; ids I01… in document order."""
    return collect_passages(data, "I", LIMITS, SECTIONS, ICR_TERMS, _section_after)


def _headline_index(m: dict) -> int | None:
    return next((i for i, c in enumerate(m["columns"]) if c.get("score") is not None), None)


def icr_facts(col: dict) -> dict:
    """What the engine counted for the triggering column."""
    return {"heading": col["heading"], "period": col["period"], "period_end": col["period"].split("--")[1],
            "ebit": col.get("ebit"), "ebit_concept": col.get("ebit_concept"), "rent": col.get("rent"),
            "interest": col.get("interest"), "interest_concept": col.get("interest_concept"),
            "interest_capitalised": col.get("interest_capitalised"), "swap_mtm_removed": col.get("swap_mtm_removed"),
            "ratio": col.get("ratio"), "score": col.get("score")}


def icr_t1_inputs(t: dict, meta: dict, fx: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": fx["period_end"]},
        "icr": {"column": fx["heading"], "ratio": round(fx["ratio"], 2) if fx["ratio"] is not None else None,
                "score": fx["score"], "trigger": f"score {ICR_T1_THRESHOLD:g} or more",
                "ebit_millions": _millions(fx["ebit"]), "ebit_source": fx["ebit_concept"],
                "rent_millions": _millions(fx["rent"]), "interest_incurred_millions": _millions(fx["interest"]),
                "interest_source": fx["interest_concept"]},
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t["passages"]],
    }


# --- validation --------------------------------------------------------------------------------------------------

def _amount(e, by_id, need, why, item, fact, held, also=None, also_why=None):
    """(value, evidence) for a verified amount, or None (held back)."""
    p, reason = _quoted(e, by_id, need, why)
    if not reason and also is not None and not also.search(e["quote"]):
        reason = also_why
    if not reason and FORECAST.search(e["quote"]):
        reason = "the quote reads as a forecast, not the year's figure"
    value = None
    if not reason:
        value, reason = _value(e, p)
    if reason:
        held.append(_held(item, fact, e, reason))
        return None
    return value, _ev(e, p)


def verified_swap(s, by_id, interest, held):
    """An undesignated swap's mark-to-market in interest expense, verified, or None (held back): its amount in a
    quote naming the swap and a change in fair value, and verified quotes saying the swaps are not designated as
    hedges and the change is in interest expense. ``effect`` is what comes out of interest (a loss out, a gain in)."""
    if not s:
        return None
    item = {"item_id": "W1", "kind": s.get("direction")}
    got = _amount(s.get("evidence"), by_id, SWAP, "the quote doesn't mention a swap or derivative", item,
                  "mark-to-market", held, FAIR_VALUE, "the quote shows no change in fair value")
    if not got:
        return None
    undesignated = None
    u = s.get("undesignated")
    if u:
        up, why = _quoted(u, by_id, UNDESIGNATED, "the quote doesn't say the swaps are not designated as hedges")
        if why:
            held.append(_held(item, "undesignated", u, why))
        else:
            undesignated = _ev(u, up)
    quotes = [got[1]["quote"]] + ([undesignated["quote"]] if undesignated else [])
    why = None
    if not any(UNDESIGNATED.search(q) for q in quotes):
        why = "no verified quote says the swaps are not designated as hedges"
    elif not any(INTEREST_EXPENSE.search(q) for q in quotes):
        why = "no verified quote says it is recorded in interest expense"
    elif s.get("direction") == "loss" and interest is not None and got[0] >= interest:
        why = f"{got[0] / 1e6:,.1f} million is not less than interest ({interest / 1e6:,.1f} million)"
    if why:
        held.append(_held(item, "mark-to-market", s.get("evidence"), why))
        return None
    return {**item, "direction": s.get("direction"), "amount": got[0], "evidence": got[1],
            "undesignated_evidence": undesignated, "effect": got[0] if s.get("direction") == "loss" else -got[0]}


def validate_icr_t1(parsed: dict, t: dict, fx: dict) -> dict:
    """Keep the evidence that holds up and re-derive the adjustments: quantified one-offs in EBIT (charges added
    back, gains removed) and an undesignated swap's mark-to-market removed from interest incurred. Neither: nothing
    found."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    held, notes = [], []
    one_offs = []
    for o in parsed.get("one_offs") or []:
        kind = o.get("kind")
        item = {"item_id": o.get("item_id"), "kind": kind, "description": o.get("description")}
        words, why = KIND_WORDS.get(kind, KIND_WORDS["charge"])
        got = _amount(o.get("evidence"), by_id, ONE_OFF, "the quote names no one-off item (impairment, "
                      "restructuring, litigation, a gain or loss on a sale…)", item, "amount", held, words, why)
        if got:
            one_offs.append({**item, "amount": got[0], "evidence": got[1],
                             "effect": got[0] if kind == "charge" else -got[0]})  # what EBIT changes by
    swap = None
    if parsed.get("swap_mark_to_market") and fx.get("swap_mtm_removed") is not None:
        held.append(_held({"item_id": "W1", "kind": parsed["swap_mark_to_market"].get("direction")}, "mark-to-market",
                          parsed["swap_mark_to_market"].get("evidence"), "already removed by ICR trigger T2"))
    else:
        swap = verified_swap(parsed.get("swap_mark_to_market"), by_id, fx["interest"], held)
    capitalised = None
    c = parsed.get("capitalised_interest")
    if c:
        got = _amount(c, by_id, CAPITALISED, "the quote doesn't mention capitalised interest", {"item_id": "C1"},
                      "capitalised interest", held)
        if got:
            capitalised = {"amount": got[0], "evidence": got[1]}
            if fx.get("interest_capitalised") == 0.0 and got[0] > 0:
                notes.append(f"Capitalised interest of {got[0] / 1e6:,.1f} million is disclosed "
                             f"({got[1]['passage_id']}) but not tagged, so interest incurred may be understated by it. "
                             "Cross-check only: ICR is not adjusted for it.")
    status = "adjusted" if one_offs or swap else "nothing_found"
    return {"status": status, "one_offs": one_offs, "swap": swap, "capitalised": capitalised,
            "ebit_change": sum(e["effect"] for e in one_offs), "interest_change": -(swap["effect"] if swap else 0.0),
            "held_back": held, "stated_status": parsed.get("status"),
            "status_consistent": parsed.get("status") == status, "validation_notes": notes}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_ICR_T1_PROMPT, template=FO_ICR_T1_TEMPLATE, response_schema=FO_ICR_T1_SCHEMA,
                      role="analyze", label="Financial Overview Trigger Agent: ICR T1")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

def _reason(status: str, checked: dict) -> str:
    if status != "adjusted":
        return STATUS_TEXT[status]
    parts = []
    if checked["one_offs"]:
        items = "; ".join(f"{e['kind']} of {e['amount'] / 1e6:,.1f} million ({e['evidence']['passage_id']})"
                          for e in checked["one_offs"])
        parts.append(f"one-offs in EBIT ({items}): charges added back, gains removed")
    if checked["swap"]:
        w = checked["swap"]
        parts.append(f"undesignated swap mark-to-market {w['direction']} of {w['amount'] / 1e6:,.1f} million "
                     f"({w['evidence']['passage_id']}) removed from interest expense")
    text = "; ".join(parts) + ", ICR recomputed"
    return text[:1].upper() + text[1:]


def icr_t1_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None, mode: str = "live",
                   case_id: str | None = None) -> dict:
    """The ICR T1 trigger result recorded in the run payload (``payload.triggers.icr_t1``). ``metrics`` are the
    scored measures; the trigger reads ICR's headline column (the latest year)."""
    icr = next((m for m in metrics if m["key"] == "icr"), None)
    score, heading = headline_score(icr) if icr else (None, None)
    result = {"trigger": ICR_T1_ID, "measure": "icr", "version": ICR_T1_VERSION, "threshold": ICR_T1_THRESHOLD,
              "score": score, "column": heading, "fired": False, "mode": mode if backend is not None else "none",
              "agent": None, "notes": [], "review_required": False}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", reason=STATUS_TEXT["skipped"],
                      effect="None: the measures are superseded by the going-concern fail.")
        return result
    if score is None or score < ICR_T1_THRESHOLD:
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason=(f"ICR scored {score:g}, below {ICR_T1_THRESHOLD:g}: trigger ICR T1 not set off"
                              if score is not None else "ICR has no score: trigger ICR T1 not set off"))
        return result
    fx = icr_facts(icr["columns"][_headline_index(icr)])
    t = icr_t1_passages(data)
    result.update(fired=True, inputs=fx, passages=t["passages"], sections=t["sections"])
    if not t["passages"]:
        checked = {"status": "nothing_found", "one_offs": [], "swap": None, "capitalised": None, "ebit_change": 0.0,
                   "interest_change": 0.0, "status_consistent": True}
        decided_by = "no_text"
        result["notes"].append("No MD&A or note passage mentions a one-off, a swap or capitalised interest: the agent "
                               "was not called.")
    else:
        require_agent(backend, ICR_T1_ID, AGENT)
        checked, decided_by = _run_agent(result, t, data, fx, session, backend, case_id)
    status = checked["status"]
    disagree = not checked["status_consistent"]
    unresolved = status == "nothing_found" or disagree
    result.update(status=status, decided_by=decided_by, one_offs=checked["one_offs"], swap=checked["swap"],
                  capitalised=checked["capitalised"], ebit_change=checked["ebit_change"],
                  interest_change=checked["interest_change"], reason=_reason(status, checked),
                  unresolved=unresolved, disagreement=disagree, review_required=unresolved,
                  unresolved_why=("the agent's answer and the verified evidence disagree" if disagree
                                  else NOTHING_FOUND) if unresolved else None,
                  effect=("ICR recomputed as above" if status == "adjusted" else "None: the ICR score stays as "
                          "calculated")
                  + ("; unresolved (" + ("the agent's answer and the verified evidence disagree" if disagree else
                                         NOTHING_FOUND)
                     + "), so ICR is escalated to an analyst, who accepts the score or overrides it." if unresolved
                     else "; resolved (the agent and the verified evidence agree), so no analyst decision is needed."))
    return result


def _run_agent(result, t, data, fx, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_ICR_T1_PROMPT,
                            icr_t1_inputs(t, data["meta"], fx))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's ICR T1 output was rejected ({agent['validation_detail']}); "
                                 "the run was not recorded. Run it again.")
    checked = validate_icr_t1(run.parsed, t, fx)
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

def apply_icr_t1(fo: list[dict], t: dict | None) -> list[dict]:
    """ICR with the trigger's outcome on its headline column (a copy; the inputs stay untouched): one-offs moved out
    of EBIT and the swap mark-to-market out of interest incurred, the ratio and score recomputed. The column keeps
    the figures as tagged. The measure is marked for escalation when the trigger is unresolved."""
    if not t or not t.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "icr":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        col = m["columns"][_headline_index(m)]
        adj = {"trigger": "ICR T1", "calculated": col["score"], "ratio_calculated": col["ratio"], "recomputed": None,
               "ebit_change": t.get("ebit_change") or 0.0, "interest_change": t.get("interest_change") or 0.0}
        done = []
        if t["status"] == "adjusted" and col["ebit"] is not None and col["interest"] is not None:
            if adj["ebit_change"]:
                col.setdefault("ebit_reported", col["ebit"])
                col["ebit"] += adj["ebit_change"]
                col["ebitr"] = col["ebit"] + col["rent"]
                done.append(f"EBIT {(col['ebit'] - adj['ebit_change']) / 1e6:,.1f} → {col['ebit'] / 1e6:,.1f} million "
                            "(one-offs)")
            if adj["interest_change"]:
                before = col["interest"]
                col.setdefault("interest_reported", before)
                col["interest"] += adj["interest_change"]
                col["fixed_charges"] = col["interest"] + col["rent"]
                done.append(f"interest incurred {before / 1e6:,.1f} → {col['interest'] / 1e6:,.1f} million (swap "
                            "mark-to-market removed)")
            col["ratio"] = col["ebitr"] / col["fixed_charges"] if col["fixed_charges"] else None
            col["score"] = adj["recomputed"] = risk_score(col["ratio"], ICR_BANDS)
            done.append(f"ICR {adj['ratio_calculated']:.2f}x → "
                        + (f"{col['ratio']:.2f}x" if col["ratio"] is not None else "no interest to cover")
                        + f", score {adj['calculated']:g} → "
                        + (f"{col['score']:g}" if col["score"] is not None else "none"))
        adj["score"] = col["score"]
        col["trigger_adjustment"] = adj
        add_trigger_note(m, (f"ICR trigger T1 (score {adj['calculated']:g} ≥ {ICR_T1_THRESHOLD:g}, {col['heading']}): "
                             + ("; ".join(done) if done else t["reason"][:1].lower() + t["reason"][1:])),
                         "trigger ICR T1: " + ("adjusted" if done else "nothing found"), t.get("unresolved", True),
                         t.get("unresolved_why") or NOTHING_FOUND)
        out.append(m)
    return out
