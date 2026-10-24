"""FO Trigger Agent, Debt/EBITDAR T4: total debt comes from a fallback, so the debt definition may be off.

``measures.total_debt`` takes a tagged total only when the balance sheet doesn't present the current and non-current
debt lines, and adds finance lease liabilities when the balance sheet shows them as lines of their own. Either route
(the total tag skipped, or finance leases added) is a fallback (``debt_fallback`` on the column): finance leases can be
counted twice, or an instrument missed. T4 fires when finance leases were added, or when a skipped total differs from
the balance-sheet lines by more than 1% (when the two agree, they already confirm the debt). T4 runs on Debt/EBITDAR's headline column FIRST among the Debt/EBITDAR triggers
(debt feeds the gross and net scores T2 compares and the with-leases score T3 compares), only when the pre-checks allow
it (``payload.gate.agent_triggers`` is "enabled"). Same three steps as the other triggers:

1. ``leverage_t4_passages`` (deterministic) collects what the agent checks: the paragraphs and table rows of the balance
   sheet, the debt note and the lease note that mention debt, borrowings or finance leases (ids B01…).
2. The Trigger Agent (prompt ``fo_trigger_leverage_t4``) ALWAYS reads them (no agent, or a rejected output, refuses
   the run). Every amount is quoted verbatim. Only a filing with none of those passages has nothing to read (no model
   call: nothing to reconcile, so unresolved).
3. ``validate_leverage_t4`` keeps only verbatim evidence that says what it must and RE-DERIVES the outcome. Code does the
   arithmetic: the debt-note instruments (at carrying amount: borrowings and finance leases, less discounts and
   issuance costs, plus premiums) must add up to the note's total, and the total must tie to the balance sheet's debt
   lines (within 1%). Finance leases are added only when they aren't already in the total (the lease note's amount,
   else the tagged one):

   - reconciles: Debt/EBITDAR recomputed with the reconciled total debt (resolved);
   - doesn't reconcile: unresolved, so Debt/EBITDAR is escalated to an analyst.

Debt/EBITDAR is escalated when unresolved, and, as for every trigger, when the agent's own answer and its verified
evidence disagree. Only Debt/EBITDAR changes: RCF / debt and the liquidity measure keep the debt as calculated.
"""

from __future__ import annotations

import copy
import re

from app.prompts.fo_prompts import FO_LEVERAGE_T4_PROMPT, FO_LEVERAGE_T4_SCHEMA, FO_LEVERAGE_T4_TEMPLATE

from .covenants import DEBT_TITLE, NOTE_HEADING, OTHER_NOTE_TITLE
from .dscr_triggers import _ev, _held, _millions, _quoted, _value, add_trigger_note, collect_passages, stated_amount
from .icr_t3 import OTHER_STATEMENT
from .leverage_triggers import _headline_index
from .measures import LEVERAGE_MEASURES
from .precheck import DATA_NOTICE, PrecheckAgentError, call_agent, require_agent
from .scoring import headline_score, leverage_settings, score_leverage

LEVERAGE_T4_VERSION = "fo-trigger-leverage-t4-1.0.0"
LEVERAGE_T4_ID = "leverage_t4"
TIE_SHARE = 0.01  # the note's total ties to the balance sheet (and the instruments to the total) within this share
AGENT = "Trigger Agent"

SECTIONS = [("balance_sheet", "Balance sheet"), ("debt_note", "Debt note"), ("lease_note", "Lease note")]
LIMITS = {"balance_sheet": 10, "debt_note": 30, "lease_note": 15}
BALANCE_SHEET = re.compile(r"balance sheets?|statements? of financial position", re.I)
LEASE_TITLE = re.compile(r"\bleases?\b|\blease obligations\b", re.I)
LEVERAGE_T4_TERMS = re.compile(
    r"\bdebt\b|borrowings?|\bnotes\b|debentures?|term loans?|credit (facilit\w*|agreement)|revolv\w*|commercial paper|"
    r"finance leas\w*|capital leas\w*|lease obligations?|unamorti[sz]ed|issuance costs?|discounts?|premiums?|"
    r"current (portion|maturities)", re.I)

