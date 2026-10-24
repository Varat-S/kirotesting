"""FO Trigger Agent, Liquidity T3: the largest single year of maturities in years 2–5 is 25% or more of total debt
(the maturity table). A concentrated wall depends on refinancing, and how easy that is depends on the kind of debt.

Liquidity T3 runs on the liquidity-and-maturity measure's headline column after Liquidity T2 (so it reads the maturity
table with T2's moves in), only when the pre-checks allow it (``payload.gate.agent_triggers`` is "enabled"), the
measure has a score and a tagged maturity table, and not after a covenant breach not waived (the score is 10 whatever
the debt). Same three steps as the other triggers:

1. ``liquidity_t3_passages`` (deterministic) collects the debt note's and MD&A liquidity's paragraphs and table rows
   that mention notes, bonds, loans, mortgages, security or maturities (ids W01…).
2. The Trigger Agent (prompt ``fo_trigger_liquidity_t3``) ALWAYS reads them (no agent, or a rejected output, refuses
   the run) and lists the instruments maturing in the wall year, each with its kind, amount and maturity date, quoted
   verbatim. A filing with none of those passages has nothing to read (no model call: mixed, no change).
3. ``validate_liquidity_t3`` keeps the instruments whose quotes hold up (the amount and a maturity date in the wall
   year in the quote, the kind's words in it) and RE-DERIVES the outcome on the wall year's total in the table:

   - more than 50% secured bank debt or mortgages: −1 on the score (not below 1);
   - more than 50% unsecured bonds: +1 (not above 10);
   - otherwise (mixed, or not identified): no change.

Liquidity and debt maturity is escalated when the agent's own answer and its verified evidence disagree.
"""

from __future__ import annotations

import copy
import re
from datetime import date

from app.prompts.fo_prompts import FO_LIQUIDITY_T3_PROMPT, FO_LIQUIDITY_T3_SCHEMA, FO_LIQUIDITY_T3_TEMPLATE

from .covenants import _section_after
from .dscr_triggers import _dated, _ev, _held, _millions, _quoted, _value, add_trigger_note, collect_passages
from .liquidity_t2 import BUCKET_TEXT, _years_after, ladder_of
from .precheck import DATA_NOTICE, PrecheckAgentError, call_agent, require_agent
from .scoring import headline_score

LIQUIDITY_T3_VERSION = "fo-trigger-liquidity-t3-1.0.0"
LIQUIDITY_T3_ID = "liquidity_t3"
LIQUIDITY_T3_SHARE = 0.25  # the wall year's maturities against total debt at or above this sets the trigger off
LIQUIDITY_T3_MAJORITY = 0.5  # more than this share of the wall year decides the kind
LIQUIDITY_T3_STEP = 1
AGENT = "Trigger Agent"
WALL_BUCKETS = ("y2", "y3", "y4", "y5")

SECTIONS = [("debt_note", "Debt note"), ("mdna_liquidity", "MD&A: liquidity")]
LIMITS = {"debt_note": 30, "mdna_liquidity": 15}
LIQUIDITY_T3_TERMS = re.compile(r"\bnotes?\b|bonds?|debentures?|term loans?|credit (facilit\w*|agreement)|"
                                r"mortgages?|secured|collateral\w*|matur\w*|\bdue (in )?(19|20)\d\d", re.I)

# --- what a quote must say ---------------------------------------------------------------------------------------
SECURED = re.compile(r"(?<!un)secured|collaterali[sz]\w*|\bliens?\b|pledg\w*", re.I)
BANK = re.compile(r"term loans?|credit (facilit\w*|agreement)|revolv\w*|\bbank\w*|\bloans?\b|\bABL\b|asset-based", re.I)
BOND = re.compile(r"\bnotes?\b|bonds?|debentures?", re.I)
KIND_WORDS = {
    "secured_bank": (re.compile(f"(?=.*(?:{SECURED.pattern}))(?=.*(?:{BANK.pattern}))", re.I | re.S),
                     "the quote doesn't show secured bank debt"),
    "mortgage": (re.compile(r"mortgage\w*", re.I), "the quote doesn't show a mortgage"),
    "unsecured_bond": (re.compile(f"(?=.*(?:{BOND.pattern}))(?=.*(?:unsecured|senior notes?|\\bnotes due\\b|"
                                  f"debentures?))", re.I | re.S), "the quote doesn't show unsecured bonds"),
    "other": (re.compile(r"\w"), ""),
}
KIND_TEXT = {"secured_bank": "Secured bank debt", "mortgage": "Mortgage", "unsecured_bond": "Unsecured bonds",
             "other": "Other"}
