"""FO Trigger Agent, RCF/Debt T2: a one-off cash inflow lifts operating cash flow, and so FFO and RCF, for one year only.

A supplemental-payment catch-up, insurance or litigation proceeds, a tax refund or a one-time working-capital release
can make RCF look stronger than the company's normal cash generation, and a good score hides it (RCF/Debt T1 only looks
at weak scores). T2 runs at any score, after RCF/Debt T1, on RCF / debt's headline column, only when the pre-checks
allow it (``payload.gate.agent_triggers`` is "enabled"). It is decided by code, on FFO (the RCF numerator: a CFO rise
from working capital alone doesn't move FFO):

- the one-off inflows tagged for the year (``measures.ONE_OFF_INFLOW_CONCEPTS``: tax refunds, insurance proceeds in
  operating activities) are above 5% × |FFO|; or
- FFO is up 25% or more on the prior year, and more than 10 points faster than revenue (a business that is simply
  growing doesn't set it off; without tagged revenue the growth alone does).

Same three steps as the other triggers:

1. ``rcf_t2_passages`` (deterministic) collects what the agent checks: the cash flow statement (operating section and
   supplemental disclosures), MD&A and the notes, paragraphs and table rows that mention a cash receipt, a refund,
   proceeds, government assistance or the change in operating cash flow (ids G01…).
2. The Trigger Agent (prompt ``fo_trigger_rcf_t2``) ALWAYS reads them, with the working-capital lines FFO already
   subtracts and the items RCF/Debt T1 counted. Every amount is quoted verbatim. Only a filing with none of those
   passages has nothing to read (no model call: no one-off inflow found, no adjustment).
3. ``validate_rcf_t2`` keeps only verbatim evidence that says what it must and RE-DERIVES the outcome. Code places each
   inflow against the working-capital lines (from the calculation linkbase) and tests recurrence (2 of the 3 years
   presented: from the tags, else from verified quotes). First match wins:

   - an inflow in operating cash flow outside the working-capital lines, quantified, not recurring: removed from CFO
     and FFO, RCF / debt recomputed;
   - a working-capital release: inside the working-capital lines, no adjustment (FFO already excludes it); outside
     them, only an amount quoted as one-time or timing is removed; no working-capital lines tagged (the composition
     can't be determined), unresolved;
   - recurs in 2 of the 3 years: no adjustment;
   - identified without an amount: unresolved;
   - in investing or financing cash flow, or none found: no adjustment.

   A quantified removal comes first; otherwise anything unresolved escalates RCF / debt, even beside items that get no
   adjustment.

When RCF/Debt T1 also fires, both outcomes apply to the same column (FFO + T1's cash costs − T2's inflows): the inflows
and outflows are netted, and an item T1 counted is held back here.
"""

from __future__ import annotations

import copy
import re

from app.prompts.fo_prompts import FO_RCF_T2_PROMPT, FO_RCF_T2_SCHEMA, FO_RCF_T2_TEMPLATE

from .dscr_triggers import _ev, _held, _millions, _quoted, _value, add_trigger_note, collect_passages, stated_amount
from .icr_t3 import OTHER_STATEMENT
from .icr_triggers import FORECAST, MDNA_SKIP
from .covenants import NOTE_HEADING
from .measures import concept_ref
from .precheck import DATA_NOTICE, PrecheckAgentError, call_agent, require_agent
from .rcf_triggers import _headline_index
from .scoring import cash_flow_settings, headline_score, score_cash_flow

RCF_T2_VERSION = "fo-trigger-rcf-t2-1.0.0"
RCF_T2_ID = "rcf_t2"
RCF_T2_SHARE = 0.05  # tagged one-off inflows above this share of |FFO| set the trigger off
RCF_T2_GROWTH = 0.25  # so does FFO up this much or more on the prior year...
RCF_T2_EXCESS = 0.10  # ...when it also outgrows revenue by more than this (10 points)
RECUR_YEARS = 2  # an inflow in this many of the years presented recurs
AGENT = "Trigger Agent"

