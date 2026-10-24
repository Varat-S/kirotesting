"""FO Trigger Agent, Debt/EBITDAR T3: leases do much of the leverage (the score with leases is far worse than without),
and some leases behave like secured debt.

Debt/EBITDAR counts operating lease liabilities as debt and adds rent back to EBITDAR. T3 rescores the headline column
without them (debt / EBITDA and (debt − cash) / EBITDA, EBITDA being EBITDAR − rent, on the same benchmark curve) and,
when the composite leverage score with leases is 2 or more worse, checks the lease and guarantee notes for a master
lease (typically with a REIT), a cross-default or a parent guarantee. It runs after Debt/EBITDAR T1 and T2 (they correct
EBITDAR and cash, which move both scores), only when the pre-checks allow it (``payload.gate.agent_triggers`` is
"enabled"). Same three steps as the other triggers:

1. ``leverage_t3_passages`` (deterministic) collects what the agent checks: the paragraphs and table rows of MD&A and
   the notes that mention master leases, REITs or other landlords, sale-leasebacks, cross-defaults, lease guarantees,
   rent escalators, renewal or termination options, or lease terms (ids R01…).
2. The Trigger Agent (prompt ``fo_trigger_leverage_t3``) ALWAYS reads them (no agent, or a rejected output, refuses
   the run). Every fact is quoted verbatim. Only a filing with none of those passages has nothing to read (no model
   call: nothing disclosed, so unresolved).
3. ``validate_leverage_t3`` keeps only verbatim evidence that says what it must and RE-DERIVES the outcome:

   - a master lease, cross-default or parent guarantee disclosed: +1 on the composite leverage score (capped at 10);
   - none disclosed: unresolved, so Debt/EBITDAR is escalated to an analyst.

   Escalators, cancellable leases and lease terms are reported but get no lease adjustment.

Debt/EBITDAR is escalated when unresolved, and, as for every trigger, when the agent's own answer and its verified
evidence disagree. Only the composite leverage score changes: the ratios and their scores stay as they are.
"""

from __future__ import annotations

import copy
import re

from app.prompts.fo_prompts import FO_LEVERAGE_T3_PROMPT, FO_LEVERAGE_T3_SCHEMA, FO_LEVERAGE_T3_TEMPLATE

from .dscr_triggers import _ev, _held, _millions, _quoted, add_trigger_note, collect_passages, stated_amount
from .icr_triggers import SECTIONS, _section_after
from .leverage_triggers import _headline_index
from .precheck import DATA_NOTICE, PrecheckAgentError, call_agent, require_agent
from .scoring import headline_score, leverage_settings, score_leverage

LEVERAGE_T3_VERSION = "fo-trigger-leverage-t3-1.0.0"
LEVERAGE_T3_ID = "leverage_t3"
LEVERAGE_T3_GAP = 2  # score with leases − score without leases at or above this sets the trigger off
LEVERAGE_T3_UPLIFT = 1  # a master lease, cross-default or parent guarantee disclosed
AGENT = "Trigger Agent"

LIMITS = {"mdna": 15, "notes": 30}
LEVERAGE_T3_TERMS = re.compile(
    r"master (lease|lease agreement)s?|cross-?default\w*|cross-?collaterali[sz]\w*|real estate investment trusts?|"
    r"\bREITs?\b|sale-?(and-)?leaseback|landlords?|lessors?|escalat\w*|"
    r"(leases?|rent\w*)\b.{0,60}\bguarant\w*|guarant\w*.{0,60}\b(leases?|rent\w*)\b|renewal options?|"
    r"(remaining |weighted[- ]average )?(remaining )?lease terms?|"
    r"(cancel\w*|terminat\w*).{0,40}\bleases?\b|\bleases?\b.{0,40}(cancel\w*|terminat\w*)", re.I)

# --- what a quote must say ---------------------------------------------------------------------------------------
LEASE = re.compile(r"leas\w*|landlord|lessor|\bREITs?\b|real estate investment trust|rent\w*", re.I)
KIND_WORDS = {
    "master_lease": (re.compile(r"master (lease|lease agreement)", re.I), "the quote doesn't mention a master lease"),
    "cross_default": (re.compile(r"cross-?default\w*|cross-?collaterali[sz]\w*|default under (any|one|each|all|the "
                                 r"other|another|such)", re.I), "the quote doesn't mention a cross-default"),
    "parent_guarantee": (re.compile(r"guarant\w*", re.I), "the quote doesn't mention a guarantee"),
    "escalator": (re.compile(r"escalat\w*|increase\w*|consumer price index|\bCPI\b", re.I),
                  "the quote doesn't mention a rent escalator"),
    "cancellable": (re.compile(r"cancel\w*|terminat\w*|early termination", re.I),
                    "the quote doesn't say the lease can be cancelled"),
    "term": (re.compile(r"term\w*|renew\w*|expir\w*|years?", re.I), "the quote doesn't give a lease term"),
}
DEBT_LIKE = ("master_lease", "cross_default", "parent_guarantee")
KIND_TEXT = {"master_lease": "Master lease", "cross_default": "Cross-default", "parent_guarantee": "Parent guarantee",
             "escalator": "Rent escalator", "cancellable": "Cancellable lease", "term": "Lease term"}

