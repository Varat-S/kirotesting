"""FO Trigger Agent, RCF/Debt T3: distributions to noncontrolling interests (NCI) are missing, while NCI is material.

RCF = FFO − dividends − distributions to NCI. The engine takes NCI distributions from the standard concepts
(``measures.NCI_DISTRIBUTION_CONCEPTS``) and counts none when none is tagged; a company that reports them under its own
label (or not at all) then shows RCF too high, which overstates debt capacity. T3 runs on RCF / debt's headline column,
after RCF/Debt T2, only when the pre-checks allow it (``payload.gate.agent_triggers`` is "enabled"). Code decides: it
fires when no standard NCI-distributions concept is tagged for the year and NCI is material:

- |NCI share of net income| > 5% of |consolidated net income|; or
- when consolidated net income is zero, negative or not tagged: NCI (equity plus redeemable) above 5% of total equity,
  or any NCI when total equity is zero or negative.

Same three steps as the other triggers:

1. ``rcf_t3_passages`` (deterministic) collects what the agent checks: the cash flow statement (financing), the
   statement of changes in equity, and the notes (the redeemable NCI rollforward, the ASC 810 ownership schedule)
   that mention NCI, minority or redeemable interests, partners or distributions (ids N01…).
2. The Trigger Agent (prompt ``fo_trigger_rcf_t3``) ALWAYS reads them (no agent, or a rejected output, refuses the
   run). Every amount is quoted verbatim. Only a filing with none of those passages has nothing to read (no model
   call: nothing found, so unresolved).
3. ``validate_rcf_t3`` keeps only verbatim evidence that says what it must and RE-DERIVES the outcome, first match wins:

   - distributions found and the cash paid stated: RCF recomputed with the cash paid (distributions to redeemable NCI
     included, purchases of NCI excluded);
   - only declared amounts disclosed: the declared amount is used, labelled as a timing difference;
   - the equity statement and redeemable NCI rollforward show no distributions in the year: zero is correct, no
     adjustment;
   - lumped into "other financing" or with buyouts without a split (or nothing found): unresolved.
"""

from __future__ import annotations

import copy
import re

from app.prompts.fo_prompts import FO_RCF_T3_PROMPT, FO_RCF_T3_SCHEMA, FO_RCF_T3_TEMPLATE

from .covenants import NOTE_HEADING
from .dscr_triggers import _ev, _held, _millions, _quoted, _value, add_trigger_note, collect_passages, stated_amount
from .icr_triggers import FORECAST
from .precheck import DATA_NOTICE, PrecheckAgentError, call_agent, require_agent
from .rcf_t2 import CASH_FLOW_TITLE
from .rcf_triggers import _headline_index
from .scoring import cash_flow_settings, headline_score, score_cash_flow

RCF_T3_VERSION = "fo-trigger-rcf-t3-1.0.0"
RCF_T3_ID = "rcf_t3"
RCF_T3_SHARE = 0.05  # NCI is material above this share of consolidated net income (or of total equity)
AGENT = "Trigger Agent"

SECTIONS = [("cash_flow", "Cash flow statement"), ("equity", "Statement of changes in equity"),
            ("notes", "Notes to the financial statements")]
LIMITS = {"cash_flow": 10, "equity": 10, "notes": 25}
EQUITY_TITLE = re.compile(r"statements? of (consolidated )?(changes in )?(stockholders|shareholders|share-?owners)['’]? "
                          r"(equity|deficit)|statements? of (changes in )?equity|equity statements?", re.I)
OTHER_TITLE = re.compile(r"balance sheets?|financial position|statements? of (consolidated )?(operations|income|"
                         r"earnings|comprehensive)", re.I)
NCI = re.compile(r"non-?controlling|minority|redeemable|partners?\b|joint ventures?|affiliates?", re.I)
RCF_T3_TERMS = re.compile(NCI.pattern + r"|distributions?|parent'?s? ownership", re.I)

# --- what a quote must say ---------------------------------------------------------------------------------------
DISTRIBUTION = re.compile(r"distribut\w*|dividends?", re.I)
PURCHASE = re.compile(r"purchas\w*|acqui\w* (of|additional)|buy-?\w*|bought|redemptions? of|repurchas\w*|buyouts?", re.I)
DECLARED = re.compile(r"declar\w*|payable|approved", re.I)
NONE = re.compile(r"\bno\b|\bnone\b|\bnil\b|—|–|(?<![\d.,])0(?![\d.,])|\|\s*-\s*(\||$)|did not (make|pay|declare)",
                  re.I)
