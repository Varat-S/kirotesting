"""FO Trigger Agent, DSCR T3: cash taxes paid far from current tax expense (refunds or one-off payments distort cash
flow).

DSCR deducts cash taxes paid (net of refunds) from EBITDAR. When |cash taxes paid − current tax expense| is more than
5% of CFADS (cash flow available for debt service, the DSCR numerator), a refund or a one-off payment may be in the
year's cash taxes. T3 runs after T2 (CFADS is the numerator after T2's CapEx correction) and before T1, whatever DSCR
scored, and only when the pre-checks allow it (``payload.gate.agent_triggers`` is "enabled"). Same three steps as
T1 and T2:

1. ``t3_passages`` (deterministic) collects what the agent checks: the paragraphs and table rows of the income tax
   note and MD&A (income taxes, liquidity, cash flows) that mention taxes paid, refunds, settlements, examinations,
   one-time payments, NOLs, bonus depreciation, R&D expensing, tax credits or the CAMT (ids X01…).
2. The Trigger Agent (prompt ``fo_trigger_dscr_t3``) ALWAYS reads them (no agent, or a rejected output, refuses the
   run). Every fact is quoted verbatim. Only a filing with none of those passages has nothing to read (no model call:
   no explanation, so low data confidence).
3. ``validate_t3`` keeps only verbatim evidence that says what it must, and RE-DERIVES the outcome, first match wins:

   - one-off quantified: input correction, the amounts are backed out of cash taxes paid (a payment added back, a
     refund subtracted) and DSCR recomputed; a settlement counts only if the filing says it was paid in cash;
   - the gap is structural (NOLs, bonus depreciation, R&D expensing, purchased tax credits, CAMT): no adjustment;
   - a one-off mentioned but not quantified, or no explanation given: the score is held, and DSCR is flagged as low
     data confidence.

As with T1 and T2, the trigger escalates DSCR only when it is unresolved: the agent's own answer and its verified
evidence disagree.
"""

from __future__ import annotations

import copy
import re

from app.prompts.fo_prompts import FO_DSCR_T3_PROMPT, FO_DSCR_T3_SCHEMA, FO_DSCR_T3_TEMPLATE

from .covenants import DEBT_TITLE, LIQUIDITY_TITLE, MDNA_END, NOTE_HEADING, OTHER_NOTE_TITLE, SUBSEQUENT_TITLE
from .dscr_t2 import FORECAST
from .dscr_triggers import _ev, _headline_index, _held, _millions, _quoted, _value, add_trigger_note, collect_passages
from .measures import DSCR_BANDS, risk_score
from .precheck import DATA_NOTICE, PrecheckAgentError, call_agent, require_agent
from .scoring import headline_score

T3_VERSION = "fo-trigger-dscr-t3-1.0.0"
T3_ID = "dscr_t3"
T3_SHARE = 0.05  # |cash taxes paid − current tax expense| above this share of CFADS sets the trigger off
AGENT = "Trigger Agent"

SECTIONS = [("tax_note", "Income tax note"), ("mdna_tax", "MD&A: income taxes, liquidity and cash flows")]
LIMITS = {"tax_note": 25, "mdna_tax": 15}
TAX_TITLE = re.compile(r"income tax|\btaxes\b|tax (provision|expense)", re.I)
CASH_FLOW_TITLE = re.compile(r"cash flows?\b", re.I)
# Tax-note and MD&A paragraphs are read only when they mention one of these.
T3_TERMS = re.compile(r"(income )?taxes paid|cash (paid|payments?) for (income )?taxes|cash taxes|refund\w*|"
                      r"settle\w*|\bIRS\b|tax(ing)? authorit\w*|tax (audit|examination)s?|one-?time|"
                      r"non-?recurring|transition tax|install?ments?|"
                      r"current (tax|income tax|provision)|net operating loss|\bNOLs?\b|carry-?forward|"
                      r"bonus depreciation|accelerated depreciation|research and (experimental|development)|"
                      r"section 174|\bR&D\b|tax credits?|transferab\w*|alternative minimum tax|\bCAMT\b|"
                      r"deferred tax", re.I)

