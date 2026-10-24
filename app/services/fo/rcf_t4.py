"""FO Trigger Agent, RCF/Debt T4: the NCI-distributions figure comes from PaymentsToMinorityShareholders, which can hold
buyouts of partners as well as distributions.

A buyout (a purchase of NCI ownership) is a discretionary investment, not a payout: mixed into the distributions, it
makes RCF look too low. T4 runs on RCF / debt's headline column after RCF/Debt T3, only when the pre-checks allow it
(``payload.gate.agent_triggers`` is "enabled"). Code decides: it fires whenever that tag is the source of the
NCI-distributions figure. T3 and T4 can't both fire: T3 needs no standard NCI-distributions concept, T4 this one.
Same three steps as the other triggers:

1. ``rcf_t4_passages`` (deterministic) collects what the agent checks: the cash flow statement (financing), the
   statement of changes in equity and the notes (the redeemable NCI rollforward, the ASC 810 ownership schedule) that
   mention NCI, partners, distributions or purchases of ownership (ids M01…).
2. The Trigger Agent (prompt ``fo_trigger_rcf_t4``) ALWAYS reads them (no agent, or a rejected output, refuses the
   run). Every amount is quoted verbatim. Only a filing with none of those passages has nothing to read (no model
   call: nothing found, so unresolved).
3. ``validate_rcf_t4`` keeps only verbatim evidence that says what it must and RE-DERIVES the outcome:

   - buyouts found in the tagged amount and quantified: excluded, RCF recomputed with distributions only;
   - buyouts mixed in but not quantified: unresolved;
   - the equity statement and ASC 810 schedule show no purchases of NCI: the tag holds distributions only, no
     adjustment;
   - nothing found either way: unresolved (the tag's content can't be confirmed).

   Before the agent, code settles the plain case from the tags: when the equity statement's distributions to NCI
   (``measures.NCI_EQUITY_DISTRIBUTION_CONCEPTS``) are at least the tagged cash amount (within 1%) and no purchase of
   NCI ownership is tagged (``measures.NCI_PURCHASE_CONCEPTS``), the cash can't hold buyouts: distributions only, no
   model call.

   A buyout identified without an amount is checked before "no purchases": the two can't both hold.
"""

from __future__ import annotations

import copy
import re

from app.prompts.fo_prompts import FO_RCF_T4_PROMPT, FO_RCF_T4_SCHEMA, FO_RCF_T4_TEMPLATE

from .dscr_triggers import _ev, _held, _millions, _quoted, _value, add_trigger_note, collect_passages, stated_amount
from .icr_triggers import FORECAST
from .precheck import DATA_NOTICE, PrecheckAgentError, call_agent, require_agent
from .rcf_t3 import LIMITS, NCI, NONE, PURCHASE, SECTIONS, _section_after
from .rcf_triggers import _headline_index
from .scoring import cash_flow_settings, headline_score, score_cash_flow

RCF_T4_VERSION = "fo-trigger-rcf-t4-1.0.0"
RCF_T4_ID = "rcf_t4"
RCF_T4_CONCEPT = "us-gaap:PaymentsToMinorityShareholders"
AGENT = "Trigger Agent"
TIE_SHARE = 0.01  # the equity statement's distributions may fall short of the cash tag by this much (rounding)

OWNERSHIP = re.compile(PURCHASE.pattern + r"|ownership interests?|additional interests?|changes? in (the )?(parent'?s? )?"
                       r"ownership", re.I)
RCF_T4_TERMS = re.compile(NCI.pattern + r"|distributions?|" + OWNERSHIP.pattern, re.I)

STATUS_TEXT = {"skipped": "Skipped: agent triggers are skipped after a going-concern fail"}
RULES = [("Buyouts found in the tagged amount and quantified",
          "Excluded: RCF recomputed with distributions only"),
         ("Buyouts mixed in but not quantified",
          "Unresolved: RCF / debt escalated to an analyst, who accepts the score or overrides it"),
         ("The equity statement and ASC 810 schedule show no purchases of NCI",
          "The tag holds distributions only: no adjustment"),
         ("Nothing found either way", "Unresolved: RCF / debt escalated to an analyst"),
         ("The agent's answer and the verified evidence disagree",
          "Unresolved: RCF / debt escalated to an analyst, who accepts the score or overrides it")]


# --- the passages ------------------------------------------------------------------------------------------------

def rcf_t4_passages(data: dict) -> dict:
    """The cash flow statement's, the equity statement's and the notes' paragraphs and tables that mention NCI,
    distributions or purchases of ownership; ids M01… in document order."""
    return collect_passages(data, "M", LIMITS, SECTIONS, RCF_T4_TERMS, _section_after)


