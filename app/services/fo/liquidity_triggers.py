"""FO Trigger Agent, Liquidity T1: the revolver availability at the year-end isn't in the extracted data (not tagged as
LineOfCreditFacilityRemainingBorrowingCapacity, not found in the 10-K text), so the main liquidity source counts as 0
and both scores (liquidity coverage and refinancing coverage) are understated.

Liquidity T1 runs on the liquidity-and-maturity measure's headline column (the latest year-end) after the measures are
scored, only when the pre-checks allow it (``payload.gate.agent_triggers`` is "enabled"), when the measure has a score
and ``revolver_found`` is None. It doesn't fire after a covenant breach not waived (the revolver availability is set to
0 whatever it is) or when the scoring settings hold a manual revolver availability. Same three steps as the other
triggers:

1. ``liquidity_t1_passages`` (deterministic) collects what the agent checks: the paragraphs and table rows of the debt
   note and MD&A liquidity that mention a revolver or credit facility, an asset-based facility or its borrowing base,
   letters of credit, or availability (ids L01…).
2. The Trigger Agent (prompt ``fo_trigger_liquidity_t1``) ALWAYS reads them (no agent, or a rejected output, refuses
   the run). Every fact is quoted verbatim. Only a filing with none of those passages has nothing to read (no model
   call: no revolver, so no adjustment).
3. ``validate_liquidity_t1`` keeps only verbatim evidence that says what it must and RE-DERIVES the outcome for each
   revolving facility, first match wins:

   - availability stated (at the year-end): an input correction with that figure;
   - not stated, but the commitment, the amount drawn and the letters of credit given: availability = commitment −
     drawn − letters of credit; for an asset-based revolver, the lower of this and the borrowing base;
   - the revolver exists, but its availability can't be worked out: unresolved.

   No revolver in the debt note: no adjustment. Availability, drawn amounts, letters of credit and the borrowing base
   must be dated at the year-end (the date in the quote, or in its passage for a table row); a nil amount counts only
   with a quote saying none is outstanding. The revolver's maturity date is checked and recorded; one maturing within
   12 months of the year-end is noted but changes nothing.

Liquidity and debt maturity is escalated when unresolved (any facility can't be worked out), and, as for every
trigger, when the agent's own answer and its verified evidence disagree.
"""

from __future__ import annotations

import copy
import math
import re
from datetime import date

from app.prompts.fo_prompts import FO_LIQUIDITY_T1_PROMPT, FO_LIQUIDITY_T1_SCHEMA, FO_LIQUIDITY_T1_TEMPLATE

from .acquisitions import _date_in_quote
from .covenants import _section_after
from .dscr_triggers import (
    REVOLVER,
    _a_year_after,
    _ev,
    _held,
    _millions,
    _quoted,
    _span,
    _value,
    add_trigger_note,
    collect_passages,
    stated_amount,
)
from .measures import MANUAL_REVOLVER, score_liquidity
from .precheck import DATA_NOTICE, PrecheckAgentError, call_agent, require_agent
from .scoring import COVENANT_RELIEF_UPLIFT, headline_score

LIQUIDITY_T1_VERSION = "fo-trigger-liquidity-t1-1.0.0"
LIQUIDITY_T1_ID = "liquidity_t1"
AGENT = "Trigger Agent"

SECTIONS = [("debt_note", "Debt note"), ("mdna_liquidity", "MD&A: liquidity")]
LIMITS = {"debt_note": 30, "mdna_liquidity": 15}
LIQUIDITY_T1_TERMS = re.compile(
    r"revolv\w*|credit (facilit\w*|agreement)|lines? of credit|\bABL\b|asset-based|borrowing base|letters? of credit|"
    r"borrowing (capacity|availability)|undrawn|unused (commitment|capacity|portion)|"
    r"availab\w* (to us )?(for borrowing|under)|availability", re.I)

# --- what a quote must say ---------------------------------------------------------------------------------------
AVAILABLE = re.compile(r"availab\w*|undrawn|unused|unutilized|(remaining|additional|unused) (borrowing )?capacity|"
                       r"borrowing capacity", re.I)
COMMITMENT = re.compile(r"commitments?|provides? for|aggregate (principal )?amount|borrowing capacity|\bup to\b|"
                        r"maximum|in the amount of|\bsize\b|facilit\w* of|(totaling|totalling|of) \$", re.I)
