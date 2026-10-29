"""Run the Financial Overview over one filing folder and record the result.

The FO counterpart of ``app.services.bo.runner``: load the filing from
``edgar_filings/``, run the pre-checks (going concern, ``precheck.py``; covenants,
``covenants.py``; acquisitions and divestitures, ``acquisitions.py``; prepaid model,
``prepaid.py``), compute the measures (DSO and Debt/EBITDAR adjusted for the year's deals; DSO on
billed revenue, or its weight factored, for a prepaid model), score them against the industry
benchmark under the pre-checks' gate, run the trigger agents the measures set off (DSCR T2,
``dscr_t2.py``, and T3, ``dscr_t3.py``, then DSCR T1, ``dscr_triggers.py``; ICR T2, ``icr_t2.py``, T3, ``icr_t3.py``, then ICR T1, ``icr_triggers.py``;
Debt/EBITDAR T4, ``leverage_t4.py``, then T1, ``leverage_triggers.py``, T2, ``leverage_t2.py``, and T3,
``leverage_t3.py``; EBITDAR Margin T1, ``margin_triggers.py``, then T2, ``margin_t2.py``; RCF/Debt T1, ``rcf_triggers.py``, T2, ``rcf_t2.py``, T3, ``rcf_t3.py``, then T4, ``rcf_t4.py``;
DSO T1, ``dso_triggers.py``; Liquidity T1, ``liquidity_triggers.py``, T2, ``liquidity_t2.py``, then T3, ``liquidity_t3.py``) and record one
``financial_overview_runs`` row holding the scored overview exactly as produced (content-hashed).
Every number is computed by deterministic code. The only model calls are the Pre-check Agent's and the Trigger Agent's, when a backend
is given; their findings are verified before they count.

Anything escalated holds the run at ``awaiting_review`` until an analyst decides
(``review.py``): a going-concern FAIL is continue (composite 10, measures superseded) or
stop; a covenant breach escalates the liquidity-and-maturity measure, an unresolved
acquisition or divestiture escalates DSO and/or Debt/EBITDAR, and an unresolved trigger
(its agent and verified evidence disagree) escalates its measure (DSCR), whose score the
analyst accepts or overrides.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.hashing import canonical_json, content_hash
from app.models.base import utcnow
from app.models.orm import FinancialOverviewRunRow

from . import loader
from .acquisitions import MEASURE_NAMES as DEAL_MEASURE_NAMES
from .acquisitions import acquisitions_precheck, apply_deal_adjustments
from .covenants import covenant_precheck
from .dso_triggers import DSO_T1_DAYS, DSO_T1_ID, apply_dso_t1, dso_t1_trigger
from .dscr_t2 import T2_ID, T2_MULTIPLE, apply_t2, dscr_t2_trigger
from .dscr_t3 import T3_ID, T3_SHARE, apply_t3, dscr_t3_trigger
from .dscr_triggers import T1_ID, T1_THRESHOLD, apply_t1, dscr_t1_trigger
from .icr_t2 import ICR_T2_ID, apply_icr_t2, icr_t2_trigger
from .icr_t3 import ICR_T3_ID, apply_icr_t3, icr_t3_trigger
from .icr_triggers import ICR_T1_ID, ICR_T1_THRESHOLD, apply_icr_t1, icr_t1_trigger
from .leverage_t2 import LEVERAGE_T2_GAP, LEVERAGE_T2_ID, apply_leverage_t2, leverage_t2_trigger
from .leverage_t3 import LEVERAGE_T3_GAP, LEVERAGE_T3_ID, apply_leverage_t3, leverage_t3_trigger
from .leverage_t4 import LEVERAGE_T4_ID, apply_leverage_t4, leverage_t4_trigger
from .rcf_t2 import RCF_T2_ID, apply_rcf_t2, rcf_t2_trigger
from .rcf_t3 import RCF_T3_ID, apply_rcf_t3, rcf_t3_trigger
from .rcf_t4 import RCF_T4_ID, apply_rcf_t4, rcf_t4_trigger
from .rcf_triggers import RCF_T1_ID, RCF_T1_THRESHOLD, apply_rcf_t1, rcf_t1_trigger
from .margin_triggers import MARGIN_T1_ID, apply_margin_t1, margin_t1_trigger
from .margin_t2 import MARGIN_T2_ID, apply_margin_t2, margin_t2_trigger
from .leverage_triggers import LEVERAGE_T1_ID, LEVERAGE_T1_SHARE, apply_leverage_t1, leverage_t1_trigger
from .liquidity_t2 import LIQUIDITY_T2_ID, LIQUIDITY_T2_THRESHOLD, apply_liquidity_t2, liquidity_t2_trigger
from .liquidity_t3 import LIQUIDITY_T3_ID, LIQUIDITY_T3_SHARE, apply_liquidity_t3, liquidity_t3_trigger
from .liquidity_triggers import LIQUIDITY_T1_ID, apply_liquidity_t1, liquidity_t1_trigger
from .prepaid import apply_prepaid_adjustments, prepaid_precheck
from .industry import classify_industry, reference_hash
from .measures import build_financial_overview, credit_rating, risk_level
from .precheck import CHECK_LABELS, going_concern_precheck
from .scoring import (
    COVENANT_BREACH_SCORE,
    COVENANT_RELIEF_UPLIFT,
    GC_WATCH_LIQUIDITY_FLOOR,
    composite_risk,
    headline_score,
    score_financial_overview,
    scoring_settings,
)

ENGINE_VERSION = "fo-engine-1.27.0"
AWAITING_REVIEW = "awaiting_review"
NOT_SCORED = "Not scored: going-concern fail"
LIQUIDITY_NAME = "Liquidity and debt maturity"
# Steps in build(): reading the filing, the four pre-checks, scoring and the twenty trigger checks.
BUILD_STEPS = 1 + 4 + 1 + 20


def gate_for(gc_status: str, covenant_status: str = "compliant", acquisitions: dict | None = None,
             prepaid: dict | None = None) -> dict:
    """What the pre-checks do to the rest of the run. ``agent_triggers`` is what every agent a measure triggers
    (today DSCR T2, T3 and T1: ``dscr_t2.py``, ``dscr_t3.py``, ``dscr_triggers.py``; ICR T2, T3 and T1, ``icr_t2.py``, ``icr_t3.py``, ``icr_triggers.py``; Debt/EBITDAR T4, T1, T2 and T3, ``leverage_t4.py``, ``leverage_triggers.py``, ``leverage_t2.py``, ``leverage_t3.py``; EBITDAR Margin T1 and T2, ``margin_triggers.py``, ``margin_t2.py``; RCF/Debt T1-T4, ``rcf_triggers.py``, ``rcf_t2.py``, ``rcf_t3.py``, ``rcf_t4.py``; DSO T1, ``dso_triggers.py``; Liquidity T1-T3, ``liquidity_triggers.py``, ``liquidity_t2.py``, ``liquidity_t3.py``) honours: "skipped" on a
    going-concern fail."""
    ad, pp = acquisitions or {}, prepaid or {}
    adjusted = [k for k, v in (ad.get("adjustments") or {}).items() if v]
    return {"going_concern": gc_status,
            "liquidity_floor": GC_WATCH_LIQUIDITY_FLOOR if gc_status == "watch" else None,
            "covenants": covenant_status,
            "liquidity_adjustment": ({"breach": f"revolver availability 0; score set to {COVENANT_BREACH_SCORE}",
                                      "relief": f"+{COVENANT_RELIEF_UPLIFT} after calculation"}
                                     .get(covenant_status)),
            "acquisitions": ad.get("status", "none"),
            "deal_adjustments": adjusted,
            "deal_unresolved": list(ad.get("unresolved_measures") or []),
            "prepaid": pp.get("status", "not_applicable"),
            "dso_weight_factor": pp.get("factor"),
            "measures_counted": gc_status != "fail",
            "agent_triggers": "skipped" if gc_status == "fail" else "enabled",
            "requires_decision": gc_status == "fail" or covenant_status == "breach"
            or ad.get("status") == "unresolved" or pp.get("status") == "unresolved"}


def escalations_for(gc: dict, cov: dict, metrics: list[dict], ad: dict | None = None,
                    pp: dict | None = None, triggers: dict | None = None) -> list[dict]:
    """What waits for an analyst. A going-concern fail is a run decision (continue or stop); with it, the measures
    are superseded and nothing else is escalated. Otherwise each unresolved pre-check escalates the measure it
    adjusts, for the analyst to accept the engine's score or override it: a covenant breach (outcome a) escalates
    liquidity and debt maturity; an unresolved acquisition or divestiture escalates DSO and/or Debt/EBITDAR. A
    trigger that fired escalates its measure only when unresolved: its agent and verified evidence disagree (DSCR
    T2, T3 and T1: DSCR, one escalation for all; ICR T2, T3 and T1: ICR, also when T2 finds no gross interest or income, T3 no pre-tax income, or T1 nothing;
    Debt/EBITDAR T1 and T2: Debt/EBITDAR, also when T1 is partly quantified with an unexplained remainder or nothing
    explains it, or T2 finds earmarked or trapped cash it can't quantify; DSO T1: DSO, also when DSO went up with a
    cause and no fix, or a mechanical cause isn't quantified; Liquidity T1 and T2: liquidity and debt maturity, also
    when a revolver's availability can't be worked out, or a refinancing is only planned or can't be placed);
    otherwise its verified outcome stands. Where the agent read a check more severely than its verified
    evidence supports (``disagreements``), that is escalated too."""
    if gc["status"] == "fail":
        return [{"id": "going_concern", "type": "run_decision", "check": "going_concern", "reason": gc["reason"]}]
    by_key = {m["key"]: m for m in metrics}
    out = []
    if cov["status"] == "breach":
        liquidity = by_key.get("liquidity")
        out.append({"id": "covenants:liquidity", "type": "measure_override", "check": "covenants",
                    "title": "covenant breach", "measure": "liquidity", "measure_name": LIQUIDITY_NAME,
                    "engine_score": headline_score(liquidity)[0] if liquidity else None,
                    "calculated_score": ((liquidity["columns"][0].get("covenant_adjustment") or {}).get("calculated")
                                         if liquidity and liquidity["columns"] else None),
                    "reason": cov["reason"],
                    "detail": f"The engine set revolver availability to 0 and the score to {COVENANT_BREACH_SCORE}."})
    for measure in (ad or {}).get("unresolved_measures") or []:
        deals = [d["deal_id"] for d in ad["deals"] if measure in d["effects"]["unresolved"]]
        _escalate(out, by_key, "acquisitions", measure, "acquisition or divestiture unresolved",
                  ad["reason"] + (f" ({', '.join(deals)})" if deals else ""),
                  "The engine left this measure as reported, without the deal's figures.")
    if (pp or {}).get("status") == "unresolved":
        _escalate(out, by_key, "prepaid", "dso", "prepaid model unresolved", pp["reason"],
                  "The engine kept DSO as calculated, at its normal weight.")
    # In run order (T2, T3, then T1): the first unresolved one's calculated score is DSCR's before the triggers.
    for key, title, why, detail in (
            (T2_ID, f"DSCR trigger T2 unresolved (CapEx above {T2_MULTIPLE:g}× depreciation)",
             lambda t: f"CapEx is {t['capex']['multiple']:.2f}× depreciation", T2_DETAIL),
            (T3_ID, f"DSCR trigger T3 unresolved (cash taxes vs current tax expense above {T3_SHARE:.0%} of CFADS)",
             lambda t: f"Cash taxes paid differ from current tax expense by {t['taxes']['gap_share']:.1%} of CFADS",
             T3_DETAIL)):
        t = (triggers or {}).get(key)
        if not (t and t.get("fired") and t.get("unresolved", True)):
            continue
        said = str(((t.get("agent") or {}).get("stated_status") or "none")).replace("_", " ")
        _escalate(out, by_key, key, "dscr", title,
                  f"{why(t)} in {t['column']}: the agent read {said}; the verified evidence gives "
                  f"{t['status'].replace('_', ' ')} ({t['reason']})" + agent_reading_text(t), detail, disagreement=True)
        e = next(e for e in out if e.get("measure") == "dscr")
        e["calculated_score"] = e.get("calculated_score") or t["score"]
    t1 = (triggers or {}).get(T1_ID)
    if t1 and t1.get("fired") and t1.get("unresolved", True):  # unresolved: agent and verified evidence disagree
        said = str(((t1.get("agent") or {}).get("stated_status") or "none")).replace("_", " ")
        _escalate(out, by_key, T1_ID, "dscr", f"DSCR trigger T1 unresolved (score {T1_THRESHOLD} or more)",
                  f"DSCR scored {t1['score']:g} in {t1['column']}: the agent read {said}; the verified evidence "
                  f"gives {t1['status'].replace('_', ' ')} ({t1['reason']})" + agent_reading_text(t1), T1_DETAIL,
                  disagreement=True)
        e = next(e for e in out if e.get("measure") == "dscr")
        e["calculated_score"] = e.get("calculated_score") or t1["score"]
    # ICR, in run order (T2, T3, then T1): unresolved when neither gross interest nor interest income is disclosed
    # (T2), pre-tax income isn't found (T3), nothing is found (T1), or the agent and the evidence disagree.
    for key, title, detail in ((ICR_T2_ID, "ICR trigger T2 unresolved (interest from a net or operating tag)",
                                ICR_T2_DETAIL),
                               (ICR_T3_ID, "ICR trigger T3 unresolved (EBIT from operating income)", ICR_T3_DETAIL),
                               (ICR_T1_ID, f"ICR trigger T1 unresolved (score {ICR_T1_THRESHOLD:g} or more)",
                                ICR_T1_DETAIL)):
        ti = (triggers or {}).get(key)
        if not (ti and ti.get("fired") and ti.get("unresolved", True)):
            continue
        said = str(((ti.get("agent") or {}).get("stated_status") or "none")).replace("_", " ")
        lead = (f"ICR scored {ti['score']:g} in {ti['column']}: " if ti.get("score") is not None
                else f"ICR in {ti['column']}: ")
        _escalate(out, by_key, key, "icr", title,
                  lead + (f"the agent read {said}; the verified evidence gives {ti['status'].replace('_', ' ')} "
                          f"({ti['reason']})" if ti.get("disagreement") else ti["reason"]) + agent_reading_text(ti),
                  detail, disagreement=bool(ti.get("disagreement")))
        e = next(e for e in out if e.get("measure") == "icr")
        e["calculated_score"] = e.get("calculated_score") or ti["score"]
    # Debt/EBITDAR, in run order (T4, T1, T2, then T3): one escalation for all of them.
    for key, title, detail in ((LEVERAGE_T4_ID, "Debt/EBITDAR trigger T4 unresolved (total debt from a fallback)",
                                LEVERAGE_T4_DETAIL),
                               (LEVERAGE_T1_ID, f"Debt/EBITDAR trigger T1 unresolved (net one-offs above "
                                                f"{LEVERAGE_T1_SHARE:.0%} of EBITDAR, or EBITDAR ≤ 0)", LEVERAGE_T1_DETAIL),
                               (LEVERAGE_T2_ID, f"Debt/EBITDAR trigger T2 unresolved (gross and net scores "
                                                f"{LEVERAGE_T2_GAP} or more apart)", LEVERAGE_T2_DETAIL),
                               (LEVERAGE_T3_ID, f"Debt/EBITDAR trigger T3 unresolved (score with leases "
                                                f"{LEVERAGE_T3_GAP} or more worse than without)", LEVERAGE_T3_DETAIL)):
        tl = (triggers or {}).get(key)
        if not (tl and tl.get("fired") and tl.get("unresolved", True)):
            continue
        said = str(((tl.get("agent") or {}).get("stated_status") or "none")).replace("_", " ")
        _escalate(out, by_key, key, "leverage", title,
                  f"Debt/EBITDAR in {tl['column']}, {tl['fired_by']}: "
                  + (f"the agent read {said}; the verified evidence gives {tl['status'].replace('_', ' ')} "
                     f"({tl['reason']})" if tl.get("disagreement") else tl["reason"]) + agent_reading_text(tl),
                  detail, disagreement=bool(tl.get("disagreement")))
        e = next(e for e in out if e.get("measure") == "leverage")
        e["calculated_score"] = e.get("calculated_score") or tl["score"]
    # The EBITDAR margin, in run order (T1, then T2): one escalation for both.
    for key, title, detail in ((MARGIN_T1_ID, "EBITDAR Margin trigger T1 unresolved (a one-off's location unclear)",
                                MARGIN_T1_DETAIL),
                               (MARGIN_T2_ID, "EBITDAR Margin trigger T2 unresolved (segment margins dispersed)",
                                MARGIN_T2_DETAIL)):
        tm = (triggers or {}).get(key)
        if not (tm and tm.get("fired") and tm.get("unresolved", True)):
            continue
        said = str(((tm.get("agent") or {}).get("stated_status") or "none")).replace("_", " ")
        _escalate(out, by_key, key, "margin", title,
                  f"EBITDAR margin in {tm['column']}, {tm['fired_by']}: "
                  + (f"the agent read {said}; the verified evidence gives {tm['status'].replace('_', ' ')} "
                     f"({tm['reason']})" if tm.get("disagreement") else tm["reason"]) + agent_reading_text(tm),
                  detail, disagreement=bool(tm.get("disagreement")))
        e = next(e for e in out if e.get("measure") == "margin")
        e["calculated_score"] = e.get("calculated_score") or tm["score"]
    # RCF / debt, in run order (T1, T2, T3, then T4): one escalation for all of them.
    for key, title, detail in ((RCF_T1_ID, f"RCF/Debt trigger T1 unresolved (score {RCF_T1_THRESHOLD} or more, or RCF "
                                           "/ debt below zero)", RCF_T1_DETAIL),
                               (RCF_T2_ID, "RCF/Debt trigger T2 unresolved (a one-off cash inflow)", RCF_T2_DETAIL),
                               (RCF_T3_ID, "RCF/Debt trigger T3 unresolved (NCI distributions missing, NCI material)",
                                RCF_T3_DETAIL),
                               (RCF_T4_ID, "RCF/Debt trigger T4 unresolved (NCI distributions from "
                                           "PaymentsToMinorityShareholders)", RCF_T4_DETAIL)):
        tr = (triggers or {}).get(key)
        if not (tr and tr.get("fired") and tr.get("unresolved", True)):
            continue
        said = str(((tr.get("agent") or {}).get("stated_status") or "none")).replace("_", " ")
        _escalate(out, by_key, key, "rcf", title,
                  f"RCF / debt in {tr['column']}, {tr['fired_by']}: "
                  + (f"the agent read {said}; the verified evidence gives {tr['status'].replace('_', ' ')} "
                     f"({tr['reason']})" if tr.get("disagreement") else tr["reason"]) + agent_reading_text(tr),
                  detail, disagreement=bool(tr.get("disagreement")))
        e = next(e for e in out if e.get("measure") == "rcf")
        e["calculated_score"] = e.get("calculated_score") or tr["score"]
    td = (triggers or {}).get(DSO_T1_ID)  # DSO T1: merges with an unresolved deal or prepaid check on DSO
    if td and td.get("fired") and td.get("unresolved", True):
        said = str(((td.get("agent") or {}).get("stated_status") or "none")).replace("_", " ")
        _escalate(out, by_key, DSO_T1_ID, "dso", f"DSO trigger T1 unresolved (DSO moved {DSO_T1_DAYS} days or more)",
                  f"DSO in {td['column']}, {td['fired_by']}: "
                  + (f"the agent read {said}; the verified evidence gives {td['status'].replace('_', ' ')} "
                     f"({td['reason']})" if td.get("disagreement") else td["reason"]) + agent_reading_text(td),
                  DSO_T1_DETAIL, disagreement=bool(td.get("disagreement")))
        e = next(e for e in out if e.get("measure") == "dso")
        e["calculated_score"] = e.get("calculated_score") or td["score"]
    # Liquidity, in run order (T1, T2, then T3; none fires after a covenant breach): one escalation for all.
    for key, title, detail in ((LIQUIDITY_T1_ID, "Liquidity trigger T1 unresolved (revolver availability not found)",
                                LIQUIDITY_T1_DETAIL),
                               (LIQUIDITY_T2_ID, f"Liquidity trigger T2 unresolved (coverage or refinancing score "
                                                 f"{LIQUIDITY_T2_THRESHOLD} or more)", LIQUIDITY_T2_DETAIL),
                               (LIQUIDITY_T3_ID, f"Liquidity trigger T3 unresolved (a maturity wall at "
                                                 f"{LIQUIDITY_T3_SHARE:.0%} or more of total debt)",
                                LIQUIDITY_T3_DETAIL)):
        tq = (triggers or {}).get(key)
        if not (tq and tq.get("fired") and tq.get("unresolved", True)):
            continue
        said = str(((tq.get("agent") or {}).get("stated_status") or "none")).replace("_", " ")
        _escalate(out, by_key, key, "liquidity", title, f"Liquidity and debt maturity in {tq['column']}, "
                  f"{tq['fired_by']}: "
                  + (f"the agent read {said}; the verified evidence gives {tq['status'].replace('_', ' ')} "
                     f"({tq['reason']})" if tq.get("disagreement") else tq["reason"]) + agent_reading_text(tq),
                  detail, disagreement=bool(tq.get("disagreement")))
        e = next(e for e in out if e.get("measure") == "liquidity")
        e["calculated_score"] = e.get("calculated_score") or tq["score"]
    for d in disagreements(gc, cov, ad, pp):  # the agent read it more severely than the verified evidence
        if d["measure"] == "run":
            out.insert(0, {"id": "going_concern:disagreement", "type": "run_decision", "check": "going_concern",
                           "disagreement": True, "title": "agent and verified evidence disagree",
                           "reason": d["reason"], "agent": d["agent"], "verified": d["verified"]})
        else:
            _escalate(out, by_key, d["check"], d["measure"], "agent and verified evidence disagree", d["reason"],
                      DISAGREEMENT_DETAIL, disagreement=True)
    return out


MEASURE_LABELS = {"liquidity": LIQUIDITY_NAME, "dscr": "Debt service coverage ratio (DSCR)",
                  "icr": "Interest coverage ratio (ICR)", "rcf": "RCF to debt", "margin": "EBITDAR margin", **DEAL_MEASURE_NAMES}
T1_DETAIL = ("The Trigger Agent's answer and its verified evidence disagree, so T1 is unresolved. The engine applied the "
             "verified outcome (T1a: rollover debt with a qualifying backstop removed from principal; T1b: −2 for an "
             "executed refinancing). Read the agent's answer and the held-back evidence below: accept the engine's "
             "score, or override it with yours.")
T2_DETAIL = ("The Trigger Agent's answer and its verified evidence disagree, so T2 is unresolved. The engine applied the "
             "verified outcome (first match: committed growth, no adjustment; a disclosed maintenance/growth split, "
             "DSCR recomputed with the disclosed maintenance CapEx; growth not quantified, depreciation as maintenance "
             "CapEx; no growth, score held). Read the agent's answer and the held-back evidence below: accept the "
             "engine's score, or override it with yours.")
ICR_T1_DETAIL = ("ICR scored 8.5 or more and ICR trigger T1 is unresolved: nothing was found to explain the weak "
                 "cover (no quantified one-off in EBIT, no undesignated swap mark-to-market in interest expense), or "
                 "the agent's answer and its verified evidence disagree. Read the agent's answer and the held-back "
                 "evidence below: accept the engine's score, or override it with yours.")
ICR_T2_DETAIL = ("ICR's interest comes from a net or operating interest tag and ICR trigger T2 is unresolved: neither "
                 "gross interest expense nor interest income is disclosed, or the agent's answer and its verified "
                 "evidence disagree. Read the agent's answer and the held-back evidence below: accept the engine's "
                 "score, or override it with yours.")
ICR_T3_DETAIL = ("ICR's EBIT is operating income and ICR trigger T3 is unresolved: pre-tax income wasn't found (or "
                 "there was no interest expense to add back), so EBIT stays operating income, which leaves out "
                 "non-operating items; or the agent's answer and its verified evidence disagree. Read the items "
                 "found between operating income and pre-tax income below: accept the engine's score, or override "
                 "it with yours.")
T3_DETAIL = ("The Trigger Agent's answer and its verified evidence disagree, so T3 is unresolved. The engine applied the "
             "verified outcome (first match: a quantified one-off, backed out of cash taxes paid and DSCR recomputed; a "
             "structural gap, no adjustment; otherwise the score held with low data confidence). Read the agent's "
             "answer and the held-back evidence below: accept the engine's score, or override it with yours.")
LEVERAGE_T1_DETAIL = ("One-offs may distort EBITDAR and Debt/EBITDAR trigger T1 is unresolved: the one-offs are partly "
                      "quantified with an unexplained remainder, nothing explains them or the level of EBITDAR, a "
                      "one-off isn't quantified and there aren't three years of EBITDAR to average, or the agent's "
                      "answer and its verified evidence disagree. Read the agent's answer and the held-back evidence "
                      "below: accept the engine's score, or override it with yours.")
LEVERAGE_T2_DETAIL = ("The Debt / EBITDAR and Net debt / EBITDAR scores are 2 or more apart, so cash does much of the "
                      "work, and Debt/EBITDAR trigger T2 is unresolved: an acquisition or payout earmark, or trapped "
                      "cash, is described but not quantified, or the agent's answer and its verified evidence disagree. "
                      "Read the agent's answer and the held-back evidence below: accept the engine's score, or override "
                      "it with yours.")
LEVERAGE_T3_DETAIL = ("The composite leverage score with leases is 2 or more worse than without them, so leases do much "
                      "of the leverage, and Debt/EBITDAR trigger T3 is unresolved: no master lease, cross-default or "
                      "parent guarantee is disclosed, or the agent's answer and its verified evidence disagree. Read the "
                      "agent's answer and the held-back evidence below: accept the engine's score, or override it with "
                      "yours.")
LEVERAGE_T4_DETAIL = ("Total debt comes from a fallback (the tagged total skipped, or finance leases added), so the debt "
                      "definition may be off, and Debt/EBITDAR trigger T4 is unresolved: the debt note's instruments "
                      "don't reconcile to the balance sheet (no total, the instruments don't add up, or the total doesn't "
                      "tie), or the agent's answer and its verified evidence disagree. Read the agent's answer and the "
                      "held-back evidence below: accept the engine's score, or override it with yours.")
RCF_T1_DETAIL = ("RCF / debt scores 9 or more, or is negative, and RCF/Debt trigger T1 is unresolved: a one-off cash "
                 "cost or special dividend is identified but not quantified, or the agent's answer and its verified "
                 "evidence disagree. Read the agent's answer and the held-back evidence below: accept the engine's "
                 "score, or override it with yours.")
RCF_T2_DETAIL = ("A one-off cash inflow may lift operating cash flow, and so RCF, and RCF/Debt trigger T2 is "
                 "unresolved: an inflow is identified but not quantified, a working-capital release can't be placed "
                 "(no working-capital lines tagged), or the agent's answer and its verified evidence disagree. Read the "
                 "agent's answer and the held-back evidence below: accept the engine's score, or override it with "
                 "yours.")
RCF_T3_DETAIL = ("Distributions to noncontrolling interests aren't tagged under a standard concept while NCI is "
                 "material, and RCF/Debt trigger T3 is unresolved: the distributions are lumped into other financing or "
                 "with buyouts without a split, nothing was found, or the agent's answer and its verified evidence "
                 "disagree. RCF may be overstated. Read the agent's answer and the held-back evidence below: accept the "
                 "engine's score, or override it with yours.")
RCF_T4_DETAIL = ("Distributions to NCI come from PaymentsToMinorityShareholders, which can hold buyouts of partners, "
                 "and RCF/Debt trigger T4 is unresolved: buyouts are mixed in but not quantified, nothing was found "
                 "either way, or the agent's answer and its verified evidence disagree. RCF may be understated. Read "
                 "the agent's answer and the held-back evidence below: accept the engine's score, or override it with "
                 "yours.")
MARGIN_T1_DETAIL = ("Debt/EBITDAR T1 removed one-offs from EBITDAR and EBITDAR Margin trigger T1 is unresolved: where an "
                    "item was booked (revenue, other operating income, below operating income) isn't clear, or the "
                    "agent's answer and its verified evidence disagree. The margin uses T1's EBITDAR with revenue as "
                    "reported. Read the agent's answer and the held-back evidence below: accept the engine's score, or "
                    "override it with yours.")
MARGIN_T2_DETAIL = ("The EBITDAR margin scores at an extreme and one large segment earns at a very different rate, and "
                    "EBITDAR Margin trigger T2 is unresolved: the filing reports a single reportable segment, no "
                    "segment profit is tagged for each segment or it isn't shown to be measured the same way, the "
                    "results don't point either way, or the agent's answer and its verified evidence disagree. The "
                    "score is as calculated. Read the agent's answer and the held-back evidence below: accept the "
                    "engine's score, or override it with yours.")
DSO_T1_DETAIL = ("DSO moved by 5 days or more and DSO trigger T1 is unresolved: DSO went up with a cause described "
                 "(payer disputes, billing or system problems, new facilities ramping up) and no evidence that "
                 "collections recovered after the year-end, a mechanical cause (receivables sold, an unusual "
                 "write-off) is mentioned but not quantified, or the agent's answer and its verified evidence "
                 "disagree. The score is as calculated. Read the agent's answer and the held-back evidence below: "
                 "accept the engine's score, or override it with yours.")
LIQUIDITY_T1_DETAIL = ("The revolver availability at the year-end isn't in the extracted data and Liquidity trigger T1 "
                       "is unresolved: a revolver exists but its availability can't be worked out (not stated, and "
                       "the commitment, the amount drawn, the letters of credit or an asset-based revolver's "
                       "borrowing base not given), or the agent's answer and its verified evidence disagree. The "
                       "engine applied the verified outcome (a stated or worked-out availability; otherwise the "
                       "revolver availability stays at 0, so both scores may be understated). Read the agent's "
                       "answer and the held-back evidence below: accept the engine's score, or override it with "
                       "yours.")
LIQUIDITY_T2_DETAIL = ("Liquidity coverage or refinancing coverage scores 9 or more and Liquidity trigger T2 is "
                       "unresolved: a refinancing, extension or repayment of the debt due soon is only planned, or "
                       "it was executed but its original maturity, amount, new maturity or source of repayment isn't "
                       "verified, or the agent's answer and its verified evidence disagree. The engine applied the "
                       "verified executed items, if any. Read the agent's answer and the held-back evidence below: "
                       "accept the engine's score, or override it with yours.")
LIQUIDITY_T3_DETAIL = ("The largest year of maturities in years 2-5 is 25% or more of total debt and Liquidity "
                       "trigger T3 is unresolved: the agent's answer (secured, unsecured or mixed) and its verified "
                       "evidence disagree. The engine applied the verified outcome. Read the agent's answer and the "
                       "held-back evidence below: accept the engine's score, or override it with yours.")
DISAGREEMENT_DETAIL = ("The engine scored this measure on the verified evidence only. Read the agent's answer and the "
                       "held-back quotes in the pre-check below: accept the engine's score, or override it with yours "
                       "(for example the score the agent's reading implies).")


def agent_recomputation(before: list[dict], apply, t: dict, key: str, label: str,
                        after: list[dict] | None = None) -> list[dict]:
    """The agent's answer as it states it (``t["agent_reading"]["applies"]``) applied to a copy of the measures (never
    to the scored ones) and its recomputation, for every trigger the agent answered, so the analyst can compare it
    with the verified outcome. Recorded on the reading too, with the measure's score three ways (``scores``): as
    calculated before this check (``before``), the verified result applied (``after``) and the agent's answer."""
    from .reporting import recomputation

    reading = t.get("agent_reading")
    if not (reading and reading.get("applies")):
        return []
    accepted = apply(before, {**t, **reading["applies"], "unresolved": False, "unresolved_why": None})
    found = recomputation(before, accepted, f"{key}:agent", f"{label}: the agent's answer, if accepted (not applied)")
    reading["recomputation"] = found

    def score(metrics):
        m = next((m for m in metrics or [] if m["key"] == t.get("measure")), None)
        return headline_score(m) if m else (None, None)

    reading["scores"] = {"measure": t.get("measure"), "column": score(before)[1], "calculated": score(before)[0],
                         "verified": score(after)[0] if after is not None else None, "agent": score(accepted)[0]}
    return found


