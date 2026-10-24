"""FO Trigger Agent, EBITDAR Margin T2: one benchmark doesn't fit a company whose segments earn at very different rates;
the outlier segment, not the credit, may be driving the score.

It runs after EBITDAR Margin T1 (on the margin T1 left), only when the pre-checks allow it
(``payload.gate.agent_triggers`` is "enabled"). Code decides whether it fires, from the segment note's tags
(``segment_table``): the margin scores 9 or more, or 2 or less; more than one reportable segment is tagged with
revenue; and a segment with 30% or more of the segments' revenue has a segment margin (segment profit / segment
revenue) 5 percentage points or more from the margin of all reportable segments together. Code names the outlier
(the furthest, when several qualify). Nothing is classified as core or non-core, so there is no list of
sub-sectors. Segment profit only compares segments: it never feeds a score. If a mix-weighted benchmark is built,
drop this trigger. Same three steps as the other triggers:

1. ``margin_t2_passages`` (deterministic) collects what the agent checks: the segment note's and MD&A's paragraphs
   and table rows that name a segment or mention segment profit or the measure the chief operating decision maker
   uses (ids G01…).
2. The Trigger Agent (prompt ``fo_trigger_margin_t2``) ALWAYS reads them, with code's segment table: whether the
   filing has a single reportable segment, whether segment profit is measured the same way across segments, and
   whether the outlier's margin is explained by how that business works or by the year's performance. Every
   finding is quoted verbatim. Only a filing with none of those passages has nothing to read (no model call:
   unresolved).
3. ``validate_margin_t2`` keeps only verbatim evidence that says what it must and RE-DERIVES the outcome. Code
   also works out the direction: the margin without the outlier (the EBITDAR margin moved by the remaining
   segments' margin less all segments' margin) is scored only to see whether it lands at least a point closer to
   p ("closer"), on the same extreme or further out ("as_extreme"), or neither. First match:

   - a single reportable segment, segment profit not shown to be measured the same way: unresolved;
   - closer, and the filing explains the outlier's margin by how that business works: 1 point toward p, never past;
   - as extreme, or the outlier's margin explained by the year's performance: no adjustment;
   - otherwise (the results don't point either way): unresolved, so the margin is escalated to an analyst.

   More than one segment tagged but no segment profit for each of them is unresolved without a model call.
"""

from __future__ import annotations

import copy
import re

from app.prompts.fo_prompts import FO_MARGIN_T2_PROMPT, FO_MARGIN_T2_SCHEMA, FO_MARGIN_T2_TEMPLATE

from .covenants import DEBT_TITLE, NOTE_HEADING, OTHER_NOTE_TITLE, SUBSEQUENT_TITLE
from .dscr_t2 import SEGMENT_TITLE
from .dscr_triggers import _ev, _held, _millions, _quoted, add_trigger_note, collect_passages, stated_amount
from .icr_triggers import MDNA_SKIP
from .measures import PRETAX_CONCEPTS, REVENUE_CONCEPTS
from .parsing import concept_label
from .precheck import DATA_NOTICE, PrecheckAgentError, call_agent, require_agent
from .scoring import headline_score, margin_settings, score_margin

MARGIN_T2_VERSION = "fo-trigger-margin-t2-1.0.0"
MARGIN_T2_ID = "margin_t2"
AGENT = "Trigger Agent"
HIGH, LOW = 9.0, 2.0  # the margin's score sets the trigger off at HIGH or more, or LOW or less
SHARE = 0.30  # an outlier has at least this share of the reportable segments' revenue
GAP = 0.05  # and a segment margin at least this far (5 percentage points) from all segments' margin
STEP = 1.0  # the move toward p

SEGMENT_AXIS = "us-gaap:StatementBusinessSegmentsAxis"
CONSOLIDATION = ("srt:ConsolidationItemsAxis", "us-gaap:OperatingSegmentsMember")
NOT_REPORTABLE = re.compile(r"Corporate|Eliminat|Intersegment|Reconcil|AllOther|Unallocat|NonSegment|Consolidat", re.I)
# Segment profit: a standard profit concept tagged for every reportable segment, else a company concept whose name
# says profit (adjusted EBITDA, segment income) and not a cost, a share or another kind of income.
PROFIT_CONCEPTS = ["us-gaap:OperatingIncomeLoss", *PRETAX_CONCEPTS, "us-gaap:GrossProfit"]
PROFIT_NAME = re.compile(r"EBITDA|Profit|Income|Earnings", re.I)
NOT_PROFIT = re.compile(r"Expense|Costs?(?!.*Income)|Attributable|EquityMethod|Interest(Income|Expense)|Comprehensive|"
                        r"PerShare|Revenue|Percentage|Tax(es)?Paid|Acquired|Translation", re.I)
