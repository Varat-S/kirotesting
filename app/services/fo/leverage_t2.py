"""FO Trigger Agent, Debt/EBITDAR T2: cash does much of the work (the gross and net leverage scores are far apart), and
cash can be spent.

Debt/EBITDAR is scored twice, on lease-adjusted debt and on that debt less unrestricted cash. When the two scores differ
by 2 or more, the net score leans on the year-end cash, so T2 checks whether that cash is trapped or earmarked. It runs
on Debt/EBITDAR's headline column after Debt/EBITDAR T1 (T1 corrects EBITDAR, which moves both scores), only when the
pre-checks allow it (``payload.gate.agent_triggers`` is "enabled"). Same three steps as the other triggers:

1. ``leverage_t2_passages`` (deterministic) collects what the agent checks: the paragraphs and table rows of MD&A and
   the notes that mention cash held abroad or by subsidiaries, repatriation, restrictions, regulatory or statutory
   capital, captive insurance, pending acquisitions, declared dividends, or cash set aside for debt (ids C01…).
2. The Trigger Agent (prompt ``fo_trigger_leverage_t2``) ALWAYS reads them (no agent, or a rejected output, refuses
   the run). Every fact is quoted verbatim. Only a filing with none of those passages has nothing to read (no model
   call: nothing earmarked or trapped, so the cash is freely available).
3. ``validate_leverage_t2`` keeps only verbatim evidence that says what it must and RE-DERIVES the outcome, first match
   wins:

   - an acquisition or payout earmark, or trapped cash, quantified: subtracted from cash (never below zero), net debt
     and Net debt / EBITDAR recomputed;
   - cash earmarked only for debt maturities: no adjustment (repaying debt with cash leaves net debt unchanged);
   - cash freely available: no adjustment;
   - an earmark or trapping described but not quantified: unresolved, so Debt/EBITDAR is escalated to an analyst.

   A share repurchase (buyback) authorisation is not an earmark.

Debt/EBITDAR is escalated when unresolved, and, as for every trigger, when the agent's own answer and its verified
evidence disagree. Only cash changes: debt, EBITDAR and the gross ratio stay as they are.
"""

from __future__ import annotations

import copy
import re

from app.prompts.fo_prompts import FO_LEVERAGE_T2_PROMPT, FO_LEVERAGE_T2_SCHEMA, FO_LEVERAGE_T2_TEMPLATE

from .dscr_triggers import _ev, _held, _millions, _quoted, _value, add_trigger_note, collect_passages, stated_amount
from .icr_triggers import SECTIONS, _section_after
from .leverage_triggers import _headline_index
from .precheck import DATA_NOTICE, PrecheckAgentError, call_agent, require_agent
from .scoring import headline_score, leverage_settings, score_leverage

LEVERAGE_T2_VERSION = "fo-trigger-leverage-t2-1.0.0"
LEVERAGE_T2_ID = "leverage_t2"
LEVERAGE_T2_GAP = 2  # |gross score − net score| at or above this sets the trigger off
AGENT = "Trigger Agent"

LIMITS = {"mdna": 25, "notes": 30}
LEVERAGE_T2_TERMS = re.compile(
    r"repatriat\w*|undistributed (foreign )?earnings|(cash|held) (outside|abroad)|outside (of )?the (U\.S\.|United "
    r"States)|foreign subsidiar\w*|held by (our )?(foreign |insurance |captive )?subsidiar\w*|trapped|"
    r"restrict\w* (cash|from|on)|restrictions on (the )?(transfer|payment|use)|statutory (capital|surplus|"
    r"requirements?)|regulatory capital|captive|variable interest|minimum (cash|liquidity|capital)|"
    r"special (cash )?dividend|dividends? (declared|payable)|declared an? (\w+ )?(cash )?dividend|"
    r"pending acquisition|definitive (merger )?agreement|agreement to acquire|fund the (acquisition|purchase|"
    r"transaction|merger)|cash on hand to (fund|repay|redeem|pay)|earmark\w*|set aside|general corporate purposes",
    re.I)

