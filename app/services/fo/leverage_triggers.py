"""FO Trigger Agent, Debt/EBITDAR T1: one-offs distort EBITDAR.

One-off charges make leverage look worse than it is, and one-off gains make it look better. Debt/EBITDAR T1 runs
after the measures are scored (and after the DSCR and ICR triggers), only when the pre-checks allow it
(``payload.gate.agent_triggers`` is "enabled"), on Debt/EBITDAR's headline column (the latest year), when
|net one-offs| > 5% × EBITDAR or EBITDAR ≤ 0. Net one-offs = one-off charges − one-off gains, as tagged
(``measures.one_offs_for``: impairments, restructuring, litigation, acquisition and integration costs; gains on
disposals and litigation settlements). Same three steps as the other triggers:

1. ``leverage_t1_passages`` (deterministic) collects what the agent checks: the paragraphs and table rows of MD&A and
   the notes that mention a one-off item (impairments, litigation, acquisition costs, restructuring, gains or losses
   on disposals) or the drivers of operating results (ids L01…).
2. The Trigger Agent (prompt ``fo_trigger_leverage_t1``) ALWAYS reads them (no agent, or a rejected output, refuses
   the run). Every fact is quoted verbatim. Only a filing with none of those passages has nothing to read (no model
   call: no explanation, so unresolved).
3. ``validate_leverage_t1`` keeps only verbatim evidence that says what it must and RE-DERIVES the outcome, first match
   wins:

   - one-offs quantified and not recurring: normalised EBITDAR = reported + one-off charges − one-off gains, Debt /
     EBITDAR recomputed;
   - the change in EBITDAR is operational, with no one-offs found: no adjustment;
   - a one-off identified but not quantified: the 3-year average EBITDAR (2-year for a smaller reporting company,
     which presents two years of income statements) is used, and labelled;
   - partly quantified with an unexplained remainder, or no explanation at all: unresolved, so Debt/EBITDAR is
     escalated to an analyst.

   The remainder is unexplained when the verified one-offs (with any verified recurring items) fall short of the
   tagged net one-offs by more than the trigger's own 5% of EBITDAR, or a one-off is left unquantified beside
   quantified ones.

Debt/EBITDAR is escalated when unresolved, and, as for every trigger, when the agent's own answer and its verified
evidence disagree. Only Debt/EBITDAR changes: the EBITDAR margin and DSCR keep their EBITDAR.
"""

from __future__ import annotations

import copy
import re

from app.prompts.fo_prompts import FO_LEVERAGE_T1_PROMPT, FO_LEVERAGE_T1_SCHEMA, FO_LEVERAGE_T1_TEMPLATE

from .dscr_triggers import _ev, _held, _millions, _quoted, add_trigger_note, collect_passages
from .icr_triggers import KIND_WORDS, ONE_OFF, SECTIONS, _amount, _section_after
from .measures import LEVERAGE_MEASURES
from .precheck import DATA_NOTICE, PrecheckAgentError, call_agent, require_agent
from .scoring import headline_score, leverage_settings, score_leverage

LEVERAGE_T1_VERSION = "fo-trigger-leverage-t1-1.0.0"
LEVERAGE_T1_ID = "leverage_t1"
LEVERAGE_T1_SHARE = 0.05  # |net one-offs| above this share of EBITDAR sets the trigger off (so does EBITDAR ≤ 0)
AVERAGE_YEARS = 3
SRC_AVERAGE_YEARS = 2  # a smaller reporting company presents two years of income statements
SRC_CHECKED = {"true", "yes", "x", "☒", "☑", "✓", "✔"}  # dei:EntitySmallBusiness as filers tag it
AGENT = "Trigger Agent"

