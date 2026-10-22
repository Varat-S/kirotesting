"""FO Trigger Agent, DSCR T2: CapEx above 1.5× depreciation (growth CapEx counted as maintenance understates DSCR).

DSCR deducts total CapEx (purchases of PP&E) as maintenance CapEx, because filers don't tag maintenance CapEx. When
the year's CapEx is more than 1.5× its depreciation (depreciation only: amortisation of acquired intangibles is
excluded, ``measures.depreciation_for``), part of it is likely growth CapEx. T2 runs after the measures are scored,
whatever DSCR scored, and before T1, so T1 judges the corrected DSCR. Only when the pre-checks allow it
(``payload.gate.agent_triggers`` is "enabled": a going-concern fail skips it). Same three steps as T1:

1. ``t2_passages`` (deterministic) collects what the agent checks: the paragraphs and table rows of MD&A (liquidity,
   capital resources, capital expenditures) and the segment note that mention capital expenditures, maintenance,
   growth, expansion, new facilities, construction, projects or commitments (ids K01…).
2. The Trigger Agent (prompt ``fo_trigger_dscr_t2``) ALWAYS reads them (no agent, or a rejected output, refuses the
   run). Every fact is quoted verbatim. Only a filing with none of those passages has nothing to read (no model call,
   the score is held).
3. ``validate_t2`` keeps only verbatim evidence that says what it must, and RE-DERIVES the outcome, first match wins:

   - growth projects committed or under construction: no adjustment;
   - maintenance/growth split disclosed: DSCR recomputed with the disclosed maintenance CapEx (or total CapEx less
     the disclosed growth CapEx);
   - growth described but not quantified: input correction, depreciation used as maintenance CapEx, DSCR recomputed;
   - no growth spending described: the score is held.

As with T1, the trigger escalates DSCR only when it is unresolved: the agent's own answer and its verified evidence
disagree. Otherwise the verified outcome stands without an analyst.
"""

from __future__ import annotations

import copy
import re

from app.prompts.fo_prompts import FO_DSCR_T2_PROMPT, FO_DSCR_T2_SCHEMA, FO_DSCR_T2_TEMPLATE

from .covenants import DEBT_TITLE, MDNA_END, NOTE_HEADING, OTHER_NOTE_TITLE, SUBSEQUENT_TITLE
from .dscr_triggers import (
    PLAN_WORDS,
    _ev,
    _headline_index,
    _held,
    _millions,
    _quoted,
    _value,
    add_trigger_note,
    collect_passages,
)
from .measures import DSCR_BANDS, risk_score
from .precheck import DATA_NOTICE, PrecheckAgentError, call_agent, require_agent
from .scoring import headline_score

T2_VERSION = "fo-trigger-dscr-t2-1.0.0"
T2_ID = "dscr_t2"
T2_MULTIPLE = 1.5  # CapEx above this multiple of depreciation sets the trigger off
AGENT = "Trigger Agent"
ADJUSTING = ("split_disclosed", "growth_not_quantified")  # the outcomes that recompute DSCR

SECTIONS = [("mdna_capex", "MD&A: liquidity and capital resources"), ("segment_note", "Segment note")]
LIMITS = {"mdna_capex": 20, "segment_note": 15}
CAPEX_TITLE = re.compile(r"capital (expenditures?|resources|spending|investments?)|\bliquidity\b", re.I)
SEGMENT_TITLE = re.compile(r"\bsegments?\b", re.I)
# MD&A and segment-note paragraphs are read only when they mention one of these.
T2_TERMS = re.compile(r"capital (expenditure|spending|investment|project)\w*|\bcapex\b|purchases? of property|"
                      r"additions to property|property(, plant)? and equipment|maintenance|sustaining|replacement|"
                      r"growth|expan\w*|new (\w+ ){0,2}(facilit|hospital|store|plant|location|capacity|campus|"
                      r"building|site|data cent)\w*|de novo|greenfield|construct\w*|capacity|projects?|committed|"
                      r"commitments?|depreciation", re.I)

# --- what a quote must say ---------------------------------------------------------------------------------------
GROWTH = re.compile(r"growth|expan\w*|new (\w+ ){0,2}(facilit|hospital|store|plant|location|capacity|campus|building|"
                    r"bed|site|data cent)\w*|de novo|greenfield|develop\w*|construct\w*|\bbuild\w*|"
                    r"(additional|added|new|increas\w*) (\w+ )?capacity|capacity (addition|increase)\w*", re.I)
COMMITTED = re.compile(r"under construction|construction in progress|being (built|constructed)|broke ground|"
                       r"\bcommitted\b|\bcommitments?\b|contract(ed|s)? (for|to)|\bapproved\b|in progress|"
                       r"under ?way", re.I)