STANDARD = ("us-gaap:", "ifrs-full:", "srt:", "dei:")

SECTIONS = [("segment_note", "Segment note"), ("mdna", "MD&A")]
LIMITS = {"segment_note": 20, "mdna": 25}
T2_TERMS = (r"reportable segments?|single (reportable |operating )?segment|segment (profit|margin|EBITDA|income|"
            r"measure|results|performance)|chief operating decision maker|\bCODM\b")

# --- what a quote must say ---------------------------------------------------------------------------------------
SINGLE = re.compile(r"(single|one) (reportable |operating )?segment|one reportable|operates? (in|as) (a single|one)",
                    re.I)
MEASURE = re.compile(r"segment (profit|income|EBITDA|operating|measure|performance)|chief operating decision maker|"
                     r"\bCODM\b|measure of (segment )?(profit|performance)|evaluat\w+ (segment )?performance|EBITDA|"
                     r"operating (income|profit)|profit or loss", re.I)
DIFFERENT = re.compile(r"different|differ\w*|not comparable|inconsisten\w*|var(y|ies)|other measures?|separate "
                       r"measures?", re.I)
BUSINESS = re.compile(r"\bmix\b|pricing|business model|structur\w*|nature of|capital[- ]intensive|asset[- ]light|"
                      r"pass[- ]through|(lower|higher)[- ]margin|inherent\w*|typically|cost structure|fee[- ]based|"
                      r"reimbursement (model|structure|methodolog\w*)|wholesale|distribution|regulated|cost[- ]plus",
                      re.I)
YEAR = re.compile(r"volumes?|declin\w*|increas\w*|decreas\w*|due to|driven by|impact\w*|weather|strikes?|hurricane|"
                  r"one-?time|during (the )?(year|20\d\d)|in 20\d\d|compared (to|with)|prior year|year-over-year|"
                  r"improv\w*|deteriorat\w*", re.I)

DIRECTION_TEXT = {"closer": "closer to p", "as_extreme": "just as extreme", "neither": "neither closer nor as extreme"}
STATUS_TEXT = {"skipped": "Skipped: agent triggers are skipped after a going-concern fail"}
RULES = [("A single reportable segment, no segment profit for each segment, or segment profit not shown to be measured "
          "the same way", "Unresolved: the margin is escalated to an analyst"),
         ("The remaining segments' margin is closer to p and the filing explains the outlier's margin by how that "
          "business works", "Move 1 point toward p, never past p"),
         ("The remaining segments' margin is just as extreme, or the outlier's margin is explained by the year's "
          "performance", "No adjustment"),
         ("The results don't point either way", "Unresolved: the margin is escalated to an analyst"),
         ("The agent's answer and the verified evidence disagree",
          "Unresolved: the margin is escalated to an analyst, who accepts the score or overrides it")]


# --- the segments ------------------------------------------------------------------------------------------------

def _segment_name(labels: dict, member: str) -> str:
    name = re.sub(r"\s*\[?Member\]?$", "", concept_label(labels, member)).strip()
    return re.sub(r"\s+(reportable |operating )?segment$", "", name, flags=re.I) or name