# --- what a quote must say ---------------------------------------------------------------------------------------
KIND_WORDS = {
    "refund": (re.compile(r"refund\w*|\breceived\b", re.I), "the quote doesn't mention a refund"),
    "settlement": (re.compile(r"settle\w*|closing agreement|resolution|resolv\w*|audit|examination", re.I),
                   "the quote doesn't mention a settlement with a tax authority"),
    "one_off_payment": (re.compile(r"one-?time|non-?recurring|transition tax|install?ments?|deferr\w*|repatriat\w*|"
                                   r"\bpayments?\b|\bpaid\b", re.I),
                        "the quote doesn't mention a one-off tax payment"),
}
PAID_IN_CASH = re.compile(r"\bpaid\b|\bcash\b|remitt\w*|\bpayments?\b", re.I)
STRUCTURAL = {
    "nol": re.compile(r"net operating loss|\bNOLs?\b|loss carry-?forwards?|carry-?forwards?", re.I),
    "bonus_depreciation": re.compile(r"bonus depreciation|accelerated (tax )?depreciation|(immediate|full) "
                                     r"expensing", re.I),
    "rd_expensing": re.compile(r"research and (experimental|development)|\bR&D\b|section 174|research "
                               r"(costs|expenditures)", re.I),
    "purchased_credits": re.compile(r"transferab\w*|purchas\w* (of )?(\w+ ){0,3}credits?|credits? (purchased|"
                                    r"acquired)|section 6418", re.I),
    "camt": re.compile(r"alternative minimum tax|\bCAMT\b|minimum tax", re.I),
}
STRUCTURAL_LABELS = {"nol": "NOLs", "bonus_depreciation": "bonus depreciation", "rd_expensing": "R&D expensing",
                     "purchased_credits": "purchased tax credits", "camt": "CAMT"}
ONE_OFF = re.compile("|".join(p.pattern for p, _ in KIND_WORDS.values()), re.I)

OUTCOMES = {
    "one_off_quantified": "One-off quantified: input correction, backed out of cash taxes paid, DSCR recomputed",
    "structural": "The gap is structural: no adjustment",
    "low_confidence": "A one-off mentioned but not quantified, or no explanation given: score held, low data "
                      "confidence",
}
STATUS_TEXT = {"skipped": "Skipped: agent triggers are skipped after a going-concern fail"}
RULES = [("One-off quantified (a settlement only if the filing says it was paid in cash)",
          "Input correction: backed out of cash taxes paid (a payment added back, a refund subtracted), DSCR "
          "recomputed"),
         ("The gap is structural (NOLs, bonus depreciation, R&D expensing, purchased tax credits, CAMT)",
          "No adjustment"),
         ("A one-off mentioned but not quantified, or no explanation given", "Hold the score: low data confidence"),
         ("Unresolved: the agent's answer and the verified evidence disagree",
          "DSCR escalated to an analyst, who accepts the score or overrides it")]


# --- the passages ------------------------------------------------------------------------------------------------

def _section_after(item: str, heading: str, current: str | None, numbered: bool) -> str | None:
    """The searched section a heading opens, keeps or closes: MD&A income taxes, liquidity and cash flows (Item 7)
    and the income tax note."""
    if item == "7":
        if TAX_TITLE.search(heading) or LIQUIDITY_TITLE.search(heading) or CASH_FLOW_TITLE.search(heading):
            return "mdna_tax"
        if current == "mdna_tax" and not MDNA_END.search(heading):
            return current
        return None
    if numbered and not NOTE_HEADING.match(heading):
        return current  # a sub-heading inside the current note
    if TAX_TITLE.search(heading) and len(heading) <= 80:
        return "tax_note"
    if numbered or OTHER_NOTE_TITLE.search(heading) or DEBT_TITLE.search(heading) or SUBSEQUENT_TITLE.search(heading):
        return None
    return current


def t3_passages(data: dict) -> dict:
    """The income tax note's and MD&A's paragraphs and tables that mention a T3 term; ids X01… in document order."""
    return collect_passages(data, "X", LIMITS, SECTIONS, T3_TERMS, _section_after)


def tax_facts(col: dict) -> dict:
    """What the engine counted for the triggering column."""
    paid, current, cfads = col.get("cash_taxes"), col.get("current_tax"), col.get("cash_flow")
    gap = paid - current if paid is not None and current is not None else None
    return {"heading": col["heading"], "period": col["period"], "period_end": col["period"].split("--")[1],
            "cash_taxes": paid, "cash_taxes_concept": col.get("cash_taxes_concept"), "current_tax": current,
            "current_tax_concept": col.get("current_tax_concept"), "gap": gap, "cfads": cfads,
            "threshold": abs(cfads) * T3_SHARE if cfads is not None else None,
            "gap_share": abs(gap) / abs(cfads) if gap is not None and cfads else None,
            "ratio": col.get("ratio"), "score": col.get("score")}


