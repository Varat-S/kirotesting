"""FO Trigger Agent, ICR T2: interest from a net-interest or operating-interest tag (it may be net of interest income,
or classified unusually).

ICR = (EBIT + rent) / (interest incurred + rent). The engine builds interest incurred on the first interest expense tag
the filing has; when that is a net tag (InterestIncomeExpenseNet, or its nonoperating variant) the figure is net of
interest income, and when it is the operating tag (InterestExpenseOperating, often a captive finance arm's funding
cost) it sits in operating expenses. ICR T2 runs on ICR's latest year, whatever ICR scored, after the DSCR triggers
and before ICR T1 (it corrects the interest base, so T1 judges the corrected ICR), only when the pre-checks allow it
(``payload.gate.agent_triggers`` is "enabled"):

- Net interest is income (no expense tag): ICR is n/a, flagged and not scored as weak cover (no agent needed).
- Otherwise ``icr_t2_passages`` (deterministic) collects the debt note, MD&A and the other notes' paragraphs and
  table rows that mention interest expense or income, or swaps (ids J01…), and the Trigger Agent (prompt
  ``fo_trigger_icr_t2``) ALWAYS reads them. ``validate_icr_t2`` keeps only verbatim evidence and RE-DERIVES the
  interest base, first match: gross interest expense disclosed; else (net tag) net + interest income; else (operating
  tag, interest income only) as tagged; else unresolved. An undesignated swap's mark-to-market is removed; other swap
  effects stay in interest expense. EBIT is not changed, so interest income is never counted as cover.
- An operating tag is flagged as making ICR less meaningful.

ICR is escalated when unresolved (neither gross interest nor interest income disclosed) or when the agent's own answer
and its verified evidence disagree.
"""

from __future__ import annotations

import copy
import re

from app.prompts.fo_prompts import FO_ICR_T2_PROMPT, FO_ICR_T2_SCHEMA, FO_ICR_T2_TEMPLATE

from .covenants import DEBT_TITLE, NOTE_HEADING
from .dscr_triggers import _held, _millions, add_trigger_note, collect_passages
from .icr_triggers import MDNA_SKIP, _amount, verified_swap
from .measures import ICR_BANDS, NET_INTEREST_CONCEPTS, OPERATING_INTEREST_CONCEPTS, risk_score
from .precheck import DATA_NOTICE, PrecheckAgentError, call_agent, require_agent

ICR_T2_VERSION = "fo-trigger-icr-t2-1.0.0"
ICR_T2_ID = "icr_t2"
AGENT = "Trigger Agent"
UNRESOLVED_T2 = "neither gross interest expense nor interest income is disclosed"
OPERATING_NOTE = ("ICR is less meaningful: interest is tagged as operating interest (often a captive finance arm's "
                  "funding cost), so it sits in operating expenses.")

SECTIONS = [("debt_note", "Debt note"), ("mdna", "MD&A"), ("notes", "Other notes")]
LIMITS = {"debt_note": 20, "mdna": 12, "notes": 15}
ICR_T2_TERMS = re.compile(r"interest expense|interest income|interest,? net|net interest|interest and (other|"
                          r"investment|dividend) income|investment income|interest costs?|\bswaps?\b|"
                          r"capitali[sz]ed interest", re.I)
GROSS = re.compile(r"interest expense|interest costs?|interest on (borrowings|debt|(senior )?notes|long-term debt)|"
                   r"interest incurred", re.I)
NET_FIGURE = re.compile(r"interest (expense|income),? net|net interest|net of interest income|interest and other "
                        r"(income|expense)", re.I)
INCOME = re.compile(r"interest income|interest and (dividend|investment|other) income|investment income", re.I)
LABELS = {"gross_disclosed": "GROSS DISCLOSED: RECOMPUTED", "gross_from_income": "NET + INCOME: RECOMPUTED",
          "kept_as_tagged": "KEPT AS TAGGED", "unresolved": "UNRESOLVED", "not_applicable": "ICR N/A: NET INTEREST INCOME",
          "not_triggered": "NOT TRIGGERED", "skipped": "SKIPPED"}