EFFECT = {"secured": -LIQUIDITY_T3_STEP, "unsecured": LIQUIDITY_T3_STEP, "mixed": 0}

STATUS_TEXT = {"skipped": "Skipped: agent triggers are skipped after a going-concern fail"}
RULES = [("More than 50% of the wall year is secured bank debt or mortgages", f"−{LIQUIDITY_T3_STEP} on the score"),
         ("More than 50% of the wall year is unsecured bonds", f"+{LIQUIDITY_T3_STEP} on the score"),
         ("Mixed (or the wall year's debt not identified)", "No change"),
         ("The agent's answer and the verified evidence disagree",
          "Unresolved: liquidity and debt maturity escalated to an analyst, who accepts the score or overrides it")]


def liquidity_t3_passages(data: dict) -> dict:
    """The debt note's and MD&A liquidity's paragraphs and tables that mention notes, bonds, loans, mortgages,
    security or maturities; ids W01… in document order."""
    return collect_passages(data, "W", LIMITS, SECTIONS, LIQUIDITY_T3_TERMS, _section_after)


def _headline_index(m: dict) -> int | None:
    return next((i for i, c in enumerate(m["columns"]) if c.get("score") is not None), None)


def wall_facts(col: dict) -> dict:
    """The wall: the largest single year of years 2–5 and its share of total debt (the table's total)."""
    ladder, total = ladder_of(col), col.get("ladder_total")
    wall = max((k for k in WALL_BUCKETS if ladder.get(k)), key=lambda k: ladder[k], default=None)
    end = date.fromisoformat(col["as_at"]) if col.get("as_at") else None
    n = int(wall[1]) if wall else None
    return {"heading": col["heading"], "as_at": col.get("as_at"), "wall": wall,
            "wall_label": BUCKET_TEXT.get(wall), "wall_amount": ladder.get(wall) if wall else None,
            "total": total, "share": ladder[wall] / total if wall and total else None,
            "wall_from": _years_after(end, n - 1).isoformat() if wall and end else None,
            "wall_to": _years_after(end, n).isoformat() if wall and end else None,
            "ladder": ladder, "score": col.get("score")}


def liquidity_t3_inputs(t: dict, meta: dict, fx: dict) -> dict:
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": fx["as_at"]},
        "wall": {"year": fx["wall_label"], "after": fx["wall_from"], "up_to": fx["wall_to"],
                 "maturities_millions": _millions(fx["wall_amount"]), "total_debt_millions": _millions(fx["total"]),
                 "share_of_total_debt": round(fx["share"], 3),
                 "trigger": f"largest single year of maturities in years 2–5 at {LIQUIDITY_T3_SHARE:.0%} or more of "
                            "total debt"},
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t["passages"]],
    }


def _instrument(o, by_id, lo, hi, held):
    """One verified instrument maturing in the wall year, or None (held back)."""
    kind = o.get("kind")
    item = {"item_id": o.get("item_id"), "kind": kind, "description": o.get("description")}
    words, why = KIND_WORDS.get(kind, KIND_WORDS["other"])
    e = o.get("evidence")
    p, reason = _quoted(e, by_id, words, why)
    value = span = None
    if not reason:
        value, reason = _value(e, p)
    if not reason:
        span, reason = _dated(e)
        if not reason and not (span[0] > lo and span[1] <= hi):
            reason = f"the maturity {e['date']} isn't in the wall year (after {lo}, up to {hi})"
    if reason:
        held.append(_held(item, "instrument", e, reason))
        return None
    return {**item, "amount": value, "maturity": e["date"], "evidence": _ev(e, p)}