LUMPED = re.compile(r"other financing|combined|together with|includ\w*|and (purchases|acquisitions|buyouts)", re.I)

STATUS_TEXT = {"skipped": "Skipped: agent triggers are skipped after a going-concern fail"}
RULES = [("Distributions found and the cash paid stated",
          "RCF recomputed with the cash paid (redeemable NCI included, purchases of NCI excluded)"),
         ("Only declared amounts disclosed", "The declared amount is used, labelled as a timing difference"),
         ("The equity statement and redeemable NCI rollforward show no distributions in the year",
          "Zero is correct: no adjustment"),
         ("Lumped into other financing or with buyouts, the split not disclosed (or nothing found)",
          "Unresolved: RCF / debt escalated to an analyst, who accepts the score or overrides it"),
         ("The agent's answer and the verified evidence disagree",
          "Unresolved: RCF / debt escalated to an analyst, who accepts the score or overrides it")]


# --- the passages ------------------------------------------------------------------------------------------------

def _section_after(item: str, heading: str, current: str | None, numbered: bool) -> str | None:
    """The searched section a heading opens, keeps or closes: the cash flow statement, the equity statement and every
    note (Item 8 and the F-pages only)."""
    if item == "7":
        return None
    if CASH_FLOW_TITLE.search(heading) and len(heading) <= 100:
        return "cash_flow"
    if EQUITY_TITLE.search(heading) and len(heading) <= 120:
        return "equity"
    if OTHER_TITLE.search(heading) and len(heading) <= 100:
        return None
    if numbered and not NOTE_HEADING.match(heading):
        return current
    if not numbered and current in ("cash_flow", "equity"):
        return current
    return "notes"


def rcf_t3_passages(data: dict) -> dict:
    """The cash flow statement's, the equity statement's and the notes' paragraphs and tables that mention NCI or
    distributions; ids N01… in document order."""
    return collect_passages(data, "N", LIMITS, SECTIONS, RCF_T3_TERMS, _section_after)


def nci_facts(col: dict) -> dict:
    """What the engine counted for the triggering column, and whether NCI is material."""
    ni, nci = col.get("consolidated_net_income"), col.get("nci_income")
    eq, nci_eq = col.get("total_equity"), col.get("nci_equity")
    test = share = None
    if ni is not None and ni > 0 and nci is not None:
        test, share = "income", abs(nci) / abs(ni)
        material = share > RCF_T3_SHARE
    elif nci_eq is not None and nci_eq > 0 and eq is not None:
        test = "equity"
        share = nci_eq / eq if eq > 0 else None
        material = eq <= 0 or share > RCF_T3_SHARE
    else:
        material = False
    return {"heading": col["heading"], "period": col["period"], "period_end": col["period"].split("--")[1],
            "tagged": bool(col.get("nci_distributions_tagged")), "nci_distributions": col.get("nci_distributions") or 0.0,
            "nci_income": nci, "nci_income_concept": col.get("nci_income_concept"), "net_income": ni,
            "net_income_concept": col.get("consolidated_net_income_concept"), "nci_equity": nci_eq,
            "nci_equity_concept": col.get("nci_equity_concept"), "total_equity": eq,
            "total_equity_concept": col.get("total_equity_concept"), "test": test, "share": share,
            "material": material, "rcf": col.get("rcf"), "ratio": col["rcf_to_debt"].get("ratio"),
            "score": col["rcf_to_debt"].get("score")}


def rcf_t3_inputs(t: dict, meta: dict, fx: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": fx["period_end"]},
        "rcf": {"column": fx["heading"], "nci_share_of_net_income_millions": _millions(fx["nci_income"]),
                "consolidated_net_income_millions": _millions(fx["net_income"]),
                "nci_equity_millions": _millions(fx["nci_equity"]), "total_equity_millions": _millions(fx["total_equity"]),
                "nci_distributions_counted_millions": _millions(fx["nci_distributions"]),
                "rcf_millions": _millions(fx["rcf"]), "score": fx["score"], "trigger": t["fired_by"]},
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t["passages"]],
    }


# --- validation --------------------------------------------------------------------------------------------------