def tag_facts(col: dict) -> dict:
    """What the engine counted for the triggering column."""
    return {"heading": col["heading"], "period": col["period"], "period_end": col["period"].split("--")[1],
            "concept": col.get("nci_distributions_concept"), "nci_distributions": col.get("nci_distributions") or 0.0,
            "equity_distributions": col.get("nci_equity_distributions"),
            "equity_distributions_concept": col.get("nci_equity_distributions_concept"),
            "purchases_tagged": col.get("nci_purchases_tagged") or [],
            "rcf": col.get("rcf"), "ratio": col["rcf_to_debt"].get("ratio"), "score": col["rcf_to_debt"].get("score")}


def rcf_t4_inputs(t: dict, meta: dict, fx: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": fx["period_end"]},
        "rcf": {"column": fx["heading"], "nci_distributions_tag": fx["concept"],
                "nci_distributions_as_tagged_millions": _millions(fx["nci_distributions"]),
                "rcf_millions": _millions(fx["rcf"]), "score": fx["score"],
                "trigger": f"the NCI-distributions figure comes from {RCF_T4_CONCEPT}"},
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t["passages"]],
    }


# --- validation --------------------------------------------------------------------------------------------------

def _buyout(o, by_id, held):
    """One verified buyout of NCI ownership, with its amount when it holds up, or None (held back)."""
    item = {"item_id": o.get("item_id"), "description": o.get("description")}
    e = o.get("evidence") or {}
    p, why = _quoted(e, by_id, OWNERSHIP, "the quote doesn't name a purchase of NCI ownership")
    if not why and not NCI.search(e["quote"]):
        why = "the quote doesn't name noncontrolling, minority or redeemable interests (or partners)"
    if not why and FORECAST.search(e["quote"]):
        why = "the quote reads as a forecast, not the year's figure"
    if why:
        held.append(_held(item, "buyout", e, why))
        return None
    entry = {**item, "amount": None, "evidence": _ev(e, p)}
    if (e.get("amount") or "").strip():
        value, why = _value(e, p)
        if why:
            held.append(_held(item, "amount", e, why))
        else:
            entry["amount"] = value
    return entry


def validate_rcf_t4(parsed: dict, t: dict, fx: dict) -> dict:
    """Keep the evidence that holds up and re-derive the outcome: buyouts excluded (quantified), unresolved (mixed in
    but not quantified), distributions only (no purchases shown), else unresolved (nothing found)."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    held, notes = [], []
    buyouts = [e for o in parsed.get("buyouts") or [] for e in [_buyout(o, by_id, held)] if e]
    quantified = [e for e in buyouts if e["amount"] is not None]
    unquantified = [e for e in buyouts if e["amount"] is None]
    none = []
    for i, q in enumerate(parsed.get("no_purchase_evidence") or [], 1):
        p, why = _quoted(q, by_id, OWNERSHIP, "the quote doesn't mention purchases of NCI ownership")
        if not why and not NONE.search(q["quote"]):
            why = "the quote doesn't show no purchase"
        if why:
            held.append(_held({"item_id": f"Z{i}"}, "no purchases", q, why))
        else:
            none.append({"item_id": f"Z{i}", **_ev(q, p)})
    total = sum(e["amount"] for e in quantified)
    excluded = min(total, fx["nci_distributions"])
    why = None
    if quantified:
        status = "buyouts_excluded"
        if total > fx["nci_distributions"]:
            notes.append(f"The buyouts ({total / 1e6:,.1f} million) exceed the tagged amount "
                         f"({fx['nci_distributions'] / 1e6:,.1f} million): the distributions are taken to zero.")
        if unquantified:
            notes.append(f"{', '.join(e['item_id'] for e in unquantified)} also identified without an amount: not "
                         "excluded (a quantified buyout comes first).")
    elif unquantified:
        status = "unresolved"
        why = (f"buyouts are mixed into the tagged amount but not quantified "
               f"({', '.join(e['item_id'] for e in unquantified)})")
    elif none:
        status = "distributions_only"
    else:
        status = "unresolved"
        why = "neither a buyout nor a statement that there were no purchases of NCI was found"
    return {"status": status, "buyouts": buyouts, "no_purchase_evidence": none,
            "excluded": excluded if status == "buyouts_excluded" else 0.0, "unresolved_why": why, "held_back": held,
            "stated_status": parsed.get("status"), "status_consistent": parsed.get("status") == status,
            "validation_notes": notes}


def agent_reading(parsed: dict, t: dict, fx: dict) -> dict:
    """The agent's answer as it states it (not verified, not applied), for an analyst to compare with the verified
    outcome: "buyouts_excluded" takes the buyouts it gives out of the distributions to NCI (not
    below zero). Any other answer changes nothing."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    stated, notes, excluded = parsed.get("status"), [], 0.0
    if stated == "buyouts_excluded":
        total = sum(v for o in parsed.get("buyouts") or []
                    for v in [stated_amount(o.get("evidence"), by_id, notes, o.get("item_id"))] if v)
        excluded = min(total, fx["nci_distributions"])
    return {"status": stated, "notes": notes, "applies": {"status": stated, "excluded": excluded}}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_RCF_T4_PROMPT, template=FO_RCF_T4_TEMPLATE, response_schema=FO_RCF_T4_SCHEMA,
                      role="analyze", label="Financial Overview Trigger Agent: RCF/Debt T4")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