# --- what a quote must say ---------------------------------------------------------------------------------------
FINANCE_LEASE = re.compile(r"finance leas\w*|capital leas\w*|lease obligations?", re.I)
KIND_WORDS = {
    "borrowing": (re.compile(r"\bnotes?\b|debentures?|bonds?|loans?|facilit\w*|revolv\w*|borrowings?|commercial paper|"
                             r"credit|\bdebt\b|obligations?|securiti[sz]ation|\bABL\b", re.I),
                  "the quote doesn't name a borrowing"),
    "finance_lease": (FINANCE_LEASE, "the quote doesn't mention finance leases"),
    "deduction": (re.compile(r"discount\w*|issuance costs?|(deferred )?financing costs?|unamorti[sz]ed|debt costs?",
                             re.I), "the quote doesn't mention a discount or issuance costs"),
    "addition": (re.compile(r"premium\w*|fair value|hedg\w*|unamorti[sz]ed", re.I),
                 "the quote doesn't mention a premium or other addition"),
}
SIGN = {"borrowing": 1, "finance_lease": 1, "deduction": -1, "addition": 1}
TOTAL = re.compile(r"total|\bdebt\b|borrowings", re.I)
KIND_TEXT = {"borrowing": "Borrowing", "finance_lease": "Finance leases in debt", "deduction": "Less",
             "addition": "Plus"}

STATUS_TEXT = {"skipped": "Skipped: agent triggers are skipped after a going-concern fail"}
RULES = [("The debt-note instruments add up to the note's total, which ties to the balance sheet (finance leases "
          "counted once)",
          "Debt / EBITDAR recomputed with the reconciled total debt (finance leases added only if not already in it)"),
         ("Doesn't reconcile (no total, the instruments don't add up, or the total doesn't tie to the balance sheet)",
          "Unresolved: Debt/EBITDAR escalated to an analyst, who accepts the score or overrides it"),
         ("The agent's answer and the verified evidence disagree",
          "Unresolved: Debt/EBITDAR escalated to an analyst, who accepts the score or overrides it")]


# --- the passages ------------------------------------------------------------------------------------------------

def _section_after(item: str, heading: str, current: str | None, numbered: bool) -> str | None:
    """The searched section a heading opens, keeps or closes: the balance sheet, the debt note and the lease note."""
    if item == "7":
        return None
    if BALANCE_SHEET.search(heading) and len(heading) <= 100:
        return "balance_sheet"
    if OTHER_STATEMENT.search(heading) and len(heading) <= 100:
        return None  # the other primary statements
    if numbered and not NOTE_HEADING.match(heading):
        return current  # a sub-heading, or a line label inside the balance sheet
    if not numbered and current == "balance_sheet":
        return current  # a line label inside the balance sheet
    if DEBT_TITLE.search(heading) and len(heading) <= 80:
        return "debt_note"
    if LEASE_TITLE.search(heading) and len(heading) <= 80:
        return "lease_note"
    if numbered or OTHER_NOTE_TITLE.search(heading):
        return None
    return current


def leverage_t4_passages(data: dict) -> dict:
    """The balance sheet's, the debt note's and the lease note's paragraphs and tables that mention debt, borrowings
    or finance leases; ids B01… in document order."""
    return collect_passages(data, "B", LIMITS, SECTIONS, LEVERAGE_T4_TERMS, _section_after)


def debt_facts(col: dict) -> dict:
    """What the engine counted for the triggering column, and the fallback it took."""
    fb = col.get("debt_fallback") or {}
    skipped, added = fb.get("total_skipped") or {}, fb.get("finance_leases_added") or {}
    return {"heading": col["heading"], "period": col["period"], "period_end": col["period"].split("--")[1],
            "debt": col.get("debt"), "debt_concept": col.get("debt_concept"),
            "borrowings": fb.get("borrowings", col.get("debt")),
            "total_skipped": skipped.get("value"), "total_skipped_concept": skipped.get("concept"),
            "finance_leases_added": added.get("value"), "finance_leases_concept": added.get("concept"),
            "op_leases": col.get("op_leases"), "ebitdar": col.get("ebitdar"),
            "debt_ratio": col["debt_to_ebitdar"].get("ratio"), "net_debt_ratio": col["net_debt_to_ebitdar"].get("ratio"),
            "score": (col.get("composite") or {}).get("score")}