SECTIONS = [("cash_flow", "Cash flow statement"), ("mdna", "MD&A"), ("notes", "Notes to the financial statements")]
LIMITS = {"cash_flow": 15, "mdna": 20, "notes": 25}
CASH_FLOW_TITLE = re.compile(r"statements? of (consolidated )?cash flows?|cash flows? statements?", re.I)
INFLOW = re.compile(r"refund\w*|insurance (proceeds|recover\w*|settlements?|claims?)|proceeds|settlements? (proceeds|"
                    r"received)|received|receipts?|collect\w*|supplemental (payments?|funding|programs?)|catch-?up|"
                    r"government (assistance|grants?|relief)|relief funds?|grants?\b|one-?time|timing|prior[- ]years?",
                    re.I)
RCF_T2_TERMS = re.compile(INFLOW.pattern + r"|operating activities|operating cash flows?|cash flows? from operations",
                          re.I)

# --- what a quote must say ---------------------------------------------------------------------------------------
NAMES = {"supplemental_payment": re.compile(r"supplemental|catch-?up|directed payment|DSH|disproportionate share|"
                                            r"upper payment limit|provider tax", re.I),
         "insurance": re.compile(r"insurance", re.I),
         "litigation": re.compile(r"litigation|settlement|lawsuit|legal|judgment|award", re.I),
         "tax_refund": re.compile(r"refund\w*|tax", re.I),
         "government_assistance": re.compile(r"government|relief|grant\w*|assistance|CARES|stimulus", re.I),
         "working_capital": re.compile(r"collect\w*|receivables?|payables?|working capital|prepaid|accru\w*|timing",
                                       re.I),
         "other": INFLOW}
RECEIVED = re.compile(r"receiv\w*|receipts?|proceeds|collect\w*|refund\w*|inflows?|recover\w*|funds?|paid to us|"
                      r"provided by|increase\w*", re.I)
ONE_TIME = re.compile(r"one-?time|non-?recurring|timing|catch-?up|prior[- ]years?|delayed|deferred from|"
                      r"previously (billed|recorded|outstanding)|unusual|not expected to recur", re.I)
CATEGORY_TAGS = {"tax_refund": "tax_refund", "insurance": "insurance"}  # categories code can test from the tags
KIND_TEXT = {"operating_inflow": "Operating inflow", "working_capital_release": "Working-capital release",
             "investing": "Investing inflow", "financing": "Financing inflow"}

STATUS_TEXT = {"skipped": "Skipped: agent triggers are skipped after a going-concern fail"}
RULES = [("An inflow in operating cash flow outside the working-capital lines, quantified, not recurring",
          "Removed from CFO and FFO; RCF / debt recomputed"),
         ("A working-capital release inside the working-capital lines", "No adjustment: FFO already excludes it"),
         ("A working-capital release outside them", "Only the amount quoted as one-time or timing is removed"),
         ("A working-capital release, but no working-capital lines are tagged (composition unknown)",
          "Unresolved: RCF / debt escalated to an analyst"),
         ("The inflow recurs (2 of the 3 years presented)", "No adjustment"),
         ("An inflow identified but not quantified", "Unresolved: RCF / debt escalated to an analyst"),
         ("In investing or financing cash flow, or no one-off inflow found", "No adjustment"),
         ("The agent's answer and the verified evidence disagree",
          "Unresolved: RCF / debt escalated to an analyst, who accepts the score or overrides it")]


# --- the passages ------------------------------------------------------------------------------------------------

def _section_after(item: str, heading: str, current: str | None, numbered: bool) -> str | None:
    """The searched section a heading opens, keeps or closes: the cash flow statement, MD&A (without its
    boilerplate) and every note."""
    if item == "7":
        return None if MDNA_SKIP.search(heading) else "mdna"
    if CASH_FLOW_TITLE.search(heading) and len(heading) <= 100:
        return "cash_flow"
    if OTHER_STATEMENT.search(heading) and len(heading) <= 100 or re.search(
            r"statements? of (consolidated )?(operations|income|earnings|comprehensive)", heading, re.I):
        return None  # the other primary statements
    if numbered and not NOTE_HEADING.match(heading):
        return current  # a sub-heading, or a line label inside the cash flow statement
    if not numbered and current == "cash_flow":
        return current
    return "notes"


