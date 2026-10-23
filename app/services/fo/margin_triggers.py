"""FO Trigger Agent, EBITDAR Margin T1: a one-off booked in revenue raises both EBITDAR and revenue, so correcting only
EBITDAR leaves the margin distorted.

Debt/EBITDAR T1 normalises EBITDAR by removing quantified, non-recurring one-offs (charges added back, gains taken out).
Margin T1 runs after it, only when the pre-checks allow it (``payload.gate.agent_triggers`` is "enabled"), whenever
that trigger removed one-offs (status "normalised") on the year the margin's headline column covers. Code decides; the
agent says where each removed item was booked. Same three steps as the other triggers:

1. ``margin_t1_passages`` (deterministic) collects what the agent checks: the income statement, MD&A and the notes'
   paragraphs and table rows that mention revenue, operating or non-operating items or a one-off (ids E01…).
2. The Trigger Agent (prompt ``fo_trigger_margin_t1``) ALWAYS reads them, with T1's items. Every location is quoted
   verbatim. Only a filing with none of those passages has nothing to read (no model call: unresolved).
3. ``validate_margin_t1`` keeps only verbatim evidence that says what it must and RE-DERIVES the outcome:

   - an item booked in revenue: it comes out of revenue too (a gain lowers revenue, a contra-revenue charge raises
     it), and the margin is recomputed;
   - every item booked outside revenue (other operating income, below operating income): no further adjustment;
   - an item's location unclear: unresolved, so the margin is escalated to an analyst.

In every outcome the margin takes Debt/EBITDAR T1's normalised EBITDAR (the same one-offs, so the same correction);
only revenue depends on where they were booked. The margin alone falls back to cash lease payments when the
lease-cost table is missing (``measures.ebitdar_margin``, labelled); that isn't a reason to leave it unresolved.
"""

from __future__ import annotations

import copy
import re

from app.prompts.fo_prompts import FO_MARGIN_T1_PROMPT, FO_MARGIN_T1_SCHEMA, FO_MARGIN_T1_TEMPLATE

from .dscr_triggers import _ev, _held, _millions, _quoted, add_trigger_note, collect_passages, stated_amount
from .icr_t3 import LIMITS, SECTIONS, _section_after
from .icr_triggers import ONE_OFF
from .precheck import DATA_NOTICE, PrecheckAgentError, call_agent, require_agent
from .scoring import headline_score, margin_settings, score_margin

MARGIN_T1_VERSION = "fo-trigger-margin-t1-1.0.0"
MARGIN_T1_ID = "margin_t1"
AGENT = "Trigger Agent"

REVENUE = re.compile(r"revenues?|net sales|sales\b|patient service|net operating revenues?|contra-?revenue", re.I)
OPERATING = re.compile(r"other operating|operating (expenses?|costs?|income)|costs? and expenses|selling, general|"
                       r"general and administrative|salaries|supplies|impairment|restructuring|within operating", re.I)
BELOW = re.compile(r"other (income|expense)\w*|non-?operating|below operating income|interest (income|expense)|"
                   r"gains? on (the )?(sale|disposal)|losses? on (the )?(sale|disposal)|investment (income|gains?)|"
                   r"equity in (the )?(earnings|income)", re.I)
MARGIN_T1_TERMS = re.compile(REVENUE.pattern + "|" + OPERATING.pattern + "|" + BELOW.pattern + "|" + ONE_OFF.pattern,
                             re.I)
WORDS = {"revenue": (REVENUE, "the quote doesn't place it in revenue"),
         "other_operating_income": (OPERATING, "the quote doesn't place it in operating income outside revenue"),
         "below_operating_income": (BELOW, "the quote doesn't place it below operating income")}
LOCATION_TEXT = {"revenue": "revenue", "other_operating_income": "other operating income",
                 "below_operating_income": "below operating income", "unclear": "unclear"}

STATUS_TEXT = {"skipped": "Skipped: agent triggers are skipped after a going-concern fail"}
RULES = [("An item Debt/EBITDAR T1 removed was booked in revenue",
          "It comes out of revenue too; the margin is recomputed on T1's EBITDAR and the corrected revenue"),
         ("Every item was booked outside revenue",
          "No further adjustment: the margin takes T1's EBITDAR, revenue as reported"),
         ("Where an item was booked isn't clear",
          "Unresolved: the margin (on T1's EBITDAR, revenue as reported) is escalated to an analyst"),
         ("The agent's answer and the verified evidence disagree",
          "Unresolved: the margin is escalated to an analyst, who accepts the score or overrides it")]