def _skipped_differs(fx: dict) -> bool:
    """A tagged total skipped for the balance-sheet lines sets T4 off only when the two differ by more than the tie
    tolerance: when they agree, two independent figures already confirm the debt."""
    skipped, lines = fx["total_skipped"], fx["borrowings"]
    return skipped is not None and lines is not None and not _near(skipped, lines, lines)


def _fired_by(fx: dict) -> str:
    parts = []
    if _skipped_differs(fx):
        parts.append(f"the tagged total ({fx['total_skipped_concept']}, {fx['total_skipped'] / 1e6:,.1f} million) "
                     f"skipped for the balance sheet's debt lines ({fx['borrowings'] / 1e6:,.1f} million), which it "
                     f"differs from by more than {TIE_SHARE:.0%}")
    if fx["finance_leases_added"] is not None:
        parts.append(f"finance lease liabilities of {fx['finance_leases_added'] / 1e6:,.1f} million added")
    return f"total debt {fx['debt'] / 1e6:,.1f} million from a fallback: " + " and ".join(parts)


def leverage_t4_inputs(t: dict, meta: dict, fx: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": fx["period_end"]},
        "leverage": {"column": fx["heading"], "total_debt_millions": _millions(fx["debt"]),
                     "total_debt_source": fx["debt_concept"],
                     "balance_sheet_debt_lines_millions": _millions(fx["borrowings"]),
                     "tagged_total_skipped": (None if fx["total_skipped"] is None else
                                              {"concept": fx["total_skipped_concept"],
                                               "millions": _millions(fx["total_skipped"])}),
                     "finance_leases_added": (None if fx["finance_leases_added"] is None else
                                              {"concept": fx["finance_leases_concept"],
                                               "millions": _millions(fx["finance_leases_added"])}),
                     "debt_to_ebitdar": round(fx["debt_ratio"], 2) if fx["debt_ratio"] is not None else None,
                     "score": fx["score"],
                     "trigger": "total debt from a fallback (the total tag skipped, or finance leases added)"},
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t["passages"]],
    }


# --- validation --------------------------------------------------------------------------------------------------

def _amount(e, by_id, words, why, item, fact, held):
    """(value, evidence) for a quoted amount that holds up, or None (held back)."""
    p, reason = _quoted(e, by_id, words, why)
    value = None
    if not reason:
        value, reason = _value(e, p)
    if reason:
        held.append(_held(item, fact, e, reason))
        return None
    return value, _ev(e, p)


def _near(a: float, b: float, base: float) -> bool:
    return abs(a - b) <= TIE_SHARE * abs(base)


def _m(v: float) -> str:
    return f"{v / 1e6:,.1f} million"