# --- what a quote must say ---------------------------------------------------------------------------------------
KIND_WORDS = {
    "acquisition": (re.compile(r"acqui\w*|merger|purchase price|consideration|business combination", re.I),
                    "the quote doesn't mention an acquisition"),
    "payout": (re.compile(r"dividends?|distribut\w*|special (cash )?payment", re.I),
               "the quote doesn't mention a dividend or other payout"),
    "trapped": (re.compile(r"restrict\w*|repatriat\w*|foreign|abroad|outside (of )?the (U\.S\.|United States)|"
                           r"subsidiar\w*|regulat\w*|statutory|captive|insurance|joint venture|variable interest|"
                           r"trapped|not (freely )?available|minimum (cash|liquidity|capital)", re.I),
                "the quote doesn't say the cash is trapped (restricted, held abroad or by a regulated subsidiary…)"),
    "debt_maturity": (re.compile(r"matur\w*|repay\w*|redeem\w*|redemption|retire\w*|notes due|debt", re.I),
                      "the quote doesn't mention a debt maturity or repayment"),
}
CASH = re.compile(r"\bcash\b|on hand|liquidity|funds", re.I)
BUYBACK = re.compile(r"repurchas\w*|buy-?backs?", re.I)
AVAILABLE = re.compile(r"general corporate purposes|available|unrestricted|freely|not (subject to )?restrict\w*", re.I)
REDUCING = ("acquisition", "payout", "trapped")
KIND_TEXT = {"acquisition": "acquisition earmark", "payout": "payout earmark", "trapped": "trapped cash",
             "debt_maturity": "earmarked for debt maturities"}

STATUS_TEXT = {"skipped": "Skipped: agent triggers are skipped after a going-concern fail"}
RULES = [("An acquisition or payout earmark, or trapped cash, quantified",
          "Subtracted from cash (never below zero); net debt and Net debt / EBITDAR recomputed"),
         ("Cash earmarked only for debt maturities",
          "No adjustment: repaying debt with cash leaves net debt unchanged"),
         ("Cash freely available", "No adjustment"),
         ("An earmark or trapping described but not quantified",
          "Unresolved: Debt/EBITDAR escalated to an analyst, who accepts the score or overrides it"),
         ("The agent's answer and the verified evidence disagree",
          "Unresolved: Debt/EBITDAR escalated to an analyst, who accepts the score or overrides it")]


# --- the passages ------------------------------------------------------------------------------------------------

def leverage_t2_passages(data: dict) -> dict:
    """MD&A's and the notes' paragraphs and tables that mention trapped or earmarked cash; ids C01… in document
    order."""
    return collect_passages(data, "C", LIMITS, SECTIONS, LEVERAGE_T2_TERMS, _section_after)


def cash_facts(col: dict) -> dict:
    """What the engine counted for the triggering column."""
    gross, net = col["debt_to_ebitdar"].get("score"), col["net_debt_to_ebitdar"].get("score")
    return {"heading": col["heading"], "period": col["period"], "period_end": col["period"].split("--")[1],
            "cash": col.get("cash"), "cash_concept": col.get("cash_concept"), "adj_debt": col.get("adj_debt"),
            "adj_net_debt": col.get("adj_net_debt"), "ebitdar": col.get("ebitdar"),
            "debt_ratio": col["debt_to_ebitdar"].get("ratio"), "net_debt_ratio": col["net_debt_to_ebitdar"].get("ratio"),
            "gross_score": gross, "net_score": net,
            "gap": round(gross - net, 1) if gross is not None and net is not None else None,
            "score": (col.get("composite") or {}).get("score")}


def leverage_t2_inputs(t: dict, meta: dict, fx: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": fx["period_end"]},
        "leverage": {"column": fx["heading"], "debt_to_ebitdar_score": fx["gross_score"],
                     "net_debt_to_ebitdar_score": fx["net_score"],
                     "debt_to_ebitdar": round(fx["debt_ratio"], 2) if fx["debt_ratio"] is not None else None,
                     "net_debt_to_ebitdar": (round(fx["net_debt_ratio"], 2) if fx["net_debt_ratio"] is not None
                                             else None),
                     "cash_millions": _millions(fx["cash"]), "cash_source": fx["cash_concept"],
                     "debt_plus_leases_millions": _millions(fx["adj_debt"]),
                     "trigger": f"|Debt / EBITDAR score − Net debt / EBITDAR score| of {LEVERAGE_T2_GAP} or more"},
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t["passages"]],
    }