# --- the passages ------------------------------------------------------------------------------------------------

def margin_t1_passages(data: dict) -> dict:
    """The income statement's, MD&A's and the notes' paragraphs and tables that mention revenue, operating or
    non-operating items or a one-off; ids E01… in document order."""
    return collect_passages(data, "E", LIMITS, SECTIONS, MARGIN_T1_TERMS, _section_after)


def _headline_index(m: dict) -> int | None:
    return next((i for i, c in enumerate(m["columns"]) if c.get("score") is not None), None)


def margin_facts(col: dict, t1: dict) -> dict:
    return {"heading": col["heading"], "period": col["period"], "period_end": col["period"].split("--")[1],
            "ebitdar": col.get("ebitdar"), "revenue": col.get("revenue"), "ratio": col.get("ratio"),
            "score": col.get("score"), "rent_basis": col.get("rent_basis"),
            "normalisation": t1.get("normalisation") or 0.0,
            "one_offs": [{"item_id": e["item_id"], "kind": e["kind"], "category": e["category"],
                          "description": e.get("description"), "amount": e["amount"], "effect": e["effect"],
                          "evidence": e["evidence"]} for e in t1.get("one_offs") or []]}


def margin_t1_inputs(t: dict, meta: dict, fx: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": fx["period_end"]},
        "margin": {"column": fx["heading"], "ebitdar_millions": _millions(fx["ebitdar"]),
                   "revenue_millions": _millions(fx["revenue"]),
                   "ebitdar_margin": round(fx["ratio"], 4) if fx["ratio"] is not None else None,
                   "one_offs_removed": [{"item_id": e["item_id"], "kind": e["kind"], "category": e["category"],
                                         "description": e["description"], "amount_millions": _millions(e["amount"]),
                                         "quote": e["evidence"]["quote"]} for e in fx["one_offs"]],
                   "trigger": "Debt/EBITDAR T1 removed one-off items from EBITDAR"},
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t["passages"]],
    }


# --- validation --------------------------------------------------------------------------------------------------