def agent_reading_text(t: dict) -> str:
    """For an escalation's reason: the score the agent's answer would give, if accepted (its recomputation on a copy),
    or "" when there is none."""
    reading = t.get("agent_reading") or {}
    for r in reading.get("recomputation") or []:
        scores = [x for x in r["results"] if x.get("score")]
        if scores:  # the headline score is each measure's last result row
            x = scores[-1]
            return (f"; the agent's answer, if accepted, gives {x['label'].lower()} {x['after']} (the score as "
                    f"calculated: {x['before']})")
    calculated = (reading.get("scores") or {}).get("calculated")
    if calculated is not None:
        return f"; the agent's answer, if accepted, leaves the score as calculated ({calculated:g})"
    return ""


def _escalate(out, by_key, check, measure, title, reason, detail, disagreement=False):
    """Add a measure escalation, or merge it into the one already open for that measure (one decision per measure;
    the escalation then carries every check's reason)."""
    same = next((e for e in out if e.get("measure") == measure), None)
    if same:
        same.update(reason=f"{same['reason']}; and: {reason}",
                    checks=list(dict.fromkeys([*same.get("checks", [same["check"]]), check])),
                    title=f"{same['title']}; {title}", disagreement=same.get("disagreement", False) or disagreement)
        return
    m = by_key.get(measure)
    out.append({"id": f"{check}:{measure}", "type": "measure_override", "check": check, "title": title,
                "measure": measure, "measure_name": MEASURE_LABELS[measure],
                "engine_score": headline_score(m)[0] if m else None, "calculated_score": None, "reason": reason,
                "detail": detail, **({"disagreement": True} if disagreement else {})})


