"""FO Trigger Agent, ICR T3: ICR's EBIT is operating income, not pre-tax income + interest expense.

ICR = (EBIT + rent) / (interest incurred + rent), and EBIT is pre-tax income from continuing operations plus interest
expense; the engine falls back to operating income when pre-tax income (or the interest to add back) isn't tagged.
Operating income leaves out non-operating items (other income and expense, the non-service part of pension cost,
investment gains and losses, FX), so EBIT can come out too high or too low against the standard definition and
companies aren't compared on the same basis. ICR T3 runs on ICR's latest year, whatever ICR scored, after ICR T2 and
before ICR T1, only when the pre-checks allow it (``payload.gate.agent_triggers`` is "enabled") and ICR isn't n/a:

1. ``icr_t3_passages`` (deterministic) collects the income statement, and the paragraphs and table rows of MD&A and
   the notes that mention pre-tax income, operating income, interest or non-operating items (ids K01…).
2. The Trigger Agent (prompt ``fo_trigger_icr_t3``) ALWAYS reads them. Every 10-K has the "income before income
   taxes" line for continuing operations (Regulation S-X 5-03).
3. ``validate_icr_t3`` keeps only verbatim evidence and decides, first match:

   - pre-tax income found: EBIT = pre-tax income + interest expense (the agent's verified figure, else the tagged
     expense), ICR recomputed: resolved;
   - pre-tax income not found: EBIT stays operating income: unresolved, so ICR is escalated to an analyst. The items
     the agent found between operating income and pre-tax income are recorded.

Only ICR's EBIT changes (the other measures keep theirs). As for every trigger, a disagreement between the agent's
answer and its verified evidence escalates ICR too.
"""

from __future__ import annotations

import copy
import re

from app.prompts.fo_prompts import FO_ICR_T3_PROMPT, FO_ICR_T3_SCHEMA, FO_ICR_T3_TEMPLATE

from .covenants import NOTE_HEADING
from .dscr_triggers import _held, _millions, add_trigger_note, collect_passages
from .icr_triggers import MDNA_SKIP, _amount
from .measures import ICR_BANDS, OPERATING_INCOME_CONCEPTS, risk_score
from .precheck import DATA_NOTICE, PrecheckAgentError, call_agent, require_agent

ICR_T3_VERSION = "fo-trigger-icr-t3-1.0.0"
ICR_T3_ID = "icr_t3"
AGENT = "Trigger Agent"
NOT_FOUND = "pre-tax income was not found, so EBIT stays operating income"
NO_INTEREST = "pre-tax income was found but no interest expense to add back, so EBIT stays operating income"

SECTIONS = [("income_statement", "Income statement"), ("mdna", "MD&A"), ("notes", "Notes")]
LIMITS = {"income_statement": 8, "mdna": 10, "notes": 15}
OTHER_STATEMENT = re.compile(r"balance sheets?|financial position|statements? of cash flows|(stockholders|"
                             r"shareholders)['’]? (equity|deficit)", re.I)
STATEMENT_TITLE = re.compile(r"statements? of (consolidated )?(operations|income|earnings)|income statements?|"
                             r"statements? of comprehensive (income|loss)", re.I)
ICR_T3_TERMS = re.compile(r"before (provision for |benefit from )?income tax\w*|pre-?tax (income|loss|earnings)|"
                          r"operating income|income from operations|operating (profit|loss)|other (income|expense)|"
                          r"non-?operating|interest expense|interest income|pension|foreign (currency|exchange)|"
                          r"investment (gains?|losses?|income)|equity in (the )?(earnings|income|losses)", re.I)

# --- what a quote must say ---------------------------------------------------------------------------------------
PRETAX = re.compile(r"before (provision for |benefit from |\(?benefit\)? ?(from|for) )?income tax\w*|"
                    r"pre-?tax (income|loss|earnings)|before taxes", re.I)
DISCONTINUED = re.compile(r"discontinued", re.I)
LOSS_SHOWN = re.compile(r"\bloss\w*|\(\s*\$?\s*\d", re.I)
INTEREST = re.compile(r"interest", re.I)
LABELS = {"pretax_found": "PRE-TAX FOUND: RECOMPUTED", "pretax_not_found": "PRE-TAX NOT FOUND: UNRESOLVED",
          "not_triggered": "NOT TRIGGERED", "skipped": "SKIPPED"}