def validate_margin_t1(parsed: dict, t: dict, fx: dict) -> dict:
    """Place each of T1's items (a verified quote, else unclear) and re-derive the outcome: revenue adjusted (an item
    in revenue), else unresolved (an item unclear), else outside revenue."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    held, notes = [], []
    given = {}
    for o in parsed.get("locations") or []:
        if o.get("item_id") in given:
            continue
        given[o.get("item_id")] = o
    items = []
    for e in fx["one_offs"]:
        o = given.get(e["item_id"])
        place, evidence = "unclear", None
        if o is None:
            notes.append(f"{e['item_id']}: the agent didn't place it, so its location is unclear.")
        elif o.get("location") in WORDS:
            words, why = WORDS[o["location"]]
            p, why = _quoted(o.get("evidence"), by_id, words, why)
            if why:
                held.append(_held({"item_id": e["item_id"]}, "location", o.get("evidence"), why))
            else:
                place, evidence = o["location"], _ev(o["evidence"], p)
        items.append({**e, "location": place, "location_evidence": evidence})
    for item_id in set(given) - {e["item_id"] for e in fx["one_offs"]}:
        held.append(_held({"item_id": item_id}, "location", given[item_id].get("evidence"),
                          "not one of the items Debt/EBITDAR T1 removed"))
    in_revenue = [e for e in items if e["location"] == "revenue"]
    unclear = [e for e in items if e["location"] == "unclear"]
    why = None
    if in_revenue:
        status = "revenue_adjusted"
        if unclear:
            notes.append(f"{', '.join(e['item_id'] for e in unclear)} also unclear: revenue not adjusted for it (an "
                         "item in revenue comes first).")
    elif unclear:
        status = "unresolved"
        why = f"where an item was booked isn't clear ({', '.join(e['item_id'] for e in unclear)})"
    else:
        status = "outside_revenue"
    return {"status": status, "items": items,
            "revenue_adjustment": sum(e["effect"] for e in in_revenue) if status == "revenue_adjusted" else 0.0,
            "unresolved_why": why, "held_back": held, "stated_status": parsed.get("status"),
            "status_consistent": parsed.get("status") == status, "validation_notes": notes}


def agent_reading(parsed: dict, fx: dict) -> dict:
    """The agent's answer as it states it (not verified, not applied), for an analyst to compare with the verified
    outcome: "revenue_adjusted" takes out of revenue the Debt/EBITDAR T1 items it places in
    revenue. Any other answer changes only EBITDAR, as Debt/EBITDAR T1 did."""
    stated = parsed.get("status")
    places = {o.get("item_id"): o.get("location") for o in parsed.get("locations") or []}
    adjustment = (sum(e["effect"] for e in fx["one_offs"] if places.get(e["item_id"]) == "revenue")
                  if stated == "revenue_adjusted" else 0.0)
    return {"status": stated, "notes": [], "applies": {"status": stated, "revenue_adjustment": adjustment}}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_MARGIN_T1_PROMPT, template=FO_MARGIN_T1_TEMPLATE, response_schema=FO_MARGIN_T1_SCHEMA,
                      role="analyze", label="Financial Overview Trigger Agent: EBITDAR Margin T1")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

def _reason(status: str, fx: dict, checked: dict) -> str:
    if status == "revenue_adjusted":
        items = "; ".join(f"{e['kind']} of {e['amount'] / 1e6:,.1f} million ({e['location_evidence']['passage_id']})"
                          for e in checked["items"] if e["location"] == "revenue")
        return (f"One-offs booked in revenue ({items}): revenue {fx['revenue'] / 1e6:,.1f} → "
                f"{(fx['revenue'] + checked['revenue_adjustment']) / 1e6:,.1f} million, margin recomputed on "
                "Debt/EBITDAR T1's EBITDAR")
    if status == "outside_revenue":
        where = "; ".join(f"{e['item_id']} {LOCATION_TEXT[e['location']]}" for e in checked["items"])
        return f"Every one-off was booked outside revenue ({where}): margin on Debt/EBITDAR T1's EBITDAR, no further " \
               "adjustment"
    return "Unresolved: " + checked["unresolved_why"]


def margin_t1_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None,
                      mode: str = "live", case_id: str | None = None, leverage_t1: dict | None = None) -> dict:
    """The EBITDAR Margin T1 trigger result recorded in the run payload (``payload.triggers.margin_t1``).
    ``leverage_t1`` is Debt/EBITDAR T1's result: Margin T1 fires when it removed one-offs."""
    m = next((x for x in metrics if x["key"] == "margin"), None)
    score, heading = headline_score(m) if m else (None, None)
    result = {"trigger": MARGIN_T1_ID, "measure": "margin", "version": MARGIN_T1_VERSION, "score": score,
              "column": heading, "fired": False, "mode": mode if backend is not None else "none", "agent": None,
              "notes": [], "review_required": False}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", reason=STATUS_TEXT["skipped"],
                      effect="None: the measures are superseded by the going-concern fail.")
        return result
    if score is None:
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason="The EBITDAR margin has no score: trigger EBITDAR Margin T1 not set off")
        return result
    t1 = leverage_t1 or {}
    col = m["columns"][_headline_index(m)]
    if not (t1.get("fired") and t1.get("status") == "normalised" and t1.get("one_offs")):
        result.update(status="not_triggered", decided_by="leverage_t1", effect="None.",
                      reason="Debt/EBITDAR T1 removed no one-off items: trigger EBITDAR Margin T1 not set off")
        return result
    period = (t1.get("inputs") or {}).get("period")
    if period != col["period"]:
        result.update(status="not_triggered", decided_by="leverage_t1", effect="None.",
                      reason=f"Debt/EBITDAR T1 corrected {t1.get('column')}, not the margin's {col['heading']}: "
                             "trigger EBITDAR Margin T1 not set off")
        return result
    fx = margin_facts(col, t1)
    result["inputs"] = fx
    t = margin_t1_passages(data)
    result.update(fired=True, fired_by=f"Debt/EBITDAR T1 removed {len(fx['one_offs'])} one-off item(s) "
                                       f"({', '.join(e['item_id'] for e in fx['one_offs'])}) from {col['heading']}'s "
                                       "EBITDAR", passages=t["passages"], sections=t["sections"])
    if fx["rent_basis"]:
        result["notes"].append(f"Rent in the margin is from {fx['rent_basis']} (labelled; resolved).")
    if not t["passages"]:
        checked = {"status": "unresolved", "items": [{**e, "location": "unclear", "location_evidence": None}
                                                     for e in fx["one_offs"]],
                   "revenue_adjustment": 0.0, "status_consistent": True,
                   "unresolved_why": "where the items were booked isn't clear (no income statement, MD&A or note "
                                     "passage to read)"}
        decided_by = "no_text"
        result["notes"].append("No income statement, MD&A or note passage to read: the agent was not called.")
    else:
        require_agent(backend, MARGIN_T1_ID, AGENT)
        checked, decided_by = _run_agent(result, t, data, fx, session, backend, case_id)
    status = checked["status"]
    disagree = not checked["status_consistent"]
    unresolved = status == "unresolved" or disagree
    why = ("the agent's answer and the verified evidence disagree" if disagree else checked["unresolved_why"]) \
        if unresolved else None
    result.update(status=status, decided_by=decided_by, items=checked["items"],
                  normalisation=fx["normalisation"], revenue_adjustment=checked["revenue_adjustment"],
                  reason=_reason(status, fx, checked), unresolved=unresolved, disagreement=disagree,
                  review_required=unresolved, unresolved_why=why,
                  effect=("The margin is recomputed on Debt/EBITDAR T1's EBITDAR"
                          + (" and revenue less the one-offs booked in it" if status == "revenue_adjusted" else
                             ", revenue as reported"))
                  + (f"; unresolved ({why}), so the margin is escalated to an analyst, who accepts the score or "
                     "overrides it." if unresolved else
                     "; resolved (the agent and the verified evidence agree), so no analyst decision is needed."))
    return result