DRAWN = re.compile(r"outstanding|borrow\w*|drawn|draws?|balance", re.I)
LETTERS = re.compile(r"letters? of credit", re.I)
BORROWING_BASE = re.compile(r"borrowing base", re.I)
ASSET_BASED = re.compile(r"asset[- ]based|borrowing base|\bABL\b", re.I)
MATURES = re.compile(r"matur\w*|expir\w*|terminat\w*|\bdue\b|\bterm of\b", re.I)
# A nil amount: "no amounts outstanding", "undrawn", or a dash in a table row.
NONE_OUTSTANDING = re.compile(r"\bno (\w+ ){0,3}(borrowings?|amounts?|loans?|letters? of credit|balances?|advances?)\b|"
                              r"\bnothing (was )?(drawn|outstanding|borrowed)|\bundrawn\b|had not (borrowed|drawn)|"
                              r"\bnone\b|\bzero\b|\$\s?0\b|\|\s*[—–-]\s*(\||$)", re.I)
FIGURE_WORDS = {"availability": (AVAILABLE, "the quote doesn't show an amount available"),
                "commitment": (COMMITMENT, "the quote doesn't show the facility's commitment"),
                "drawn": (DRAWN, "the quote doesn't show an amount drawn or outstanding"),
                "letters_of_credit": (LETTERS, "the quote doesn't mention letters of credit"),
                "borrowing_base": (BORROWING_BASE, "the quote doesn't mention the borrowing base")}
DATED = ("availability", "drawn", "letters_of_credit", "borrowing_base")  # balances at the year-end
NIL_ALLOWED = ("drawn", "letters_of_credit")
FIGURE_TEXT = {"availability": "Availability", "commitment": "Commitment", "drawn": "Drawn",
               "letters_of_credit": "Letters of credit", "borrowing_base": "Borrowing base"}
ROUTE_TEXT = {"stated": "availability stated", "computed": "commitment − drawn − letters of credit",
              "computed_abl": "lower of commitment − drawn − letters of credit and the borrowing base",
              "unresolved": "can't be worked out"}

STATUS_TEXT = {"skipped": "Skipped: agent triggers are skipped after a going-concern fail"}
RULES = [("Availability stated (at the year-end)", "Input correction with that figure; both scores recomputed"),
         ("Not stated, but commitment, drawn and letters of credit given",
          "Availability = commitment − drawn − letters of credit (asset-based revolver: the lower of this and the "
          "borrowing base); both scores recomputed"),
         ("No revolver in the debt note", "No adjustment: the revolver availability stays at 0"),
         ("Revolver exists, but availability can't be worked out",
          "Unresolved: liquidity and debt maturity escalated to an analyst, who accepts the score or overrides it"),
         ("The agent's answer and the verified evidence disagree",
          "Unresolved: liquidity and debt maturity escalated to an analyst, who accepts the score or overrides it")]


# --- the passages ------------------------------------------------------------------------------------------------

def liquidity_t1_passages(data: dict) -> dict:
    """The debt note's and MD&A liquidity's paragraphs and tables that mention a revolver or credit facility, an
    asset-based facility or its borrowing base, letters of credit or availability; ids L01… in document order."""
    return collect_passages(data, "L", LIMITS, SECTIONS, LIQUIDITY_T1_TERMS, _section_after)


def _headline_index(m: dict) -> int | None:
    return next((i for i, c in enumerate(m["columns"]) if c.get("score") is not None), None)


def liquidity_facts(m: dict, i: int) -> dict:
    """What the engine counted for the triggering column."""
    col = m["columns"][i]
    return {"heading": col["heading"], "as_at": col.get("as_at"), "cash": col.get("cash"), "cfo": col.get("cfo"),
            "y1": col.get("y1"), "capex": col.get("capex"), "dividends": col.get("dividends"),
            "nci_distributions": col.get("nci_distributions"), "fcf": col.get("fcf"),
            "weighted_maturities": col.get("weighted_maturities"), "coverage": col.get("coverage"),
            "coverage_score": col.get("coverage_score"), "refinancing": col.get("refinancing"),
            "refinancing_score": col.get("refinancing_score"), "score": col.get("score"),
            "revolver_found_source": col.get("revolver_found_source")}