RULES = [("Net interest is income (no interest expense)", "ICR n/a: flagged, not scored as weak cover"),
         ("Gross interest expense disclosed", "Recompute ICR with gross interest expense"),
         ("Only net interest given, interest income disclosed", "Gross = net + interest income, recompute"),
         ("An undesignated swap's mark-to-market in interest expense", "Removed (other swap effects stay)"),
         ("Neither gross interest nor interest income disclosed",
          "Unresolved: ICR escalated to an analyst, who accepts the score or overrides it"),
         ("An operating-interest tag (often a captive finance arm)", "Flagged: ICR less meaningful"),
         ("The agent's answer and the verified evidence disagree",
          "Unresolved: ICR escalated to an analyst, who accepts the score or overrides it")]


# --- the passages ------------------------------------------------------------------------------------------------

def _section_after(item: str, heading: str, current: str | None, numbered: bool) -> str | None:
    """The searched section a heading opens, keeps or closes: MD&A (without its boilerplate), the debt note and
    every other note."""
    if item == "7":
        return None if MDNA_SKIP.search(heading) else "mdna"
    if numbered and not NOTE_HEADING.match(heading):
        return current
    return "debt_note" if DEBT_TITLE.search(heading) and len(heading) <= 80 else "notes"


def icr_t2_passages(data: dict) -> dict:
    """The debt note's, MD&A's and the other notes' paragraphs and tables that mention interest expense or income,
    or swaps; ids J01… in document order."""
    return collect_passages(data, "J", LIMITS, SECTIONS, ICR_T2_TERMS, _section_after)


def icr_t2_facts(col: dict) -> dict:
    """What the engine counted for the latest year: the interest tag and its kind ("net", "operating" or
    "net_income"; None for a gross tag)."""
    concept = col.get("interest_expense_concept")
    kind = ("net_income" if col.get("net_interest_income") is not None else "net" if concept in NET_INTEREST_CONCEPTS
            else "operating" if concept in OPERATING_INTEREST_CONCEPTS else None)
    return {"heading": col["heading"], "period": col["period"], "period_end": col["period"].split("--")[1],
            "kind": kind, "concept": concept, "tagged_expense": col.get("interest_expense"),
            "net_interest_income": col.get("net_interest_income"),
            "capitalised": col.get("interest_capitalised") or 0.0, "interest": col.get("interest"),
            "ebit": col.get("ebit"), "rent": col.get("rent"), "ratio": col.get("ratio"), "score": col.get("score")}


def icr_t2_inputs(t: dict, meta: dict, fx: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": fx["period_end"]},
        "icr": {"column": fx["heading"], "ratio": round(fx["ratio"], 2) if fx["ratio"] is not None else None,
                "score": fx["score"], "ebit_millions": _millions(fx["ebit"]), "rent_millions": _millions(fx["rent"]),
                "interest_incurred_millions": _millions(fx["interest"])},
        "interest_source": {"kind": fx["kind"], "concept": fx["concept"],
                            "amount_millions": _millions(fx["tagged_expense"]),
                            "capitalised_millions": _millions(fx["capitalised"])},
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t["passages"]],
    }


# --- validation --------------------------------------------------------------------------------------------------