LIMITS = {"mdna": 25, "notes": 30}
ONE_OFF_ITEM = re.compile(ONE_OFF.pattern + r"|acquisition (costs?|expenses?)", re.I)
OPERATIONAL = re.compile(r"EBITDAR?\b|operating (income|results|margin)|income from operations|admissions|"
                         r"same-(store|facility)|payer mix|patient volumes?|wages and (salaries|benefits)|"
                         r"salaries and benefits|labor costs?", re.I)
LEVERAGE_TERMS = re.compile(ONE_OFF_ITEM.pattern + "|" + OPERATIONAL.pattern, re.I)

# --- what a quote must say ---------------------------------------------------------------------------------------
RECURRING = re.compile(r"recurr\w*|each (year|period)|every year|annual(ly)?|ongoing|in each of|regular(ly)?|"
                       r"normal course|routine", re.I)
DRIVER = re.compile(r"volumes?|pric\w*|payer mix|demand|wages?|salar\w*|labor|staffing|costs?|reimburs\w*|"
                    r"admissions|occupancy|census|revenues?|sales|margins?", re.I)

STATUS_TEXT = {"skipped": "Skipped: agent triggers are skipped after a going-concern fail"}
RULES = [("One-offs quantified and not recurring",
          "Normalised EBITDAR = reported + one-off charges − one-off gains; Debt / EBITDAR recomputed"),
         ("The change in EBITDAR is operational, with no one-offs found", "No adjustment"),
         ("A one-off identified but not quantified", f"The {AVERAGE_YEARS}-year average EBITDAR ({SRC_AVERAGE_YEARS}-year "
                                                     "for a smaller reporting company) is used, and labelled"),
         ("Partly quantified with an unexplained remainder, or no explanation at all",
          "Unresolved: Debt/EBITDAR escalated to an analyst, who accepts the score or overrides it"),
         ("The agent's answer and the verified evidence disagree",
          "Unresolved: Debt/EBITDAR escalated to an analyst, who accepts the score or overrides it")]


# --- the passages ------------------------------------------------------------------------------------------------

def leverage_t1_passages(data: dict) -> dict:
    """MD&A's and the notes' paragraphs and tables that mention a one-off or an operating driver; ids L01… in
    document order."""
    return collect_passages(data, "L", LIMITS, SECTIONS, LEVERAGE_TERMS, _section_after)


def _headline_index(m: dict) -> int | None:
    return next((i for i, c in enumerate(m["columns"]) if (c.get("composite") or {}).get("score") is not None), None)


def leverage_facts(col: dict) -> dict:
    """What the engine counted for the triggering column."""
    ebitdar, net = col.get("ebitdar"), col.get("net_one_offs") or 0.0
    return {"heading": col["heading"], "period": col["period"], "period_end": col["period"].split("--")[1],
            "ebitdar": ebitdar, "one_off_charges": col.get("one_off_charges") or 0.0,
            "one_off_gains": col.get("one_off_gains") or 0.0, "net_one_offs": net,
            "net_one_offs_concept": col.get("net_one_offs_concept"),
            "threshold": abs(ebitdar) * LEVERAGE_T1_SHARE if ebitdar is not None else None,
            "share": abs(net) / abs(ebitdar) if ebitdar else None,
            "debt_ratio": col["debt_to_ebitdar"].get("ratio"), "net_debt_ratio": col["net_debt_to_ebitdar"].get("ratio"),
            "score": (col.get("composite") or {}).get("score")}


def smaller_reporting_company(data: dict) -> bool:
    """Whether the cover page ticks the smaller reporting company box (``dei:EntitySmallBusiness``: tagged as a
    boolean, "true", or the ticked box character itself)."""
    return any(f["concept"] == "dei:EntitySmallBusiness" and not f.get("dimensions")
               and (f["value"] is True or str(f["value"]).strip().lower() in SRC_CHECKED) for f in data["facts"])


def average_years(data: dict) -> int:
    return SRC_AVERAGE_YEARS if smaller_reporting_company(data) else AVERAGE_YEARS