STATUS_TEXT = {"skipped": "Skipped: agent triggers are skipped after a going-concern fail"}
RULES = [("A master lease, cross-default or parent guarantee disclosed",
          f"+{LEVERAGE_T3_UPLIFT} on the composite leverage score (capped at 10)"),
         ("None disclosed", "Unresolved: Debt/EBITDAR escalated to an analyst, who accepts the score or overrides it"),
         ("Rent escalators or cancellable leases", "No lease adjustment"),
         ("The agent's answer and the verified evidence disagree",
          "Unresolved: Debt/EBITDAR escalated to an analyst, who accepts the score or overrides it")]


# --- the passages ------------------------------------------------------------------------------------------------

def leverage_t3_passages(data: dict) -> dict:
    """MD&A's and the notes' paragraphs and tables that mention master leases, cross-defaults, lease guarantees,
    escalators or lease terms; ids R01… in document order."""
    return collect_passages(data, "R", LIMITS, SECTIONS, LEVERAGE_T3_TERMS, _section_after)


def without_leases(col: dict, settings: dict) -> dict:
    """The column rescored without leases: debt / EBITDA and (debt − cash) / EBITDA, EBITDA = EBITDAR − rent (the
    EBITDAR as the measure holds it, so after any T1 correction; the cash after any T2 correction)."""
    rent = col.get("rent") or 0.0
    ebitda = col["ebitdar"] - rent if col.get("ebitdar") is not None else None
    debt, cash = col.get("debt"), col.get("cash")
    out = {"ebitda": ebitda, "debt": debt, "net_debt": debt - cash if debt is not None and cash is not None else None}
    for key, num in (("debt_to_ebitdar", out["debt"]), ("net_debt_to_ebitdar", out["net_debt"])):
        out[key] = {"ratio": num / ebitda if num is not None and ebitda and ebitda > 0 else None,
                    "negative_ebitda": ebitda is not None and ebitda <= 0, "reason": col[key].get("reason")}
    score_leverage(out, settings)
    return out


def lease_facts(col: dict, settings: dict) -> dict:
    """What the engine counted for the triggering column, with and without leases."""
    w = without_leases(col, settings)
    with_score, without_score = (col.get("composite") or {}).get("score"), w["composite"]["score"]
    return {"heading": col["heading"], "period": col["period"], "period_end": col["period"].split("--")[1],
            "debt": col.get("debt"), "op_leases": col.get("op_leases"), "op_leases_concept": col.get("op_leases_concept"),
            "rent": col.get("rent"), "rent_concept": col.get("rent_concept"), "ebitdar": col.get("ebitdar"),
            "ebitda": w["ebitda"], "cash": col.get("cash"),
            "debt_ratio": col["debt_to_ebitdar"].get("ratio"), "net_debt_ratio": col["net_debt_to_ebitdar"].get("ratio"),
            "ratio_without": w["debt_to_ebitdar"]["ratio"], "net_ratio_without": w["net_debt_to_ebitdar"]["ratio"],
            "score": with_score, "score_without": without_score,
            "gap": (round(with_score - without_score, 1) if with_score is not None and without_score is not None
                    else None)}


def _r(v):
    return round(v, 2) if v is not None else None


def leverage_t3_inputs(t: dict, meta: dict, fx: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": fx["period_end"]},
        "leverage": {"column": fx["heading"], "score_with_leases": fx["score"], "score_without_leases": fx["score_without"],
                     "debt_to_ebitdar": _r(fx["debt_ratio"]), "net_debt_to_ebitdar": _r(fx["net_debt_ratio"]),
                     "debt_to_ebitda_without_leases": _r(fx["ratio_without"]),
                     "net_debt_to_ebitda_without_leases": _r(fx["net_ratio_without"]),
                     "operating_lease_liabilities_millions": _millions(fx["op_leases"]),
                     "rent_millions": _millions(fx["rent"]), "debt_millions": _millions(fx["debt"]),
                     "trigger": f"composite leverage score with leases − without leases of {LEVERAGE_T3_GAP} or more"},
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t["passages"]],
    }