def segment_table(facts: list[dict], labels: dict, period: str) -> dict:
    """The reportable segments the segment note tags for ``period``: revenue (the first revenue concept tagged for
    each), segment profit (one concept tagged for every segment) and margin, with each segment's share of the
    segments' revenue and the margin of all of them together. Facts on the segment axis alone, or with the
    operating-segments member of the consolidation axis; corporate, other and elimination members are not
    segments."""
    by_member: dict[str, dict] = {}
    for f in facts:
        dims = f["dimensions"]
        if (f["type"] != "numeric" or f["period_key"] != period or SEGMENT_AXIS not in dims
                or not isinstance(f["value"], (int, float))):
            continue
        other = {k: v for k, v in dims.items() if k != SEGMENT_AXIS}
        if other and tuple(other.items()) != (CONSOLIDATION,):
            continue
        member = dims[SEGMENT_AXIS]
        if NOT_REPORTABLE.search(member.split(":")[-1]):
            continue
        by_member.setdefault(member, {}).setdefault(f["concept"], f["value"])
    segments = []
    for member, values in by_member.items():
        rev_c = next((c for c in REVENUE_CONCEPTS if c in values), None)
        if rev_c and values[rev_c] > 0:
            segments.append({"member": member, "name": _segment_name(labels, member), "revenue": values[rev_c],
                             "revenue_concept": rev_c, "values": values})
    profit_c = _profit_concept(segments)
    total_rev = sum(s["revenue"] for s in segments)
    for s in segments:
        values = s.pop("values")
        s["profit"] = values.get(profit_c) if profit_c else None
        s["margin"] = s["profit"] / s["revenue"] if s["profit"] is not None else None
        s["share"] = s["revenue"] / total_rev if total_rev else None
    segments.sort(key=lambda s: -s["revenue"])
    total_profit = sum(s["profit"] for s in segments) if profit_c else None
    return {"segments": segments, "profit_concept": profit_c,
            "profit_label": concept_label(labels, profit_c) if profit_c else None,
            "revenue": total_rev, "profit": total_profit,
            "margin": total_profit / total_rev if profit_c and total_rev else None}


def _profit_concept(segments: list[dict]) -> str | None:
    """The segment profit concept: tagged for every segment, standard concepts first, then a company concept whose
    name says profit (segment-named first, then EBITDA, then the rest, alphabetically)."""
    if not segments:
        return None
    common = set.intersection(*(set(s["values"]) for s in segments))
    found = next((c for c in PROFIT_CONCEPTS if c in common), None)
    if found:
        return found
    company = [c for c in common if not c.startswith(STANDARD) and PROFIT_NAME.search(c.split(":")[-1])
               and not NOT_PROFIT.search(c.split(":")[-1])]
    company = [c for c in company if all(abs(s["values"][c]) < s["revenue"] for s in segments)]
    company.sort(key=lambda c: ("Segment" not in c, "EBITDA" not in c.upper(), c))
    return company[0] if company else None


def find_outlier(table: dict) -> dict | None:
    """The segment with SHARE or more of revenue whose margin is GAP or more from all segments' margin (the
    furthest; then the largest), with the remaining segments' revenue, profit and margin."""
    if table["margin"] is None:
        return None
    cands = [s for s in table["segments"] if s["share"] >= SHARE and abs(s["margin"] - table["margin"]) >= GAP - 1e-9]
    if not cands:
        return None
    out = max(cands, key=lambda s: (abs(s["margin"] - table["margin"]), s["revenue"]))
    rest_rev, rest_profit = table["revenue"] - out["revenue"], table["profit"] - out["profit"]
    return {**out, "gap": out["margin"] - table["margin"], "rest_revenue": rest_rev, "rest_profit": rest_profit,
            "rest_margin": rest_profit / rest_rev if rest_rev > 0 else None}


def direction(col: dict, settings: dict, table: dict, outlier: dict) -> dict:
    """Whether the margin without the outlier would score closer to p: the EBITDAR margin moved by the remaining
    segments' margin less all segments' margin, scored on the measure's own settings for this comparison only (never
    as the measure's score). 'closer': at least STEP closer to p; 'as_extreme': still on the same extreme, or further
    from p; else 'neither'."""
    p, score = settings["values"]["p"], col["score"]
    shift = outlier["rest_margin"] - table["margin"]
    probe = {"ratio": col["ratio"] + shift, "reason": None}
    score_margin(probe, settings)
    implied = probe["score"]
    if abs(implied - p) <= abs(score - p) - STEP + 1e-9:
        d = "closer"
    elif (score >= HIGH and implied >= HIGH) or (score <= LOW and implied <= LOW) or abs(implied - p) >= abs(score - p):
        d = "as_extreme"
    else:
        d = "neither"
    return {"direction": d, "p": p, "shift": shift, "implied_ratio": probe["ratio"], "implied_score": implied}


# --- the passages ------------------------------------------------------------------------------------------------

def _section_after(item: str, heading: str, current: str | None, numbered: bool) -> str | None:
    """The searched section a heading opens, keeps or closes: MD&A (without its boilerplate) and the segment note."""
    if item == "7":
        return None if MDNA_SKIP.search(heading) else "mdna"
    if numbered and not NOTE_HEADING.match(heading):
        return current  # a sub-heading inside the current note
    if SEGMENT_TITLE.search(heading) and len(heading) <= 80:
        return "segment_note"
    if numbered or OTHER_NOTE_TITLE.search(heading) or DEBT_TITLE.search(heading) or SUBSEQUENT_TITLE.search(heading):
        return None
    return current