RULES = [("Pre-tax income found", "EBIT = pre-tax income + interest expense, ICR recomputed (resolved)"),
         ("Pre-tax income not found", "EBIT stays operating income: unresolved, ICR escalated to an analyst; the "
                                      "items between operating income and pre-tax income are recorded"),
         ("The agent's answer and the verified evidence disagree",
          "Unresolved: ICR escalated to an analyst, who accepts the score or overrides it")]


# --- the passages ------------------------------------------------------------------------------------------------

def _section_after(item: str, heading: str, current: str | None, numbered: bool) -> str | None:
    """The searched section a heading opens, keeps or closes: the income statement, MD&A (without its boilerplate)
    and every note."""
    if item == "7":
        return None if MDNA_SKIP.search(heading) else "mdna"
    if STATEMENT_TITLE.search(heading) and len(heading) <= 100:
        return "income_statement"
    if OTHER_STATEMENT.search(heading) and len(heading) <= 100:
        return None  # the other primary statements
    if numbered and not NOTE_HEADING.match(heading):
        return current  # a sub-heading (or a line label inside the income statement)
    if not numbered and current == "income_statement":
        return current  # a line label inside the income statement
    return "notes"


def icr_t3_passages(data: dict) -> dict:
    """The income statement's, MD&A's and the notes' paragraphs and tables that mention pre-tax income, operating
    income, interest or non-operating items; ids K01… in document order."""
    return collect_passages(data, "K", LIMITS, SECTIONS, ICR_T3_TERMS, _section_after)


def icr_t3_facts(col: dict) -> dict:
    return {"heading": col["heading"], "period": col["period"], "period_end": col["period"].split("--")[1],
            "ebit": col.get("ebit"), "ebit_concept": col.get("ebit_concept"), "rent": col.get("rent"),
            "interest": col.get("interest"), "tagged_expense": col.get("interest_expense"),
            "expense_concept": col.get("interest_expense_concept"), "ratio": col.get("ratio"),
            "score": col.get("score")}


def icr_t3_inputs(t: dict, meta: dict, fx: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": fx["period_end"]},
        "icr": {"column": fx["heading"], "ratio": round(fx["ratio"], 2) if fx["ratio"] is not None else None,
                "score": fx["score"], "ebit_millions": _millions(fx["ebit"]), "ebit_source": fx["ebit_concept"],
                "rent_millions": _millions(fx["rent"]), "interest_incurred_millions": _millions(fx["interest"]),
                "interest_expense_tagged_millions": _millions(fx["tagged_expense"])},
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t["passages"]],
    }


# --- validation --------------------------------------------------------------------------------------------------