def rcf_t2_passages(data: dict) -> dict:
    """The cash flow statement's, MD&A's and the notes' paragraphs and tables that mention a cash receipt, a refund,
    proceeds, government assistance or operating cash flow; ids G01… in document order."""
    return collect_passages(data, "G", LIMITS, SECTIONS, RCF_T2_TERMS, _section_after)


def rcf_t2_facts(m: dict, idx: int, wc_lines: dict, labels: dict) -> dict:
    """What the engine counted for the triggering column, the prior year's FFO and the tagged one-off inflows of each
    year presented."""
    col = m["columns"][idx]
    prior = m["columns"][idx + 1] if idx + 1 < len(m["columns"]) else None
    ffo, prior_ffo = col.get("ffo"), (prior or {}).get("ffo")
    revenue, prior_revenue = col.get("revenue"), (prior or {}).get("revenue")
    growth = ffo / prior_ffo - 1 if ffo is not None and prior_ffo and prior_ffo > 0 else None
    revenue_growth = revenue / prior_revenue - 1 if revenue is not None and prior_revenue and prior_revenue > 0 else None
    tagged = col.get("tagged_inflows") or []
    total = sum(e["value"] for e in tagged)
    return {"heading": col["heading"], "period": col["period"], "period_end": col["period"].split("--")[1],
            "cfo": col.get("cfo"), "wc_change": col.get("wc_change"), "ffo": ffo, "ffo_basis": col.get("ffo_basis"),
            "prior_heading": (prior or {}).get("heading"), "prior_ffo": prior_ffo,
            "growth": growth, "revenue": revenue, "prior_revenue": prior_revenue, "revenue_growth": revenue_growth,
            "excess": growth - revenue_growth if growth is not None and revenue_growth is not None else None,
            "tagged_inflows": tagged, "tagged_total": total,
            "share": total / abs(ffo) if ffo else None,
            "years": [{"heading": c["heading"], "tagged_inflows": c.get("tagged_inflows") or []}
                      for c in m["columns"][:3]],
            "working_capital_lines": [{"concept": c, "label": concept_ref(labels, c)} for c in wc_lines or {}],
            "rcf": col.get("rcf"), "ratio": col["rcf_to_debt"].get("ratio"), "score": col["rcf_to_debt"].get("score")}


def rcf_t2_inputs(t: dict, meta: dict, fx: dict, t1_items: list) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": fx["period_end"]},
        "rcf": {"column": fx["heading"], "operating_cash_flow_millions": _millions(fx["cfo"]),
                "working_capital_changes_millions": _millions(fx["wc_change"]), "ffo_millions": _millions(fx["ffo"]),
                "ffo_basis": fx["ffo_basis"], "prior_year_ffo_millions": _millions(fx["prior_ffo"]),
                "rcf_to_debt": round(fx["ratio"], 4) if fx["ratio"] is not None else None, "score": fx["score"],
                "working_capital_lines": fx["working_capital_lines"],
                "tagged_one_off_inflows": [{"concept": e["concept"], "millions": _millions(e["value"])}
                                           for e in fx["tagged_inflows"]],
                "counted_by_t1": [{"description": e.get("description"), "passage_id": e["evidence"]["passage_id"],
                                   "amount": e["evidence"].get("amount")} for e in t1_items],
                "trigger": t["fired_by"]},
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t["passages"]],
    }


# --- validation --------------------------------------------------------------------------------------------------