MAINTENANCE = re.compile(r"maint\w*|sustain\w*|replac\w*|routine", re.I)
# A later year's figure, not the year's spending ("of this budgeted amount ... maintenance expenditures").
FORECAST = re.compile(PLAN_WORDS.pattern + r"|\bbudget\w*|\bforecast\w*|\bguidance\b|\boutlook\b|\bprojected\b",
                      re.I)

OUTCOMES = {
    "committed_growth": "Growth projects committed or under construction: no adjustment",
    "split_disclosed": "Maintenance/growth split disclosed: DSCR recomputed with the disclosed maintenance CapEx",
    "growth_not_quantified": "Growth described but not quantified: input correction, depreciation used as "
                             "maintenance CapEx, DSCR recomputed",
    "no_growth": "No growth spending described: score held",
}
STATUS_TEXT = {
    "skipped": "Skipped: agent triggers are skipped after a going-concern fail",
    "no_growth": OUTCOMES["no_growth"],
}
RULES = [("Growth projects committed or under construction", "No adjustment"),
         ("Maintenance/growth split disclosed", "Recompute DSCR with the disclosed maintenance CapEx"),
         ("Growth described but not quantified", "Input correction: depreciation used as maintenance CapEx, DSCR "
                                                 "recomputed"),
         ("No growth spending described", "Hold the score"),
         ("Unresolved: the agent's answer and the verified evidence disagree",
          "DSCR escalated to an analyst, who accepts the score or overrides it")]


# --- the passages ------------------------------------------------------------------------------------------------

def _section_after(item: str, heading: str, current: str | None, numbered: bool) -> str | None:
    """The searched section a heading opens, keeps or closes: MD&A liquidity and capital resources (Item 7) and the
    segment note."""
    if item == "7":
        if CAPEX_TITLE.search(heading):
            return "mdna_capex"
        if current == "mdna_capex" and not MDNA_END.search(heading):
            return current  # a sub-heading (investing activities, capital expenditures)
        return None
    if numbered and not NOTE_HEADING.match(heading):
        return current  # a sub-heading inside the current note
    if SEGMENT_TITLE.search(heading) and len(heading) <= 80:
        return "segment_note"
    if numbered or OTHER_NOTE_TITLE.search(heading) or DEBT_TITLE.search(heading) or SUBSEQUENT_TITLE.search(heading):
        return None
    return current


def t2_passages(data: dict) -> dict:
    """MD&A liquidity and capital resources' and the segment note's paragraphs and tables that mention a T2 term;
    ids K01… in document order."""
    return collect_passages(data, "K", LIMITS, SECTIONS, T2_TERMS, _section_after)


def capex_facts(col: dict) -> dict:
    """What the engine counted for the triggering column."""
    capex, dep = col.get("capex"), col.get("depreciation")
    return {"heading": col["heading"], "period": col["period"], "period_end": col["period"].split("--")[1],
            "capex": capex, "capex_concept": col.get("capex_concept"), "depreciation": dep,
            "depreciation_concept": col.get("depreciation_concept"),
            "multiple": capex / dep if capex is not None and dep else None,
            "cash_flow": col.get("cash_flow"), "debt_service": col.get("debt_service"), "ratio": col.get("ratio"),
            "score": col.get("score")}