def validate_icr_t3(parsed: dict, t: dict, fx: dict) -> dict:
    """Keep the evidence that holds up and decide: pre-tax income found (EBIT = pre-tax income + interest expense)
    or not found (EBIT stays operating income). The items between operating income and pre-tax income are kept."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    held = []
    pretax = None
    pt = parsed.get("pretax_income")
    if pt:
        got = _amount(pt, by_id, PRETAX, "the quote doesn't show income before income taxes", {"item_id": "P1"},
                      "pre-tax income", held)
        why = None
        if got and DISCONTINUED.search(got[1]["quote"]) and not re.search(r"continuing", got[1]["quote"], re.I):
            why = "the quote is for discontinued operations, not continuing operations"
        elif got and pt.get("is_loss") and not LOSS_SHOWN.search(got[1]["quote"]):
            why = "the agent called it a loss, but the quote doesn't show one"
        elif got and not pt.get("is_loss") and re.match(r"\s*\(", pt.get("amount") or ""):
            why = "the amount is in parentheses (a loss), but the agent didn't call it one"
        if why:
            held.append(_held({"item_id": "P1"}, "pre-tax income", pt, why))
        elif got:
            pretax = {"amount": -got[0] if pt.get("is_loss") else got[0], "evidence": got[1]}
    interest = None
    ie = parsed.get("interest_expense")
    if ie:
        got = _amount(ie, by_id, INTEREST, "the quote doesn't mention interest", {"item_id": "I1"},
                      "interest expense", held)
        if got:
            interest = {"amount": got[0], "evidence": got[1], "source": "filing"}
    if interest is None and fx["tagged_expense"] is not None:
        interest = {"amount": fx["tagged_expense"], "evidence": None, "source": fx["expense_concept"] or "tagged"}
    items = []
    for n, it in enumerate(parsed.get("items_between") or [], 1):
        got = _amount(it.get("evidence"), by_id, None, None, {"item_id": f"B{n}"}, "item between", held)
        if got:
            items.append({"item_id": f"B{n}", "description": it.get("description"), "kind": it.get("kind"),
                          "amount": got[0], "evidence": got[1]})
    status = "pretax_found" if pretax else "pretax_not_found"
    ebit = pretax["amount"] + interest["amount"] if pretax and interest else None
    return {"status": status, "pretax": pretax, "interest_expense": interest, "items_between": items,
            "ebit": ebit, "held_back": held, "stated_status": parsed.get("status"),
            "status_consistent": parsed.get("status") == status}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_ICR_T3_PROMPT, template=FO_ICR_T3_TEMPLATE, response_schema=FO_ICR_T3_SCHEMA,
                      role="analyze", label="Financial Overview Trigger Agent: ICR T3")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

def _m(v):
    return f"{v / 1e6:,.1f} million"


def _reason(fx: dict, checked: dict) -> str:
    items = checked["items_between"]
    between = ("; items between operating income and pre-tax income: "
               + ", ".join(f"{e['description']} ({e['kind']} {_m(e['amount'])}, {e['evidence']['passage_id']})"
                           for e in items) if items else "")
    if checked["ebit"] is not None:
        p, i = checked["pretax"], checked["interest_expense"]
        return (f"Pre-tax income found ({_m(p['amount'])}, {p['evidence']['passage_id']}): EBIT = pre-tax income + "
                f"interest expense {_m(i['amount'])} ({i['evidence']['passage_id'] if i['evidence'] else i['source']}) "
                f"= {_m(checked['ebit'])} instead of operating income {_m(fx['ebit'])}, ICR recomputed{between}")
    if checked["pretax"]:
        return f"Pre-tax income found, but no interest expense to add back: EBIT stays operating income{between}"
    return f"Pre-tax income not found: EBIT stays operating income ({_m(fx['ebit'])}){between}"


def icr_t3_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None, mode: str = "live",
                   case_id: str | None = None) -> dict:
    """The ICR T3 trigger result recorded in the run payload (``payload.triggers.icr_t3``), on ICR's latest year."""
    icr = next((m for m in metrics if m["key"] == "icr"), None)
    col = icr["columns"][0] if icr and icr.get("columns") else None
    result = {"trigger": ICR_T3_ID, "measure": "icr", "version": ICR_T3_VERSION, "score": (col or {}).get("score"),
              "column": (col or {}).get("heading"), "fired": False, "mode": mode if backend is not None else "none",
              "agent": None, "notes": [], "review_required": False}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", effect="None: the measures are superseded by the "
                      "going-concern fail.", reason="Skipped: agent triggers are skipped after a going-concern fail")
        return result
    if not col or icr.get("not_applicable") or col.get("ebit_concept") not in OPERATING_INCOME_CONCEPTS:
        result.update(status="not_triggered", decided_by="tags", effect="None.",
                      reason=("ICR is n/a: trigger ICR T3 not set off" if icr and icr.get("not_applicable") else
                              f"EBIT is {(col or {}).get('ebit_concept') or 'not tagged'}, not operating income: "
                              "trigger ICR T3 not set off"))
        return result
    fx = icr_t3_facts(col)
    t = icr_t3_passages(data)
    result.update(fired=True, inputs=fx, passages=t["passages"], sections=t["sections"])
    if not t["passages"]:
        checked = {"status": "pretax_not_found", "pretax": None, "interest_expense": None, "items_between": [],
                   "ebit": None, "status_consistent": True}
        decided_by = "no_text"
        result["notes"].append("No income statement, MD&A or note passage mentions pre-tax income or non-operating "
                               "items: the agent was not called.")
    else:
        require_agent(backend, ICR_T3_ID, AGENT)
        checked, decided_by = _run_agent(result, t, data, fx, session, backend, case_id)
    status = checked["status"]
    disagree = not checked["status_consistent"]
    unresolved = checked["ebit"] is None or disagree
    why = ("the agent's answer and the verified evidence disagree" if disagree else
           NO_INTEREST if checked["pretax"] else NOT_FOUND)
    result.update(status=status, decided_by=decided_by, pretax=checked["pretax"],
                  interest_expense=checked["interest_expense"], items_between=checked["items_between"],
                  ebit=checked["ebit"], reason=_reason(fx, checked), unresolved=unresolved, disagreement=disagree,
                  review_required=unresolved, unresolved_why=why if unresolved else None,
                  effect=("ICR recomputed with EBIT = pre-tax income + interest expense" if checked["ebit"] is not None
                          else "None: EBIT stays operating income, the ICR score stays as calculated")
                  + (f"; unresolved ({why}), so ICR is escalated to an analyst, who accepts the score or overrides it."
                     if unresolved else "; resolved (the agent and the verified evidence agree), so no analyst "
                                        "decision is needed."))
    return result