def _recurs(entry, other_years, by_id, fx, held) -> tuple[bool, str | None]:
    """(recurs, how): in 2 of the 3 years presented. A category the tags cover is tested on the tags; otherwise on
    verified amounts the agent quotes for the earlier years."""
    cat = CATEGORY_TAGS.get(entry["category"])
    if cat:
        years = [y["heading"] for y in fx["years"] if any(e["category"] == cat for e in y["tagged_inflows"])]
        if years:
            return len(years) >= RECUR_YEARS, f"tagged in {len(years)} of {len(fx['years'])} years ({', '.join(years)})"
    verified = 1  # the year itself
    for i, q in enumerate(other_years or [], 1):
        p, why = _quoted(q, by_id)
        value = None
        if not why:
            value, why = _value(q, p)
        if why:
            held.append(_held({"item_id": f"{entry['item_id']}-Y{i}"}, "earlier year", q, why))
        elif value:
            verified += 1
    return verified >= RECUR_YEARS, f"in {verified} of the years presented (verified quotes)"


def _inflow(o, by_id, fx, t1_items, held, notes):
    """One verified inflow, classified, or None (held back): (entry, outcome) where outcome is "remove", "inside",
    "recurring", "not_one_time", "outside_cash_flow", "unquantified" or "unknown_composition"."""
    kind, cat = o.get("kind"), o.get("category")
    item = {"item_id": o.get("item_id"), "kind": kind, "category": cat, "description": o.get("description"),
            "working_capital_line": o.get("working_capital_line")}
    e = o.get("evidence") or {}
    p, why = _quoted(e, by_id, NAMES.get(cat, NAMES["other"]), "the quote doesn't name the inflow")
    if not why and not RECEIVED.search(e["quote"]):
        why = "the quote doesn't show cash received"
    if not why and FORECAST.search(e["quote"]):
        why = "the quote reads as a forecast, not the year's figure"
    if not why and any(x["evidence"]["passage_id"] == e.get("passage_id") and x["evidence"].get("amount") ==
                       e.get("amount") for x in t1_items):
        why = "RCF/Debt trigger T1 already counted this item"
    lines = {x["concept"] for x in fx["working_capital_lines"]}
    if not why and item["working_capital_line"] and item["working_capital_line"] not in lines:
        why = f"{item['working_capital_line']} isn't one of the working-capital lines FFO subtracts"
    if why:
        held.append(_held(item, "inflow", e, why))
        return None
    entry = {**item, "amount": None, "evidence": _ev(e, p), "one_time_evidence": None, "recurrence": None}
    if (e.get("amount") or "").strip():
        value, why = _value(e, p)
        if why:
            held.append(_held(item, "amount", e, why))
        else:
            entry["amount"] = value
    if kind in ("investing", "financing"):
        return entry, "outside_cash_flow"
    if item["working_capital_line"]:
        return entry, "inside"  # inside the working-capital lines: FFO already excludes it
    if kind == "working_capital_release" and not lines:
        return entry, "unknown_composition"
    if entry["amount"] is None:
        return entry, "unquantified"
    recurs, how = _recurs(entry, o.get("other_years"), by_id, fx, held)
    entry["recurrence"] = how
    if recurs:
        return entry, "recurring"
    if kind == "working_capital_release":
        q = o.get("one_time_evidence")
        tp, why = _quoted(q, by_id, ONE_TIME, "the quote doesn't describe it as one-time or timing")
        if why:
            if q:
                held.append(_held(item, "one-time", q, why))
            notes.append(f"{item['item_id']}: a working-capital release outside the working-capital lines not "
                         "described as one-time or timing: not removed.")
            return entry, "not_one_time"
        entry["one_time_evidence"] = _ev(q, tp)
    return entry, "remove"