def _terms(names: list[str]):
    return re.compile("|".join([*(re.escape(n) for n in names if n), T2_TERMS]), re.I)


def margin_t2_passages(data: dict, names: list[str]) -> dict:
    """The segment note's and MD&A's paragraphs and tables that name a segment or mention segment profit, reportable
    segments or the chief operating decision maker; ids G01… in document order."""
    return collect_passages(data, "G", LIMITS, SECTIONS, _terms(names), _section_after)


def _headline_index(m: dict) -> int | None:
    return next((i for i, c in enumerate(m["columns"]) if c.get("score") is not None), None)


def _pct(v):
    return None if v is None else round(v * 100, 1)


def margin_t2_inputs(t: dict, meta: dict, fx: dict) -> dict:
    o = fx["outlier"]
    return {
        "data_notice": DATA_NOTICE,
        "company": {"entity_name": meta.get("entity_name"), "fiscal_year": meta.get("fiscal_year"),
                    "period_end": fx["period_end"]},
        "margin": {"column": fx["heading"], "ebitdar_margin_pct": _pct(fx["ratio"]), "score": fx["score"],
                   "p": fx["p"], "trigger": f"the EBITDAR margin scores {HIGH:g} or more, or {LOW:g} or less, and a "
                                            f"segment with {SHARE:.0%} or more of revenue has a margin "
                                            f"{GAP * 100:g} points or more from all segments' margin"},
        "segments": {
            "profit_measure_tagged": fx["profit_label"],
            "reportable": [{"name": s["name"], "revenue_millions": _millions(s["revenue"]),
                            "profit_millions": _millions(s["profit"]), "margin_pct": _pct(s["margin"]),
                            "share_of_revenue_pct": _pct(s["share"])} for s in fx["segments"]],
            "all_segments_margin_pct": _pct(fx["segment_margin"]),
            "outlier": o["name"], "outlier_margin_pct": _pct(o["margin"]),
            "remaining_segments_margin_pct": _pct(o["rest_margin"]),
            "direction": fx["direction"],
        },
        "sections_searched": [{"section": s["label"], "located": s["located"], "passages": s["passages"]}
                              for s in t["sections"]],
        "passages": [{"passage_id": p["passage_id"], "item": p["item"], "section": p["section_label"],
                      "kind": p["kind"], "text": p["text"]} for p in t["passages"]],
    }


# --- validation --------------------------------------------------------------------------------------------------

def _finding(parsed: dict, key: str, allowed: dict, by_id: dict, held: list, default: str, name: str | None = None):
    """(finding, evidence): the agent's finding when its quote is verbatim and says what the finding needs (the
    passage names the outlier too, for ``name``), else ``default``."""
    o = parsed.get(key) or {}
    finding = o.get("finding")
    if finding not in allowed:
        return default, None
    need, why = allowed[finding]
    p, why = _quoted(o.get("evidence"), by_id, need, why)
    if not why and name and name.lower() not in p["text"].lower():
        why = f"the cited passage doesn't name the {name} segment"
    if why:
        held.append(_held({"item_id": key}, finding, o.get("evidence"), why))
        return default, None
    return finding, _ev(o["evidence"], p)