# --- validation --------------------------------------------------------------------------------------------------

def _item(o, by_id, held):
    """One verified lease feature, or None (held back): a verbatim quote that names it and is about a lease."""
    kind = o.get("kind")
    item = {"item_id": o.get("item_id"), "kind": kind, "counterparty": o.get("counterparty"),
            "description": o.get("description")}
    if kind not in KIND_WORDS:
        held.append(_held(item, "item", o.get("evidence"), f"unknown kind {kind!r}"))
        return None
    words, why = KIND_WORDS[kind]
    e = o.get("evidence")
    p, reason = _quoted(e, by_id, words, why)
    if not reason and not LEASE.search(e["quote"]):
        reason = "the quote isn't about a lease"
    if reason:
        held.append(_held(item, "item", e, reason))
        return None
    return {**item, "evidence": _ev(e, p)}


def validate_leverage_t3(parsed: dict, t: dict) -> dict:
    """Keep the evidence that holds up and re-derive the outcome: disclosed (a master lease, cross-default or parent
    guarantee, +1), else not disclosed (unresolved). Escalators, cancellable leases and terms never count."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    held = []
    items = [e for o in parsed.get("items") or [] for e in [_item(o, by_id, held)] if e]
    debt_like = [e for e in items if e["kind"] in DEBT_LIKE]
    other = [e for e in items if e["kind"] not in DEBT_LIKE]
    status = "disclosed" if debt_like else "not_disclosed"
    why = None if debt_like else ("no master lease, cross-default or parent guarantee is disclosed"
                                  + (" (only " + ", ".join(sorted({KIND_TEXT[e['kind']].lower() for e in other}))
                                     + ", which get no lease adjustment)" if other else ""))
    return {"status": status, "debt_like": debt_like, "other": other, "unresolved_why": why, "held_back": held,
            "stated_status": parsed.get("status"), "status_consistent": parsed.get("status") == status,
            "validation_notes": []}


def agent_reading(parsed: dict) -> dict:
    """The agent's answer as it states it (not verified, not applied), for an analyst to compare with the verified
    outcome: "disclosed" adds the uplift to the composite leverage score. Any other answer changes
    nothing."""
    stated = parsed.get("status")
    return {"status": stated, "notes": [], "applies": {"status": stated}}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_LEVERAGE_T3_PROMPT, template=FO_LEVERAGE_T3_TEMPLATE, response_schema=FO_LEVERAGE_T3_SCHEMA,
                      role="analyze", label="Financial Overview Trigger Agent: Debt/EBITDAR T3")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

def _reason(status: str, fx: dict, checked: dict) -> str:
    if status == "disclosed":
        items = "; ".join(f"{KIND_TEXT[e['kind']].lower()}" + (f" with {e['counterparty']}" if e.get("counterparty")
                                                               else "") + f" ({e['evidence']['passage_id']})"
                          for e in checked["debt_like"])
        new = min(10, fx["score"] + LEVERAGE_T3_UPLIFT)
        return (f"Debt-like leases disclosed ({items}): composite leverage score {fx['score']:g} → {new:g} "
                f"(+{LEVERAGE_T3_UPLIFT})")
    return "Unresolved: " + checked["unresolved_why"]


def leverage_t3_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None,
                        mode: str = "live", case_id: str | None = None) -> dict:
    """The Debt/EBITDAR T3 trigger result recorded in the run payload (``payload.triggers.leverage_t3``). ``metrics``
    are the scored measures (after Debt/EBITDAR T1 and T2); the trigger reads Debt/EBITDAR's headline column."""
    lev = next((m for m in metrics if m["key"] == "leverage"), None)
    score, heading = headline_score(lev) if lev else (None, None)
    result = {"trigger": LEVERAGE_T3_ID, "measure": "leverage", "version": LEVERAGE_T3_VERSION,
              "gap_threshold": LEVERAGE_T3_GAP, "uplift": LEVERAGE_T3_UPLIFT, "score": score, "column": heading,
              "fired": False, "mode": mode if backend is not None else "none", "agent": None, "notes": [],
              "review_required": False}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", reason=STATUS_TEXT["skipped"],
                      effect="None: the measures are superseded by the going-concern fail.")
        return result
    if score is None:
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason="Debt/EBITDAR has no score: trigger Debt/EBITDAR T3 not set off")
        return result
    fx = lease_facts(lev["columns"][_headline_index(lev)], leverage_settings(lev))
    result["inputs"] = fx
    if fx["gap"] is None or fx["gap"] < LEVERAGE_T3_GAP:
        result.update(status="not_triggered", decided_by="scores", effect="None.",
                      reason=(f"The composite leverage score with leases ({fx['score']:g}) less the score without "
                              f"leases ({fx['score_without']:g}) is {fx['gap']:g}, less than {LEVERAGE_T3_GAP}: "
                              "trigger Debt/EBITDAR T3 not set off" if fx["gap"] is not None
                              else "Debt/EBITDAR has no score without leases: trigger Debt/EBITDAR T3 not set off"))
        return result
    t = leverage_t3_passages(data)
    result.update(fired=True, passages=t["passages"], sections=t["sections"],
                  fired_by=f"composite leverage score {fx['score']:g} with leases vs {fx['score_without']:g} "
                           f"without: {fx['gap']:g} worse")
    if not t["passages"]:
        checked = {"status": "not_disclosed", "debt_like": [], "other": [], "status_consistent": True,
                   "unresolved_why": "no master lease, cross-default or parent guarantee is disclosed"}
        decided_by = "no_text"
        result["notes"].append("No MD&A or note passage mentions a master lease, a cross-default, a lease guarantee or "
                               "lease terms: the agent was not called. Nothing is disclosed, so Debt/EBITDAR is "
                               "unresolved.")
    else:
        require_agent(backend, LEVERAGE_T3_ID, AGENT)
        checked, decided_by = _run_agent(result, t, data, fx, session, backend, case_id)
    status = checked["status"]
    disagree = not checked["status_consistent"]
    unresolved = status == "not_disclosed" or disagree
    why = ("the agent's answer and the verified evidence disagree" if disagree else checked["unresolved_why"]) \
        if unresolved else None
    result.update(status=status, decided_by=decided_by, debt_like=checked["debt_like"], other=checked["other"],
                  reason=_reason(status, fx, checked), unresolved=unresolved, disagreement=disagree,
                  review_required=unresolved, unresolved_why=why,
                  effect=(f"+{LEVERAGE_T3_UPLIFT} on the composite leverage score (capped at 10)"
                          if status == "disclosed" else "None: the Debt/EBITDAR score stays as calculated")
                  + (f"; unresolved ({why}), so Debt/EBITDAR is escalated to an analyst, who accepts the score or "
                     "overrides it." if unresolved else
                     "; resolved (the agent and the verified evidence agree), so no analyst decision is needed."))
    return result