GC_SEVERITY = {"pass": 0, "watch": 1, "fail": 2}
COVENANT_SEVERITY = {"compliant": 0, "routine": 0, "unclear": 0, "relief": 1, "breach": 2}


def _said(result: dict | None) -> str | None:
    return ((result or {}).get("agent") or {}).get("stated_status")


def disagreements(gc: dict, cov: dict, ad: dict | None, pp: dict | None) -> list[dict]:
    """Where the agent's own answer is more severe than the outcome its verified evidence supports. The verified
    outcome still drives the measures, but each of these goes to an analyst instead of being settled by either side:

    - going concern: the agent said FAIL, the evidence gives less (a run decision); the agent said WATCH, the evidence
      gives PASS (liquidity: the watch floor of 7 wasn't applied);
    - covenants: the agent said breach, or relief, and the evidence gives an outcome with less effect (liquidity);
    - acquisitions: a deal the agent called unresolved that the evidence resolves, or that was held back (DSO, and
      Debt/EBITDAR for a divestiture after the year-end);
    - prepaid model: the agent said unresolved, the evidence gives another outcome (DSO).

    A trigger (DSCR T2, T3, T1; ICR T2, T3, T1; Debt/EBITDAR T1, T2) is escalated whenever its agent and verified evidence disagree, in either direction: that is
    what makes it unresolved (``escalations_for``)."""
    out = []
    said = _said(gc)
    if said in GC_SEVERITY and GC_SEVERITY[said] > GC_SEVERITY.get(gc["status"], 0):
        out.append({"check": "going_concern", "measure": "run" if said == "fail" else "liquidity", "agent": said,
                    "verified": gc["status"],
                    "reason": f"Going concern: the agent read {said.upper()}; the verified evidence gives "
                              f"{gc['status'].upper()}"})
    said = _said(cov)
    if said in COVENANT_SEVERITY and COVENANT_SEVERITY[said] > COVENANT_SEVERITY.get(cov["status"], 0):
        out.append({"check": "covenants", "measure": "liquidity", "agent": said, "verified": cov["status"],
                    "reason": f"Covenants: the agent read {said}; the verified evidence gives {cov['status']}"})
    if ad and ad.get("agent"):
        deals = [(d["deal_id"], d["kind"], d["timing"], d["presentation"], d["outcome"])
                 for d in ad["deals"] if d["stated"].get("outcome") == "unresolved" and d["outcome"] != "unresolved"]
        deals += [(h["deal_id"], h.get("kind"), h.get("timing"), h.get("presentation"), "held back")
                  for h in ad["agent"].get("held_back_deals") or [] if h.get("outcome") == "unresolved"]
        for did, kind, timing, presentation, verified in deals:
            measures = []
            if kind == "acquisition" or timing == "in_year" or presentation == "held_for_sale":
                measures.append("dso")
            if kind == "divestiture" and timing == "after_year_end":
                measures.append("leverage")
            for measure in measures:
                out.append({"check": "acquisitions", "measure": measure, "agent": "unresolved", "verified": verified,
                            "reason": f"Acquisitions: the agent called {did} unresolved; the verified evidence gives "
                                      f"{verified.replace('_', ' ')}"})
    said = _said(pp)
    if said == "unresolved" and pp["status"] != "unresolved":
        out.append({"check": "prepaid", "measure": "dso", "agent": said, "verified": pp["status"],
                    "reason": f"Prepaid model: the agent read unresolved; the verified evidence gives "
                              f"{pp['status'].replace('_', ' ')}"})
    return out