def validate_margin_t2(parsed: dict, t: dict, fx: dict) -> dict:
    """Verify the agent's three findings and re-derive the outcome (first match): unresolved on a single reportable
    segment or segment profit not shown to be measured the same way; moved toward p when closer and explained by how
    the business works; no adjustment when as extreme or explained by the year; otherwise unresolved."""
    by_id = {p["passage_id"]: p for p in t["passages"]}
    held, notes = [], []
    segments, seg_ev = _finding(parsed, "reportable_segments",
                                {"single": (SINGLE, "the quote doesn't say there is a single reportable segment")},
                                by_id, held, "multiple")
    measure, measure_ev = _finding(parsed, "profit_measure",
                                   {"same_measure": (MEASURE, "the quote doesn't name the segment profit measure"),
                                    "not_comparable": (DIFFERENT, "the quote doesn't say segments are measured "
                                                                  "differently")},
                                   by_id, held, "unclear")
    if measure == "not_comparable" and not MEASURE.search(measure_ev["quote"]):
        held.append(_held({"item_id": "profit_measure"}, measure, measure_ev, "the quote doesn't name a measure"))
        measure, measure_ev = "unclear", None
    explanation, expl_ev = _finding(parsed, "outlier_explanation",
                                    {"business_model": (BUSINESS, "the quote doesn't explain the margin by how the "
                                                                  "business works"),
                                     "year_performance": (YEAR, "the quote doesn't explain the margin by the year's "
                                                                "performance")},
                                    by_id, held, "none", name=fx["outlier"]["name"])
    d, why = fx["direction"], None
    if segments == "single":
        status, why = "unresolved", "the filing reports a single reportable segment"
    elif measure == "not_comparable":
        status, why = "unresolved", "segment profit isn't measured the same way across segments"
    elif measure != "same_measure":
        status, why = "unresolved", "the segment note doesn't show segment profit is measured the same way across " \
                                    "segments"
    elif d == "closer" and explanation == "business_model":
        status = "moved_toward_p"
    elif d == "as_extreme" or explanation == "year_performance":
        status = "no_adjustment"
    else:
        status = "unresolved"
        why = (f"the results don't point either way (the remaining segments' margin is {DIRECTION_TEXT[d]}; the "
               f"{fx['outlier']['name']} margin is " + {"business_model": "explained by how that business works",
                                                         "none": "not explained"}.get(explanation, "explained") + ")")
    if d == "closer" and explanation == "none" and status == "unresolved" and measure == "same_measure":
        notes.append("Closer to p, but nothing in MD&A or the segment note explains the outlier's margin by how that "
                     "business works.")
    return {"status": status, "reportable_segments": segments, "segments_evidence": seg_ev,
            "profit_measure": measure, "measure_evidence": measure_ev, "outlier_explanation": explanation,
            "explanation_evidence": expl_ev, "unresolved_why": why, "held_back": held,
            "stated_status": parsed.get("status"), "status_consistent": parsed.get("status") == status,
            "validation_notes": notes}


def agent_reading(parsed: dict) -> dict:
    """The agent's answer as it states it (not verified, not applied), for an analyst to compare with the verified
    outcome: "moved_toward_p" moves the score toward p. Any other answer changes nothing."""
    stated = parsed.get("status")
    return {"status": stated, "notes": [], "applies": {"status": stated}}


def register_prompt(session):
    from app.prompts.registry import PromptRegistry

    registry = PromptRegistry(session)
    registry.register(FO_MARGIN_T2_PROMPT, template=FO_MARGIN_T2_TEMPLATE, response_schema=FO_MARGIN_T2_SCHEMA,
                      role="analyze", label="Financial Overview Trigger Agent: EBITDAR Margin T2")
    return registry


# --- the trigger -------------------------------------------------------------------------------------------------

def moved_score(score: float, p: float) -> float:
    """STEP toward p, never past it."""
    return round(max(p, score - STEP) if score > p else min(p, score + STEP), 1)


def _reason(status: str, fx: dict, checked: dict) -> str:
    o = fx["outlier"]
    if status == "moved_toward_p":
        return (f"Without {o['name']} the segments' margin is closer to p ({_pct(o['rest_margin'])}% against "
                f"{_pct(fx['segment_margin'])}% for all segments) and the filing explains {o['name']}'s margin by how "
                f"that business works ({checked['explanation_evidence']['passage_id']}): score {fx['score']:g} → "
                f"{moved_score(fx['score'], fx['p']):g}")
    if status == "no_adjustment":
        return (f"{o['name']} ({_pct(o['margin'])}% margin against {_pct(fx['segment_margin'])}% for all segments): "
                + ("the remaining segments' margin is just as extreme" if fx["direction"] == "as_extreme" else
                   f"its margin is explained by the year's performance "
                   f"({checked['explanation_evidence']['passage_id']})") + ", no adjustment")
    return "Unresolved: " + checked["unresolved_why"]