def validate_rcf_t2(parsed: dict, t: dict, fx: dict, t1_items: list | None = None) -> dict:
    """Keep the evidence that holds up and re-derive the outcome: removed (a quantified, non-recurring inflow outside
    the working-capital lines), else unresolved (identified without an amount, or a release whose place can't be
    determined), else no adjustment."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    held, notes = [], []
    t1_items = t1_items or []
    classified = [r for o in parsed.get("inflows") or [] for r in [_inflow(o, by_id, fx, t1_items, held, notes)] if r]
    groups = {}
    for entry, outcome in classified:
        entry["outcome"] = outcome
        groups.setdefault(outcome, []).append(entry)
    mentioned = []  # inflows the agent names without an amount
    for u in parsed.get("unquantified") or []:
        item = {"item_id": u.get("item_id"), "kind": u.get("kind"), "category": u.get("category"),
                "description": u.get("description"), "outcome": "unquantified"}
        p, why = _quoted(u.get("evidence"), by_id, NAMES.get(u.get("category"), NAMES["other"]),
                         "the quote doesn't name the inflow")
        if why:
            held.append(_held(item, "inflow mentioned", u.get("evidence"), why))
        elif u.get("kind") not in ("investing", "financing"):
            mentioned.append({**item, "evidence": _ev(u["evidence"], p)})
    groups.setdefault("unquantified", []).extend(mentioned)
    removed = groups.get("remove", [])
    pending = groups.get("unquantified", []) + groups.get("unknown_composition", [])
    why = None
    if removed:
        status = "removed"
        if pending:
            notes.append(f"{', '.join(e['item_id'] for e in pending)} also identified but not quantified or placed: "
                         "not removed (a quantified inflow comes first).")
    elif pending:
        status = "unresolved"
        why = ("a one-off inflow is identified but not quantified, or a working-capital release can't be placed "
               f"(no working-capital lines tagged) ({', '.join(e['item_id'] for e in pending)})")
    else:
        status = "no_adjustment"
    return {"status": status, "inflows": [e for e, _ in classified] + mentioned, "removed_items": removed, "removed": sum(e["amount"] for e in removed) if status == "removed" else 0.0,
            "unresolved_why": why, "held_back": held, "stated_status": parsed.get("status"),
            "status_consistent": parsed.get("status") == status, "validation_notes": notes}


def agent_reading(parsed: dict, t: dict) -> dict:
    """The agent's answer as it states it (not verified, not applied), for an analyst to compare with the verified
    outcome: "removed" takes out every operating inflow it gives an amount for and doesn't place
    in a working-capital line. Any other answer changes nothing."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    stated, notes, removed = parsed.get("status"), [], 0.0
    if stated == "removed":
        removed = sum(v for o in parsed.get("inflows") or []
                      if o.get("kind") not in ("investing", "financing") and not o.get("working_capital_line")
                      for v in [stated_amount(o.get("evidence"), by_id, notes, o.get("item_id"))] if v)
    return {"status": stated, "notes": notes, "applies": {"status": stated, "removed": removed}}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_RCF_T2_PROMPT, template=FO_RCF_T2_TEMPLATE, response_schema=FO_RCF_T2_SCHEMA,
                      role="analyze", label="Financial Overview Trigger Agent: RCF/Debt T2")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

OUTCOME_TEXT = {"inside": "inside the working-capital lines (FFO already excludes it)", "recurring": "recurs",
                "not_one_time": "not described as one-time", "outside_cash_flow": "investing or financing"}


def _reason(status: str, fx: dict, checked: dict) -> str:
    if status == "removed":
        items = "; ".join(f"{KIND_TEXT[e['kind']].lower()} of {e['amount'] / 1e6:,.1f} million "
                          f"({e['evidence']['passage_id']})" for e in checked["removed_items"])
        return (f"One-off inflows quantified and not recurring ({items}): FFO {fx['ffo'] / 1e6:,.1f} → "
                f"{(fx['ffo'] - checked['removed']) / 1e6:,.1f} million, RCF / debt recomputed")
    if status == "unresolved":
        return "Unresolved: " + checked["unresolved_why"]
    seen = [f"{e['item_id']} {OUTCOME_TEXT[e['outcome']]}" for e in checked["inflows"] if e["outcome"] in OUTCOME_TEXT]
    return ("No one-off inflow to remove (" + "; ".join(seen) + ")" if seen else "No one-off inflow found") \
        + ": no adjustment"


def _growth_fires(fx: dict) -> bool:
    """FFO up 25% or more, and more than 10 points faster than revenue (or revenue isn't tagged for both years)."""
    return (fx["growth"] is not None and fx["growth"] >= RCF_T2_GROWTH
            and (fx["excess"] is None or fx["excess"] > RCF_T2_EXCESS))