def _reason(status: str, fx: dict, checked: dict) -> str:
    if status == "buyouts_excluded":
        items = "; ".join(f"{e['amount'] / 1e6:,.1f} million ({e['evidence']['passage_id']})"
                          for e in checked["buyouts"] if e["amount"] is not None)
        return (f"Buyouts of NCI in the tagged amount, quantified ({items}): distributions to NCI "
                f"{fx['nci_distributions'] / 1e6:,.1f} → {(fx['nci_distributions'] - checked['excluded']) / 1e6:,.1f} "
                "million, RCF / debt recomputed")
    if status == "distributions_only":
        ids = ", ".join(q["passage_id"] for q in checked["no_purchase_evidence"])
        return f"No purchases of NCI in the year ({ids}): the tag holds distributions only, no adjustment"
    return "Unresolved: " + checked["unresolved_why"]


def rcf_t4_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None, mode: str = "live",
                   case_id: str | None = None) -> dict:
    """The RCF/Debt T4 trigger result recorded in the run payload (``payload.triggers.rcf_t4``). ``metrics`` are the
    scored measures (after RCF/Debt T3); the trigger reads RCF / debt's headline column."""
    m = next((x for x in metrics if x["key"] == "rcf"), None)
    score, heading = headline_score(m) if m else (None, None)
    result = {"trigger": RCF_T4_ID, "measure": "rcf", "version": RCF_T4_VERSION, "concept": RCF_T4_CONCEPT,
              "score": score, "column": heading, "fired": False, "mode": mode if backend is not None else "none",
              "agent": None, "notes": [], "review_required": False}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", reason=STATUS_TEXT["skipped"],
                      effect="None: the measures are superseded by the going-concern fail.")
        return result
    if score is None:
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason="RCF / debt has no score: trigger RCF/Debt T4 not set off")
        return result
    fx = tag_facts(m["columns"][_headline_index(m)])
    result["inputs"] = fx
    if fx["concept"] != RCF_T4_CONCEPT:
        result.update(status="not_triggered", decided_by="tag", effect="None.",
                      reason=f"Distributions to NCI come from {fx['concept']}, not {RCF_T4_CONCEPT}: trigger "
                             "RCF/Debt T4 not set off")
        return result
    fired_by = f"distributions to NCI of {fx['nci_distributions'] / 1e6:,.1f} million come from {RCF_T4_CONCEPT}"
    eq = fx["equity_distributions"]
    if eq is not None and not fx["purchases_tagged"] and eq >= fx["nci_distributions"] * (1 - TIE_SHARE):
        result.update(fired=True, fired_by=fired_by, status="distributions_only", decided_by="tags", buyouts=[],
                      no_purchase_evidence=[], excluded=0.0, unresolved=False, disagreement=False, unresolved_why=None,
                      reason=(f"The equity statement's distributions to NCI ({eq / 1e6:,.1f} million, "
                              f"{fx['equity_distributions_concept']}) are at least the cash tagged "
                              f"({fx['nci_distributions'] / 1e6:,.1f} million), and no purchase of NCI ownership is "
                              "tagged: the tag holds distributions only, no adjustment"),
                      effect="None: the RCF / debt score stays as calculated; resolved from the tags, so no model call "
                             "and no analyst decision is needed.")
        result["notes"].append("Settled from the tags: the agent was not called.")
        return result
    t = rcf_t4_passages(data)
    result.update(fired=True, fired_by=f"distributions to NCI of {fx['nci_distributions'] / 1e6:,.1f} million come from "
                                       f"{RCF_T4_CONCEPT}", passages=t["passages"], sections=t["sections"])
    if not t["passages"]:
        checked = {"status": "unresolved", "buyouts": [], "no_purchase_evidence": [], "excluded": 0.0,
                   "status_consistent": True,
                   "unresolved_why": "neither a buyout nor a statement that there were no purchases of NCI was found"}
        decided_by = "no_text"
        result["notes"].append("No cash flow statement, equity statement or note passage mentions NCI or purchases of "
                               "ownership: the agent was not called. Nothing found, so RCF / debt is unresolved.")
    else:
        require_agent(backend, RCF_T4_ID, AGENT)
        checked, decided_by = _run_agent(result, t, data, fx, session, backend, case_id)
    status = checked["status"]
    disagree = not checked["status_consistent"]
    unresolved = status == "unresolved" or disagree
    why = ("the agent's answer and the verified evidence disagree" if disagree else checked["unresolved_why"]) \
        if unresolved else None
    result.update(status=status, decided_by=decided_by, buyouts=checked["buyouts"],
                  no_purchase_evidence=checked["no_purchase_evidence"], excluded=checked["excluded"],
                  reason=_reason(status, fx, checked), unresolved=unresolved, disagreement=disagree,
                  review_required=unresolved, unresolved_why=why,
                  effect=("RCF / debt recomputed with distributions only (buyouts excluded)"
                          if status == "buyouts_excluded" else "None: the RCF / debt score stays as calculated")
                  + (f"; unresolved ({why}), so RCF / debt is escalated to an analyst, who accepts the score or "
                     "overrides it." if unresolved else
                     "; resolved (the agent and the verified evidence agree), so no analyst decision is needed."))
    return result