def average_ebitdar(m: dict, years: int = AVERAGE_YEARS) -> dict | None:
    """The average EBITDAR over the latest ``years`` years the measure has (each year's EBITDAR as the measure holds
    it), or None with fewer."""
    got = [(c["heading"], c["ebitdar"]) for c in m["columns"][:years] if c.get("ebitdar") is not None]
    if len(got) < years:
        return None
    return {"years": [{"heading": h, "ebitdar": v} for h, v in got], "n": years,
            "average": sum(v for _, v in got) / years,
            "label": f"{years}-year average EBITDAR ({got[-1][0]}–{got[0][0]})"}


def leverage_t1_inputs(t: dict, meta: dict, fx: dict, years: int = AVERAGE_YEARS) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": fx["period_end"]},
        "leverage": {"column": fx["heading"], "ebitdar_millions": _millions(fx["ebitdar"]),
                     "debt_to_ebitdar": round(fx["debt_ratio"], 2) if fx["debt_ratio"] is not None else None,
                     "net_debt_to_ebitdar": (round(fx["net_debt_ratio"], 2) if fx["net_debt_ratio"] is not None
                                             else None),
                     "score": fx["score"],
                     "tagged_one_offs": {"charges_millions": _millions(fx["one_off_charges"]),
                                         "gains_millions": _millions(fx["one_off_gains"]),
                                         "net_millions": _millions(fx["net_one_offs"]),
                                         "source": fx["net_one_offs_concept"]},
                     "trigger": f"|net one-offs| above {LEVERAGE_T1_SHARE:.0%} of EBITDAR, or EBITDAR ≤ 0",
                     "smaller_reporting_company": years == SRC_AVERAGE_YEARS, "average_years": years},
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t["passages"]],
    }


# --- validation --------------------------------------------------------------------------------------------------

def _one_off(o, by_id, held, notes):
    """One verified, quantified one-off, or None (held back). ``recurring`` holds only with a verified quote showing
    the item recurs; a recurring claim that doesn't hold up leaves the item a one-off."""
    kind = o.get("kind")
    item = {"item_id": o.get("item_id"), "kind": kind, "category": o.get("category"),
            "description": o.get("description")}
    words, why = KIND_WORDS.get(kind, KIND_WORDS["charge"])
    got = _amount(o.get("evidence"), by_id, ONE_OFF_ITEM, "the quote names no one-off item (impairment, litigation, "
                  "acquisition costs, restructuring, a gain or loss on a disposal…)", item, "amount", held, words, why)
    if not got:
        return None
    entry = {**item, "amount": got[0], "evidence": got[1], "recurring": False, "recurring_evidence": None,
             "effect": got[0] if kind == "charge" else -got[0]}  # what EBITDAR changes by when normalised
    if o.get("recurring"):
        r = o.get("recurring_evidence")
        rp, why = _quoted(r, by_id, RECURRING, "the quote doesn't show the item recurs")
        if why:
            held.append(_held(item, "recurring", r, why))
            notes.append(f"{item['item_id']}: the claim that it recurs doesn't hold up, so it is counted as a one-off.")
        else:
            entry.update(recurring=True, recurring_evidence=_ev(r, rp))
    return entry