def _fired_by(fx: dict) -> str:
    parts = []
    if fx["share"] is not None and fx["share"] > RCF_T2_SHARE:
        parts.append(f"tagged one-off inflows of {fx['tagged_total'] / 1e6:,.1f} million ({fx['share']:.1%} of FFO, "
                     f"above {RCF_T2_SHARE:.0%})")
    if _growth_fires(fx):
        parts.append(f"FFO up {fx['growth']:.1%} on {fx['prior_heading']} ({RCF_T2_GROWTH:.0%} or more), "
                     + (f"{fx['excess'] * 100:.1f} points above revenue growth of {fx['revenue_growth']:.1%}"
                        if fx["excess"] is not None else "revenue growth not tagged"))
    return " and ".join(parts)


def rcf_t2_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None, mode: str = "live",
                   case_id: str | None = None, t1: dict | None = None) -> dict:
    """The RCF/Debt T2 trigger result recorded in the run payload (``payload.triggers.rcf_t2``). ``metrics`` are the
    scored measures (after RCF/Debt T1); ``t1`` is RCF/Debt T1's result, whose counted items T2 won't count again."""
    m = next((x for x in metrics if x["key"] == "rcf"), None)
    score, heading = headline_score(m) if m else (None, None)
    result = {"trigger": RCF_T2_ID, "measure": "rcf", "version": RCF_T2_VERSION, "share_threshold": RCF_T2_SHARE,
              "growth_threshold": RCF_T2_GROWTH, "excess_threshold": RCF_T2_EXCESS, "score": score, "column": heading, "fired": False,
              "mode": mode if backend is not None else "none", "agent": None, "notes": [], "review_required": False}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", reason=STATUS_TEXT["skipped"],
                      effect="None: the measures are superseded by the going-concern fail.")
        return result
    if score is None:
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason="RCF / debt has no score: trigger RCF/Debt T2 not set off")
        return result
    fx = rcf_t2_facts(m, _headline_index(m), data.get("working_capital") or {}, data.get("labels") or {})
    result["inputs"] = fx
    fired_by = _fired_by(fx)
    if not fired_by:
        result.update(status="not_triggered", decided_by="ffo", effect="None.",
                      reason=(f"Tagged one-off inflows are {fx['tagged_total'] / 1e6:,.1f} million"
                              + (f" ({fx['share']:.1%} of FFO)" if fx["share"] is not None else "")
                              + f", not above {RCF_T2_SHARE:.0%}, and FFO "
                              + (f"is {fx['growth']:+.1%} on the prior year" if fx["growth"] is not None
                                 else "has no positive prior year to compare")
                              + (f" against revenue {fx['revenue_growth']:+.1%}" if fx["revenue_growth"] is not None
                                 else "")
                              + f", not up {RCF_T2_GROWTH:.0%} or more and over {RCF_T2_EXCESS * 100:.0f} points faster "
                              "than revenue: trigger RCF/Debt T2 not set off"))
        return result
    t = rcf_t2_passages(data)
    t1_items = (t1 or {}).get("one_offs") or []
    result.update(fired=True, fired_by=fired_by, passages=t["passages"], sections=t["sections"])
    if not t["passages"]:
        checked = {"status": "no_adjustment", "inflows": [], "removed_items": [], "removed": 0.0,
                   "unresolved_why": None, "status_consistent": True}
        decided_by = "no_text"
        result["notes"].append("No cash flow statement, MD&A or note passage mentions a cash receipt or operating cash "
                               "flow: the agent was not called. No one-off inflow found, so no adjustment.")
    else:
        require_agent(backend, RCF_T2_ID, AGENT)
        checked, decided_by = _run_agent(result, t, data, fx, t1_items, session, backend, case_id)
    status = checked["status"]
    disagree = not checked["status_consistent"]
    unresolved = status == "unresolved" or disagree
    why = ("the agent's answer and the verified evidence disagree" if disagree else checked["unresolved_why"]) \
        if unresolved else None
    result.update(status=status, decided_by=decided_by, inflows=checked["inflows"],
                  removed_items=checked["removed_items"], removed=checked["removed"],
                  reason=_reason(status, fx, checked), unresolved=unresolved, disagreement=disagree,
                  review_required=unresolved, unresolved_why=why,
                  effect=("CFO and FFO less the one-off inflows; RCF / debt recomputed" if status == "removed"
                          else "None: the RCF / debt score stays as calculated")
                  + (f"; unresolved ({why}), so RCF / debt is escalated to an analyst, who accepts the score or "
                     "overrides it." if unresolved else
                     "; resolved (the agent and the verified evidence agree), so no analyst decision is needed."))
    return result