def _distribution(o, by_id, held):
    """One verified distribution to NCI holders, or None (held back)."""
    item = {"item_id": o.get("item_id"), "holder": o.get("holder"), "basis": o.get("basis"),
            "description": o.get("description")}
    e = o.get("evidence") or {}
    p, why = _quoted(e, by_id, DISTRIBUTION, "the quote doesn't name a distribution")
    if not why and not NCI.search(e["quote"]):
        why = "the quote doesn't name noncontrolling, minority or redeemable interests (or partners)"
    if not why and PURCHASE.search(e["quote"]):
        why = "the quote combines distributions with purchases of NCI ownership (purchases are always excluded)"
    if not why and FORECAST.search(e["quote"]):
        why = "the quote reads as a forecast, not the year's figure"
    if not why and item["basis"] == "declared" and not DECLARED.search(e["quote"]):
        why = "the quote doesn't show a declared amount"
    value = None
    if not why:
        value, why = _value(e, p)
    if why:
        held.append(_held(item, "distribution", e, why))
        return None
    return {**item, "amount": value, "evidence": _ev(e, p)}


def _quotes(items, by_id, need, why, prefix, held):
    out = []
    for i, q in enumerate(items or [], 1):
        p, reason = _quoted(q, by_id, need, why)
        if reason:
            held.append(_held({"item_id": f"{prefix}{i}"}, prefix, q, reason))
        else:
            out.append({"item_id": f"{prefix}{i}", **_ev(q, p)})
    return out


def validate_rcf_t3(parsed: dict, t: dict) -> dict:
    """Keep the evidence that holds up and re-derive the outcome, first match wins: cash paid, declared (a timing
    difference), none in the year (zero is correct), else unresolved (lumped without a split, or nothing found)."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    held, notes = [], []
    found = [e for o in parsed.get("distributions") or [] for e in [_distribution(o, by_id, held)] if e]
    paid = [e for e in found if e["basis"] == "cash_paid"]
    declared = [e for e in found if e["basis"] == "declared"]
    purchases = _quotes(parsed.get("purchases"), by_id, PURCHASE, "the quote doesn't show a purchase of NCI", "P", held)
    none = _quotes(parsed.get("none_evidence"), by_id, re.compile(NONE.pattern + "|" + DISTRIBUTION.pattern, re.I),
                   "the quote doesn't show no distribution", "Z", held)
    none = [q for q in none if NONE.search(q["quote"]) and (DISTRIBUTION.search(q["quote"]) or NCI.search(q["quote"]))]
    lumped = _quotes(parsed.get("lumped"), by_id, LUMPED, "the quote doesn't show distributions combined", "L", held)
    why = None
    if paid:
        status, used = "cash_paid", paid
        if declared:
            notes.append(f"{', '.join(e['item_id'] for e in declared)} declared amounts also given: the cash paid is "
                         "used.")
    elif declared:
        status, used = "declared", declared
    elif none and not lumped:
        status, used = "none_in_year", []
    else:
        status, used = "unresolved", []
        why = ("distributions are lumped into other financing or with buyouts, and the split isn't disclosed"
               if lumped else "no distribution to NCI, and no statement that there was none, was found")
    return {"status": status, "distributions": used, "purchases": purchases, "none_evidence": none, "lumped": lumped,
            "amount": sum(e["amount"] for e in used), "unresolved_why": why, "held_back": held,
            "stated_status": parsed.get("status"), "status_consistent": parsed.get("status") == status,
            "validation_notes": notes}


def agent_reading(parsed: dict, t: dict) -> dict:
    """The agent's answer as it states it (not verified, not applied), for an analyst to compare with the verified
    outcome: "cash_paid" or "declared" uses its distributions to NCI on that basis. Any other
    answer changes nothing."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    stated, notes, amount = parsed.get("status"), [], 0.0
    if stated in ("cash_paid", "declared"):
        amount = sum(v for o in parsed.get("distributions") or [] if o.get("basis") == stated
                     for v in [stated_amount(o.get("evidence"), by_id, notes, o.get("item_id"))] if v)
    return {"status": stated, "notes": notes, "applies": {"status": stated, "amount": amount}}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_RCF_T3_PROMPT, template=FO_RCF_T3_TEMPLATE, response_schema=FO_RCF_T3_SCHEMA,
                      role="analyze", label="Financial Overview Trigger Agent: RCF/Debt T3")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