def validate_leverage_t4(parsed: dict, t: dict, fx: dict) -> dict:
    """Keep the evidence that holds up and re-derive the outcome: reconciles (the instruments add up to the note's
    total, which ties to the balance sheet's debt lines; finance leases counted once), else doesn't reconcile."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    held, notes = [], []
    instruments = []
    for o in parsed.get("instruments") or []:
        kind = o.get("kind")
        item = {"item_id": o.get("item_id"), "kind": kind, "description": o.get("description")}
        words, why = KIND_WORDS.get(kind, KIND_WORDS["borrowing"])
        got = _amount(o.get("evidence"), by_id, words, why, item, "amount", held)
        if got:
            instruments.append({**item, "amount": got[0], "signed": SIGN.get(kind, 1) * got[0], "evidence": got[1]})
    note_total = fl_note = None
    if parsed.get("note_total"):
        got = _amount(parsed["note_total"], by_id, TOTAL, "the quote doesn't name total debt",
                      {"item_id": "TOTAL"}, "note total", held)
        if got:
            note_total = {"amount": got[0], "evidence": got[1]}
    if parsed.get("finance_lease_liabilities"):
        got = _amount(parsed["finance_lease_liabilities"], by_id, FINANCE_LEASE,
                      "the quote doesn't mention finance leases", {"item_id": "FL"}, "finance lease liabilities", held)
        if got:
            fl_note = {"amount": got[0], "evidence": got[1]}
    inside_quote = None
    fin = parsed.get("finance_leases_in_total") or {}
    if fin.get("included"):
        p, why = _quoted(fin.get("evidence"), by_id, FINANCE_LEASE, "the quote doesn't mention finance leases")
        if why:
            held.append(_held({"item_id": "IN"}, "finance leases in the total", fin.get("evidence"), why))
        else:
            inside_quote = _ev(fin["evidence"], p)
    inside = bool(inside_quote) or any(e["kind"] == "finance_lease" for e in instruments)

    summed = sum(e["signed"] for e in instruments) if instruments else None
    total = note_total["amount"] if note_total else summed
    bs, added = fx["borrowings"], fx["finance_leases_added"]
    targets = [bs] + ([bs + added] if inside and added else [])
    fl = fl_note["amount"] if fl_note else (added or 0.0)
    if fl_note and added is not None and not _near(fl_note["amount"], added, added):
        notes.append(f"The lease note's finance lease liabilities ({_m(fl_note['amount'])}) differ from the tagged ones "
                     f"({_m(added)}); the lease note's are used.")
    why = None
    if total is None:
        why = "the debt note's total debt wasn't found (no verified total or instrument amounts)"
    elif note_total and summed is not None and not _near(summed, total, total):
        why = f"the debt-note instruments ({_m(summed)}) don't add up to the note's total ({_m(total)})"
    elif bs is None or not any(_near(total, x, x) for x in targets):
        why = (f"the debt note's total ({_m(total)}) doesn't tie to the balance sheet's debt ("
               + " or ".join(_m(x) for x in targets if x is not None) + ")")
    status = "does_not_reconcile" if why else "reconciles"
    reconciled = None if why else (total if inside else total + fl)
    return {"status": status, "instruments": instruments, "note_total": note_total, "finance_leases_note": fl_note,
            "finance_leases_inside": inside, "finance_leases_inside_evidence": inside_quote, "instruments_sum": summed,
            "finance_leases_added": 0.0 if inside or why else fl, "reconciled_debt": reconciled, "unresolved_why": why,
            "held_back": held, "stated_status": parsed.get("status"),
            "status_consistent": parsed.get("status") == status, "validation_notes": notes}


def agent_reading(parsed: dict, t: dict, fx: dict) -> dict:
    """The agent's answer as it states it (not verified, not applied), for an analyst to compare with the verified
    outcome: "reconciles" makes total debt the debt note's total it gives (else its instruments
    added up), plus finance leases when it doesn't say they are inside. Any other answer changes nothing."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    stated, notes, reconciled = parsed.get("status"), [], None
    if stated == "reconciles":
        total = stated_amount(parsed.get("note_total"), by_id, notes, "the note's total debt")
        amounts = [(o, stated_amount(o.get("evidence"), by_id, notes, o.get("item_id")))
                   for o in parsed.get("instruments") or []]
        if total is None and any(v is not None for _, v in amounts):
            total = sum(SIGN.get(o.get("kind"), 1) * v for o, v in amounts if v is not None)
        inside = bool((parsed.get("finance_leases_in_total") or {}).get("included")) or any(
            o.get("kind") == "finance_lease" for o in parsed.get("instruments") or [])
        fl = stated_amount(parsed.get("finance_lease_liabilities"), by_id, notes, "finance lease liabilities")
        fl = (fx["finance_leases_added"] or 0.0) if fl is None else fl
        if total is None:
            notes.append("it gives no readable total debt or instrument amounts.")
        else:
            reconciled = total if inside else total + fl
    return {"status": stated, "notes": notes, "applies": {"status": stated, "reconciled_debt": reconciled}}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_LEVERAGE_T4_PROMPT, template=FO_LEVERAGE_T4_TEMPLATE, response_schema=FO_LEVERAGE_T4_SCHEMA,
                      role="analyze", label="Financial Overview Trigger Agent: Debt/EBITDAR T4")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