def _run_agent(result, t, data, fx, t1_items, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_RCF_T2_PROMPT,
                            rcf_t2_inputs({**t, "fired_by": result["fired_by"]}, data["meta"], fx, t1_items))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's RCF/Debt T2 output was rejected "
                                 f"({agent['validation_detail']}); the run was not recorded. Run it again.")
    checked = validate_rcf_t2(run.parsed, t, fx, t1_items)
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

def apply_rcf_t2(fo: list[dict], t: dict | None) -> list[dict]:
    """RCF / debt with the trigger's outcome on its headline column (a copy; the inputs stay untouched): the one-off
    inflows come out of CFO and FFO, RCF, RCF / debt and the CFO-based comparison are recomputed and rescored with the
    settings the measure was scored with. Runs after RCF/Debt T1, so T1's add-backs and T2's removals net. The column
    keeps CFO as tagged. The measure is marked for escalation when the trigger is unresolved."""
    if not t or not t.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "rcf":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        col = m["columns"][_headline_index(m)]
        adj = {"trigger": "RCF/Debt T2", "outcome": t["status"], "calculated": col["rcf_to_debt"]["score"],
               "ratio_calculated": col["rcf_to_debt"]["ratio"], "ffo_calculated": col["ffo"],
               "rcf_calculated": col["rcf"], "removed": t.get("removed") or 0.0, "recomputed": None}
        done = None
        if t["status"] == "removed" and adj["removed"]:
            col["cfo_before_trigger"] = col["cfo"]
            col.setdefault("ffo_before_trigger", col["ffo"])
            col.setdefault("rcf_before_trigger", col["rcf"])
            col["cfo"] -= adj["removed"]
            col["ffo"] -= adj["removed"]
            col["rcf"] = col["ffo"] - col["dividends"] - col["nci_distributions"]
            if col.get("rcf_cfo") is not None:
                col["rcf_cfo"] -= adj["removed"]
            rc = col["rcf_to_debt"]
            if not rc["reason"] and not rc["no_debt"]:
                rc["ratio"] = col["rcf"] / col["adj_debt"]
                if col.get("rcf_to_debt_cfo") is not None:
                    col["rcf_to_debt_cfo"] = col["rcf_cfo"] / col["adj_debt"]
            score_cash_flow(col, cash_flow_settings(m))
            adj["recomputed"] = col["rcf_to_debt"]["score"]
            done = (f"FFO {adj['ffo_calculated'] / 1e6:,.1f} → {col['ffo'] / 1e6:,.1f} million (one-off inflows out), "
                    "RCF / debt score "
                    + (f"{adj['calculated']:g} → " if adj["calculated"] is not None else "")
                    + (f"{adj['recomputed']:g}" if adj["recomputed"] is not None else "none"))
        adj["rcf"], adj["score"] = col["rcf"], col["rcf_to_debt"]["score"]
        col["rcf_t2_adjustment"] = adj
        add_trigger_note(m, (f"RCF/Debt trigger T2 ({t.get('fired_by')}, {col['heading']}): "
                             + (done or "no adjustment" if t["status"] == "unresolved"
                                else done or t["reason"][:1].lower() + t["reason"][1:])),
                         "trigger RCF/Debt T2: " + {"removed": "one-off inflows removed",
                                                    "no_adjustment": "no adjustment"}.get(t["status"], "unresolved"),
                         t.get("unresolved", True), t.get("unresolved_why") or "identified but not quantified")
        out.append(m)
    return out