def _run_agent(result, t, data, fx, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_LEVERAGE_T3_PROMPT,
                            leverage_t3_inputs(t, data["meta"], fx))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's Debt/EBITDAR T3 output was rejected "
                                 f"({agent['validation_detail']}); the run was not recorded. Run it again.")
    checked = validate_leverage_t3(run.parsed, t)
    result["agent_reading"] = agent_reading(run.parsed)
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

def apply_leverage_t3(fo: list[dict], t: dict | None) -> list[dict]:
    """Debt/EBITDAR with the trigger's outcome on its headline column (a copy; the inputs stay untouched): +1 on the
    composite leverage score (capped at 10) when a master lease, cross-default or parent guarantee is disclosed. The
    ratios and their scores stay as they are; the column keeps the score as calculated and the score without leases.
    The measure is marked for escalation when the trigger is unresolved."""
    if not t or not t.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "leverage":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        col = m["columns"][_headline_index(m)]
        fx = t.get("inputs") or {}
        adj = {"trigger": "Debt/EBITDAR T3", "outcome": t["status"], "calculated": col["composite"]["score"],
               "score_without_leases": fx.get("score_without"), "recomputed": None}
        done = None
        if t["status"] == "disclosed":
            col["composite"]["score"] = round(min(10, adj["calculated"] + LEVERAGE_T3_UPLIFT), 1)
            col["composite"]["lease_uplift"] = True
            adj["recomputed"] = col["composite"]["score"]
            done = (f"composite leverage score {adj['calculated']:g} → {adj['recomputed']:g} (+{LEVERAGE_T3_UPLIFT}: "
                    "master lease, cross-default or parent guarantee disclosed)")
        adj["score"] = col["composite"]["score"]
        col["lease_adjustment"] = adj
        add_trigger_note(m, (f"Debt/EBITDAR trigger T3 ({t.get('fired_by')}, {col['heading']}): "
                             + (done or "no adjustment")),
                         "trigger Debt/EBITDAR T3: " + ("+1, debt-like leases disclosed" if t["status"] == "disclosed"
                                                        else "not disclosed"),
                         t.get("unresolved", True), t.get("unresolved_why") or "not disclosed")
        out.append(m)
    return out
