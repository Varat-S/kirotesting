"""FO Trigger Agent, RCF/Debt T1: a one-off cash cost or a one-off payout may be driving the weak result, not the
company's normal cash generation.

RCF = FFO − dividends paid − distributions to noncontrolling interests, so a one-off cash cost paid in the year
(restructuring or severance, a litigation settlement, acquisition or integration costs, a special pension contribution,
tax on a disposal) depresses FFO, and a special dividend inflates dividends paid. RCF/Debt T1 runs on RCF / debt's
headline column (the latest year) after the measures are scored, only when the pre-checks allow it
(``payload.gate.agent_triggers`` is "enabled"), when the score is 9 or more or the ratio is negative. Same three steps as
the other triggers:

1. ``rcf_t1_passages`` (deterministic) collects what the agent checks: the paragraphs and table rows of MD&A and the
   notes that mention a one-off cash item, a special dividend or operating cash flow (ids F01…).
2. The Trigger Agent (prompt ``fo_trigger_rcf_t1``) ALWAYS reads them (no agent, or a rejected output, refuses the
   run). Every fact is quoted verbatim. Only a filing with none of those passages has nothing to read (no model call:
   no one-offs found, so no adjustment).
3. ``validate_rcf_t1`` keeps only verbatim evidence that says what it must and RE-DERIVES the outcome, first match wins:

   - a one-off cash cost or special dividend, quantified and not recurring: added back (cash costs to FFO, special
     dividends out of dividends paid) and RCF / debt recomputed;
   - no one-offs found: no adjustment;
   - a one-off identified but not quantified: unresolved, so RCF / debt is escalated to an analyst.

   An item counts as recurring (no adjustment) only with a verbatim quote showing it recurs. A cash cost needs a quote
   showing cash paid (not a charge or an accrual); a special dividend a quote showing it was paid.

RCF / debt is escalated when unresolved, and, as for every trigger, when the agent's own answer and its verified
evidence disagree. Only RCF / debt changes; the CFO-based comparison stays as tagged.
"""

from __future__ import annotations

import copy
import re

from app.prompts.fo_prompts import FO_RCF_T1_PROMPT, FO_RCF_T1_SCHEMA, FO_RCF_T1_TEMPLATE

from .dscr_triggers import _ev, _held, _millions, _quoted, add_trigger_note, collect_passages, stated_amount
from .icr_triggers import ONE_OFF, SECTIONS, _amount, _section_after
from .leverage_triggers import RECURRING
from .precheck import DATA_NOTICE, PrecheckAgentError, call_agent, require_agent
from .scoring import cash_flow_settings, headline_score, score_cash_flow

RCF_T1_VERSION = "fo-trigger-rcf-t1-1.0.0"
RCF_T1_ID = "rcf_t1"
RCF_T1_THRESHOLD = 9  # an RCF / debt score at or above this sets the trigger off (so does a negative ratio)
AGENT = "Trigger Agent"

LIMITS = {"mdna": 25, "notes": 30}
CASH_ONE_OFF = re.compile(ONE_OFF.pattern + r"|acquisition (costs?|expenses?)|pension contributions?|"
                          r"(discretionary|voluntary|special) (pension )?contributions?|tax(es)? (paid )?on the (sale|gain)",
                          re.I)
SPECIAL = re.compile(r"special|one-?time|extra(ordinary)?|non-?recurring|supplemental", re.I)
DIVIDEND = re.compile(r"dividends?|distribut\w*", re.I)
RCF_T1_TERMS = re.compile(CASH_ONE_OFF.pattern + r"|special (cash )?dividends?|one-?time (cash )?(dividend|"
                          r"distribution|payment)s?|cash (provided by|from|used in) operating activities|"
                          r"operating cash flows?", re.I)

# --- what a quote must say ---------------------------------------------------------------------------------------
CASH_PAID = re.compile(r"\bpaid\b|\bpay(ment|ments|ing)\b|cash (outflows?|used|payments?|spent|costs?)|\bspent\b|"
                       r"contribut\w*|disburs\w*|funded", re.I)