def t3_inputs(t3: dict, meta: dict, tx: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": tx["period_end"]},
        "dscr": {"column": tx["heading"], "ratio": round(tx["ratio"], 2) if tx["ratio"] is not None else None,
                 "score": tx["score"]},
        "cash_taxes_paid": {"amount_millions": _millions(tx["cash_taxes"]), "source": tx["cash_taxes_concept"],
                            "note": "net of refunds; a negative amount is a net refund"},
        "current_tax_expense": {"amount_millions": _millions(tx["current_tax"]), "source": tx["current_tax_concept"]},
        "gap": {"amount_millions": _millions(tx["gap"]), "cash_taxes_paid_minus_current_tax_expense": True},
        "cfads": {"amount_millions": _millions(tx["cfads"]), "trigger": f"|gap| above {T3_SHARE:.0%} of CFADS"},
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t3["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t3["passages"]],
    }


# --- validation --------------------------------------------------------------------------------------------------

def _one_off(o, by_id, held):
    """One verified, quantified one-off, or None (held back)."""
    kind = o.get("kind")
    item = {"item_id": o.get("item_id"), "kind": kind, "description": o.get("description")}
    words, why_not = KIND_WORDS.get(kind, KIND_WORDS["one_off_payment"])
    e = o.get("evidence")
    p, why = _quoted(e, by_id, words, why_not)
    value = None
    if not why and FORECAST.search(e["quote"]):
        why = "the quote reads as a forecast, not the year's cash taxes"
    if not why:
        value, why = _value(e, p)
    if why:
        held.append(_held(item, "amount", e, why))
        return None
    entry = {**item, "amount": value, "evidence": _ev(e, p), "paid_in_cash_evidence": None}
    cash = o.get("paid_in_cash")
    if cash:
        cp, why = _quoted(cash, by_id, PAID_IN_CASH, "the quote doesn't say it was paid in cash")
        if why:
            held.append(_held(item, "paid in cash", cash, why))
        else:
            entry["paid_in_cash_evidence"] = _ev(cash, cp)
    if kind == "settlement" and not (entry["paid_in_cash_evidence"] or PAID_IN_CASH.search(e["quote"])):
        held.append(_held(item, "paid in cash", cash or e, "a settlement counts only if the filing says it was paid "
                                                           "in cash"))
        return None
    entry["effect"] = -value if kind == "refund" else value  # what comes out of cash taxes paid
    return entry


def validate_t3(parsed: dict, t3: dict, tx: dict) -> dict:
    """Keep the evidence that holds up and re-derive the outcome, first match wins: one-off quantified (backed out of
    cash taxes paid), structural (no adjustment), else low data confidence (score held)."""
    by_id = {p["passage_id"]: p for p in t3["passages"]}
    held, notes = [], []
    one_offs = [e for o in parsed.get("one_offs") or [] for e in [_one_off(o, by_id, held)] if e]
    structural = []
    for i, s in enumerate(parsed.get("structural") or [], 1):
        reason = s.get("reason")
        p, why = _quoted(s, by_id, STRUCTURAL.get(reason, STRUCTURAL["nol"]),
                         f"the quote doesn't mention {STRUCTURAL_LABELS.get(reason, reason)}")
        if why:
            held.append(_held({"item_id": f"S{i}", "kind": reason}, "structural", s, why))
        else:
            structural.append({"item_id": f"S{i}", "reason": reason, "label": STRUCTURAL_LABELS[reason], **_ev(s, p)})
    unquantified = []
    for i, u in enumerate(parsed.get("unquantified") or [], 1):
        p, why = _quoted(u, by_id, ONE_OFF, "the quote mentions no refund, settlement or one-off payment")
        if why:
            held.append(_held({"item_id": f"U{i}"}, "one-off mentioned", u, why))
        else:
            unquantified.append({"item_id": f"U{i}", **_ev(u, p)})
    backed_out = sum(e["effect"] for e in one_offs)
    if one_offs:
        status = "one_off_quantified"
        if structural:
            notes.append("A structural reason is given too, but a quantified one-off comes first: it is backed out.")
    elif structural:
        status = "structural"
    else:
        status = "low_confidence"
    return {"status": status, "one_offs": one_offs, "structural": structural, "unquantified": unquantified,
            "backed_out": backed_out if one_offs else 0.0, "held_back": held,
            "stated_status": parsed.get("status"), "status_consistent": parsed.get("status") == status,
            "validation_notes": notes}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_DSCR_T3_PROMPT, template=FO_DSCR_T3_TEMPLATE, response_schema=FO_DSCR_T3_SCHEMA,
                      role="analyze", label="Financial Overview Trigger Agent: DSCR T3")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