def _run_agent(result, t, data, fx, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_MARGIN_T1_PROMPT,
                            margin_t1_inputs(t, data["meta"], fx))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's EBITDAR Margin T1 output was rejected "
                                 f"({agent['validation_detail']}); the run was not recorded. Run it again.")
    checked = validate_margin_t1(run.parsed, t, fx)
    result["agent_reading"] = agent_reading(run.parsed, fx)
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

def apply_margin_t1(fo: list[dict], t: dict | None) -> list[dict]:
    """The EBITDAR margin with the trigger's outcome on its headline column (a copy; the inputs stay untouched):
    EBITDAR corrected by Debt/EBITDAR T1's normalisation, revenue by the one-offs booked in it, the margin recomputed
    and rescored with the settings the measure was scored with. The column keeps EBITDAR and revenue as calculated.
    The measure is marked for escalation when the trigger is unresolved."""
    if not t or not t.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "margin":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        col = m["columns"][_headline_index(m)]
        adj = {"trigger": "EBITDAR Margin T1", "outcome": t["status"], "calculated": col["score"],
               "ratio_calculated": col["ratio"], "ebitdar_calculated": col["ebitdar"],
               "revenue_calculated": col["revenue"], "normalisation": t.get("normalisation") or 0.0,
               "revenue_adjustment": t.get("revenue_adjustment") or 0.0, "recomputed": None}
        col["ebitdar_before_trigger"], col["revenue_before_trigger"] = col["ebitdar"], col["revenue"]
        col["ebitdar"] += adj["normalisation"]
        col["revenue"] += adj["revenue_adjustment"]
        col["ratio"] = col["ebitdar"] / col["revenue"] if col["revenue"] and col["revenue"] > 0 else None
        col["reason"] = None if col["ratio"] is not None else "No revenue"
        score_margin(col, margin_settings(m))
        adj["recomputed"] = col["score"]
        done = (f"EBITDAR {adj['ebitdar_calculated'] / 1e6:,.1f} → {col['ebitdar'] / 1e6:,.1f} million (Debt/EBITDAR "
                "T1's normalisation)"
                + (f", revenue {adj['revenue_calculated'] / 1e6:,.1f} → {col['revenue'] / 1e6:,.1f} million (one-offs "
                   "booked in revenue)" if adj["revenue_adjustment"] else ", revenue as reported")
                + ", risk score "
                + (f"{adj['calculated']:g} → " if adj["calculated"] is not None else "")
                + (f"{adj['recomputed']:g}" if adj["recomputed"] is not None else "none"))
        adj.update(ebitdar=col["ebitdar"], revenue=col["revenue"], score=col["score"])
        col["margin_adjustment"] = adj
        add_trigger_note(m, f"EBITDAR Margin trigger T1 ({t.get('fired_by')}, {col['heading']}): {done}",
                         "trigger EBITDAR Margin T1: " + {"revenue_adjusted": "revenue adjusted",
                                                          "outside_revenue": "outside revenue"}.get(t["status"],
                                                                                                    "unresolved"),
                         t.get("unresolved", True), t.get("unresolved_why") or "location unclear")
        out.append(m)
    return out