PAID_OUT = re.compile(r"\bpaid\b|distributed|\bpayments?\b", re.I)
WORDS = {"cash_cost": (CASH_ONE_OFF, "the quote names no one-off item (restructuring, litigation, acquisition costs, a "
                                     "special pension contribution…)"),
         "special_dividend": (re.compile(r"(special|one-?time|extra(ordinary)?|non-?recurring|supplemental)\W+"
                                         r"(\w+\W+){0,3}(dividends?|distribut\w*)", re.I),
                              "the quote doesn't name a special or one-time dividend")}
PAID_WORDS = {"cash_cost": (CASH_PAID, "the quote doesn't show cash paid (a charge or accrual isn't cash)"),
              "special_dividend": (PAID_OUT, "the quote doesn't show the dividend was paid")}

STATUS_TEXT = {"skipped": "Skipped: agent triggers are skipped after a going-concern fail"}
RULES = [("A one-off cash cost or special dividend, quantified and not recurring",
          "Added back (cash costs to FFO, special dividends out of dividends paid); RCF / debt recomputed"),
         ("No one-offs found", "No adjustment"),
         ("A one-off identified but not quantified",
          "Unresolved: RCF / debt escalated to an analyst, who accepts the score or overrides it"),
         ("The agent's answer and the verified evidence disagree",
          "Unresolved: RCF / debt escalated to an analyst, who accepts the score or overrides it")]


# --- the passages ------------------------------------------------------------------------------------------------

def rcf_t1_passages(data: dict) -> dict:
    """MD&A's and the notes' paragraphs and tables that mention a one-off cash item, a special dividend or operating
    cash flow; ids F01… in document order."""
    return collect_passages(data, "F", LIMITS, SECTIONS, RCF_T1_TERMS, _section_after)


def _headline_index(m: dict) -> int | None:
    return next((i for i, c in enumerate(m["columns"]) if c["rcf_to_debt"].get("score") is not None), None)


def rcf_facts(col: dict) -> dict:
    """What the engine counted for the triggering column."""
    return {"heading": col["heading"], "period": col["period"], "period_end": col["period"].split("--")[1],
            "cfo": col.get("cfo"), "wc_change": col.get("wc_change"), "ffo": col.get("ffo"),
            "ffo_basis": col.get("ffo_basis"), "dividends": col.get("dividends") or 0.0,
            "nci_distributions": col.get("nci_distributions") or 0.0, "rcf": col.get("rcf"),
            "adj_debt": col.get("adj_debt"), "ratio": col["rcf_to_debt"].get("ratio"),
            "score": col["rcf_to_debt"].get("score")}


def rcf_t1_inputs(t: dict, meta: dict, fx: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": fx["period_end"]},
        "rcf": {"column": fx["heading"], "operating_cash_flow_millions": _millions(fx["cfo"]),
                "working_capital_changes_millions": _millions(fx["wc_change"]), "ffo_millions": _millions(fx["ffo"]),
                "ffo_basis": fx["ffo_basis"], "dividends_paid_millions": _millions(fx["dividends"]),
                "nci_distributions_millions": _millions(fx["nci_distributions"]), "rcf_millions": _millions(fx["rcf"]),
                "debt_plus_leases_millions": _millions(fx["adj_debt"]),
                "rcf_to_debt": round(fx["ratio"], 4) if fx["ratio"] is not None else None, "score": fx["score"],
                "trigger": f"score {RCF_T1_THRESHOLD} or more, or RCF / debt below zero"},
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t["passages"]],
    }


# --- validation --------------------------------------------------------------------------------------------------