def _m(v):
    return f"{v / 1e6:,.1f} million"


def _reason(status: str, checked: dict) -> str:
    if status in ("cash_paid", "declared"):
        items = "; ".join(f"{'redeemable NCI' if e['holder'] == 'redeemable_nci' else 'NCI'} {_m(e['amount'])} "
                          f"({e['evidence']['passage_id']})" for e in checked["distributions"])
        return ((f"Distributions to NCI, cash paid ({items})" if status == "cash_paid" else
                 f"Only declared distributions to NCI disclosed ({items}): used, a timing difference (declared, not "
                 "cash paid)") + f": {_m(checked['amount'])} out of RCF, RCF / debt recomputed")
    if status == "none_in_year":
        ids = ", ".join(q["passage_id"] for q in checked["none_evidence"])
        return f"No distributions to NCI in the year ({ids}): zero is correct, no adjustment"
    return "Unresolved: " + checked["unresolved_why"]


def _fired_by(fx: dict) -> str:
    if fx["test"] == "income":
        size = (f"the NCI share of net income ({_m(fx['nci_income'])}) is {fx['share']:.1%} of consolidated net income, "
                f"above {RCF_T3_SHARE:.0%}")
    elif fx["share"] is None:
        size = f"NCI equity of {_m(fx['nci_equity'])} against total equity of {_m(fx['total_equity'])} (zero or below)"
    else:
        size = f"NCI equity ({_m(fx['nci_equity'])}) is {fx['share']:.1%} of total equity, above {RCF_T3_SHARE:.0%}"
    return f"no standard NCI-distributions concept tagged, and {size}"


def rcf_t3_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None, mode: str = "live",
                   case_id: str | None = None) -> dict:
    """The RCF/Debt T3 trigger result recorded in the run payload (``payload.triggers.rcf_t3``). ``metrics`` are the
    scored measures (after RCF/Debt T1 and T2); the trigger reads RCF / debt's headline column."""
    m = next((x for x in metrics if x["key"] == "rcf"), None)
    score, heading = headline_score(m) if m else (None, None)
    result = {"trigger": RCF_T3_ID, "measure": "rcf", "version": RCF_T3_VERSION, "share_threshold": RCF_T3_SHARE,
              "score": score, "column": heading, "fired": False, "mode": mode if backend is not None else "none",
              "agent": None, "notes": [], "review_required": False}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", reason=STATUS_TEXT["skipped"],
                      effect="None: the measures are superseded by the going-concern fail.")
        return result
    if score is None:
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason="RCF / debt has no score: trigger RCF/Debt T3 not set off")
        return result
    fx = nci_facts(m["columns"][_headline_index(m)])
    result["inputs"] = fx
    if fx["tagged"] or not fx["material"]:
        result.update(status="not_triggered", decided_by="nci", effect="None.",
                      reason=("Distributions to NCI are tagged under a standard concept" if fx["tagged"] else
                              "NCI isn't material (" + (f"{fx['share']:.1%} of "
                                                        + ("consolidated net income" if fx["test"] == "income"
                                                           else "total equity") if fx["share"] is not None
                                                        else "no NCI tagged") + ")")
                      + ": trigger RCF/Debt T3 not set off")
        return result
    t = rcf_t3_passages(data)
    result.update(fired=True, fired_by=_fired_by(fx), passages=t["passages"], sections=t["sections"])
    if not t["passages"]:
        checked = {"status": "unresolved", "distributions": [], "purchases": [], "none_evidence": [], "lumped": [],
                   "amount": 0.0, "status_consistent": True,
                   "unresolved_why": "no distribution to NCI, and no statement that there was none, was found"}
        decided_by = "no_text"
        result["notes"].append("No cash flow statement, equity statement or note passage mentions NCI or "
                               "distributions: the agent was not called. Nothing found, so RCF / debt is unresolved.")
    else:
        require_agent(backend, RCF_T3_ID, AGENT)
        checked, decided_by = _run_agent(result, t, data, fx, session, backend, case_id)
    status = checked["status"]
    disagree = not checked["status_consistent"]
    unresolved = status == "unresolved" or disagree
    why = ("the agent's answer and the verified evidence disagree" if disagree else checked["unresolved_why"]) \
        if unresolved else None
    result.update(status=status, decided_by=decided_by, distributions=checked["distributions"],
                  purchases=checked["purchases"], none_evidence=checked["none_evidence"], lumped=checked["lumped"],
                  amount=checked["amount"], reason=_reason(status, checked), unresolved=unresolved,
                  disagreement=disagree, review_required=unresolved, unresolved_why=why,
                  effect=("RCF / debt recomputed with the distributions to NCI "
                          + ("(cash paid)" if status == "cash_paid" else "(declared: a timing difference)")
                          if status in ("cash_paid", "declared") else "None: the RCF / debt score stays as calculated")
                  + (f"; unresolved ({why}), so RCF / debt is escalated to an analyst, who accepts the score or "
                     "overrides it." if unresolved else
                     "; resolved (the agent and the verified evidence agree), so no analyst decision is needed."))
    return result