def validate_icr_t2(parsed: dict, t: dict, fx: dict) -> dict:
    """Keep the evidence that holds up and re-derive the interest base, first match: gross interest expense
    disclosed; else, for a net tag, net + interest income; else, for an operating tag with interest income disclosed,
    the tagged figure; else unresolved. An undesignated swap's mark-to-market is then removed."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    held = []
    gross = None
    g = parsed.get("gross_interest")
    if g:
        got = _amount(g, by_id, GROSS, "the quote doesn't mention interest expense", {"item_id": "G1"},
                      "gross interest", held)
        if got and NET_FIGURE.search(got[1]["quote"]):
            held.append(_held({"item_id": "G1"}, "gross interest", g,
                              "the quote describes a net figure, not gross interest expense"))
        elif got and fx["kind"] == "net" and fx["tagged_expense"] is not None and got[0] < fx["tagged_expense"]:
            held.append(_held({"item_id": "G1"}, "gross interest", g,
                              f"{got[0] / 1e6:,.1f} million is less than the net interest expense tagged "
                              f"({fx['tagged_expense'] / 1e6:,.1f} million)"))
        elif got:
            gross = {"amount": got[0], "evidence": got[1]}
    income = None
    i = parsed.get("interest_income")
    if i:
        got = _amount(i, by_id, INCOME, "the quote doesn't mention interest income", {"item_id": "N1"},
                      "interest income", held)
        if got:
            income = {"amount": got[0], "evidence": got[1]}
    tagged = fx["tagged_expense"] or 0.0
    if gross:
        status, base = "gross_disclosed", gross["amount"]
    elif income and fx["kind"] == "net":
        status, base = "gross_from_income", tagged + income["amount"]
    elif income and fx["kind"] == "operating":
        status, base = "kept_as_tagged", tagged
    else:
        status, base = "unresolved", tagged
    swap = verified_swap(parsed.get("swap_mark_to_market"), by_id, base, held)
    if swap:
        base -= swap["effect"]
    return {"status": status, "gross": gross, "income": income, "swap": swap, "gross_used": base,
            "interest_change": base + fx["capitalised"] - fx["interest"] if fx["interest"] is not None else 0.0,
            "held_back": held, "stated_status": parsed.get("status"),
            "status_consistent": parsed.get("status") == status}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_ICR_T2_PROMPT, template=FO_ICR_T2_TEMPLATE, response_schema=FO_ICR_T2_SCHEMA,
                      role="analyze", label="Financial Overview Trigger Agent: ICR T2")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

def _m(v):
    return f"{v / 1e6:,.1f} million"


def _reason(status: str, fx: dict, checked: dict) -> str:
    if status == "not_applicable":
        return f"Net interest is income ({_m(fx['net_interest_income'])}): ICR is n/a, flagged and not scored as weak cover"
    swap = checked.get("swap")
    tail = (f"; undesignated swap mark-to-market {swap['direction']} of {_m(swap['amount'])} "
            f"({swap['evidence']['passage_id']}) removed" if swap else "")
    if status == "gross_disclosed":
        return (f"Gross interest expense of {_m(checked['gross']['amount'])} disclosed "
                f"({checked['gross']['evidence']['passage_id']}) instead of {_m(fx['tagged_expense'])} tagged{tail}, "
                "ICR recomputed")
    if status == "gross_from_income":
        income = checked["income"]
        return (f"Only net interest given: gross = {_m(fx['tagged_expense'])} net + {_m(income['amount'])} interest "
                f"income ({income['evidence']['passage_id']}) = {_m(fx['tagged_expense'] + income['amount'])}{tail}, "
                "ICR recomputed")
    if status == "kept_as_tagged":
        return f"Operating interest kept as tagged ({_m(fx['tagged_expense'])}){tail}" + (
            ", ICR recomputed" if swap else ": no adjustment")
    return "Neither gross interest expense nor interest income is disclosed: unresolved" + (
        f"{tail}, ICR recomputed" if swap else "")


def icr_t2_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None, mode: str = "live",
                   case_id: str | None = None) -> dict:
    """The ICR T2 trigger result recorded in the run payload (``payload.triggers.icr_t2``), on ICR's latest year."""
    icr = next((m for m in metrics if m["key"] == "icr"), None)
    col = icr["columns"][0] if icr and icr.get("columns") else None
    result = {"trigger": ICR_T2_ID, "measure": "icr", "version": ICR_T2_VERSION, "score": (col or {}).get("score"),
              "column": (col or {}).get("heading"), "fired": False, "mode": mode if backend is not None else "none",
              "agent": None, "notes": [], "flags": [], "review_required": False}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", effect="None: the measures are superseded by the "
                      "going-concern fail.", reason="Skipped: agent triggers are skipped after a going-concern fail")
        return result
    fx = icr_t2_facts(col) if col else None
    if not fx or fx["kind"] is None:
        concept = (fx or {}).get("concept")
        result.update(status="not_triggered", decided_by="tags", effect="None.",
                      reason=(f"Interest is from {concept}, a gross interest tag" if concept else
                              "Interest is the tagged interest costs incurred, or not tagged")
                      + ": trigger ICR T2 not set off")
        return result
    result.update(fired=True, inputs=fx)
    if fx["kind"] == "net_income":
        result.update(status="not_applicable", decided_by="tags", unresolved=False, disagreement=False,
                      flags=["net_interest_income"], reason=_reason("not_applicable", fx, {}), interest_change=0.0,
                      gross=None, income=None, swap=None, passages=[], sections=[],
                      effect="ICR is n/a: no score, not counted in the composite; resolved, no analyst decision.")
        return result
    if fx["kind"] == "operating":
        result["flags"].append("operating_interest")
    t = icr_t2_passages(data)
    result.update(passages=t["passages"], sections=t["sections"])
    if not t["passages"]:
        checked = {"status": "unresolved", "gross": None, "income": None, "swap": None, "interest_change": 0.0,
                   "gross_used": fx["tagged_expense"], "status_consistent": True}
        decided_by = "no_text"
        result["notes"].append("No debt note, MD&A or note passage mentions interest expense or income: the agent was "
                               "not called.")
    else:
        require_agent(backend, ICR_T2_ID, AGENT)
        checked, decided_by = _run_agent(result, t, data, fx, session, backend, case_id)
    status = checked["status"]
    disagree = not checked["status_consistent"]
    unresolved = status == "unresolved" or disagree
    why = "the agent's answer and the verified evidence disagree" if disagree else UNRESOLVED_T2
    result.update(status=status, decided_by=decided_by, gross=checked["gross"], income=checked["income"],
                  swap=checked["swap"], gross_used=checked["gross_used"], interest_change=checked["interest_change"],
                  reason=_reason(status, fx, checked), unresolved=unresolved, disagreement=disagree,
                  review_required=unresolved, unresolved_why=why if unresolved else None,
                  effect=("ICR recomputed as above" if checked["interest_change"] else
                          "None: the ICR score stays as calculated")
                  + ("; flagged: ICR is less meaningful (operating interest)" if fx["kind"] == "operating" else "")
                  + (f"; unresolved ({why}), so ICR is escalated to an analyst, who accepts the score or overrides it."
                     if unresolved else "; resolved (the agent and the verified evidence agree), so no analyst "
                                        "decision is needed."))
    return result