def _one_off(o, by_id, held, notes):
    """One verified, quantified one-off cash item, or None (held back). ``recurring`` holds only with a verified quote
    showing the item recurs; a recurring claim that doesn't hold up leaves the item a one-off."""
    kind = o.get("kind")
    item = {"item_id": o.get("item_id"), "kind": kind, "category": o.get("category"),
            "description": o.get("description")}
    words, why = WORDS.get(kind, WORDS["cash_cost"])
    paid, paid_why = PAID_WORDS.get(kind, PAID_WORDS["cash_cost"])
    got = _amount(o.get("evidence"), by_id, words, why, item, "amount", held, paid, paid_why)
    if not got:
        return None
    entry = {**item, "amount": got[0], "evidence": got[1], "recurring": False, "recurring_evidence": None}
    if o.get("recurring"):
        r = o.get("recurring_evidence")
        rp, why = _quoted(r, by_id, RECURRING, "the quote doesn't show the item recurs")
        if why:
            held.append(_held(item, "recurring", r, why))
            notes.append(f"{item['item_id']}: the claim that it recurs doesn't hold up, so it is counted as a one-off.")
        else:
            entry.update(recurring=True, recurring_evidence=_ev(r, rp))
    return entry


def validate_rcf_t1(parsed: dict, t: dict, fx: dict) -> dict:
    """Keep the evidence that holds up and re-derive the outcome, first match wins: added back (quantified,
    non-recurring one-offs), no one-offs (no adjustment), else unresolved (identified but not quantified)."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    held, notes = [], []
    items = [e for o in parsed.get("one_offs") or [] for e in [_one_off(o, by_id, held, notes)] if e]
    one_offs = [e for e in items if not e["recurring"]]
    recurring = [e for e in items if e["recurring"]]
    unquantified = []
    for u in parsed.get("unquantified") or []:
        item = {"item_id": u.get("item_id"), "kind": u.get("kind"), "category": u.get("category"),
                "description": u.get("description")}
        words, why = WORDS.get(u.get("kind"), WORDS["cash_cost"])
        p, why = _quoted(u.get("evidence"), by_id, words, why)
        if why:
            held.append(_held(item, "one-off mentioned", u.get("evidence"), why))
        else:
            unquantified.append({**item, "evidence": _ev(u["evidence"], p)})
    costs = sum(e["amount"] for e in one_offs if e["kind"] == "cash_cost")
    special = sum(e["amount"] for e in one_offs if e["kind"] == "special_dividend")
    if special > fx["dividends"]:
        notes.append(f"The special dividends ({special / 1e6:,.1f} million) exceed the dividends paid as tagged "
                     f"({fx['dividends'] / 1e6:,.1f} million): only the dividends paid are taken out.")
        special = fx["dividends"]
    why = None
    if one_offs:
        status = "added_back"
        if unquantified:
            notes.append(f"{', '.join(u['item_id'] for u in unquantified)} also identified without a cash amount: not "
                         "added back (a quantified one-off comes first).")
    elif not unquantified:
        status = "no_one_offs"
    else:
        status = "unresolved"
        why = f"a one-off is identified but not quantified ({', '.join(u['item_id'] for u in unquantified)})"
    added = status == "added_back"
    return {"status": status, "one_offs": one_offs, "recurring": recurring, "unquantified": unquantified,
            "cash_costs": costs if added else 0.0, "special_dividends": special if added else 0.0,
            "unresolved_why": why, "held_back": held, "stated_status": parsed.get("status"),
            "status_consistent": parsed.get("status") == status, "validation_notes": notes}


def agent_reading(parsed: dict, t: dict, fx: dict) -> dict:
    """The agent's answer as it states it (not verified, not applied), for an analyst to compare with the verified
    outcome: "added_back" adds back the one-off cash costs it calls non-recurring and takes its
    special dividends out (up to the dividends paid). Any other answer changes nothing."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    stated, notes, costs, special = parsed.get("status"), [], 0.0, 0.0
    if stated == "added_back":
        for o in parsed.get("one_offs") or []:
            if o.get("recurring"):
                continue
            v = stated_amount(o.get("evidence"), by_id, notes, o.get("item_id")) or 0.0
            if o.get("kind") == "special_dividend":
                special += v
            elif o.get("kind") == "cash_cost":
                costs += v
        special = min(special, fx["dividends"])
    return {"status": stated, "notes": notes,
            "applies": {"status": stated, "cash_costs": costs, "special_dividends": special}}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_RCF_T1_PROMPT, template=FO_RCF_T1_TEMPLATE, response_schema=FO_RCF_T1_SCHEMA,
                      role="analyze", label="Financial Overview Trigger Agent: RCF/Debt T1")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