# --- validation --------------------------------------------------------------------------------------------------

def _item(o, by_id, held):
    """One verified earmark or trapped item, with its amount when the amount holds up, or None (held back). An amount
    that doesn't hold up leaves the item described but not quantified."""
    kind = o.get("kind")
    item = {"item_id": o.get("item_id"), "kind": kind, "description": o.get("description")}
    words, why = KIND_WORDS.get(kind, KIND_WORDS["trapped"])
    e = o.get("evidence")
    p, reason = _quoted(e, by_id, words, why)
    if not reason and not CASH.search(e["quote"]):
        reason = "the quote doesn't mention cash"
    if not reason and BUYBACK.search(e["quote"]) and kind != "debt_maturity":
        reason = "a share repurchase (buyback) authorisation isn't an earmark"
    if reason:
        held.append(_held(item, "item", e, reason))
        return None
    entry = {**item, "amount": None, "evidence": _ev(e, p)}
    if (e.get("amount") or "").strip():
        value, reason = _value(e, p)
        if reason:
            held.append(_held(item, "amount", e, reason))
        else:
            entry["amount"] = value
    return entry


def validate_leverage_t2(parsed: dict, t: dict, fx: dict) -> dict:
    """Keep the evidence that holds up and re-derive the outcome, first match wins: cash reduced (a quantified
    acquisition or payout earmark, or trapped cash), debt maturities only, freely available, else unresolved
    (described but not quantified)."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    held, notes = [], []
    items = [e for o in parsed.get("items") or [] for e in [_item(o, by_id, held)] if e]
    available = []
    for i, q in enumerate(parsed.get("available") or [], 1):
        p, why = _quoted(q, by_id, AVAILABLE, "the quote doesn't say the cash is available")
        if why:
            held.append(_held({"item_id": f"A{i}"}, "available", q, why))
        else:
            available.append({"item_id": f"A{i}", **_ev(q, p)})
    quantified = [e for e in items if e["kind"] in REDUCING and e["amount"] is not None]
    unquantified = [e for e in items if e["kind"] in REDUCING and e["amount"] is None]
    maturities = [e for e in items if e["kind"] == "debt_maturity"]
    total = sum(e["amount"] for e in quantified)
    removed = min(total, max(fx["cash"], 0.0))
    why = None
    if quantified:
        status = "cash_reduced"
        if unquantified:
            notes.append(f"{', '.join(e['item_id'] for e in unquantified)} also described without an amount: not "
                         "subtracted (a quantified earmark or trapped cash comes first).")
        if removed < total:
            notes.append(f"The earmarked and trapped cash ({total / 1e6:,.1f} million) exceeds the cash counted "
                         f"({fx['cash'] / 1e6:,.1f} million): cash is taken to zero.")
    elif maturities and not unquantified:
        status = "debt_maturities"
    elif not unquantified:
        status = "freely_available"
    else:
        status = "unresolved"
        why = (f"an earmark or trapped cash is described but not quantified "
               f"({', '.join(e['item_id'] for e in unquantified)})")
    return {"status": status, "quantified": quantified, "unquantified": unquantified, "maturities": maturities,
            "available": available, "removed": removed if status == "cash_reduced" else 0.0, "unresolved_why": why,
            "held_back": held, "stated_status": parsed.get("status"),
            "status_consistent": parsed.get("status") == status, "validation_notes": notes}


def agent_reading(parsed: dict, t: dict, fx: dict) -> dict:
    """The agent's answer as it states it (not verified, not applied), for an analyst to compare with the verified
    outcome: "cash_reduced" takes the acquisition, payout and trapped amounts it gives out of cash
    (not below zero). Any other answer changes nothing."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    stated, notes, removed = parsed.get("status"), [], 0.0
    if stated == "cash_reduced":
        total = sum(v for o in parsed.get("items") or [] if o.get("kind") in REDUCING
                    for v in [stated_amount(o.get("evidence"), by_id, notes, o.get("item_id"))] if v)
        removed = min(total, max(fx["cash"], 0.0))
    return {"status": stated, "notes": notes, "applies": {"status": stated, "removed": removed}}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_LEVERAGE_T2_PROMPT, template=FO_LEVERAGE_T2_TEMPLATE, response_schema=FO_LEVERAGE_T2_SCHEMA,
                      role="analyze", label="Financial Overview Trigger Agent: Debt/EBITDAR T2")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