def _run_agent(result, t, data, fx, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_ICR_T2_PROMPT,
                            icr_t2_inputs(t, data["meta"], fx))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's ICR T2 output was rejected ({agent['validation_detail']}); "
                                 "the run was not recorded. Run it again.")
    checked = validate_icr_t2(run.parsed, t, fx)
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

def apply_icr_t2(fo: list[dict], t: dict | None) -> list[dict]:
    """ICR with the trigger's outcome on its latest column (a copy; the inputs stay untouched): n/a when net interest
    is income; otherwise interest incurred rebuilt on the gross interest base and the ratio and score recomputed. The
    column keeps the figures as tagged. An operating tag is flagged."""
    if not t or not t.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "icr":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        col = m["columns"][0]
        reason = t["reason"][:1].lower() + t["reason"][1:]
        if t["status"] == "not_applicable":
            m["not_applicable"] = t["reason"]
            m.setdefault("flag_notes", []).append(f"ICR n/a (trigger ICR T2): {reason}.")
            add_trigger_note(m, f"ICR trigger T2 ({col['heading']}): {reason}", "trigger ICR T2: n/a", False)
            out.append(m)
            continue
        adj = {"trigger": "ICR T2", "calculated": col["score"], "ratio_calculated": col["ratio"], "recomputed": None,
               "interest_change": t.get("interest_change") or 0.0, "gross_used": t.get("gross_used"),
               "outcome": t["status"]}
        done = None
        if adj["interest_change"] and col["interest"] is not None and col.get("ebitr") is not None:
            before = col["interest"]
            col.setdefault("interest_reported", before)
            col["interest"] += adj["interest_change"]
            col["fixed_charges"] = col["interest"] + col["rent"]
            col["ratio"] = col["ebitr"] / col["fixed_charges"] if col["fixed_charges"] else None
            col["score"] = adj["recomputed"] = risk_score(col["ratio"], ICR_BANDS)
            done = (f"interest incurred {before / 1e6:,.1f} → {col['interest'] / 1e6:,.1f} million: ICR "
                    f"{adj['ratio_calculated']:.2f}x → "
                    + (f"{col['ratio']:.2f}x" if col["ratio"] is not None else "no interest to cover")
                    + f", score {adj['calculated']:g} → " + (f"{col['score']:g}" if col["score"] is not None
                                                              else "none"))
        if t.get("swap"):
            col["swap_mtm_removed"] = t["swap"]["amount"]
        adj["score"] = col["score"]
        col["interest_adjustment"] = adj
        if "operating_interest" in t.get("flags", []):
            m.setdefault("flag_notes", []).append(OPERATING_NOTE)
        add_trigger_note(m, f"ICR trigger T2 (interest from {t['inputs']['concept']}, {col['heading']}): "
                         + (done or reason), "trigger ICR T2: " + ("recomputed" if done else "no adjustment"),
                         t.get("unresolved", True), t.get("unresolved_why") or UNRESOLVED_T2)
        out.append(m)
    return out