def _reason(status: str, fx: dict, checked: dict) -> str:
    if status == "added_back":
        items = "; ".join(f"{'special dividend' if e['kind'] == 'special_dividend' else 'cash cost'} of "
                          f"{e['amount'] / 1e6:,.1f} million ({e['evidence']['passage_id']})" for e in checked["one_offs"])
        rcf = fx["rcf"] + checked["cash_costs"] + checked["special_dividends"]
        return (f"One-offs quantified and not recurring ({items}): RCF {fx['rcf'] / 1e6:,.1f} → {rcf / 1e6:,.1f} "
                "million, RCF / debt recomputed")
    if status == "no_one_offs":
        tail = (f" (recurring: {', '.join(e['evidence']['passage_id'] for e in checked['recurring'])})"
                if checked["recurring"] else "")
        return f"No one-offs found{tail}: no adjustment"
    return "Unresolved: " + checked["unresolved_why"]


def _fired_by(fx: dict) -> str:
    parts = []
    if fx["score"] is not None and fx["score"] >= RCF_T1_THRESHOLD:
        parts.append(f"RCF / debt scores {fx['score']:g} ({RCF_T1_THRESHOLD} or more)")
    if fx["ratio"] is not None and fx["ratio"] < 0:
        parts.append(f"RCF / debt is {fx['ratio']:.1%} (below zero)")
    return " and ".join(parts)


def rcf_t1_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None,
                   mode: str = "live", case_id: str | None = None) -> dict:
    """The RCF/Debt T1 trigger result recorded in the run payload (``payload.triggers.rcf_t1``). ``metrics`` are the
    scored measures; the trigger reads RCF / debt's headline column (the latest year)."""
    m = next((x for x in metrics if x["key"] == "rcf"), None)
    score, heading = headline_score(m) if m else (None, None)
    result = {"trigger": RCF_T1_ID, "measure": "rcf", "version": RCF_T1_VERSION, "threshold": RCF_T1_THRESHOLD,
              "score": score, "column": heading, "fired": False, "mode": mode if backend is not None else "none",
              "agent": None, "notes": [], "review_required": False}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", reason=STATUS_TEXT["skipped"],
                      effect="None: the measures are superseded by the going-concern fail.")
        return result
    if score is None:
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason="RCF / debt has no score: trigger RCF/Debt T1 not set off")
        return result
    fx = rcf_facts(m["columns"][_headline_index(m)])
    result["inputs"] = fx
    if score < RCF_T1_THRESHOLD and not (fx["ratio"] is not None and fx["ratio"] < 0):
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason=f"RCF / debt scored {score:g}, below {RCF_T1_THRESHOLD}, and isn't negative: trigger "
                             "RCF/Debt T1 not set off")
        return result
    t = rcf_t1_passages(data)
    result.update(fired=True, fired_by=_fired_by(fx), passages=t["passages"], sections=t["sections"])
    if not t["passages"]:
        checked = {"status": "no_one_offs", "one_offs": [], "recurring": [], "unquantified": [], "cash_costs": 0.0,
                   "special_dividends": 0.0, "unresolved_why": None, "status_consistent": True}
        decided_by = "no_text"
        result["notes"].append("No MD&A or note passage mentions a one-off cash item, a special dividend or operating "
                               "cash flow: the agent was not called. No one-offs found, so no adjustment.")
    else:
        require_agent(backend, RCF_T1_ID, AGENT)
        checked, decided_by = _run_agent(result, t, data, fx, session, backend, case_id)
    status = checked["status"]
    disagree = not checked["status_consistent"]
    unresolved = status == "unresolved" or disagree
    why = ("the agent's answer and the verified evidence disagree" if disagree else checked["unresolved_why"]) \
        if unresolved else None
    result.update(status=status, decided_by=decided_by, one_offs=checked["one_offs"], recurring=checked["recurring"],
                  unquantified=checked["unquantified"], cash_costs=checked["cash_costs"],
                  special_dividends=checked["special_dividends"],
                  reason=_reason(status, fx, checked), unresolved=unresolved, disagreement=disagree,
                  review_required=unresolved, unresolved_why=why,
                  effect=("RCF / debt recomputed with the one-offs added back" if status == "added_back"
                          else "None: the RCF / debt score stays as calculated")
                  + (f"; unresolved ({why}), so RCF / debt is escalated to an analyst, who accepts the score or "
                     "overrides it." if unresolved else
                     "; resolved (the agent and the verified evidence agree), so no analyst decision is needed."))
    return result