def _ratio(v):
    return None if v is None or math.isinf(v) else round(v, 2)


def liquidity_t1_inputs(t: dict, meta: dict, fx: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": fx["as_at"]},
        "liquidity": {"column": fx["heading"], "cash_millions": _millions(fx["cash"]),
                      "operating_cash_flow_millions": _millions(fx["cfo"]),
                      "debt_due_in_12_months_millions": _millions(fx["y1"]),
                      "liquidity_coverage": _ratio(fx["coverage"]), "coverage_score": fx["coverage_score"],
                      "refinancing_coverage": _ratio(fx["refinancing"]), "refinancing_score": fx["refinancing_score"],
                      "score": fx["score"], "revolver_availability": None,
                      "revolver_search": fx["revolver_found_source"],
                      "trigger": "revolver availability at the year-end not found in the extracted data"},
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t["passages"]],
    }


# --- validation --------------------------------------------------------------------------------------------------

def _at_year_end(e, p, end):
    """Why the figure isn't dated at the year-end, or None: its date must be in the quote (or, for a table row's
    column heading, in the cited passage) and cover the year-end."""
    if not (e.get("date") or "").strip():
        return "no date given"
    stated, why = _date_in_quote(e["date"], e["quote"])
    if why:
        stated, why = _date_in_quote(e["date"], p["text"])
    if why:
        return f"the date {e['date']} is not in the quote or its passage"
    span = _span(stated)
    if span is None or not (span[0] <= end <= span[1]):
        return f"the date {e['date']} isn't the year-end ({end.isoformat()})"
    return None


def _figure(item, key, e, by_id, end, held):
    """(amount, evidence) for one of a facility's figures once verified, or None (held back, or not given)."""
    if e is None:
        return None
    need, why = FIGURE_WORDS[key]
    p, reason = _quoted(e, by_id, need, why)
    value = None
    if not reason:
        if not (e.get("amount") or "").strip():
            if key in NIL_ALLOWED and NONE_OUTSTANDING.search(e["quote"]):
                value = 0.0
            else:
                reason = "no amount given" + (" and the quote doesn't say none is outstanding"
                                              if key in NIL_ALLOWED else "")
        else:
            value, reason = _value(e, p)
    if not reason and key in DATED:
        reason = _at_year_end(e, p, end)
    if reason:
        held.append(_held(item, key, e, reason))
        return None
    return value, _ev(e, p)


def _maturity(item, e, by_id, end, held):
    """The revolver's verified maturity ({date, within_12_months, evidence}), or None."""
    if e is None:
        return None
    p, reason = _quoted(e, by_id, MATURES, "the quote doesn't show when the facility matures")
    span = None
    if not reason:
        if not (e.get("date") or "").strip():
            reason = "no date given"
        else:
            stated, reason = _date_in_quote(e["date"], e["quote"])
            span = _span(stated) if not reason else None
            if not reason and span is None:
                reason = f"date {e['date']!r} isn't a date"
    if reason:
        held.append(_held(item, "maturity", e, reason))
        return None
    return {"date": e["date"], "within_12_months": span[0] <= _a_year_after(end), "evidence": _ev(e, p)}


def _facility(o, by_id, end, held):
    """One verified revolving facility with its availability re-derived (first match), or None (held back)."""
    item = {"item_id": o.get("facility_id"), "description": o.get("description")}
    p, reason = _quoted(o.get("evidence"), by_id, REVOLVER, "the quote doesn't name a revolving credit facility")
    if reason:
        held.append(_held(item, "facility", o.get("evidence"), reason))
        return None
    figures = {}
    for key in FIGURE_TEXT:
        got = _figure(item, key, o.get(key), by_id, end, held)
        if got:
            figures[key] = got
    asset_based = bool(o.get("asset_based")) or any(
        ASSET_BASED.search(x["quote"]) for x in [o["evidence"], *(ev for _, ev in figures.values())])
    f = {"facility_id": item["item_id"], "description": item["description"], "asset_based": asset_based,
         "evidence": _ev(o["evidence"], p),
         "figures": {k: {"amount": v, "evidence": ev} for k, (v, ev) in figures.items()},
         "maturity": _maturity(item, o.get("maturity"), by_id, end, held), "availability": None,
         "route": "unresolved", "unresolved_why": None}
    if "availability" in figures:
        f.update(route="stated", availability=figures["availability"][0])
        return f
    missing = [FIGURE_TEXT[k].lower() for k in ("commitment", "drawn", "letters_of_credit") if k not in figures]
    if missing:
        f["unresolved_why"] = "no availability stated and no verified " + ", ".join(missing)
        return f
    computed = max(0.0, figures["commitment"][0] - figures["drawn"][0] - figures["letters_of_credit"][0])
    if asset_based:
        if "borrowing_base" not in figures:
            f["unresolved_why"] = "an asset-based revolver with no verified borrowing base"
            return f
        f.update(route="computed_abl", availability=min(computed, figures["borrowing_base"][0]), computed=computed)
        return f
    f.update(route="computed", availability=computed)
    return f