def _run_agent(result, t, data, fx, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_ICR_T3_PROMPT,
                            icr_t3_inputs(t, data["meta"], fx))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's ICR T3 output was rejected ({agent['validation_detail']}); "
                                 "the run was not recorded. Run it again.")
    checked = validate_icr_t3(run.parsed, t, fx)
    agent.update(rationale=run.parsed.get("rationale"), missing_information=run.parsed.get("missing_information") or [],
                 held_back=checked["held_back"], derived_status=checked["status"],
                 stated_status=checked["stated_status"], status_consistent=checked["status_consistent"])
    if checked["held_back"]:
        result["notes"].append(f"{len(checked['held_back'])} agent item(s) held back by validation.")
    if not checked["status_consistent"]:
        result["notes"].append(f"The agent stated {str(checked['stated_status']).upper()} but its verified evidence "
                               f"supports {checked['status'].upper()}; the verified evidence decides.")
    return checked, "agent"


# --- the measure -------------------------------------------------------------------------------------------------

def apply_icr_t3(fo: list[dict], t: dict | None) -> list[dict]:
    """ICR with the trigger's outcome on its latest column (a copy; the inputs stay untouched): EBIT rebuilt as pre-tax
    income + interest expense and the ratio and score recomputed; otherwise EBIT stays operating income. The column
    keeps the figures as tagged."""
    if not t or not t.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "icr":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        col = m["columns"][0]
        adj = {"trigger": "ICR T3", "calculated": col["score"], "ratio_calculated": col["ratio"], "recomputed": None,
               "ebit_operating_income": col["ebit"], "ebit": t.get("ebit"), "outcome": t["status"]}
        done = None
        if t.get("ebit") is not None and col.get("fixed_charges"):
            before = col["ebit"]
            col.setdefault("ebit_reported", before)
            col["ebit_concept_reported"] = col["ebit_concept"]
            col["ebit"], col["ebit_concept"] = t["ebit"], "EBIT (pre-tax income + interest, from the filing: ICR T3)"
            col["ebitr"] = col["ebit"] + col["rent"]
            col["ratio"] = col["ebitr"] / col["fixed_charges"]
            col["score"] = adj["recomputed"] = risk_score(col["ratio"], ICR_BANDS)
            done = (f"EBIT {_m(before)} (operating income) → {_m(col['ebit'])} (pre-tax income + interest expense): "
                    f"ICR {adj['ratio_calculated']:.2f}x → {col['ratio']:.2f}x, score {adj['calculated']:g} → "
                    f"{col['score']:g}")
        adj["score"] = col["score"]
        col["ebit_adjustment"] = adj
        add_trigger_note(m, f"ICR trigger T3 (EBIT from operating income, {col['heading']}): "
                         + (done or t["reason"][:1].lower() + t["reason"][1:]),
                         "trigger ICR T3: " + ("recomputed" if done else "operating income kept"),
                         t.get("unresolved", True), t.get("unresolved_why") or NOT_FOUND)
        out.append(m)
    return out


def items_evidence(t: dict) -> list[dict]:
    """The verified quotes, for the page."""
    out = []
    if t.get("pretax"):
        out.append({"fact": f"Pre-tax income: {_m(t['pretax']['amount'])}", **t["pretax"]["evidence"]})
    ie = t.get("interest_expense") or {}
    if ie.get("evidence"):
        out.append({"fact": f"Interest expense: {_m(ie['amount'])}", **ie["evidence"]})
    out += [{"fact": f"Between operating and pre-tax income: {e['description']} ({e['kind']}, {_m(e['amount'])})",
             **e["evidence"]} for e in t.get("items_between") or []]
    return out