def _run_agent(result, t, data, fx, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_RCF_T1_PROMPT,
                            rcf_t1_inputs(t, data["meta"], fx))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's RCF/Debt T1 output was rejected "
                                 f"({agent['validation_detail']}); the run was not recorded. Run it again.")
    checked = validate_rcf_t1(run.parsed, t, fx)
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

def apply_rcf_t1(fo: list[dict], t: dict | None) -> list[dict]:
    """RCF / debt with the trigger's outcome on its headline column (a copy; the inputs stay untouched): one-off cash
    costs added back to FFO, special dividends taken out of dividends paid, RCF and RCF / debt recomputed and rescored
    with the settings the measure was scored with. The column keeps the figures as calculated. The measure is marked
    for escalation when the trigger is unresolved."""
    if not t or not t.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "rcf":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        col = m["columns"][_headline_index(m)]
        adj = {"trigger": "RCF/Debt T1", "outcome": t["status"], "calculated": col["rcf_to_debt"]["score"],
               "ratio_calculated": col["rcf_to_debt"]["ratio"], "rcf_calculated": col["rcf"],
               "cash_costs": t.get("cash_costs") or 0.0, "special_dividends": t.get("special_dividends") or 0.0,
               "recomputed": None}
        done = None
        if t["status"] == "added_back" and (adj["cash_costs"] or adj["special_dividends"]):
            col["ffo_before_trigger"], col["dividends_before_trigger"] = col["ffo"], col["dividends"]
            col["rcf_before_trigger"] = col["rcf"]
            col["ffo"] += adj["cash_costs"]
            col["dividends"] -= adj["special_dividends"]
            col["rcf"] = col["ffo"] - col["dividends"] - col["nci_distributions"]
            rc = col["rcf_to_debt"]
            if not rc["reason"] and not rc["no_debt"]:
                rc["ratio"] = col["rcf"] / col["adj_debt"]
            score_cash_flow(col, cash_flow_settings(m))
            adj["recomputed"] = col["rcf_to_debt"]["score"]
            done = (f"RCF {adj['rcf_calculated'] / 1e6:,.1f} → {col['rcf'] / 1e6:,.1f} million (one-offs added back), "
                    "RCF / debt score "
                    + (f"{adj['calculated']:g} → " if adj["calculated"] is not None else "")
                    + (f"{adj['recomputed']:g}" if adj["recomputed"] is not None else "none"))
        adj["rcf"], adj["score"] = col["rcf"], col["rcf_to_debt"]["score"]
        col["rcf_adjustment"] = adj
        add_trigger_note(m, (f"RCF/Debt trigger T1 ({t.get('fired_by')}, {col['heading']}): "
                             + (done or "no adjustment" if t["status"] == "unresolved"
                                else done or t["reason"][:1].lower() + t["reason"][1:])),
                         "trigger RCF/Debt T1: " + {"added_back": "one-offs added back",
                                                    "no_one_offs": "no one-offs"}.get(t["status"], "unresolved"),
                         t.get("unresolved", True), t.get("unresolved_why") or "identified but not quantified")
        out.append(m)
    return out