def _reason(status: str, tx: dict, checked: dict, held_one_offs: bool) -> str:
    if status == "one_off_quantified":
        parts = [f"{'refund' if e['kind'] == 'refund' else e['kind'].replace('_', ' ')} of "
                 f"{e['amount'] / 1e6:,.1f} million ({e['evidence']['passage_id']})" for e in checked["one_offs"]]
        return (f"One-off quantified: {'; '.join(parts)} backed out of cash taxes paid (cash taxes "
                f"{tx['cash_taxes'] / 1e6:,.1f} → {(tx['cash_taxes'] - checked['backed_out']) / 1e6:,.1f} million), "
                "DSCR recomputed")
    if status == "structural":
        reasons = ", ".join(dict.fromkeys(f"{e['label']} ({e['passage_id']})" for e in checked["structural"]))
        return f"The gap is structural: {reasons}: no adjustment"
    if checked["unquantified"] or held_one_offs:
        ids = ", ".join(e["passage_id"] for e in checked["unquantified"])
        return ("A one-off is mentioned but not quantified" + (f" ({ids})" if ids else "")
                + ": score held, low data confidence")
    return "No explanation given for the gap: score held, low data confidence"


def dscr_t3_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None, mode: str = "live",
                    case_id: str | None = None) -> dict:
    """The DSCR T3 trigger result recorded in the run payload (``payload.triggers.dscr_t3``). ``metrics`` are the
    scored measures (after T2); the trigger reads DSCR's headline column (the forward column first)."""
    dscr = next((m for m in metrics if m["key"] == "dscr"), None)
    score, heading = headline_score(dscr) if dscr else (None, None)
    result = {"trigger": T3_ID, "measure": "dscr", "version": T3_VERSION, "share_threshold": T3_SHARE,
              "score": score, "column": heading, "fired": False, "mode": mode if backend is not None else "none",
              "agent": None, "notes": [], "review_required": False, "data_confidence": None}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", reason=STATUS_TEXT["skipped"],
                      effect="None: the measures are superseded by the going-concern fail.")
        return result
    if score is None:
        result.update(status="not_triggered", decided_by="score", reason="DSCR has no score: trigger T3 not set off",
                      effect="None.")
        return result
    tx = tax_facts(dscr["columns"][_headline_index(dscr)])
    result["taxes"] = tx
    missing = [n for n, v in (("cash taxes paid", tx["cash_taxes"]), ("current tax expense", tx["current_tax"]),
                              ("cash flow for debt service", tx["cfads"])) if v is None]
    if missing:
        result.update(status="not_triggered", decided_by="taxes", effect="None.",
                      reason=f"No {' or '.join(missing)} tagged: trigger T3 not set off")
        return result
    if abs(tx["gap"]) <= tx["threshold"]:
        result.update(status="not_triggered", decided_by="taxes", effect="None.",
                      reason=f"Cash taxes paid differ from current tax expense by {abs(tx['gap']) / 1e6:,.1f} million "
                             f"({tx['gap_share']:.1%} of CFADS), not more than {T3_SHARE:.0%}: trigger T3 not set off")
        return result
    t3 = t3_passages(data)
    result.update(fired=True, passages=t3["passages"], sections=t3["sections"])
    if not t3["passages"]:
        checked = {"status": "low_confidence", "one_offs": [], "structural": [], "unquantified": [],
                   "backed_out": 0.0, "status_consistent": True, "held_back": []}
        decided_by = "no_text"
        result["notes"].append("No income tax note or MD&A tax passage to read: the agent was not called. No "
                               "explanation for the gap: the score is held, with low data confidence.")
    else:
        require_agent(backend, T3_ID, AGENT)
        checked, decided_by = _run_agent(result, t3, data, tx, session, backend, case_id)
    status = checked["status"]
    unresolved = not checked["status_consistent"]
    held_one_offs = any(h.get("fact") == "amount" for h in checked.get("held_back") or [])
    result.update(status=status, decided_by=decided_by, one_offs=checked["one_offs"],
                  structural=checked["structural"], unquantified=checked["unquantified"],
                  backed_out=checked["backed_out"], reason=_reason(status, tx, checked, held_one_offs),
                  data_confidence="low" if status == "low_confidence" else None,
                  unresolved=unresolved, review_required=unresolved,
                  effect=("DSCR recomputed with the one-offs backed out of cash taxes paid" if status ==
                          "one_off_quantified" else "None: the DSCR score stays as calculated"
                          + (", flagged as low data confidence" if status == "low_confidence" else ""))
                  + ("; unresolved (the agent's answer and the verified evidence disagree), so DSCR is escalated to "
                     "an analyst, who accepts the score or overrides it." if unresolved else
                     "; resolved (the agent and the verified evidence agree), so no analyst decision is needed."))
    return result