def composite_for(payload: dict, overrides: dict | None = None, decisions: dict | None = None,
                  gc_status: str | None = None) -> dict:
    """The payload's composite re-weighted with an analyst's measure scores ({measure key: score}); ``decisions``
    ({measure key: "override" or "accept"}) labels each. ``gc_status`` replaces the going-concern result: "fail"
    when the analyst sided with the agent's FAIL against the verified evidence."""
    return composite_risk(payload["metrics"][1:], {"values": payload["scoring_settings"]["values"]},
                          gc_status or payload["gate"]["going_concern"], overrides, decisions)
SCOPE = "financial_overview_only"
CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


class FoRunError(ValueError):
    """The run request is invalid (bad case id)."""


def json_safe(value):
    """The payload as plain JSON: tuples become lists and non-finite floats (an infinite coverage when nothing
    falls due) become the strings "inf" / "-inf" / "nan", so it stores, hashes and serves identically."""
    if isinstance(value, float) and not math.isfinite(value):
        return "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def default_case_id(meta: dict, folder: str) -> str:
    """TICKER_FY<year> from the filing's cover page, else the folder name made safe."""
    ticker, year = meta.get("ticker"), str(meta.get("fiscal_year") or "").strip()
    if ticker and year:
        candidate = f"{ticker}_FY{year}"
    else:
        candidate = re.sub(r"[^A-Za-z0-9_.-]+", "_", folder).strip("_.-")
    candidate = re.sub(r"[^A-Za-z0-9_.-]+", "_", candidate)[:64]
    return candidate if CASE_ID.fullmatch(candidate or "") else "FO_CASE"