def validate_liquidity_t1(parsed: dict, t: dict, fx: dict) -> dict:
    """Keep the evidence that holds up and re-derive the outcome: no revolver (no verified facility: no adjustment);
    unresolved (a facility whose availability can't be worked out); otherwise every facility's availability, summed:
    computed (at least one worked out from commitment, drawn and letters of credit) or stated."""
    end = date.fromisoformat(fx["as_at"])
    by_id = {p["passage_id"]: p for p in t["passages"]}
    held, notes = [], []
    facilities = [f for o in parsed.get("facilities") or [] for f in [_facility(o, by_id, end, held)] if f]
    unresolved = [f for f in facilities if f["route"] == "unresolved"]
    why = None
    if not facilities:
        status = "no_revolver"
    elif unresolved:
        status = "unresolved"
        why = "a revolver exists but its availability can't be worked out (" + "; ".join(
            f"{f['facility_id']}: {f['unresolved_why']}" for f in unresolved) + ")"
    elif any(f["route"].startswith("computed") for f in facilities):
        status = "computed"
    else:
        status = "stated"
    for f in facilities:
        if (f["maturity"] or {}).get("within_12_months"):
            notes.append(f"{f['facility_id']}: the revolver matures within 12 months of the year-end "
                         f"({f['maturity']['date']}); its availability is counted as the filing gives it.")
    availability = sum(f["availability"] for f in facilities) if status in ("stated", "computed") else None
    return {"status": status, "facilities": facilities, "availability": availability, "unresolved_why": why,
            "held_back": held, "stated_status": parsed.get("status"),
            "status_consistent": parsed.get("status") == status, "validation_notes": notes}