def _run_agent(result, t, data, fx, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_RCF_T3_PROMPT,
                            rcf_t3_inputs({**t, "fired_by": result["fired_by"]}, data["meta"], fx))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's RCF/Debt T3 output was rejected "
                                 f"({agent['validation_detail']}); the run was not recorded. Run it again.")
    checked = validate_rcf_t3(run.parsed, t)
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

def apply_rcf_t3(fo: list[dict], t: dict | None) -> list[dict]:
    """RCF / debt with the trigger's outcome on its headline column (a copy; the inputs stay untouched): the
    distributions to NCI found replace the ones counted (none, as tagged), RCF, RCF / debt and the CFO-based comparison
    are recomputed and rescored. A declared amount is labelled as a timing difference. The column keeps the
    distributions as tagged. The measure is marked for escalation when the trigger is unresolved."""
    if not t or not t.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "rcf":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        col = m["columns"][_headline_index(m)]
        adj = {"trigger": "RCF/Debt T3", "outcome": t["status"], "calculated": col["rcf_to_debt"]["score"],
               "ratio_calculated": col["rcf_to_debt"]["ratio"], "nci_calculated": col["nci_distributions"],
               "rcf_calculated": col["rcf"], "recomputed": None}
        done = None
        if t["status"] in ("cash_paid", "declared"):
            change = t["amount"] - col["nci_distributions"]
            col["nci_distributions_before_trigger"] = col["nci_distributions"]
            col["nci_distributions"] = t["amount"]
            col["nci_distributions_basis"] = ("cash paid (trigger RCF/Debt T3)" if t["status"] == "cash_paid" else
                                              "declared, not cash paid: a timing difference (trigger RCF/Debt T3)")
            col["rcf"] -= change
            if col.get("rcf_cfo") is not None:
                col["rcf_cfo"] -= change
            rc = col["rcf_to_debt"]
            if not rc["reason"] and not rc["no_debt"]:
                rc["ratio"] = col["rcf"] / col["adj_debt"]
                if col.get("rcf_to_debt_cfo") is not None:
                    col["rcf_to_debt_cfo"] = col["rcf_cfo"] / col["adj_debt"]
            score_cash_flow(col, cash_flow_settings(m))
            adj["recomputed"] = col["rcf_to_debt"]["score"]
            done = (f"distributions to NCI {adj['nci_calculated'] / 1e6:,.1f} → {t['amount'] / 1e6:,.1f} million ("
                    + ("cash paid" if t["status"] == "cash_paid" else "declared: a timing difference")
                    + f"), RCF {adj['rcf_calculated'] / 1e6:,.1f} → {col['rcf'] / 1e6:,.1f} million, RCF / debt score "
                    + (f"{adj['calculated']:g} → " if adj["calculated"] is not None else "")
                    + (f"{adj['recomputed']:g}" if adj["recomputed"] is not None else "none"))
        adj["rcf"], adj["score"] = col["rcf"], col["rcf_to_debt"]["score"]
        col["rcf_t3_adjustment"] = adj
        add_trigger_note(m, (f"RCF/Debt trigger T3 ({t.get('fired_by')}, {col['heading']}): "
                             + (done or "no adjustment" if t["status"] == "unresolved"
                                else done or t["reason"][:1].lower() + t["reason"][1:])),
                         "trigger RCF/Debt T3: " + {"cash_paid": "NCI distributions (cash paid)",
                                                    "declared": "NCI distributions (declared, timing)",
                                                    "none_in_year": "no NCI distributions"}.get(t["status"],
                                                                                                "unresolved"),
                         t.get("unresolved", True), t.get("unresolved_why") or "not found")
        out.append(m)
    return out