def margin_t2_trigger(data: dict, metrics: list[dict], gate: dict, *, session=None, backend=None,
                      mode: str = "live", case_id: str | None = None) -> dict:
    """The EBITDAR Margin T2 trigger result recorded in the run payload (``payload.triggers.margin_t2``)."""
    m = next((x for x in metrics if x["key"] == "margin"), None)
    score, heading = headline_score(m) if m else (None, None)
    result = {"trigger": MARGIN_T2_ID, "measure": "margin", "version": MARGIN_T2_VERSION, "score": score,
              "column": heading, "fired": False, "mode": mode if backend is not None else "none", "agent": None,
              "notes": [], "review_required": False, "thresholds": {"high": HIGH, "low": LOW, "share": SHARE,
                                                                     "gap": GAP}}
    if gate.get("agent_triggers") != "enabled":
        result.update(status="skipped", decided_by="gate", reason=STATUS_TEXT["skipped"],
                      effect="None: the measures are superseded by the going-concern fail.")
        return result
    if score is None or LOW < score < HIGH:
        result.update(status="not_triggered", decided_by="score", effect="None.",
                      reason=("The EBITDAR margin has no score" if score is None else
                              f"EBITDAR margin score {score:g} (between {LOW:g} and {HIGH:g})")
                      + ": trigger EBITDAR Margin T2 not set off")
        return result
    col = m["columns"][_headline_index(m)]
    settings = margin_settings(m)
    table = segment_table(data["facts"], data["labels"], col["period"])
    segs = table["segments"]
    fx = {"heading": col["heading"], "period": col["period"], "period_end": col["period"].split("--")[1],
          "ratio": col["ratio"], "score": score, "p": settings["values"]["p"], "segments": segs,
          "profit_concept": table["profit_concept"], "profit_label": table["profit_label"],
          "segment_revenue": table["revenue"], "segment_profit": table["profit"], "segment_margin": table["margin"]}
    result["inputs"] = fx
    if len(segs) < 2:
        result.update(status="not_triggered", decided_by="segments", effect="None.",
                      reason=(f"{'One reportable segment' if segs else 'No reportable segment'} tagged with revenue "
                              f"for {col['heading']}: trigger EBITDAR Margin T2 not set off"))
        return result
    if table["profit_concept"] is None:
        why = "no segment profit is tagged for each reportable segment, so segment margins can't be compared"
        result.update(fired=True, fired_by=f"EBITDAR margin score {score:g} with {len(segs)} reportable segments",
                      status="unresolved", decided_by="segments", passages=[], sections=[], unresolved=True,
                      disagreement=False, review_required=True, unresolved_why=why, reason="Unresolved: " + why,
                      effect=f"None to the score; unresolved ({why}), so the margin is escalated to an analyst, who "
                             "accepts the score or overrides it.")
        result["notes"].append("No segment profit tagged for every segment: the agent was not called.")
        return result
    outlier = find_outlier(table)
    if outlier is None or outlier["rest_margin"] is None:
        result.update(status="not_triggered", decided_by="segments", effect="None.",
                      reason=f"No segment with {SHARE:.0%} or more of revenue has a margin {GAP * 100:g} points or "
                             f"more from all segments' {_pct(table['margin'])}%: trigger EBITDAR Margin T2 not set off")
        return result
    fx["outlier"] = outlier
    fx.update(direction(col, settings, table, outlier))
    t = margin_t2_passages(data, [s["name"] for s in segs])
    result.update(fired=True, fired_by=(f"EBITDAR margin score {score:g}; {outlier['name']} has {_pct(outlier['share'])}%"
                                        f" of segment revenue at a {_pct(outlier['margin'])}% margin against "
                                        f"{_pct(table['margin'])}% for all segments"),
                  passages=t["passages"], sections=t["sections"])
    if not t["passages"]:
        checked = {"status": "unresolved", "reportable_segments": "multiple", "segments_evidence": None,
                   "profit_measure": "unclear", "measure_evidence": None, "outlier_explanation": "none",
                   "explanation_evidence": None, "status_consistent": True,
                   "unresolved_why": "no segment note or MD&A passage to read"}
        decided_by = "no_text"
        result["notes"].append("No segment note or MD&A passage to read: the agent was not called.")
    else:
        require_agent(backend, MARGIN_T2_ID, AGENT)
        checked, decided_by = _run_agent(result, t, data, fx, session, backend, case_id)
    status = checked["status"]
    disagree = not checked["status_consistent"]
    unresolved = status == "unresolved" or disagree
    why = ("the agent's answer and the verified evidence disagree" if disagree else checked["unresolved_why"]) \
        if unresolved else None
    result.update(status=status, decided_by=decided_by,
                  findings={k: checked[k] for k in ("reportable_segments", "profit_measure", "outlier_explanation")},
                  evidence={k: checked[e] for k, e in (("reportable_segments", "segments_evidence"),
                                                       ("profit_measure", "measure_evidence"),
                                                       ("outlier_explanation", "explanation_evidence"))
                            if checked[e]},
                  recomputed=moved_score(score, fx["p"]) if status == "moved_toward_p" else None,
                  reason=_reason(status, fx, checked), unresolved=unresolved, disagreement=disagree,
                  review_required=unresolved, unresolved_why=why,
                  effect=({"moved_toward_p": f"The margin's score moves {STEP:g} point toward p ({fx['p']:g}), never "
                                             "past it; the ratio is unchanged",
                           "no_adjustment": "No adjustment"}.get(status, "No adjustment to the score"))
                  + (f"; unresolved ({why}), so the margin is escalated to an analyst, who accepts the score or "
                     "overrides it." if unresolved else
                     "; resolved (the agent and the verified evidence agree), so no analyst decision is needed."))
    return result