def _reason(status: str, fx: dict, checked: dict) -> str:
    if status == "reconciles":
        lease = ("finance leases already in it" if checked["finance_leases_inside"] else
                 f"+ finance leases {_m(checked['finance_leases_added'])}" if checked["finance_leases_added"]
                 else "no finance leases")
        total = checked["note_total"]["amount"] if checked["note_total"] else checked["instruments_sum"]
        return (f"The debt note ties to the balance sheet (total {_m(total)}, {lease}): total debt {_m(fx['debt'])} → {_m(checked['reconciled_debt'])}, Debt / EBITDAR recomputed")
    return "Unresolved: " + checked["unresolved_why"]


def leverage_t4_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None,
                        mode: str = "live", case_id: str | None = None) -> dict:
    """The Debt/EBITDAR T4 trigger result recorded in the run payload (``payload.triggers.leverage_t4``). ``metrics``
    are the scored measures; the trigger reads Debt/EBITDAR's headline column."""
    lev = next((m for m in metrics if m["key"] == "leverage"), None)
    score, heading = headline_score(lev) if lev else (None, None)
    result = {"trigger": LEVERAGE_T4_ID, "measure": "leverage", "version": LEVERAGE_T4_VERSION,
              "tie_share": TIE_SHARE, "score": score, "column": heading, "fired": False,
              "mode": mode if backend is not None else "none", "agent": None, "notes": [], "review_required": False}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", reason=STATUS_TEXT["skipped"],
                      effect="None: the measures are superseded by the going-concern fail.")
        return result
    if score is None:
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason="Debt/EBITDAR has no score: trigger Debt/EBITDAR T4 not set off")
        return result
    col = lev["columns"][_headline_index(lev)]
    fx = debt_facts(col)
    result["inputs"] = fx
    if not col.get("debt_fallback"):
        result.update(status="not_triggered", decided_by="debt", effect="None.",
                      reason=f"Total debt is from {fx['debt_concept']}, not a fallback: trigger Debt/EBITDAR T4 not "
                             "set off")
        return result
    if fx["finance_leases_added"] is None and not _skipped_differs(fx):
        result.update(status="not_triggered", decided_by="debt", effect="None.",
                      reason=f"The tagged total ({fx['total_skipped_concept']}, {fx['total_skipped'] / 1e6:,.1f} "
                             f"million) skipped for the balance sheet's debt lines ties to them "
                             f"({fx['borrowings'] / 1e6:,.1f} million, within {TIE_SHARE:.0%}), and no finance leases "
                             "were added: trigger Debt/EBITDAR T4 not set off")
        return result
    t = leverage_t4_passages(data)
    result.update(fired=True, fired_by=_fired_by(fx), passages=t["passages"], sections=t["sections"])
    if not t["passages"]:
        checked = {"status": "does_not_reconcile", "instruments": [], "note_total": None, "finance_leases_note": None,
                   "finance_leases_inside": False, "finance_leases_inside_evidence": None, "instruments_sum": None,
                   "finance_leases_added": 0.0, "reconciled_debt": None, "status_consistent": True,
                   "unresolved_why": "no balance-sheet, debt-note or lease-note passage to reconcile"}
        decided_by = "no_text"
        result["notes"].append("No balance-sheet, debt-note or lease-note passage mentions debt or finance leases: the "
                               "agent was not called. Nothing reconciles, so Debt/EBITDAR is unresolved.")
    else:
        require_agent(backend, LEVERAGE_T4_ID, AGENT)
        checked, decided_by = _run_agent(result, t, data, fx, session, backend, case_id)
    status = checked["status"]
    disagree = not checked["status_consistent"]
    unresolved = status == "does_not_reconcile" or disagree
    why = ("the agent's answer and the verified evidence disagree" if disagree else checked["unresolved_why"]) \
        if unresolved else None
    result.update(status=status, decided_by=decided_by, instruments=checked["instruments"],
                  note_total=checked["note_total"], finance_leases_note=checked["finance_leases_note"],
                  finance_leases_inside=checked["finance_leases_inside"],
                  finance_leases_inside_evidence=checked["finance_leases_inside_evidence"],
                  instruments_sum=checked["instruments_sum"], finance_leases_added=checked["finance_leases_added"],
                  reconciled_debt=checked["reconciled_debt"],
                  reason=_reason(status, fx, checked), unresolved=unresolved, disagreement=disagree,
                  review_required=unresolved, unresolved_why=why,
                  effect=("Debt / EBITDAR recomputed with the reconciled total debt" if status == "reconciles"
                          else "None: the Debt/EBITDAR score stays as calculated")
                  + (f"; unresolved ({why}), so Debt/EBITDAR is escalated to an analyst, who accepts the score or "
                     "overrides it." if unresolved else
                     "; resolved (the agent and the verified evidence agree), so no analyst decision is needed."))
    return result