def validate_case_id(case_id: str) -> str:
    if not CASE_ID.fullmatch(case_id or ""):
        raise FoRunError("Case id must be 1-64 letters, digits, '_', '.' or '-', starting with a letter or digit.")
    return case_id


def run_state(payload: dict) -> tuple[str, str]:
    """(run status, human review status) a new run starts in: anything escalated waits for an analyst."""
    escalated = payload.get("escalations")
    if escalated is None:  # runs recorded before the covenant pre-check
        escalated = ((payload.get("precheck") or {}).get("going_concern") or {}).get("review_required")
    return (AWAITING_REVIEW, "escalated") if escalated else ("completed", "pending")


def next_step(run_id: str, escalation: dict) -> str:
    if escalation["type"] == "run_decision":
        choices = "use_verified|continue|stop" if escalation.get("disagreement") else "continue|stop"
        return (f'python -m app.cli fo-review {run_id} --decision {choices} --reviewer "<name>" '
                '--reason "<why>"')
    return (f'python -m app.cli fo-override {run_id} --measure {escalation["measure"]} --decision accept|override '
            '[--score <1-10>] --reviewer "<name>" --reason "<why>"')


@dataclass(frozen=True)
class FoRunResult:
    run_id: str
    case_id: str
    payload: dict
    content_hash: str
    paths: dict = field(default_factory=dict)

    def summary(self) -> dict:
        composite = self.payload["composite"]
        gc = self.payload["precheck"]["going_concern"]
        cov = self.payload["precheck"]["covenants"]
        ad = self.payload["precheck"].get("acquisitions")
        pp = self.payload["precheck"].get("prepaid")
        status, review = run_state(self.payload)
        withheld = status == AWAITING_REVIEW
        escalations = self.payload["escalations"]
        return {
            "case_id": self.case_id,
            "run_id": self.run_id,
            "scope": SCOPE,
            "run_status": status,
            "filing_folder": self.payload["filing"]["folder"],
            "entity_name": self.payload["meta"].get("entity_name"),
            "period_end": self.payload["meta"].get("period_end"),
            "industry_group": self.payload["industry"].get("industry_group"),
            "agent_model": self.payload.get("agent_model"),
            "going_concern": {"status": gc["status"], "decided_by": gc["decided_by"], "mode": gc["mode"],
                              "reason": gc["reason"], "effect": gc["effect"]},
            "covenants": {"status": cov["status"], "outcome": cov["outcome"], "decided_by": cov["decided_by"],
                          "mode": cov["mode"], "reason": cov["reason"], "effect": cov["effect"],
                          "info_flags": cov["info_flags"]},
            **({"acquisitions": {"status": ad["status"], "decided_by": ad["decided_by"], "mode": ad["mode"],
                                 "reason": ad["reason"],
                                 "deals": [{"deal_id": d["deal_id"], "kind": d["kind"], "outcome": d["outcome"]}
                                           for d in ad["deals"]],
                                 "adjusted": self.payload["gate"].get("deal_adjustments", []),
                                 "unresolved": ad["unresolved_measures"], "info_flags": ad["info_flags"]}}
               if ad else {}),
            **({"prepaid": {"status": pp["status"], "decided_by": pp["decided_by"], "mode": pp["mode"],
                            "reason": pp["reason"], "model": pp["model"], "dso_weight_factor": pp["factor"]}}
               if pp else {}),
            "agent_triggers": self.payload["gate"]["agent_triggers"],
            "triggers": {k: {"status": t["status"], "score": t["score"], "fired": t["fired"],
                             "unresolved": bool(t["fired"] and t.get("unresolved", True))}
                         for k, t in (self.payload.get("triggers") or {}).items()},
            "composite_score": None if withheld else composite["score"],
            "credit_rating": None if withheld else composite["rating"],
            "risk_level": None if withheld else composite["risk_level"],
            **({"provisional_composite_score": composite["score"]}
               if withheld and gc["status"] != "fail" else {}),
            "measures": {m["key"]: headline_score(m)[0] for m in self.payload["metrics"][1:]},
            "human_review_status": review,
            "escalations": [{k: e[k] for k in ("id", "type", "reason", "disagreement") if k in e}
                            | ({"measure": e["measure"], "engine_score": e["engine_score"]}
                               if e["type"] == "measure_override" else {}) for e in escalations],
            **({"next_step": next_step(self.run_id, escalations[0])} if withheld else {}),
            "content_hash": self.content_hash,
            "outputs": {name: str(path) for name, path in self.paths.items()},
        }