def _run_agent(result, t, data, fx, session, backend, case_id):
    run, agent = call_agent(session, backend, case_id, register_prompt(session), FO_MARGIN_T2_PROMPT,
                            margin_t2_inputs(t, data["meta"], fx))
    result["agent"] = agent
    if not run.is_valid:
        raise PrecheckAgentError(f"The Trigger Agent's EBITDAR Margin T2 output was rejected "
                                 f"({agent['validation_detail']}); the run was not recorded. Run it again.")
    checked = validate_margin_t2(run.parsed, t, fx)
    result["agent_reading"] = agent_reading(run.parsed)
    agent.update(rationale=run.parsed.get("rationale"), missing_information=run.parsed.get("missing_information") or [],
                 held_back=checked["held_back"], derived_status=checked["status"],
                 stated_status=checked["stated_status"], status_consistent=checked["status_consistent"])
    result["notes"] += checked["validation_notes"]
    if checked["held_back"]:
        result["notes"].append(f"{len(checked['held_back'])} agent finding(s) held back by validation.")
    if not checked["status_consistent"]:
        result["notes"].append(f"The agent stated {str(checked['stated_status']).upper()} but its verified evidence "
                               f"supports {checked['status'].upper()}; the verified evidence decides.")
    return checked, "agent"


# --- the measure -------------------------------------------------------------------------------------------------

def apply_margin_t2(fo: list[dict], t: dict | None) -> list[dict]:
    """The EBITDAR margin with the trigger's outcome on its headline column (a copy; the inputs stay untouched): the
    score moved 1 point toward p, never past it, when the outcome is "moved_toward_p". The ratio stays as it is; the
    column keeps the score as calculated. The measure is marked for escalation when the trigger is unresolved."""
    if not t or not t.get("fired"):
        return fo
    out = []
    for m in fo:
        if m["key"] != "margin":
            out.append(m)
            continue
        m = copy.deepcopy(m)
        col = m["columns"][_headline_index(m)]
        fx = t.get("inputs") or {}
        o = fx.get("outlier") or {}
        adj = {"trigger": "EBITDAR Margin T2", "outcome": t["status"], "calculated": col["score"],
               "ratio_calculated": col["ratio"], "outlier": o.get("name"), "outlier_margin": o.get("margin"),
               "segment_margin": fx.get("segment_margin"), "remaining_margin": o.get("rest_margin"),
               "direction": fx.get("direction"), "recomputed": None}
        done = "no adjustment"
        if t["status"] == "moved_toward_p":
            col["score"] = moved_score(col["score"], fx["p"])
            adj["recomputed"] = col["score"]
            done = (f"risk score {adj['calculated']:g} → {adj['recomputed']:g} ({STEP:g} point toward p = {fx['p']:g}: "
                    f"{o.get('name')} earns at a different rate by how that business works)")
        adj["score"] = col["score"]
        col["margin_t2_adjustment"] = adj
        add_trigger_note(m, f"EBITDAR Margin trigger T2 ({t.get('fired_by')}, {col['heading']}): {done}",
                         "trigger EBITDAR Margin T2: " + {"moved_toward_p": "1 point toward p",
                                                          "no_adjustment": "no adjustment"}.get(t["status"],
                                                                                                "unresolved"),
                         t.get("unresolved", True), t.get("unresolved_why") or "segment dispersion unresolved")
        out.append(m)
    return out