def _run_agent(result, t, data, fx, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_RCF_T4_PROMPT,
                            rcf_t4_inputs(t, data["meta"], fx))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's RCF/Debt T4 output was rejected "
                                 f"({agent['validation_detail']}); the run was not recorded. Run it again.")
    checked = validate_rcf_t4(run.parsed, t, fx)
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

def apply_rcf_t4(fo: list[dict], t: dict | None) -> list[dict]:
    """RCF / debt with the trigger's outcome on its headline column (a copy; the inputs stay untouched): the buyouts
    come out of the distributions to NCI, RCF, RCF / debt and the CFO-based comparison are recomputed and rescored.
    The column keeps the distributions as tagged. The measure is marked for escalation when the trigger is
    unresolved."""
    if not t or not t.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "rcf":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        col = m["columns"][_headline_index(m)]
        adj = {"trigger": "RCF/Debt T4", "outcome": t["status"], "calculated": col["rcf_to_debt"]["score"],
               "ratio_calculated": col["rcf_to_debt"]["ratio"], "nci_calculated": col["nci_distributions"],
               "rcf_calculated": col["rcf"], "excluded": t.get("excluded") or 0.0, "recomputed": None}
        done = None
        if t["status"] == "buyouts_excluded" and adj["excluded"]:
            col.setdefault("nci_distributions_before_trigger", col["nci_distributions"])
            col["nci_distributions"] -= adj["excluded"]
            col["nci_distributions_basis"] = (f"distributions only: buyouts of {adj['excluded'] / 1e6:,.1f} million "
                                              f"excluded from {RCF_T4_CONCEPT} (trigger RCF/Debt T4)")
            col["rcf"] += adj["excluded"]
            if col.get("rcf_cfo") is not None:
                col["rcf_cfo"] += adj["excluded"]
            rc = col["rcf_to_debt"]
            if not rc["reason"] and not rc["no_debt"]:
                rc["ratio"] = col["rcf"] / col["adj_debt"]
                if col.get("rcf_to_debt_cfo") is not None:
                    col["rcf_to_debt_cfo"] = col["rcf_cfo"] / col["adj_debt"]
            score_cash_flow(col, cash_flow_settings(m))
            adj["recomputed"] = col["rcf_to_debt"]["score"]
            done = (f"distributions to NCI {adj['nci_calculated'] / 1e6:,.1f} → {col['nci_distributions'] / 1e6:,.1f} "
                    f"million (buyouts excluded), RCF {adj['rcf_calculated'] / 1e6:,.1f} → {col['rcf'] / 1e6:,.1f} "
                    "million, RCF / debt score "
                    + (f"{adj['calculated']:g} → " if adj["calculated"] is not None else "")
                    + (f"{adj['recomputed']:g}" if adj["recomputed"] is not None else "none"))
        adj["rcf"], adj["score"] = col["rcf"], col["rcf_to_debt"]["score"]
        col["rcf_t4_adjustment"] = adj
        add_trigger_note(m, (f"RCF/Debt trigger T4 ({t.get('fired_by')}, {col['heading']}): "
                             + (done or "no adjustment" if t["status"] == "unresolved"
                                else done or t["reason"][:1].lower() + t["reason"][1:])),
                         "trigger RCF/Debt T4: " + {"buyouts_excluded": "buyouts excluded",
                                                    "distributions_only": "distributions only"}.get(t["status"],
                                                                                                    "unresolved"),
                         t.get("unresolved", True), t.get("unresolved_why") or "not determined")
        out.append(m)
    return out