def validate_liquidity_t3(parsed: dict, t: dict, fx: dict) -> dict:
    """Keep the instruments that hold up and re-derive the outcome on the wall year's total in the table: secured
    (secured bank debt and mortgages more than 50%), unsecured (unsecured bonds more than 50%), else mixed."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    lo, hi = date.fromisoformat(fx["wall_from"]), date.fromisoformat(fx["wall_to"])
    held, notes = [], []
    found = [e for o in parsed.get("instruments") or [] for e in [_instrument(o, by_id, lo, hi, held)] if e]
    wall = fx["wall_amount"]
    secured = sum(e["amount"] for e in found if e["kind"] in ("secured_bank", "mortgage"))
    unsecured = sum(e["amount"] for e in found if e["kind"] == "unsecured_bond")
    if secured + unsecured > wall * 1.05:
        notes.append(f"The instruments found ({(secured + unsecured) / 1e6:,.1f} million) are more than the wall "
                     f"year's maturities in the table ({wall / 1e6:,.1f} million); the shares use the table's total.")
    shares = {"secured": secured / wall, "unsecured": unsecured / wall}
    status = ("secured" if shares["secured"] > LIQUIDITY_T3_MAJORITY else
              "unsecured" if shares["unsecured"] > LIQUIDITY_T3_MAJORITY else "mixed")
    return {"status": status, "instruments": found, "secured_share": shares["secured"],
            "unsecured_share": shares["unsecured"], "held_back": held, "stated_status": parsed.get("status"),
            "status_consistent": parsed.get("status") == status, "validation_notes": notes}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_LIQUIDITY_T3_PROMPT, template=FO_LIQUIDITY_T3_TEMPLATE,
                      response_schema=FO_LIQUIDITY_T3_SCHEMA, role="analyze",
                      label="Financial Overview Trigger Agent: Liquidity T3")
    return registry


def _reason(status, checked, fx):
    shares = (f"secured bank debt and mortgages {checked['secured_share']:.0%}, unsecured bonds "
              f"{checked['unsecured_share']:.0%} of {fx['wall_label'].lower()}")
    return {"secured": f"Mostly secured bank debt or mortgages ({shares}): −{LIQUIDITY_T3_STEP} on the score",
            "unsecured": f"Mostly unsecured bonds ({shares}): +{LIQUIDITY_T3_STEP} on the score",
            "mixed": f"Mixed ({shares}): no change"}[status]


def liquidity_t3_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None,
                         mode: str = "live", case_id: str | None = None) -> dict:
    """The Liquidity T3 trigger result recorded in the run payload (``payload.triggers.liquidity_t3``), on the
    measures after Liquidity T2."""
    m = next((x for x in metrics if x["key"] == "liquidity"), None)
    score, heading = headline_score(m) if m else (None, None)
    result = {"trigger": LIQUIDITY_T3_ID, "measure": "liquidity", "version": LIQUIDITY_T3_VERSION,
              "threshold": LIQUIDITY_T3_SHARE, "score": score, "column": heading, "fired": False,
              "mode": mode if backend is not None else "none", "agent": None, "notes": [], "review_required": False}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", reason=STATUS_TEXT["skipped"],
                      effect="None: the measures are superseded by the going-concern fail.")
        return result

    def not_triggered(reason):
        result.update(status="not_triggered", decided_by="score", effect="None.", reason=reason)
        return result

    if score is None:
        return not_triggered("Liquidity and debt maturity has no score: trigger Liquidity T3 not set off")
    col = m["columns"][_headline_index(m)]
    if (col.get("covenant_adjustment") or {}).get("status") == "breach":
        return not_triggered("The score is set to 10 after the covenant breach (the covenant pre-check), whatever the "
                             "debt: trigger Liquidity T3 not set off")
    if not col.get("ladder_source"):
        return not_triggered("No debt maturity table tagged: trigger Liquidity T3 not set off")
    fx = wall_facts(col)
    result["inputs"] = fx
    if fx["share"] is None or fx["share"] < LIQUIDITY_T3_SHARE:
        return not_triggered(
            f"The largest year of years 2–5 ({fx['wall_label'] or 'none'}) is "
            + (f"{fx['share']:.1%}" if fx["share"] is not None else "nothing")
            + f" of total debt, below {LIQUIDITY_T3_SHARE:.0%}: trigger Liquidity T3 not set off")
    t = liquidity_t3_passages(data)
    result.update(fired=True, passages=t["passages"], sections=t["sections"],
                  fired_by=f"{fx['wall_label']}: {fx['wall_amount'] / 1e6:,.1f} million, {fx['share']:.1%} of total "
                           f"debt ({LIQUIDITY_T3_SHARE:.0%} or more)")
    if not t["passages"]:
        checked = {"status": "mixed", "instruments": [], "secured_share": 0.0, "unsecured_share": 0.0,
                   "status_consistent": True}
        decided_by = "no_text"
        result["notes"].append("No debt-note or MD&A liquidity passage describes the debt: the agent was not called. "
                               "The wall year's debt isn't identified, so no change.")
    else:
        require_agent(backend, LIQUIDITY_T3_ID, AGENT)
        run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_LIQUIDITY_T3_PROMPT,
                                liquidity_t3_inputs(t, data["meta"], fx))
        result["agent"] = agent
        if not run.is_valid:
            raise PrecheckAgentError(f"The Trigger Agent's Liquidity T3 output was rejected "
                                     f"({agent['validation_detail']}); the run was not recorded. Run it again.")
        checked, decided_by = validate_liquidity_t3(run.parsed, t, fx), "agent"
        stated = run.parsed.get("status")
        result["agent_reading"] = {"status": stated, "notes": [],
                                   "applies": {"status": stated, "step": EFFECT.get(stated, 0)}}
        agent.update(rationale=run.parsed.get("rationale"),
                     missing_information=run.parsed.get("missing_information") or [],
                     held_back=checked["held_back"], derived_status=checked["status"], stated_status=stated,
                     status_consistent=checked["status_consistent"])
        result["notes"] += checked["validation_notes"]
        if checked["held_back"]:
            result["notes"].append(f"{len(checked['held_back'])} agent item(s) held back by validation.")
        if not checked["status_consistent"]:
            result["notes"].append(f"The agent stated {str(stated).upper()} but its verified evidence supports "
                                   f"{checked['status'].upper()}; the verified evidence decides.")
    status = checked["status"]
    unresolved = not checked["status_consistent"]
    why = "the agent's answer and the verified evidence disagree" if unresolved else None
    result.update(status=status, decided_by=decided_by, instruments=checked["instruments"],
                  secured_share=checked["secured_share"], unsecured_share=checked["unsecured_share"],
                  step=EFFECT[status], reason=_reason(status, checked, fx), unresolved=unresolved,
                  disagreement=unresolved, review_required=unresolved, unresolved_why=why,
                  effect=({"secured": f"−{LIQUIDITY_T3_STEP} on the score", "unsecured": f"+{LIQUIDITY_T3_STEP} on "
                           "the score"}.get(status, "None: the score stays as calculated"))
                  + (f"; unresolved ({why}), so liquidity and debt maturity is escalated to an analyst, who accepts "
                     "the score or overrides it." if unresolved else
                     "; resolved (the agent and the verified evidence agree), so no analyst decision is needed."))
    return result


def apply_liquidity_t3(fo: list[dict], t: dict | None) -> list[dict]:
    """Liquidity and debt maturity with the trigger's step on its headline column's score (a copy; the inputs stay
    untouched), kept within 1–10. The measure is marked for escalation when the trigger is unresolved."""
    if not t or not t.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "liquidity":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        col = m["columns"][_headline_index(m)]
        step = t.get("step") or 0
        adj = {"trigger": "Liquidity T3", "outcome": t["status"], "calculated": col["score"], "step": step,
               "recomputed": None}
        done = None
        if step and col["score"] is not None:
            col["score"] = adj["recomputed"] = max(1.0, min(10.0, col["score"] + step))
            done = f"score {adj['calculated']:g} → {col['score']:g} ({step:+d}: {t['status']} wall year)"
        adj["score"] = col["score"]
        col["wall_adjustment"] = adj
        add_trigger_note(m, f"Liquidity trigger T3 ({t.get('fired_by')}, {col['heading']}): "
                         + (done or t["reason"][:1].lower() + t["reason"][1:]),
                         "trigger Liquidity T3: " + {"secured": f"−{LIQUIDITY_T3_STEP}, secured wall",
                                                     "unsecured": f"+{LIQUIDITY_T3_STEP}, unsecured wall"}
                         .get(t["status"], "mixed, no change"), t.get("unresolved", True),
                         t.get("unresolved_why") or "the agent's answer and the verified evidence disagree")
        out.append(m)
    return out