def validate_leverage_t1(parsed: dict, t: dict, fx: dict, average: dict | None, years: int = AVERAGE_YEARS) -> dict:
    """Keep the evidence that holds up and re-derive the outcome, first match wins: normalised (quantified,
    non-recurring one-offs), operational (no one-offs; no adjustment), the average EBITDAR (a one-off identified but
    not quantified), else unresolved (partly quantified with an unexplained remainder, or no explanation)."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    held, notes = [], []
    items = [e for o in parsed.get("one_offs") or [] for e in [_one_off(o, by_id, held, notes)] if e]
    one_offs = [e for e in items if not e["recurring"]]
    recurring = [e for e in items if e["recurring"]]
    unquantified = []
    for u in parsed.get("unquantified") or []:
        item = {"item_id": u.get("item_id"), "category": u.get("category"), "description": u.get("description")}
        p, why = _quoted(u.get("evidence"), by_id, ONE_OFF_ITEM, "the quote names no one-off item")
        if why:
            held.append(_held(item, "one-off mentioned", u.get("evidence"), why))
        else:
            unquantified.append({**item, "evidence": _ev(u["evidence"], p)})
    operational = []
    for i, q in enumerate(parsed.get("operational") or [], 1):
        p, why = _quoted(q, by_id, DRIVER, "the quote names no operating driver (volumes, pricing, costs…)")
        if why:
            held.append(_held({"item_id": f"P{i}"}, "operational", q, why))
        else:
            operational.append({"item_id": f"P{i}", **_ev(q, p)})
    normalisation = sum(e["effect"] for e in one_offs)
    explained = normalisation + sum(e["effect"] for e in recurring)
    remainder = fx["net_one_offs"] - explained
    # Only a shortfall counts: the filing can disclose one-offs the tags miss, never the reverse.
    short = abs(fx["net_one_offs"]) - abs(explained) > fx["threshold"]
    why = None
    if one_offs and not unquantified and not short:
        status = "normalised"
    elif not one_offs and not unquantified and (operational or recurring) and not short:
        status = "operational"
    elif unquantified and not one_offs and average:
        status = "average_ebitdar"
    else:
        status = "unresolved"
        if one_offs and unquantified:
            why = f"partly quantified: {', '.join(u['item_id'] for u in unquantified)} left unquantified"
        elif short and (one_offs or recurring or operational):
            why = f"the verified evidence leaves {remainder / 1e6:,.1f} million of the tagged net one-offs unexplained"
        elif unquantified:
            why = (f"a one-off is identified but not quantified, and fewer than {years} years of EBITDAR are "
                   "available to average")
        else:
            why = "no explanation for the one-offs or the level of EBITDAR"
    return {"status": status, "one_offs": one_offs, "recurring": recurring, "unquantified": unquantified,
            "operational": operational, "normalisation": normalisation if status == "normalised" else 0.0,
            "remainder": remainder, "unresolved_why": why, "held_back": held,
            "stated_status": parsed.get("status"), "status_consistent": parsed.get("status") == status,
            "validation_notes": notes}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_LEVERAGE_T1_PROMPT, template=FO_LEVERAGE_T1_TEMPLATE, response_schema=FO_LEVERAGE_T1_SCHEMA,
                      role="analyze", label="Financial Overview Trigger Agent: Debt/EBITDAR T1")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

def _reason(status: str, fx: dict, checked: dict, average: dict | None) -> str:
    if status == "normalised":
        items = "; ".join(f"{e['kind']} of {e['amount'] / 1e6:,.1f} million ({e['evidence']['passage_id']})"
                          for e in checked["one_offs"])
        return (f"One-offs quantified and not recurring ({items}): normalised EBITDAR {fx['ebitdar'] / 1e6:,.1f} → "
                f"{(fx['ebitdar'] + checked['normalisation']) / 1e6:,.1f} million, Debt / EBITDAR recomputed")
    if status == "operational":
        ids = ", ".join(e["passage_id"] for e in checked["operational"]) or ", ".join(
            e["evidence"]["passage_id"] for e in checked["recurring"])
        return f"The change in EBITDAR is operational ({ids}), with no one-offs found: no adjustment"
    if status == "average_ebitdar":
        ids = ", ".join(e["evidence"]["passage_id"] for e in checked["unquantified"])
        return (f"A one-off is identified but not quantified ({ids}): the {average['label']} of "
                f"{average['average'] / 1e6:,.1f} million is used")
    return "Unresolved: " + checked["unresolved_why"]


def _fired_by(fx: dict) -> str:
    if fx["ebitdar"] <= 0:
        return f"EBITDAR is {fx['ebitdar'] / 1e6:,.1f} million (zero or negative)"
    return (f"net one-offs as tagged are {fx['net_one_offs'] / 1e6:,.1f} million ({fx['share']:.1%} of EBITDAR, above "
            f"{LEVERAGE_T1_SHARE:.0%})")


def leverage_t1_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None,
                        mode: str = "live", case_id: str | None = None) -> dict:
    """The Debt/EBITDAR T1 trigger result recorded in the run payload (``payload.triggers.leverage_t1``). ``metrics``
    are the scored measures; the trigger reads Debt/EBITDAR's headline column (the latest year)."""
    lev = next((m for m in metrics if m["key"] == "leverage"), None)
    score, heading = headline_score(lev) if lev else (None, None)
    result = {"trigger": LEVERAGE_T1_ID, "measure": "leverage", "version": LEVERAGE_T1_VERSION,
              "share_threshold": LEVERAGE_T1_SHARE, "score": score, "column": heading, "fired": False,
              "mode": mode if backend is not None else "none", "agent": None, "notes": [], "review_required": False}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", reason=STATUS_TEXT["skipped"],
                      effect="None: the measures are superseded by the going-concern fail.")
        return result
    if score is None:
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason="Debt/EBITDAR has no score: trigger Debt/EBITDAR T1 not set off")
        return result
    fx = leverage_facts(lev["columns"][_headline_index(lev)])
    result["inputs"] = fx
    if fx["ebitdar"] is None:
        result.update(status="not_triggered", decided_by="one_offs", effect="None.",
                      reason="No EBITDAR for the year: trigger Debt/EBITDAR T1 not set off")
        return result
    if fx["ebitdar"] > 0 and abs(fx["net_one_offs"]) <= fx["threshold"]:
        result.update(status="not_triggered", decided_by="one_offs", effect="None.",
                      reason=f"Net one-offs as tagged are {fx['net_one_offs'] / 1e6:,.1f} million ({fx['share']:.1%} of "
                             f"EBITDAR), not more than {LEVERAGE_T1_SHARE:.0%}, and EBITDAR is positive: trigger "
                             "Debt/EBITDAR T1 not set off")
        return result
    years = average_years(data)
    average = average_ebitdar(lev, years)
    t = leverage_t1_passages(data)
    result.update(fired=True, fired_by=_fired_by(fx), smaller_reporting_company=years == SRC_AVERAGE_YEARS,
                  average_years=years, average=average, passages=t["passages"], sections=t["sections"])
    if not t["passages"]:
        checked = {"status": "unresolved", "one_offs": [], "recurring": [], "unquantified": [], "operational": [],
                   "normalisation": 0.0, "remainder": fx["net_one_offs"], "status_consistent": True,
                   "unresolved_why": "no explanation for the one-offs or the level of EBITDAR"}
        decided_by = "no_text"
        result["notes"].append("No MD&A or note passage mentions a one-off or an operating driver: the agent was not "
                               "called. No explanation, so Debt/EBITDAR is unresolved.")
    else:
        require_agent(backend, LEVERAGE_T1_ID, AGENT)
        checked, decided_by = _run_agent(result, t, data, fx, average, years, session, backend, case_id)
    status = checked["status"]
    disagree = not checked["status_consistent"]
    unresolved = status == "unresolved" or disagree
    why = ("the agent's answer and the verified evidence disagree" if disagree else checked["unresolved_why"]) \
        if unresolved else None
    adjusted = status in ("normalised", "average_ebitdar")
    result.update(status=status, decided_by=decided_by, one_offs=checked["one_offs"], recurring=checked["recurring"],
                  unquantified=checked["unquantified"], operational=checked["operational"],
                  normalisation=checked["normalisation"], remainder=checked["remainder"],
                  ebitdar_used=(fx["ebitdar"] + checked["normalisation"] if status == "normalised" else
                                average["average"] if status == "average_ebitdar" else None),
                  reason=_reason(status, fx, checked, average), unresolved=unresolved, disagreement=disagree,
                  review_required=unresolved, unresolved_why=why,
                  effect=("Debt / EBITDAR recomputed on " + ("normalised EBITDAR" if status == "normalised" else
                                                             f"the {average['label']}") if adjusted
                          else "None: the Debt/EBITDAR score stays as calculated")
                  + (f"; unresolved ({why}), so Debt/EBITDAR is escalated to an analyst, who accepts the score or "
                     "overrides it." if unresolved else
                     "; resolved (the agent and the verified evidence agree), so no analyst decision is needed."))
    return result