def t2_inputs(t2: dict, meta: dict, cx: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": cx["period_end"]},
        "dscr": {"column": cx["heading"], "ratio": round(cx["ratio"], 2) if cx["ratio"] is not None else None,
                 "score": cx["score"]},
        "capex": {"amount_millions": _millions(cx["capex"]), "source": cx["capex_concept"]},
        "depreciation": {"amount_millions": _millions(cx["depreciation"]), "source": cx["depreciation_concept"],
                         "excludes": "amortisation of acquired intangibles"},
        "capex_to_depreciation": round(cx["multiple"], 2),
        "trigger": f"CapEx above {T2_MULTIPLE:g}× depreciation",
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t2["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t2["passages"]],
    }


# --- validation --------------------------------------------------------------------------------------------------

def _amount(e, by_id, need, why, cx, fact, held):
    """The verified amount of the year's maintenance or growth CapEx, or None (held back)."""
    if not e:
        return None
    p, reason = _quoted(e, by_id, need, why)
    value = None
    if not reason and FORECAST.search(e["quote"]):
        reason = "the quote reads as a forecast, not the year's spending"
    if not reason:
        value, reason = _value(e, p)
    if not reason and value > cx["capex"]:
        reason = f"{value / 1e6:,.1f} million is more than total CapEx ({cx['capex'] / 1e6:,.1f} million)"
    if reason:
        held.append(_held({"item_id": "S1"}, fact, e, reason))
        return None
    return value, _ev(e, p)


def validate_t2(parsed: dict, t2: dict, cx: dict) -> dict:
    """Keep the evidence that holds up and re-derive the outcome, first match wins: committed growth (no
    adjustment), the split disclosed (the disclosed maintenance CapEx), growth not quantified (depreciation as
    maintenance CapEx), no growth (score held)."""
    by_id = {p["passage_id"]: p for p in t2["passages"]}
    held, notes = [], []
    growth, committed = [], []
    for i, e in enumerate(parsed.get("growth") or [], 1):
        p, why = _quoted(e, by_id, GROWTH, "the quote describes no growth spending (expansion, new capacity, "
                                           "development or construction)")
        if why:
            held.append(_held({"item_id": f"G{i}"}, "growth", e, why))
        else:
            growth.append({"item_id": f"G{i}", **_ev(e, p)})
    for i, e in enumerate(parsed.get("committed") or [], 1):
        p, why = _quoted(e, by_id, COMMITTED, "the quote doesn't say the project is committed or under construction")
        if not why and not GROWTH.search(e["quote"]):
            why = "the quote names no growth project"
        if why:
            held.append(_held({"item_id": f"K{i}"}, "committed", e, why))
        else:
            committed.append({"item_id": f"K{i}", **_ev(e, p)})
    maintenance = _amount(parsed.get("maintenance_capex"), by_id, MAINTENANCE,
                          "the quote doesn't say it is maintenance CapEx", cx, "maintenance CapEx", held)
    grown = _amount(parsed.get("growth_capex"), by_id, GROWTH, "the quote doesn't say it is growth CapEx", cx,
                    "growth CapEx", held)
    split = None
    if maintenance:
        split = {"basis": "maintenance", "maintenance": maintenance[0], "evidence": maintenance[1]}
    elif grown:
        split = {"basis": "growth", "growth": grown[0], "maintenance": cx["capex"] - grown[0], "evidence": grown[1]}
    if committed:
        status, used = "committed_growth", None
        if split:
            notes.append("A maintenance/growth split is disclosed too, but growth projects committed or under "
                         "construction come first: no adjustment.")
    elif split:
        status, used = "split_disclosed", split["maintenance"]
    elif growth:
        status, used = "growth_not_quantified", cx["depreciation"]
    else:
        status, used = "no_growth", None
    return {"status": status, "growth": growth, "committed": committed, "split": split, "maintenance_capex": used,
            "held_back": held, "stated_status": parsed.get("status"),
            "status_consistent": parsed.get("status") == status, "validation_notes": notes}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_DSCR_T2_PROMPT, template=FO_DSCR_T2_TEMPLATE, response_schema=FO_DSCR_T2_SCHEMA,
                      role="analyze", label="Financial Overview Trigger Agent: DSCR T2")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

def _reason(status: str, cx: dict, used: float | None, checked: dict) -> str:
    if status == "committed_growth":
        ids = ", ".join(e["passage_id"] for e in checked["committed"])
        return f"Growth projects committed or under construction ({ids}): no adjustment"
    if status == "split_disclosed":
        split = checked["split"]
        how = ("disclosed" if split["basis"] == "maintenance" else
               f"total CapEx less the disclosed growth CapEx of {split['growth'] / 1e6:,.1f} million")
        return (f"Maintenance/growth split disclosed ({split['evidence']['passage_id']}): DSCR recomputed with "
                f"maintenance CapEx of {used / 1e6:,.1f} million ({how}) instead of total CapEx of "
                f"{cx['capex'] / 1e6:,.1f} million")
    if status == "growth_not_quantified":
        ids = ", ".join(e["passage_id"] for e in checked["growth"])
        return (f"Growth described but not quantified ({ids}): input correction, depreciation of "
                f"{used / 1e6:,.1f} million used as maintenance CapEx instead of total CapEx of "
                f"{cx['capex'] / 1e6:,.1f} million, DSCR recomputed")
    return STATUS_TEXT[status]