class FinancialOverviewRunner:
    """FO runs over ``edgar_filings/`` folders, recorded in ``financial_overview_runs``.

    ``agent_backend`` runs the Pre-check Agent (``agent_mode`` "live" or "scripted"). It is required whenever a
    pre-check has passages to interpret (an auditor's report or going-concern language; a covenant search hit):
    without it, or when its output is rejected, ``PrecheckAgentError`` and nothing is recorded. The keyword rules
    only cross-check. With an ``output_root`` each run also writes, as BO does under
    ``<output>/bo/``: ``<output>/fo/<case_id>/<run_id>/financial_overview.json`` (the recorded payload),
    ``financial_overview.html`` (the standalone scorecard) and, when the agent ran,
    ``precheck_transcript.json`` (exactly what it was sent and returned)."""

    def __init__(self, session: Session, *, output_root: str | Path | None = None,
                 agent_backend=None, agent_mode: str = "live", check_stop=None, on_work=None) -> None:
        self.session = session
        self.output_root = Path(output_root) if output_root is not None else None
        self.agent_backend = agent_backend
        self.agent_mode = agent_mode if agent_backend is not None else "none"
        # Called between steps with the next step's name; raises to stop the run (``cancel.checker``).
        self.check_stop = check_stop or (lambda step: None)
        # Called with (steps done, steps in the run) as each step starts, and once more when the run is done.
        self.on_work = on_work or (lambda done, total: None)
        self._done, self._total = 0, BUILD_STEPS

    def _step(self, step: str) -> None:
        self.check_stop(step)
        self.on_work(self._done, self._total)
        self._done += 1

    def build(self, folder: str, case_id: str | None = None) -> dict:
        """The scored overview for one filing folder (not recorded; the agent's model run is logged)."""
        self._step("reading the filing")
        data, info = loader.load(folder)
        industry = classify_industry(data)
        checks = {}
        for check, run in (("going_concern", going_concern_precheck), ("covenants", covenant_precheck),
                           ("acquisitions", acquisitions_precheck), ("prepaid", prepaid_precheck)):
            self._step(f"the {check.replace('_', '-')} pre-check")
            checks[check] = run(data, session=self.session, backend=self.agent_backend, mode=self.agent_mode,
                                case_id=case_id)
        gc, cov, ad, pp = (checks[k] for k in ("going_concern", "covenants", "acquisitions", "prepaid"))
        self._step("scoring the measures")
        settings = scoring_settings({}, industry["industry_group"])
        base = build_financial_overview(data)
        dealt = apply_deal_adjustments(base, ad)
        measures = apply_prepaid_adjustments(dealt, pp)
        metrics = score_financial_overview(measures, settings, gc["status"], cov["status"])
        # What each pre-check changed, step by step: the measures scored without it, then with it.
        from .reporting import recomputation

        scored = [score_financial_overview(x, settings, gc["status"], "compliant") for x in (base, dealt, measures)]
        recomputations = []
        for (step, by), before, after in ((("acquisitions", "Acquisitions-and-divestitures pre-check"), scored[0],
                                           scored[1]),
                                          (("prepaid", "Prepaid-model pre-check"), scored[1], scored[2]),
                                          (("covenants", "Covenant pre-check"), scored[2], metrics)):
            recomputations += recomputation(before, after, step, by)
        gate = gate_for(gc["status"], cov["status"], ad, pp)
        # Trigger agents: a measure that trips its trigger sets its agent off, unless the gate skips them.
        # DSCR T2 and T3 first: they correct CapEx and cash taxes in cash flow, so T1 judges the corrected DSCR.
        triggers = {}
        for key, trigger, apply in ((T2_ID, dscr_t2_trigger, apply_t2), (T3_ID, dscr_t3_trigger, apply_t3),
                                    (T1_ID, dscr_t1_trigger, apply_t1), (ICR_T2_ID, icr_t2_trigger, apply_icr_t2),
                                    (ICR_T3_ID, icr_t3_trigger, apply_icr_t3), (ICR_T1_ID, icr_t1_trigger, apply_icr_t1),
                                    (LEVERAGE_T4_ID, leverage_t4_trigger, apply_leverage_t4),
                                    (LEVERAGE_T1_ID, leverage_t1_trigger, apply_leverage_t1),
                                    (LEVERAGE_T2_ID, leverage_t2_trigger, apply_leverage_t2),
                                    (LEVERAGE_T3_ID, leverage_t3_trigger, apply_leverage_t3),
                                    (MARGIN_T1_ID, margin_t1_trigger, apply_margin_t1),
                                    (MARGIN_T2_ID, margin_t2_trigger, apply_margin_t2),
                                    (RCF_T1_ID, rcf_t1_trigger, apply_rcf_t1),
                                    (RCF_T2_ID, rcf_t2_trigger, apply_rcf_t2),
                                    (RCF_T3_ID, rcf_t3_trigger, apply_rcf_t3),
                                    (RCF_T4_ID, rcf_t4_trigger, apply_rcf_t4),
                                    (DSO_T1_ID, dso_t1_trigger, apply_dso_t1),
                                    (LIQUIDITY_T1_ID, liquidity_t1_trigger, apply_liquidity_t1),
                                    (LIQUIDITY_T2_ID, liquidity_t2_trigger, apply_liquidity_t2),
                                    (LIQUIDITY_T3_ID, liquidity_t3_trigger, apply_liquidity_t3)):
            self._step(f"the {CHECK_LABELS[key]} check")
            triggers[key] = trigger(data, metrics, gate, session=self.session, backend=self.agent_backend,
                                    mode=self.agent_mode, case_id=case_id,
                                    **({"t1": triggers.get(RCF_T1_ID)} if key == RCF_T2_ID else
                                       {"leverage_t1": triggers.get(LEVERAGE_T1_ID)} if key == MARGIN_T1_ID else
                                       {"dscr_t1": triggers.get(T1_ID)} if key == LIQUIDITY_T2_ID else {}))
            if triggers[key]["fired"]:
                before = metrics
                metrics = apply(metrics, triggers[key])
                label = CHECK_LABELS[key][:1].upper() + CHECK_LABELS[key][1:]
                recomputations += recomputation(before, metrics, key, label)
                recomputations += agent_recomputation(before, apply, triggers[key], key, label, metrics)
        if any(t["fired"] for t in triggers.values()):
            before = metrics
            metrics = [composite_risk(metrics[1:], settings, gc["status"]), *metrics[1:]]
            recomputations += recomputation(before, metrics, "triggers", "The trigger agents' recomputations")
        score = metrics[0]["columns"][0]["score"]
        escalations = escalations_for(gc, cov, metrics, ad, pp, triggers)
        gate.update(requires_decision=gate["requires_decision"] or bool(escalations),
                    disagreements=[e["id"] for e in escalations if e.get("disagreement")],
                    triggers={k: t["status"] for k, t in triggers.items()})
        return json_safe({
            "engine_version": ENGINE_VERSION,
            "reference_hash": reference_hash(),
            "agent_model": agent_model(self.agent_backend),
            "precheck": checks,
            "triggers": triggers,
            "gate": gate,
            "escalations": escalations,
            "recomputations": recomputations,
            "meta": data["meta"],
            "industry": industry,
            "scoring_settings": {k: settings[k] for k in ("sector", "values", "defaults", "changed", "errors")},
            "filing": {
                "folder": info["name"],
                "signature": info["signature"],
                "fact_count": len(data["facts"]),
                "context_count": data["context_count"],
                "section_count": len(data["sections"]),
                "working_capital_lines": len(data["working_capital"] or {}),
                "files": data["meta"]["files"],
                "company_info": bool(data.get("company")),
            },
            "composite": {
                "score": score,
                "rating": credit_rating(score) if score is not None else None,
                "risk_level": risk_level(score)[0] if score is not None else None,
                "reason": metrics[0]["columns"][0]["reason"],
            },
            "metrics": metrics,
        })

    def write_outputs(self, case_id: str, run_id: str, payload: dict, run: dict) -> dict[str, Path]:
        """Write the run's JSON and HTML under ``<output_root>/fo/<case_id>/<run_id>/``."""
        from .reporting import render_overview_html

        folder = output_folder(self.output_root, case_id, run_id)
        folder.mkdir(parents=True, exist_ok=True)
        paths = {"financial_overview.json": folder / "financial_overview.json",
                 "financial_overview.html": folder / "financial_overview.html"}
        paths["financial_overview.json"].write_text(canonical_json(payload) + "\n", encoding="utf-8", newline="\n")
        paths["financial_overview.html"].write_text(render_overview_html(payload, run=run),
                                                    encoding="utf-8", newline="\n")
        # The memo as at the run (before any analyst decision; the browser's memo reflects later ones).
        from .memo import build_memo, filing_source, render_memo_html

        memo = build_memo(payload, run=run, source=filing_source(payload["filing"]["folder"]))
        paths["financial_memo.json"] = folder / "financial_memo.json"
        paths["financial_memo.json"].write_text(canonical_json(memo) + "\n", encoding="utf-8", newline="\n")
        paths["financial_memo.html"] = folder / "financial_memo.html"
        paths["financial_memo.html"].write_text(render_memo_html(memo), encoding="utf-8", newline="\n")
        agents = {check: transcript(result["agent"], check) for check, result in payload["precheck"].items()
                  if result.get("agent")}
        if agents:
            paths["precheck_transcript.json"] = folder / "precheck_transcript.json"
            paths["precheck_transcript.json"].write_text(canonical_json(agents) + "\n", encoding="utf-8", newline="\n")
        fired = {key: transcript(t["agent"], key) for key, t in (payload.get("triggers") or {}).items()
                 if t.get("agent")}
        if fired:
            paths["trigger_transcript.json"] = folder / "trigger_transcript.json"
            paths["trigger_transcript.json"].write_text(canonical_json(fired) + "\n", encoding="utf-8", newline="\n")
        return paths

    def run_filing(self, folder: str, case_id: str | None = None) -> FoRunResult:
        started = utcnow()
        self._done, self._total = 0, (0 if case_id else 1) + BUILD_STEPS + 1
        if case_id:
            validate_case_id(case_id)
        else:
            self._step("reading the filing's cover page")
            case_id = default_case_id(loader.load(folder)[0]["meta"], folder)
        payload = self.build(folder, case_id)
        self._step("recording the run and writing its files")
        digest = content_hash(payload)
        run_id = f"fo-{uuid.uuid4().hex[:16]}"
        status, review = run_state(payload)
        withheld = status == AWAITING_REVIEW
        self.session.add(FinancialOverviewRunRow(
            run_id=run_id,
            case_id=case_id,
            filing_folder=payload["filing"]["folder"],
            folder_signature=payload["filing"]["signature"],
            entity_name=payload["meta"].get("entity_name"),
            ticker=payload["meta"].get("ticker"),
            period_end=payload["meta"].get("period_end"),
            engine_version=ENGINE_VERSION,
            reference_hash=payload["reference_hash"],
            status=status,
            composite_score=None if withheld else payload["composite"]["score"],
            credit_rating=None if withheld else payload["composite"]["rating"],
            human_review_status=review,
            payload=payload,
            content_hash=digest,
            started_at=started,
            completed_at=None if withheld else utcnow(),
        ))
        self.session.flush()
        paths = {}
        if self.output_root is not None:
            paths = self.write_outputs(case_id, run_id, payload, {
                "case_id": case_id, "run_id": run_id, "started_at": started.isoformat(), "content_hash": digest,
                "run_status": status})
        self.on_work(self._total, self._total)
        return FoRunResult(run_id, case_id, payload, digest, paths)