def _run_agent(result, t3, data, tx, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_DSCR_T3_PROMPT,
                            t3_inputs(t3, data["meta"], tx))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's DSCR T3 output was rejected ({agent['validation_detail']}); "
                                 "the run was not recorded. Run it again.")
    checked = validate_t3(run.parsed, t3, tx)
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

def apply_t3(fo: list[dict], t3: dict | None) -> list[dict]:
    """DSCR with the trigger's outcome on its headline column (a copy; the inputs stay untouched): quantified
    one-offs are backed out of cash taxes paid and the ratio and score recomputed; with low data confidence the score
    is held and the measure flagged. The column keeps the figures as tagged. The measure is marked for escalation
    only when the trigger is unresolved."""
    if not t3 or not t3.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "dscr":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        col = m["columns"][_headline_index(m)]
        backed_out = t3.get("backed_out") or 0.0
        adj = {"trigger": "T3", "calculated": col["score"], "ratio_calculated": col["ratio"], "recomputed": None,
               "cash_taxes_reported": col["cash_taxes"], "backed_out": backed_out, "outcome": t3["status"]}
        done = None
        if t3["status"] == "one_off_quantified" and backed_out and col["cash_flow"] is not None:
            col["cash_taxes_reported"] = col["cash_taxes"]
            col.setdefault("cash_flow_reported", col["cash_flow"])  # as tagged: before T2 too
            col["cash_taxes"] -= backed_out
            col["cash_flow"] += backed_out
            col["ratio"] = col["cash_flow"] / col["debt_service"] if col["debt_service"] else None
            col["score"] = adj["recomputed"] = risk_score(col["ratio"], DSCR_BANDS)
            done = (f"{abs(backed_out) / 1e6:,.1f} million of one-off "
                    + ("payments added back to" if backed_out > 0 else "refunds taken out of")
                    + f" cash flow: DSCR {adj['ratio_calculated']:.2f}x → "
                    + (f"{col['ratio']:.2f}x" if col["ratio"] is not None else "no debt service") + ", score "
                    f"{adj['calculated']:g} → " + (f"{col['score']:g}" if col["score"] is not None else "none"))
        adj["score"] = col["score"]
        col["tax_adjustment"] = adj
        if t3.get("data_confidence") == "low":
            m["data_confidence"] = "low"
            m["data_confidence_note"] = ("Low data confidence (trigger T3): cash taxes paid differ from current tax "
                                         "expense by more than 5% of CFADS, and the filing doesn't quantify why.")
        gap_share = (t3.get("taxes") or {}).get("gap_share")
        add_trigger_note(m, (f"DSCR trigger T3 (cash taxes vs current tax expense: gap {gap_share:.1%} of CFADS > "
                             f"{T3_SHARE:.0%}, {col['heading']}): "
                             + (done or t3["reason"][:1].lower() + t3["reason"][1:])),
                         "trigger T3: " + ("recomputed" if done else "low data confidence"
                                           if t3["status"] == "low_confidence" else "no adjustment"),
                         t3.get("unresolved", True))
        out.append(m)
    return out