def agent_reading(parsed: dict, t: dict) -> dict:
    """The agent's answer as it states it (not verified, not applied), for an analyst to compare with the verified
    outcome: "stated" or "computed" puts each facility's availability, as the agent gives it (stated, else commitment
    − drawn − letters of credit, the lower of that and the borrowing base for an asset-based revolver), into the
    liquidity sources. Any other answer changes nothing."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    stated, notes, total = parsed.get("status"), [], 0.0
    if stated in ("stated", "computed"):
        for o in parsed.get("facilities") or []:
            what = o.get("facility_id")
            amount = {k: stated_amount(o.get(k), by_id, notes, f"{what} {FIGURE_TEXT[k].lower()}") for k in FIGURE_TEXT}
            if amount["availability"] is not None:
                total += amount["availability"]
            elif amount["commitment"] is not None:
                v = max(0.0, amount["commitment"] - (amount["drawn"] or 0.0) - (amount["letters_of_credit"] or 0.0))
                total += min(v, amount["borrowing_base"]) if o.get("asset_based") and amount["borrowing_base"] else v
    return {"status": stated, "notes": notes,
            "applies": {"status": stated, "availability": total if stated in ("stated", "computed") else None,
                        "source": "the agent's answer, as it states it"}}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_LIQUIDITY_T1_PROMPT, template=FO_LIQUIDITY_T1_TEMPLATE,
                      response_schema=FO_LIQUIDITY_T1_SCHEMA, role="analyze",
                      label="Financial Overview Trigger Agent: Liquidity T1")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

def _source(facilities: list[dict]) -> str:
    """How the availability was worked out, facility by facility, with the passages cited."""
    parts = []
    for f in facilities:
        fig = f["figures"]
        ids = ", ".join(dict.fromkeys(x["evidence"]["passage_id"] for x in fig.values()))
        if f["route"] == "stated":
            parts.append(f"{f['facility_id']}: {f['availability'] / 1e6:,.1f} million available as stated ({ids})")
            continue
        text = (f"{fig['commitment']['amount'] / 1e6:,.1f} committed − {fig['drawn']['amount'] / 1e6:,.1f} drawn − "
                f"{fig['letters_of_credit']['amount'] / 1e6:,.1f} letters of credit")
        if f["route"] == "computed_abl":
            text = (f"lower of ({text}) and the borrowing base of {fig['borrowing_base']['amount'] / 1e6:,.1f}")
        parts.append(f"{f['facility_id']}: {text} = {f['availability'] / 1e6:,.1f} million ({ids})")
    return "; ".join(parts)


def _reason(status: str, checked: dict) -> str:
    if status in ("stated", "computed"):
        return (f"Revolver availability {'stated' if status == 'stated' else 'worked out'}: "
                f"{checked['availability'] / 1e6:,.1f} million ({_source(checked['facilities'])}); both scores "
                "recomputed")
    if status == "no_revolver":
        return "No revolver in the debt note: no adjustment, the revolver availability stays at 0"
    return "Unresolved: " + checked["unresolved_why"]


def liquidity_t1_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None,
                         mode: str = "live", case_id: str | None = None) -> dict:
    """The Liquidity T1 trigger result recorded in the run payload (``payload.triggers.liquidity_t1``). ``metrics``
    are the scored measures; the trigger reads the liquidity-and-maturity measure's headline column (the latest
    year-end)."""
    m = next((x for x in metrics if x["key"] == "liquidity"), None)
    score, heading = headline_score(m) if m else (None, None)
    result = {"trigger": LIQUIDITY_T1_ID, "measure": "liquidity", "version": LIQUIDITY_T1_VERSION, "threshold": None,
              "score": score, "column": heading, "fired": False, "mode": mode if backend is not None else "none",
              "agent": None, "notes": [], "review_required": False}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", reason=STATUS_TEXT["skipped"],
                      effect="None: the measures are superseded by the going-concern fail.")
        return result
    if score is None:
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason="Liquidity and debt maturity has no score: trigger Liquidity T1 not set off")
        return result
    fx = liquidity_facts(m, _headline_index(m))
    col = m["columns"][_headline_index(m)]
    result["inputs"] = fx
    found = col.get("revolver_found")
    if found is not None:
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason=f"Revolver availability found ({found / 1e6:,.1f} million, "
                             f"{col['revolver_found_source']}): trigger Liquidity T1 not set off")
        return result
    if (col.get("covenant_adjustment") or {}).get("status") == "breach":
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason="Revolver availability is set to 0 after the covenant breach (the covenant pre-check), "
                             "whatever it is: trigger Liquidity T1 not set off")
        return result
    if col.get("revolver_source") == MANUAL_REVOLVER:
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason="Revolver availability entered manually in the scoring settings: trigger Liquidity T1 "
                             "not set off")
        return result
    t = liquidity_t1_passages(data)
    result.update(fired=True, passages=t["passages"], sections=t["sections"],
                  fired_by=f"revolver availability at {fx['as_at']} not found ({fx['revolver_found_source']})")
    if not t["passages"]:
        checked = {"status": "no_revolver", "facilities": [], "availability": None, "unresolved_why": None,
                   "status_consistent": True}
        decided_by = "no_text"
        result["notes"].append("No debt-note or MD&A liquidity passage mentions a revolver, credit facility or "
                               "letters of credit: the agent was not called. No revolver, so no adjustment.")
    else:
        require_agent(backend, LIQUIDITY_T1_ID, AGENT)
        checked, decided_by = _run_agent(result, t, data, fx, session, backend, case_id)
    status = checked["status"]
    disagree = not checked["status_consistent"]
    unresolved = status == "unresolved" or disagree
    why = ("the agent's answer and the verified evidence disagree" if disagree else checked["unresolved_why"]) \
        if unresolved else None
    result.update(status=status, decided_by=decided_by, facilities=checked["facilities"],
                  availability=checked["availability"],
                  source=_source(checked["facilities"]) if status in ("stated", "computed") else None,
                  reason=_reason(status, checked), unresolved=unresolved, disagreement=disagree,
                  review_required=unresolved, unresolved_why=why,
                  effect=("Revolver availability set to the verified figure; liquidity coverage and refinancing "
                          "coverage recomputed" if status in ("stated", "computed")
                          else "None: the revolver availability stays at 0")
                  + (f"; unresolved ({why}), so liquidity and debt maturity is escalated to an analyst, who accepts "
                     "the score or overrides it." if unresolved else
                     "; resolved (the agent and the verified evidence agree), so no analyst decision is needed."))
    return result


def _run_agent(result, t, data, fx, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_LIQUIDITY_T1_PROMPT,
                            liquidity_t1_inputs(t, data["meta"], fx))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's Liquidity T1 output was rejected "
                                 f"({agent['validation_detail']}); the run was not recorded. Run it again.")
    checked = validate_liquidity_t1(run.parsed, t, fx)
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

def _times(v):
    return "—" if v is None else "∞" if math.isinf(v) else f"{v:.2f}x"


def _score(v):
    return "none" if v is None else f"{v:g}"


def rescore_liquidity(col: dict) -> None:
    """Rerun both liquidity tests on a column whose inputs a trigger changed, as it was scored: a manual revolver
    availability from the settings stays in, and a covenant relief's +1 is applied again after the calculation (the
    liquidity triggers don't run after a breach)."""
    manual = col.get("revolver_source") == MANUAL_REVOLVER
    score_liquidity(col, col["revolver"] / 1e6 if manual else None)
    relief = col.get("covenant_adjustment") or {}
    if relief.get("status") == "relief" and col["score"] is not None:
        calculated = col["score"]
        col["score"] = min(10, calculated + COVENANT_RELIEF_UPLIFT)
        relief.update(calculated=calculated, score=col["score"])