def _run_agent(result, t, data, fx, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_LEVERAGE_T4_PROMPT,
                            leverage_t4_inputs(t, data["meta"], fx))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's Debt/EBITDAR T4 output was rejected "
                                 f"({agent['validation_detail']}); the run was not recorded. Run it again.")
    checked = validate_leverage_t4(run.parsed, t, fx)
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

def apply_leverage_t4(fo: list[dict], t: dict | None) -> list[dict]:
    """Debt/EBITDAR with the trigger's outcome on its headline column (a copy; the inputs stay untouched): total debt
    replaced by the reconciled total, lease-adjusted debt and net debt recomputed, both ratios recomputed and rescored
    with the settings the measure was scored with. The column keeps the debt as calculated. The measure is marked for
    escalation when the trigger is unresolved."""
    if not t or not t.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "leverage":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        col = m["columns"][_headline_index(m)]
        adj = {"trigger": "Debt/EBITDAR T4", "outcome": t["status"], "calculated": col["composite"]["score"],
               "ratio_calculated": col["debt_to_ebitdar"]["ratio"],
               "net_ratio_calculated": col["net_debt_to_ebitdar"]["ratio"], "debt_calculated": col["debt"],
               "recomputed": None}
        done = None
        if t["status"] == "reconciles" and t.get("reconciled_debt") is not None:
            col["debt_before_trigger"] = col["debt"]
            col["debt"] = t["reconciled_debt"]
            col["adj_debt"] = col["debt"] + (col.get("op_leases") or 0.0)
            col["adj_net_debt"] = col["adj_debt"] - col["cash"] if col.get("cash") is not None else None
            for key, _, numerator, denominator in LEVERAGE_MEASURES:
                num, den = col[numerator], col[denominator]
                col[key]["ratio"] = num / den if num is not None and den and den > 0 else None
            score_leverage(col, leverage_settings(m))
            adj["recomputed"] = col["composite"]["score"]
            done = (f"total debt {adj['debt_calculated'] / 1e6:,.1f} → {col['debt'] / 1e6:,.1f} million (reconciled to "
                    "the debt note and the balance sheet), composite leverage score "
                    + (f"{adj['calculated']:g} → " if adj["calculated"] is not None else "")
                    + (f"{adj['recomputed']:g}" if adj["recomputed"] is not None else "none"))
        adj["debt"], adj["score"] = col["debt"], col["composite"]["score"]
        col["debt_adjustment"] = adj
        add_trigger_note(m, (f"Debt/EBITDAR trigger T4 ({t.get('fired_by')}, {col['heading']}): "
                             + (done or "no adjustment")),
                         "trigger Debt/EBITDAR T4: " + ("reconciled" if t["status"] == "reconciles"
                                                        else "doesn't reconcile"),
                         t.get("unresolved", True), t.get("unresolved_why") or "doesn't reconcile")
        out.append(m)
    return out