def _run_agent(result, t, data, fx, average, years, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_LEVERAGE_T1_PROMPT,
                            leverage_t1_inputs(t, data["meta"], fx, years))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's Debt/EBITDAR T1 output was rejected "
                                 f"({agent['validation_detail']}); the run was not recorded. Run it again.")
    checked = validate_leverage_t1(run.parsed, t, fx, average, years)
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

def apply_leverage_t1(fo: list[dict], t: dict | None) -> list[dict]:
    """Debt/EBITDAR with the trigger's outcome on its headline column (a copy; the inputs stay untouched): EBITDAR
    normalised, or replaced by the 3-year (2-year for a smaller reporting company) average (labelled), both ratios and
    the scores recomputed with the settings the measure was scored with. The column keeps the EBITDAR as calculated.
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
        adj = {"trigger": "Debt/EBITDAR T1", "outcome": t["status"], "calculated": col["composite"]["score"],
               "ratio_calculated": col["debt_to_ebitdar"]["ratio"],
               "net_ratio_calculated": col["net_debt_to_ebitdar"]["ratio"], "ebitdar_calculated": col["ebitdar"],
               "recomputed": None}
        done = None
        if t.get("ebitdar_used") is not None:
            col["ebitdar_before_trigger"] = col["ebitdar"]
            col["ebitdar"] = t["ebitdar_used"]
            if t["status"] == "average_ebitdar":
                col["ebitdar_basis"] = t["average"]["label"]
            for key, _, numerator, denominator in LEVERAGE_MEASURES:
                num, den = col[numerator], col[denominator]
                col[key]["ratio"] = num / den if num is not None and den and den > 0 else None
                col[key]["negative_ebitda"] = den is not None and den <= 0
            score_leverage(col, leverage_settings(m))
            adj["recomputed"] = col["composite"]["score"]
            done = (f"EBITDAR {adj['ebitdar_calculated'] / 1e6:,.1f} → {col['ebitdar'] / 1e6:,.1f} million ("
                    + ("normalised: one-off charges added back, gains removed" if t["status"] == "normalised"
                       else col["ebitdar_basis"])
                    + "), composite leverage score "
                    + (f"{adj['calculated']:g} → " if adj["calculated"] is not None else "")
                    + (f"{adj['recomputed']:g}" if adj["recomputed"] is not None else "none"))
        adj["ebitdar"], adj["score"] = col["ebitdar"], col["composite"]["score"]
        col["ebitdar_adjustment"] = adj
        add_trigger_note(m, (f"Debt/EBITDAR trigger T1 ({t.get('fired_by')}, {col['heading']}): "
                             + (done or "no adjustment" if t["status"] == "unresolved"
                                else done or t["reason"][:1].lower() + t["reason"][1:])),
                         "trigger Debt/EBITDAR T1: " + ({"normalised": "normalised", "average_ebitdar":
                                                        f"{(t.get('average') or {}).get('n', AVERAGE_YEARS)}-year "
                                                        "average", "operational": "operational"}
                                                       .get(t["status"], "unresolved")),
                         t.get("unresolved", True), t.get("unresolved_why") or "no explanation")
        out.append(m)
    return out