def _reason(status: str, fx: dict, checked: dict) -> str:
    if status == "cash_reduced":
        items = "; ".join(f"{KIND_TEXT[e['kind']]} of {e['amount'] / 1e6:,.1f} million ({e['evidence']['passage_id']})"
                          for e in checked["quantified"])
        return (f"Earmarked or trapped cash quantified ({items}): cash {fx['cash'] / 1e6:,.1f} → "
                f"{(fx['cash'] - checked['removed']) / 1e6:,.1f} million, Net debt / EBITDAR recomputed")
    if status == "debt_maturities":
        ids = ", ".join(e["evidence"]["passage_id"] for e in checked["maturities"])
        return (f"Cash earmarked only for debt maturities ({ids}): no adjustment (repaying debt with cash leaves net "
                "debt unchanged)")
    if status == "freely_available":
        ids = ", ".join(e["passage_id"] for e in checked["available"])
        return "Cash freely available" + (f" ({ids})" if ids else ": no earmark or trapped cash found") \
            + ": no adjustment"
    return "Unresolved: " + checked["unresolved_why"]


def leverage_t2_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None,
                        mode: str = "live", case_id: str | None = None) -> dict:
    """The Debt/EBITDAR T2 trigger result recorded in the run payload (``payload.triggers.leverage_t2``). ``metrics``
    are the scored measures (after Debt/EBITDAR T1); the trigger reads Debt/EBITDAR's headline column."""
    lev = next((m for m in metrics if m["key"] == "leverage"), None)
    score, heading = headline_score(lev) if lev else (None, None)
    result = {"trigger": LEVERAGE_T2_ID, "measure": "leverage", "version": LEVERAGE_T2_VERSION,
              "gap_threshold": LEVERAGE_T2_GAP, "score": score, "column": heading, "fired": False,
              "mode": mode if backend is not None else "none", "agent": None, "notes": [], "review_required": False}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", reason=STATUS_TEXT["skipped"],
                      effect="None: the measures are superseded by the going-concern fail.")
        return result
    if score is None:
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason="Debt/EBITDAR has no score: trigger Debt/EBITDAR T2 not set off")
        return result
    fx = cash_facts(lev["columns"][_headline_index(lev)])
    result["inputs"] = fx
    if fx["gap"] is None or fx["cash"] is None or abs(fx["gap"]) < LEVERAGE_T2_GAP:
        result.update(status="not_triggered", decided_by="scores", effect="None.",
                      reason=(f"The Debt / EBITDAR score ({fx['gross_score']:g}) and the Net debt / EBITDAR score "
                              f"({fx['net_score']:g}) differ by {abs(fx['gap']):g}, less than {LEVERAGE_T2_GAP}: "
                              "trigger Debt/EBITDAR T2 not set off" if fx["gap"] is not None and fx["cash"] is not None
                              else "Debt/EBITDAR has no gross or net score: trigger Debt/EBITDAR T2 not set off"))
        return result
    t = leverage_t2_passages(data)
    result.update(fired=True, passages=t["passages"], sections=t["sections"],
                  fired_by=f"Debt / EBITDAR score {fx['gross_score']:g} vs Net debt / EBITDAR score "
                           f"{fx['net_score']:g}: a gap of {abs(fx['gap']):g}")
    if not t["passages"]:
        checked = {"status": "freely_available", "quantified": [], "unquantified": [], "maturities": [],
                   "available": [], "removed": 0.0, "unresolved_why": None, "status_consistent": True}
        decided_by = "no_text"
        result["notes"].append("No MD&A or note passage mentions trapped or earmarked cash: the agent was not called. "
                               "Nothing is earmarked or trapped, so the cash is taken as freely available.")
    else:
        require_agent(backend, LEVERAGE_T2_ID, AGENT)
        checked, decided_by = _run_agent(result, t, data, fx, session, backend, case_id)
    status = checked["status"]
    disagree = not checked["status_consistent"]
    unresolved = status == "unresolved" or disagree
    why = ("the agent's answer and the verified evidence disagree" if disagree else checked["unresolved_why"]) \
        if unresolved else None
    result.update(status=status, decided_by=decided_by, quantified=checked["quantified"],
                  unquantified=checked["unquantified"], maturities=checked["maturities"],
                  available=checked["available"], removed=checked["removed"],
                  reason=_reason(status, fx, checked), unresolved=unresolved, disagreement=disagree,
                  review_required=unresolved, unresolved_why=why,
                  effect=("Net debt / EBITDAR recomputed on cash less the earmarked and trapped cash"
                          if status == "cash_reduced" else "None: the Debt/EBITDAR score stays as calculated")
                  + (f"; unresolved ({why}), so Debt/EBITDAR is escalated to an analyst, who accepts the score or "
                     "overrides it." if unresolved else
                     "; resolved (the agent and the verified evidence agree), so no analyst decision is needed."))
    return result