def dscr_t2_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None, mode: str = "live",
                    case_id: str | None = None) -> dict:
    """The DSCR T2 trigger result recorded in the run payload (``payload.triggers.dscr_t2``). ``metrics`` are the
    scored measures; the trigger reads DSCR's headline column (the forward column first)."""
    dscr = next((m for m in metrics if m["key"] == "dscr"), None)
    score, heading = headline_score(dscr) if dscr else (None, None)
    result = {"trigger": T2_ID, "measure": "dscr", "version": T2_VERSION, "multiple_threshold": T2_MULTIPLE,
              "score": score, "column": heading, "fired": False, "mode": mode if backend is not None else "none",
              "agent": None, "notes": [], "review_required": False}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", reason=STATUS_TEXT["skipped"],
                      effect="None: the measures are superseded by the going-concern fail.")
        return result
    if score is None:
        result.update(status="not_triggered", decided_by="score", reason="DSCR has no score: trigger T2 not set off",
                      effect="None.")
        return result
    cx = capex_facts(dscr["columns"][_headline_index(dscr)])
    result["capex"] = cx
    if cx["capex"] is None or cx["depreciation"] is None:
        result.update(status="not_triggered", decided_by="capex",
                      reason="No " + " or ".join(n for n, v in (("CapEx", cx["capex"]),
                                                                ("depreciation", cx["depreciation"])) if v is None)
                      + " tagged: trigger T2 not set off", effect="None.")
        return result
    if cx["depreciation"] <= 0:
        result.update(status="not_triggered", decided_by="capex",
                      reason="Depreciation is not positive: trigger T2 not set off", effect="None.")
        return result
    if cx["capex"] <= T2_MULTIPLE * cx["depreciation"]:
        result.update(status="not_triggered", decided_by="capex",
                      reason=f"CapEx is {cx['multiple']:.2f}× depreciation, not above {T2_MULTIPLE:g}×: trigger T2 "
                             "not set off", effect="None.")
        return result
    t2 = t2_passages(data)
    result.update(fired=True, passages=t2["passages"], sections=t2["sections"])
    if not t2["passages"]:
        checked = {"status": "no_growth", "growth": [], "committed": [], "split": None, "maintenance_capex": None,
                   "status_consistent": True}
        decided_by = "no_text"
        result["notes"].append("No MD&A liquidity and capital resources or segment-note passage to read: the agent "
                               "was not called, and the score is held.")
    else:
        require_agent(backend, T2_ID, AGENT)
        checked, decided_by = _run_agent(result, t2, data, cx, session, backend, case_id)
    status, used = checked["status"], checked["maintenance_capex"]
    unresolved = not checked["status_consistent"]
    result.update(status=status, decided_by=decided_by, growth=checked["growth"], committed=checked["committed"],
                  split=checked["split"], maintenance_capex=used, reason=_reason(status, cx, used, checked),
                  unresolved=unresolved, review_required=unresolved,
                  effect=("DSCR recomputed with maintenance CapEx as above" if status in ADJUSTING
                          else "None: the DSCR score stays as calculated")
                  + ("; unresolved (the agent's answer and the verified evidence disagree), so DSCR is escalated to "
                     "an analyst, who accepts the score or overrides it." if unresolved else
                     "; resolved (the agent and the verified evidence agree), so no analyst decision is needed."))
    return result


def _run_agent(result, t2, data, cx, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_DSCR_T2_PROMPT,
                            t2_inputs(t2, data["meta"], cx))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's DSCR T2 output was rejected ({agent['validation_detail']}); "
                                 "the run was not recorded. Run it again.")
    checked = validate_t2(run.parsed, t2, cx)
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

def apply_t2(fo: list[dict], t2: dict | None) -> list[dict]:
    """DSCR with the trigger's outcome on its headline column (a copy; the inputs stay untouched): for a disclosed
    split, or growth not quantified, maintenance CapEx replaces total CapEx in cash flow for debt service and the
    ratio and score are recomputed. The column keeps the figures as tagged. The measure is marked for escalation only
    when the trigger is unresolved."""
    if not t2 or not t2.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "dscr":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        col = m["columns"][_headline_index(m)]
        used = t2.get("maintenance_capex")
        adj = {"trigger": "T2", "calculated": col["score"], "ratio_calculated": col["ratio"], "recomputed": None,
               "capex_reported": col["capex"], "maintenance_capex": used, "outcome": t2["status"]}
        done = None
        if used is not None and col["cash_flow"] is not None:
            col["capex_reported"], col["cash_flow_reported"] = col["capex"], col["cash_flow"]
            col["cash_flow"] += col["capex"] - used
            col["capex"] = used
            col["ratio"] = col["cash_flow"] / col["debt_service"] if col["debt_service"] else None
            col["score"] = adj["recomputed"] = risk_score(col["ratio"], DSCR_BANDS)
            done = (f"maintenance CapEx {used / 1e6:,.1f} million instead of total CapEx "
                    f"{adj['capex_reported'] / 1e6:,.1f} million: DSCR {adj['ratio_calculated']:.2f}x → "
                    + (f"{col['ratio']:.2f}x" if col["ratio"] is not None else "no debt service") + ", score "
                    f"{adj['calculated']:g} → " + (f"{col['score']:g}" if col["score"] is not None else "none"))
        adj["score"] = col["score"]
        col["capex_adjustment"] = adj
        multiple = (t2.get("capex") or {}).get("multiple")
        add_trigger_note(m, (f"DSCR trigger T2 (CapEx {multiple:.2f}× depreciation > {T2_MULTIPLE:g}×, "
                             f"{col['heading']}): " + (done or t2["reason"][:1].lower() + t2["reason"][1:])),
                         "trigger T2: " + ("recomputed" if done else "no adjustment"), t2.get("unresolved", True))
        out.append(m)
    return out