def agent_model(backend) -> str | None:
    """The model the Pre-check Agent runs on (its narrow tier), as the run records it."""
    if backend is None:
        return None
    pick = getattr(backend, "model_for_tier", None)
    return (pick("narrow") if pick else None) or getattr(backend, "model_id", getattr(backend, "_model_id", None))


def output_folder(output_root: str | Path, case_id: str, run_id: str) -> Path:
    return Path(output_root) / "fo" / case_id / run_id


def transcript(agent: dict, check: str = "going_concern") -> dict:
    """Exactly what the Pre-check Agent was sent and returned for one check, and what validation kept."""
    from app.prompts import fo_prompts

    template, schema = {"going_concern": (fo_prompts.FO_GOING_CONCERN_TEMPLATE, fo_prompts.FO_GOING_CONCERN_SCHEMA),
                        "covenants": (fo_prompts.FO_COVENANTS_TEMPLATE, fo_prompts.FO_COVENANTS_SCHEMA),
                        "acquisitions": (fo_prompts.FO_ACQUISITIONS_TEMPLATE,
                                         fo_prompts.FO_ACQUISITIONS_SCHEMA),
                        "prepaid": (fo_prompts.FO_PREPAID_TEMPLATE, fo_prompts.FO_PREPAID_SCHEMA),
                        T1_ID: (fo_prompts.FO_DSCR_T1_TEMPLATE, fo_prompts.FO_DSCR_T1_SCHEMA),
                        T2_ID: (fo_prompts.FO_DSCR_T2_TEMPLATE, fo_prompts.FO_DSCR_T2_SCHEMA),
                        T3_ID: (fo_prompts.FO_DSCR_T3_TEMPLATE, fo_prompts.FO_DSCR_T3_SCHEMA),
                        ICR_T1_ID: (fo_prompts.FO_ICR_T1_TEMPLATE, fo_prompts.FO_ICR_T1_SCHEMA),
                        ICR_T2_ID: (fo_prompts.FO_ICR_T2_TEMPLATE, fo_prompts.FO_ICR_T2_SCHEMA),
                        ICR_T3_ID: (fo_prompts.FO_ICR_T3_TEMPLATE, fo_prompts.FO_ICR_T3_SCHEMA),
                        LEVERAGE_T1_ID: (fo_prompts.FO_LEVERAGE_T1_TEMPLATE, fo_prompts.FO_LEVERAGE_T1_SCHEMA),
                        LEVERAGE_T2_ID: (fo_prompts.FO_LEVERAGE_T2_TEMPLATE, fo_prompts.FO_LEVERAGE_T2_SCHEMA),
                        LEVERAGE_T3_ID: (fo_prompts.FO_LEVERAGE_T3_TEMPLATE, fo_prompts.FO_LEVERAGE_T3_SCHEMA),
                        LEVERAGE_T4_ID: (fo_prompts.FO_LEVERAGE_T4_TEMPLATE, fo_prompts.FO_LEVERAGE_T4_SCHEMA),
                        RCF_T1_ID: (fo_prompts.FO_RCF_T1_TEMPLATE, fo_prompts.FO_RCF_T1_SCHEMA),
                        RCF_T2_ID: (fo_prompts.FO_RCF_T2_TEMPLATE, fo_prompts.FO_RCF_T2_SCHEMA),
                        RCF_T3_ID: (fo_prompts.FO_RCF_T3_TEMPLATE, fo_prompts.FO_RCF_T3_SCHEMA),
                        RCF_T4_ID: (fo_prompts.FO_RCF_T4_TEMPLATE, fo_prompts.FO_RCF_T4_SCHEMA),
                        MARGIN_T1_ID: (fo_prompts.FO_MARGIN_T1_TEMPLATE, fo_prompts.FO_MARGIN_T1_SCHEMA),
                        MARGIN_T2_ID: (fo_prompts.FO_MARGIN_T2_TEMPLATE, fo_prompts.FO_MARGIN_T2_SCHEMA),
                        DSO_T1_ID: (fo_prompts.FO_DSO_T1_TEMPLATE, fo_prompts.FO_DSO_T1_SCHEMA),
                        LIQUIDITY_T1_ID: (fo_prompts.FO_LIQUIDITY_T1_TEMPLATE,
                                          fo_prompts.FO_LIQUIDITY_T1_SCHEMA),
                        LIQUIDITY_T2_ID: (fo_prompts.FO_LIQUIDITY_T2_TEMPLATE,
                                          fo_prompts.FO_LIQUIDITY_T2_SCHEMA),
                        LIQUIDITY_T3_ID: (fo_prompts.FO_LIQUIDITY_T3_TEMPLATE,
                                          fo_prompts.FO_LIQUIDITY_T3_SCHEMA)}[check]
    return {
        "prompt_id": agent["prompt_id"], "prompt_hash": agent["prompt_hash"], "model_id": agent["model_id"],
        "model_run_id": agent["model_run_id"],
        "system_prompt": template, "response_schema": schema,
        "inputs": agent["inputs"], "raw_response": agent["raw_response"],
        "validation_outcome": agent["validation_outcome"],
        "validation_detail": agent.get("validation_detail"),
        "accepted": agent.get("accepted"), "held_back": agent.get("held_back"),
        **({"headroom": agent.get("headroom"), "headroom_held_back": agent.get("headroom_held_back")}
           if check == "covenants" else {}),
        **({"accepted_deals": agent.get("accepted_deals"), "held_back_deals": agent.get("held_back_deals"),
            "held_back_evidence": agent.get("held_back_evidence")} if check == "acquisitions" else {}),
        **({"held_back": agent.get("held_back")} if check in ("prepaid", T1_ID, T2_ID, T3_ID, ICR_T1_ID, ICR_T2_ID, ICR_T3_ID,
                                                       LEVERAGE_T1_ID, LEVERAGE_T2_ID, LEVERAGE_T3_ID,
                                                       LEVERAGE_T4_ID, RCF_T1_ID, RCF_T2_ID, RCF_T3_ID,
                                                       RCF_T4_ID, MARGIN_T1_ID, MARGIN_T2_ID, DSO_T1_ID,
                                                       LIQUIDITY_T1_ID, LIQUIDITY_T2_ID, LIQUIDITY_T3_ID) else {}),
        "stated_status": agent.get("stated_status"), "derived_status": agent.get("derived_status"),
    }