def _run_agent(result, t, data, fx, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_LEVERAGE_T2_PROMPT,
                            leverage_t2_inputs(t, data["meta"], fx))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's Debt/EBITDAR T2 output was rejected "
                                 f"({agent['validation_detail']}); the run was not recorded. Run it again.")
    checked = validate_leverage_t2(run.parsed, t, fx)
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

def apply_leverage_t2(fo: list[dict], t: dict | None) -> list[dict]:
    """Debt/EBITDAR with the trigger's outcome on its headline column (a copy; the inputs stay untouched): the
    earmarked and trapped cash comes out of cash, net debt and Net debt / EBITDAR are recomputed and the scores
    rescored with the settings the measure was scored with. The column keeps the cash as counted. The measure is marked
    for escalation when the trigger is unresolved."""
    if not t or not t.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "leverage":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        col = m["columns"][_headline_index(m)]
        adj = {"trigger": "Debt/EBITDAR T2", "outcome": t["status"], "calculated": col["composite"]["score"],
               "net_score_calculated": col["net_debt_to_ebitdar"].get("score"),
               "net_ratio_calculated": col["net_debt_to_ebitdar"]["ratio"], "cash_calculated": col["cash"],
               "removed": t.get("removed") or 0.0, "recomputed": None}
        done = None
        if t["status"] == "cash_reduced" and adj["removed"]:
            col["cash_before_trigger"] = col["cash"]
            col["cash"] -= adj["removed"]
            col["adj_net_debt"] = col["adj_debt"] - col["cash"]
            den = col["ebitdar"]
            col["net_debt_to_ebitdar"]["ratio"] = col["adj_net_debt"] / den if den and den > 0 else None
            score_leverage(col, leverage_settings(m))
            adj["recomputed"] = col["composite"]["score"]
            done = (f"cash {adj['cash_calculated'] / 1e6:,.1f} → {col['cash'] / 1e6:,.1f} million (earmarked or trapped "
                    f"cash out), Net debt / EBITDAR score "
                    + (f"{adj['net_score_calculated']:g} → " if adj["net_score_calculated"] is not None else "")
                    + (f"{col['net_debt_to_ebitdar']['score']:g}" if col["net_debt_to_ebitdar"].get("score")
                       is not None else "none")
                    + ", composite leverage score "
                    + (f"{adj['calculated']:g} → " if adj["calculated"] is not None else "")
                    + (f"{adj['recomputed']:g}" if adj["recomputed"] is not None else "none"))
        adj["cash"], adj["score"] = col["cash"], col["composite"]["score"]
        col["cash_adjustment"] = adj
        add_trigger_note(m, (f"Debt/EBITDAR trigger T2 ({t.get('fired_by')}, {col['heading']}): "
                             + (done or "no adjustment" if t["status"] == "unresolved"
                                else done or t["reason"][:1].lower() + t["reason"][1:])),
                         "trigger Debt/EBITDAR T2: " + {"cash_reduced": "cash reduced",
                                                        "debt_maturities": "debt maturities only",
                                                        "freely_available": "freely available"}
                         .get(t["status"], "unresolved"),
                         t.get("unresolved", True), t.get("unresolved_why") or "described but not quantified")
        out.append(m)
    return out