def apply_liquidity_t1(fo: list[dict], t: dict | None) -> list[dict]:
    """Liquidity and debt maturity with the trigger's outcome on its headline column (a copy; the inputs stay
    untouched): the verified revolver availability put into the liquidity sources and the refinancing sources, both
    coverages and scores recomputed on the measure's fixed bands (a covenant relief's +1 is applied again after). The
    column keeps the figures as calculated. The measure is marked for escalation when the trigger is unresolved."""
    if not t or not t.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "liquidity":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        col = m["columns"][_headline_index(m)]
        adj = {"trigger": "Liquidity T1", "outcome": t["status"], "calculated": col["score"],
               "coverage_calculated": col.get("coverage"), "coverage_score_calculated": col.get("coverage_score"),
               "refinancing_calculated": col.get("refinancing"),
               "refinancing_score_calculated": col.get("refinancing_score"),
               "availability": t.get("availability"), "recomputed": None}
        done = None
        if t["status"] in ("stated", "computed") and t.get("availability") is not None:
            col["revolver_found"] = t["availability"]
            col["revolver_found_source"] = f"Trigger Liquidity T1: {t.get('source')}"
            rescore_liquidity(col)
            adj["recomputed"] = col["score"]
            done = (f"revolver availability 0 → {t['availability'] / 1e6:,.1f} million, liquidity coverage "
                    f"{_times(adj['coverage_calculated'])} → {_times(col['coverage'])}, refinancing coverage "
                    f"{_times(adj['refinancing_calculated'])} → {_times(col['refinancing'])}, score "
                    f"{_score(adj['calculated'])} → {_score(col['score'])}")
        adj["score"] = col["score"]
        col["liquidity_adjustment"] = adj
        add_trigger_note(m, (f"Liquidity trigger T1 ({t.get('fired_by')}, {col['heading']}): "
                             + (done or "no adjustment" if t["status"] == "unresolved"
                                else done or t["reason"][:1].lower() + t["reason"][1:])),
                         "trigger Liquidity T1: " + {"stated": "availability stated",
                                                     "computed": "availability worked out",
                                                     "no_revolver": "no revolver"}.get(t["status"], "unresolved"),
                         t.get("unresolved", True), t.get("unresolved_why") or "availability not resolved")
        out.append(m)
    return out
