"""Benchmark-relative risk scores and the composite risk score.

Ported verbatim from the "10-K with FO" project (``credit_memo_app.py``: "benchmark-relative
risk scores" and ``composite_risk``). Scores run 1 (lowest risk) to 10 (highest). Only the
defaults are used for now; ``scoring_settings`` keeps the FO project's override parsing so
the Scoring settings form can be added later without touching the engine.
"""

from __future__ import annotations

import copy
import math
from urllib.parse import urlencode

from .industry import BENCHMARK_FILE_NAME, load_benchmarks
from .measures import BENCHMARK_KEYS, LEVERAGE_MEASURES, score_liquidity

# The score for a company exactly at its industry benchmark. A benchmark file entry can set its own with "p".
INDUSTRY_P = {
    "Drugs (Pharmaceutical)": 4, "Healthcare Products": 4, "Healthcare Support Services": 5,
    "Healthcare Information and Technology": 5, "Hospitals/Healthcare Facilities": 5, "Drugs (Biotechnology)": 6,
}
DEFAULT_P = 5
# m: how many times worse (better) than the benchmark moves the score one step, Δ, up (down);
# D: the same step for points measures (e.g. 5 percentage points of margin); D_dso: the step in days for the change
# in DSO; cap: the largest gap counted.
# revolver: a manual revolver availability (millions) for the liquidity coverage, replacing the tagged / text figure.
SCORING_DEFAULTS = {"m": 2.0, "delta": 2.5, "cap": 3.0, "D": 5.0, "D_dso": 3.0, "revolver": None,
                    "w_leverage": 25.0, "w_liquidity": 20.0, "w_rcf": 15.0, "w_dscr": 10.0, "w_icr": 10.0,
                    "w_margin": 12.5, "w_dso": 7.5}  # composite risk score weights, in percent

# Composite risk score: a weighted average of each measure's headline score (weights in percent, re-normalised
# over the measures available), then a severity floor and the going-concern gate.
COMPOSITE_WEIGHTS = [("w_leverage", "leverage", "Debt / EBITDAR (composite)", 25.0),
                     ("w_liquidity", "liquidity", "Liquidity and debt maturity", 20.0),
                     ("w_rcf", "rcf", "RCF / debt", 15.0), ("w_dscr", "dscr", "DSCR", 10.0), ("w_icr", "icr", "ICR", 10.0),
                     ("w_margin", "margin", "EBITDAR margin", 12.5), ("w_dso", "dso", "DSO change", 7.5)]
COMPOSITE_REQUIRED = ("leverage", "liquidity")  # without either, no composite
COMPOSITE_CORE = ("leverage", "liquidity", "dscr")  # a score at or above the trigger in any of these floors the composite
COMPOSITE_FLOOR_TRIGGER, COMPOSITE_FLOOR = 9, 7
GC_WATCH_LIQUIDITY_FLOOR = 7
DSO_LOW_RELEVANCE_DAYS = 15  # below this DSO the business is largely paid in advance: DSO gets half weight
# Composite leverage score: the net debt score, one step worse when the gross debt score is this much worse.
COMPOSITE_GAP, COMPOSITE_UPLIFT = 2, 1
# Debt/EBITDAR conventions: EBITDAR ≤ 0 scores the worst (no ratio), debt or net debt ≤ 0 the best (no ratio).
LEVERAGE_WORST, LEVERAGE_BEST = 10, 1


def logit(x):
    """L(x): where a 1-10 score x sits on the logistic curve r = 1 + 9 / (1 + e^-x)."""
    return math.log((x - 1) / (10 - x))


def ratio_gap(company, benchmark, direction, m, cap):
    """g = direction × ln(company / benchmark) / ln(m), capped at ±cap. A ratio at or below zero (net cash, for
    a debt ratio) is as far on the safe side as the cap allows."""
    g = direction * math.log(company / benchmark) / math.log(m) if company > 0 else -direction * cap
    return max(-cap, min(cap, g))


def points_gap(company, benchmark, direction, D, cap):
    """g = direction × (company − benchmark) / D, capped at ±cap: for measures in percentage points."""
    return max(-cap, min(cap, direction * (company - benchmark) / D))


def gap_score(g, p, delta):
    """r = 1 + 9 / (1 + e^-(k·g + c)), c = L(p), with k fitted separately on each side so that g = 0 scores p,
    g = 1 scores p + Δ and g = -1 scores p - Δ."""
    c = logit(p)
    k = logit(p + delta) - c if g > 0 else c - logit(p - delta)
    return 1 + 9 / (1 + math.exp(-(k * g + c)))


def scoring_settings(args, industry_group):
    """The benchmark-scoring settings in effect: the industry group's defaults, then any valid overrides
    (s_sector, s_p, s_bm_<measure>, s_m, s_delta, s_cap, ...). Overrides of p and the benchmarks only apply to
    the industry group they were entered for (s_for). The FO section currently passes no overrides."""
    bench, files = load_benchmarks()
    errors = []
    default_sector = industry_group if industry_group in bench else None
    chosen = (args.get("s_sector") or "").strip()
    if chosen and chosen not in bench:
        errors.append(f"No benchmarks for industry group \"{chosen}\"; using the company's own.")
        chosen = ""
    sector = chosen or default_sector
    base = bench.get(sector, {})
    defaults = {"p": float(base.get("p", INDUSTRY_P.get(sector, DEFAULT_P))),
                **{f"bm_{k}": base.get(k) for k in BENCHMARK_KEYS}, **SCORING_DEFAULTS}
    rules = {"p": (lambda v: 1 < v < 10, "p must be between 1 and 10"),
             "bm_rcf_to_debt": (lambda v: True, ""),  # an industry's RCF can be negative
             "bm_days_sales_outstanding": (lambda v: True, ""),  # a change in days: zero or negative is fine
             "D_dso": (lambda v: v > 0, "the DSO step must be greater than 0"),
             "revolver": (lambda v: v >= 0, "revolver availability can't be negative"),
             **{w: (lambda v: v >= 0, "composite weights can't be negative") for w, _, _, _ in COMPOSITE_WEIGHTS},
             "m": (lambda v: v > 1, "m must be greater than 1"),
             "delta": (lambda v: 0 < v < 9, "Δ must be between 0 and 9"),
             "cap": (lambda v: v > 0, "the cap must be greater than 0"),
             "D": (lambda v: v > 0, "D must be greater than 0")}
    same_sector = (args.get("s_for") or sector) == sector
    values = {}
    for name, default in defaults.items():
        raw = (args.get("s_" + name) or "").strip()
        sector_specific = name == "p" or name.startswith("bm_")
        if not raw or (sector_specific and not same_sector):
            values[name] = default
            continue
        ok, msg = rules.get(name, (lambda v: v > 0, "benchmarks must be greater than 0"))
        try:
            v = float(raw)
        except ValueError:
            v, ok = None, lambda v: False
        if v is None or not math.isfinite(v) or not ok(v):
            errors.append(f"{msg} (got \"{raw}\"); using {default:g}." if default is not None else f"{msg} (got \"{raw}\").")
            v = default
        values[name] = v
    if not 1 < values["p"] - values["delta"] < values["p"] + values["delta"] < 10:
        p, d = defaults["p"], min(defaults["delta"], defaults["p"] - 1.01, 9.99 - defaults["p"])
        errors.append(f"p ± Δ must stay between 1 and 10 (p = {values['p']:g}, Δ = {values['delta']:g}); "
                      f"using p = {p:g}, Δ = {d:g}.")
        values.update(p=p, delta=d)
    query = [(k, v) for k, v in args.items() if k.startswith("s_")]
    return {"sector": sector, "classified": industry_group, "default_sector": default_sector,
            "sectors": sorted(bench), "files": files,
            "values": values, "defaults": defaults, "errors": errors,
            "changed": [k for k, v in values.items() if v != defaults[k]],
            "benchmarks": {k: values[f"bm_{k}"] for k in BENCHMARK_KEYS},
            "query": urlencode(query)}


def composite_leverage(gross, net):
    """Risk score = net score + 1 if (gross score − net score) ≥ 2, else the net score; capped at 10."""
    if gross is None or net is None:
        missing = " and ".join(name for name, s in (("Debt / EBITDAR", gross), ("Net debt / EBITDAR", net)) if s is None)
        return {"score": None, "uplift": False, "reason": f"No {missing} score"}
    uplift = gross - net >= COMPOSITE_GAP
    return {"score": round(min(10, net + COMPOSITE_UPLIFT if uplift else net), 1), "uplift": uplift, "reason": None}


def score_leverage(col, settings):
    """Score one year's Debt / EBITDAR and Net debt / EBITDAR against their benchmarks, then the composite leverage
    score. ``settings`` needs "values" (m, cap, p, delta), "benchmarks" and "sector": the scoring settings, or a scored
    measure's ``scoring`` record (``leverage_settings``)."""
    v = settings["values"]
    for key, short, _, den in LEVERAGE_MEASURES:
        mc, bench = col[key], settings["benchmarks"][key]
        # The ratio as computed: a ratio cleared for debt ≤ 0 keeps its value in "raw" until it is recomputed.
        raw = mc["ratio"] if mc["ratio"] is not None else mc.get("raw")
        mc.update(score=None, g=None, note=None, flag=None, net_cash=False, raw=raw)
        if mc["reason"]:
            continue
        if bench is None:
            mc["reason"] = (f"No {short} benchmark for {settings['sector']} in {BENCHMARK_FILE_NAME}"
                            if settings["sector"] else
                            "No industry benchmark for this company's industry group")
        elif mc["negative_ebitda"]:  # EBITDAR ≤ 0 (after any normalisation): 10, no ratio, flagged
            mc.update(score=float(LEVERAGE_WORST), ratio=None, raw=None, flag=f"{den.upper()} ≤ 0",
                      note=f"{den.upper()} ≤ 0: scores {LEVERAGE_WORST}, no ratio")
        elif raw is not None and raw <= 0:  # debt or net debt ≤ 0: 1, no ratio
            mc.update(score=float(LEVERAGE_BEST), ratio=None, net_cash=True,
                      note=("Net debt" if key == "net_debt_to_ebitdar" else "Debt") + f" ≤ 0: scores {LEVERAGE_BEST}, "
                      "no ratio")
        else:
            mc["g"] = ratio_gap(mc["ratio"], bench, 1, v["m"], v["cap"])
            mc["score"] = round(gap_score(mc["g"], v["p"], v["delta"]), 1)
    col["composite"] = composite_leverage(col["debt_to_ebitdar"]["score"], col["net_debt_to_ebitdar"]["score"])


def leverage_settings(m):
    """The settings a scored leverage measure was scored with (its ``scoring`` record), to rescore a column."""
    s = m["scoring"]
    return {"values": {k: s[k] for k in ("p", "m", "delta", "cap")}, "benchmarks": s["benchmarks"],
            "sector": s["sector"]}


def cash_flow_settings(m):
    """The settings a points-scored measure (RCF / debt, the EBITDAR margin) was scored with (its ``scoring`` record),
    to rescore a column."""
    s = m["scoring"]
    return {"values": {k: s[k] for k in ("p", "delta", "cap", "D")}, "benchmarks": s["benchmarks"],
            "sector": s["sector"]}


def score_margin(col, settings):
    """Score one year's EBITDAR margin against the benchmark, in percentage points (higher is lower risk)."""
    v, bench = settings["values"], settings["benchmarks"]["ebitdar_margin"]
    col.update(score=None, g=None, note=None)
    if col["reason"]:
        return
    if bench is None:
        col["reason"] = "No industry benchmark for this company's industry group"
        return
    col["g"] = points_gap(col["ratio"] * 100, bench * 100, -1, v["D"], v["cap"])
    col["score"] = round(gap_score(col["g"], v["p"], v["delta"]), 1)


def dso_settings(m):
    """The settings DSO was scored with (its ``scoring`` record), to rescore a column."""
    s = m["scoring"]
    return {"values": {k: s[k] for k in ("p", "delta", "cap", "D_dso")}, "benchmarks": s["benchmarks"],
            "sector": s["sector"]}


def score_dso(col, settings):
    """Score one year's change in DSO against the benchmark, in days (more days is higher risk)."""
    v, bench = settings["values"], settings["benchmarks"]["days_sales_outstanding"]
    col.update(score=None, g=None)
    if col["reason"]:
        return
    if bench is None:
        col["reason"] = (f"No DSO benchmark for {settings['sector']} in {BENCHMARK_FILE_NAME}"
                         if settings["sector"] else
                         "No industry benchmark for this company's industry group")
        return
    col["g"] = points_gap(col["change"], bench, 1, v["D_dso"], v["cap"])  # more days = worse
    col["score"] = round(gap_score(col["g"], v["p"], v["delta"]), 1)


def score_cash_flow(col, settings):
    """Score one year's RCF / debt against the benchmark."""
    v, bench = settings["values"], settings["benchmarks"]["rcf_to_debt"]
    rc = col["rcf_to_debt"]
    rc.update(score=None, g=None, note=None)
    if rc["reason"]:
        return
    if bench is None:
        rc["reason"] = "No industry benchmark for this company's industry group"
    elif rc["no_debt"]:
        if col["rcf"] > 0:
            rc["g"], rc["note"] = -v["cap"], "No debt: best capped gap"
        else:
            rc["reason"] = "No debt and no positive RCF"
    else:
        rc["g"] = points_gap(rc["ratio"] * 100, bench * 100, -1, v["D"], v["cap"])
    if rc["g"] is not None:
        rc["score"] = round(gap_score(rc["g"], v["p"], v["delta"]), 1)


GC_SUPERSEDED = "Superseded by going-concern fail: shown for information only, not counted in the overall score."
COVENANT_BREACH_SCORE, COVENANT_RELIEF_UPLIFT = 10, 1
COVENANT_BLOCK = "covenant breach not waived (lenders can block revolver draws)"


def adjust_for_covenants(m, covenant_status):
    """The covenant pre-check's effect on the liquidity-and-maturity measure (already scored): a breach (a) sets the
    score to 10 (the revolver availability was set to 0 when scoring); a waiver over 12 months or a relief amendment
    (b) adds 1 after calculation, capped at 10. Each column keeps its calculated score."""
    if covenant_status not in ("breach", "relief"):
        return m
    for col in m["columns"]:
        calculated = col.get("score")
        if calculated is None and covenant_status == "relief":
            continue
        new = COVENANT_BREACH_SCORE if covenant_status == "breach" else min(10, calculated + COVENANT_RELIEF_UPLIFT)
        col["covenant_adjustment"] = {"status": covenant_status, "calculated": calculated, "score": new}
        col["score"], col["reason"] = new, None
    m["covenant_note"] = (
        f"Covenant breach not waived (pre-check, outcome a): revolver availability set to 0 in the coverage and "
        f"refinancing tests and the liquidity-and-maturity score set to {COVENANT_BREACH_SCORE}. Escalated to an "
        "analyst, who can override this score."
        if covenant_status == "breach" else
        f"Covenant waiver over 12 months or relief amendment (pre-check, outcome b): +{COVENANT_RELIEF_UPLIFT} on the "
        "liquidity-and-maturity score after calculation.")
    return m


def score_financial_overview(fo, settings, gc_status="pass", covenant_status="compliant"):
    """The financial overview with the benchmark-relative scores filled in for the given settings, the composite
    first. ``gc_status`` is the going-concern pre-check result (pass / watch / fail); ``covenant_status`` the
    covenant pre-check's outcome (breach / relief / routine / compliant / unclear)."""
    v = settings["values"]
    out = []
    for m in fo:
        if m["key"] == "liquidity":  # fixed bands; only the revolver override comes from the settings
            m = copy.deepcopy(m)
            for col in m["columns"]:
                score_liquidity(col, v.get("revolver"), COVENANT_BLOCK if covenant_status == "breach" else None)
            out.append(adjust_for_covenants(m, covenant_status))
            continue
        if m["key"] == "leverage":
            m = copy.deepcopy(m)
            for col in m["columns"]:
                score_leverage(col, settings)
        elif m["key"] == "rcf":
            m = copy.deepcopy(m)
            for col in m["columns"]:
                score_cash_flow(col, settings)
        elif m["key"] == "margin":
            m = copy.deepcopy(m)
            for col in m["columns"]:
                score_margin(col, settings)
        elif m["key"] == "dso":
            m = copy.deepcopy(m)
            for col in m["columns"]:
                score_dso(col, settings)
        else:
            out.append(m)
            continue
        m["scoring"] = {k: settings[k] for k in ("sector", "classified", "default_sector",
                                                 "benchmarks", "errors", "changed", "files")}
        m["scoring"].update(p=v["p"], m=v["m"], delta=v["delta"], cap=v["cap"], D=v["D"], D_dso=v["D_dso"])
        out.append(m)
    # Mark the measures the going-concern gate overrides (shallow copies: the inputs stay untouched).
    if gc_status == "fail":
        out = [{**m, "superseded": GC_SUPERSEDED} for m in out]
    elif gc_status == "watch":
        out = [{**m, "gc_note": f"Going-concern watch: the liquidity-and-maturity score is floored at "
                                f"{GC_WATCH_LIQUIDITY_FLOOR} before weighting (the company needs specific actions to "
                                "get out of doubt)."}
               if m["key"] == "liquidity" else m for m in out]
    out.insert(0, composite_risk(out, settings, gc_status))
    return out


def headline_score(m):
    """(score, column heading) a measure contributes: its first scored column (the forward or latest year). A
    measure marked not applicable (ICR when net interest is income) contributes nothing."""
    if m.get("not_applicable"):
        return None, None
    get = {"leverage": lambda c: (c.get("composite") or {}).get("score"),
           "rcf": lambda c: c["rcf_to_debt"].get("score")}.get(m["key"], lambda c: c.get("score"))
    return next(((get(c), c["heading"]) for c in m["columns"] if get(c) is not None), (None, None))


def composite_risk(fo, settings, gc_status="pass", overrides=None, decisions=None):
    """The composite risk score from the scored measures: the weighted average, floored at 7 when leverage, liquidity
    or DSCR scores 9 or more. The going-concern pre-check then gates it: a watch floors the liquidity-and-maturity
    score at 7 before weighting; a fail sets the composite to 10 (even when measures are missing) and supersedes
    every measure: each stays visible with its own score but none is counted. A going-concern measure in ``fo``
    (the FO project's layout) takes precedence. ``overrides`` ({measure key: score}) are an analyst's scores for
    escalated measures; each replaces that measure's score and is final (no floor applies to it). ``decisions``
    ({measure key: "override" or "accept"}) says which of them is the engine's own score, accepted."""
    v = settings["values"]
    overrides = overrides or {}
    decisions = decisions or {}
    by_key = {m["key"]: m for m in fo}
    gc = by_key.get("going_concern")
    status = gc["status"] if gc else gc_status
    # Prepaid-model pre-check: DSO weight = w_dso × factor; every other weight × (100 − DSO weight) ÷ (100 − w_dso).
    factor = (by_key.get("dso") or {}).get("prepaid_factor")
    dso_weight = v["w_dso"] * factor if factor is not None else None
    scale = (100 - dso_weight) / (100 - v["w_dso"]) if factor is not None and v["w_dso"] < 100 else 1.0
    rows = []
    for wkey, key, name, _ in COMPOSITE_WEIGHTS:
        m = by_key.get(key)
        score, heading = headline_score(m) if m else (None, None)
        weight, notes = v[wkey], []
        if factor is not None:
            weight = dso_weight if key == "dso" else weight * scale
            if key == "dso":
                notes.append(f"prepaid model: weight {v['w_dso']:g}% × {factor:g}")
        if m and m.get("prepaid_unresolved") and key not in overrides:
            notes.append("prepaid model unresolved: as calculated, escalated")
        if key == "liquidity" and m and any(c.get("covenant_adjustment") for c in m["columns"]):
            notes.append("covenant breach: set to 10" if m["columns"][0].get("covenant_adjustment", {}).get(
                "status") == "breach" else "covenant relief: +1 after calculation")
        if m and m["columns"] and m["columns"][0].get("deal_adjustment"):
            notes.append("recomputed for the year's acquisitions or divestitures")
        if m and m.get("deal_unresolved") and key not in overrides:
            notes.append("acquisition or divestiture unresolved: as reported, escalated")
        if m and m.get("trigger_summary") and key not in overrides:
            notes.append(m["trigger_summary"])
        if status == "fail":
            notes.append("superseded by going-concern fail: not counted")
        elif key in overrides and decisions.get(key) == "accept":
            notes.append("engine score (accepted by the analyst)")
        elif key in overrides:
            notes.append(f"overridden by analyst: {score:g} → {overrides[key]:g}" if score is not None
                         else f"overridden by analyst: {overrides[key]:g}")
            score = overrides[key]
        elif key == "liquidity" and score is not None and status == "watch" and score < GC_WATCH_LIQUIDITY_FLOOR:
            score, notes = GC_WATCH_LIQUIDITY_FLOOR, notes + [f"floored at {GC_WATCH_LIQUIDITY_FLOOR}: going-concern watch"]
        if key == "dso" and m and factor is None:  # the prepaid pre-check's factor replaces this rule of thumb
            level = next((c["dso"] for c in m["columns"] if c.get("dso") is not None), None)
            if level is not None and level < DSO_LOW_RELEVANCE_DAYS:
                weight, notes = weight / 2, notes + [f"half weight: DSO {level:.0f} days, largely paid in advance"]
        if score is None and status != "fail":
            notes.append("not available: weight spread over the others")
        rows.append({"key": key, "name": name, "score": score, "heading": heading, "weight": weight, "notes": notes})
    # On a going-concern fail no measure is counted: the overall score is 10 whatever they show.
    used = [] if status == "fail" else [r for r in rows if r["score"] is not None and r["weight"] > 0]
    total = sum(r["weight"] for r in used)
    for r in rows:
        r["share"] = r["weight"] / total if r in used else 0.0
        r["contribution"] = r["share"] * r["score"] if r in used else None
    missing = [r["name"] for r in rows if r["key"] in COMPOSITE_REQUIRED and r["score"] is None]
    average = sum(r["contribution"] for r in used) if used else None
    score, reason = average, None
    adjustments = [] if status == "fail" else [
        f"{r['name']}: engine score {overrides[r['key']]:g} accepted by the analyst"
        if decisions.get(r["key"]) == "accept" else f"{r['name']}: overridden by analyst to {overrides[r['key']]:g}"
        for r in rows if r["key"] in overrides]
    if factor is not None and factor != 1 and status != "fail":
        adjustments.append(f"Prepaid model: DSO weight {v['w_dso']:g}% × {factor:g} = {dso_weight:g}%; every other "
                           f"weight × (100 − {dso_weight:g}) ÷ (100 − {v['w_dso']:g}) = × {scale:.4f}")
    if status == "fail":
        score = 10
        adjustments.append("Set to 10: going-concern fail (the measures are superseded and not counted)")
    elif missing or not total:
        score, reason = None, "Incomplete: no " + " or ".join(missing or ["weighted measure"]) + " score"
    else:
        core = [r["name"] for r in rows if r["key"] in COMPOSITE_CORE and r["score"] is not None
                and r["score"] >= COMPOSITE_FLOOR_TRIGGER]
        if core and score < COMPOSITE_FLOOR:
            score = COMPOSITE_FLOOR
            adjustments.append(f"Floored at {COMPOSITE_FLOOR}: {', '.join(core)} scores {COMPOSITE_FLOOR_TRIGGER} or more")
        score = round(score, 1)
    anchor = next((c for m in fo if m["key"] in ("leverage", "icr", "margin") for c in m["columns"]), None)
    col = {"heading": anchor["heading"] if anchor else "", "period": anchor["period"] if anchor else "--",
           "basis": "historical", "unit": None, "score": score, "ratio": None, "reason": reason}
    return {"key": "overall", "title": "Composite risk score", "short": "Composite risk", "columns": [col],
            "rows": rows, "average": average, "adjustments": adjustments, "gc_status": status,
            "overrides": dict(overrides),
            "used": len(used), "formula": "Weighted average of the measures' scores, then a severity floor and the "
                                          "going-concern gate",
            "sources": {}, "empty": "No measures could be scored.",
            "notes": "Each measure contributes its headline score (the forward or latest year). Weights (in percent) "
                     "are re-normalised over the measures available, so a missing measure's weight is spread over the "
                     "rest; no composite without both leverage and liquidity. DSO gets half weight when DSO is under "
                     f"{DSO_LOW_RELEVANCE_DAYS} days (a business largely paid in advance). Severity floor: if leverage, "
                     f"liquidity or DSCR scores {COMPOSITE_FLOOR_TRIGGER} or more, the composite is at least "
                     f"{COMPOSITE_FLOOR}, so a default-relevant weakness can't be averaged away. Going-concern gate "
                     "(from the pre-check): a fail sets the composite to 10, after an analyst decides the analysis "
                     "continues, and the measures stay visible but superseded and not counted; a watch floors the "
                     f"liquidity-and-maturity score at {GC_WATCH_LIQUIDITY_FLOOR} before weighting. Covenant pre-check: "
                     "a breach not waived sets the revolver availability to 0 and the liquidity-and-maturity score to "
                     "10 (an analyst can override that score); a waiver over 12 months or a relief amendment adds 1 to "
                     "it after calculation. Acquisitions and divestitures pre-check: DSO and Debt/EBITDAR are recomputed "
                     "for the year's deals (pro forma revenue, divested revenue, or debt and EBITDAR together after a "
                     "divestiture after the year-end); an unresolved deal leaves the measure as reported and escalates "
                     "it. Prepaid-model pre-check: DSO weight = 7.5% × factor (1 with billed revenue from a payer "
                     "split, 0 when contract liabilities ≥ receivables, 0.5 for a mixed model) and every other weight "
                     "× (1 − DSO weight) ÷ (1 − 7.5%); that factor replaces the half-weight rule for DSO under "
                     f"{DSO_LOW_RELEVANCE_DAYS} days. DSCR trigger T2 (CapEx above 1.5× depreciation, unless a "
                     "going-concern fail skips agent triggers; it runs first): committed growth projects get no "
                     "adjustment, a disclosed maintenance/growth split recomputes DSCR with the disclosed maintenance "
                     "CapEx, growth described but not quantified recomputes it with depreciation as maintenance CapEx, "
                     "and no growth holds the score. DSCR trigger T3 (|cash taxes paid − current tax expense| above 5% of "
                     "CFADS; after T2): a quantified one-off (a settlement only if paid in cash) is backed out of "
                     "cash taxes paid and DSCR recomputed, a structural gap gets no adjustment, and otherwise the "
                     "score is held with low data confidence. ICR trigger T1 (an ICR score of 8.5 or more): quantified "
                     "one-offs in EBIT are corrected (charges added back, gains removed) and an undesignated swap's "
                     "mark-to-market is removed from interest incurred, ICR recomputed; nothing found escalates ICR. ICR "
                     "trigger T2 (interest from a net or operating tag; before ICR T1): ICR is recomputed on gross "
                     "interest expense (else net + interest income); neither disclosed escalates ICR; net interest "
                     "income makes ICR n/a (not counted); an operating tag is flagged. ICR trigger T3 (EBIT from operating "
                     "income; after T2): EBIT = pre-tax income + interest expense when pre-tax income is found, "
                     "otherwise operating income is kept and ICR escalated. RCF/Debt trigger T1 (an RCF / debt score of 9 or more, or a negative ratio; first match): a quantified, non-recurring one-off cash cost (added back to FFO) or special dividend (taken out of dividends paid) recomputes RCF / debt; no one-offs found gets no adjustment; a one-off identified but not quantified escalates RCF / debt. RCF/Debt trigger T2 (at any score: tagged one-off inflows above 5% of FFO, or FFO up 25% or more and over 10 points faster than revenue; after T1): a quantified, non-recurring one-off inflow in operating cash flow outside the working-capital lines comes out of CFO and FFO and RCF / debt is recomputed; one inside the working-capital lines, recurring, or in investing or financing gets no adjustment; one identified but not quantified escalates RCF / debt. RCF/Debt trigger T3 (no standard NCI-distributions concept tagged while NCI is material: its share of net income above 5%, or of total equity when net income is zero or negative; after T2): distributions found with the cash paid recompute RCF / debt (redeemable NCI included, purchases of NCI excluded); only declared amounts are used and labelled a timing difference; none in the year gets no adjustment; lumped without a split escalates RCF / debt. RCF/Debt trigger T4 (NCI distributions from PaymentsToMinorityShareholders, which can hold buyouts; after T3): quantified buyouts are excluded and RCF / debt recomputed with distributions only; no purchases of NCI shown gets no adjustment; buyouts mixed in but not quantified escalate RCF / debt. EBITDAR Margin trigger T1 (Debt/EBITDAR T1 removed one-offs): the margin takes T1's normalised EBITDAR; a one-off booked in revenue also comes out of revenue; one booked outside revenue gets no further adjustment; an unclear location escalates the margin. The margin alone falls back to cash lease payments (labelled) without the lease-cost table. Debt/EBITDAR conventions: net debt ≤ 0 scores 1 and EBITDAR ≤ 0 scores 10 (flagged), with no ratio computed in either case; rent is the operating lease cost, never cash paid. "
                     "Debt/EBITDAR trigger T1 (|net one-offs as "
                     "tagged| above 5% of EBITDAR, or EBITDAR ≤ 0; first match): quantified non-recurring one-offs "
                     "normalise EBITDAR (charges added back, gains removed) and Debt/EBITDAR is recomputed; an "
                     "operational change with no one-offs gets no adjustment; a one-off identified but not quantified "
                     "uses the 3-year average EBITDAR (2-year for a smaller reporting company); partly quantified with an unexplained remainder, or no "
                     "explanation, escalates Debt/EBITDAR. Debt/EBITDAR trigger T2 (gross and net leverage scores 2 or more apart; after T1; first match): a quantified acquisition or payout earmark, or trapped cash, comes out of cash and Net debt / EBITDAR is recomputed; cash earmarked only for debt maturities, or freely available, gets no adjustment; an earmark or trapping described but not quantified escalates Debt/EBITDAR (a buyback authorisation isn't an earmark). Debt/EBITDAR trigger T3 (composite leverage score with leases 2 or more worse than without; after T2): a master lease, cross-default or parent guarantee disclosed adds 1 to the composite leverage score; none disclosed escalates Debt/EBITDAR (escalators and cancellable leases get no adjustment). DSO trigger T1 (|change in DSO| of 5 days or more, after the acquisitions-and-divestitures pre-check's correction; first match): a quantified mechanical cause (receivables sold and derecognised, or an unusual write-off) is added back to receivables in both years and DSO recomputed; DSO up with an operational cause and collections recovered after the year-end takes 1 off the DSO score; DSO up with a cause and no fix, or a mechanical cause not quantified, escalates DSO; no cause found gets no adjustment (a securitisation accounted for as a secured borrowing stays in receivables).Debt/EBITDAR trigger T4 (total debt from a fallback: the tagged total skipped, or finance leases added; before T1): the debt note's instruments reconciled to the balance sheet, finance leases counted once, recompute Debt/EBITDAR with the reconciled total debt; no reconciliation escalates Debt/EBITDAR. "
                     "DSCR trigger T1 (a DSCR score of 9 or more, unless a "
                     "going-concern fail skips agent triggers): rollover debt with backstop capacity is removed from "
                     "year-1 principal and DSCR recomputed, an executed refinancing of year-1 maturities after the "
                     "year-end takes 2 off the score, and DSCR is escalated. Liquidity trigger T1 (the revolver "
                     "availability at the year-end not found; first match): a stated availability is used, else "
                     "commitment − drawn − letters of credit (an asset-based revolver: the lower of this and the "
                     "borrowing base), and both liquidity tests are recomputed; no revolver gets no adjustment; a "
                     "revolver whose availability can't be worked out escalates liquidity. Liquidity trigger T2 (a coverage or "
                     "refinancing score of 9 or more after T1; first match, reusing DSCR T1's refinancing record): "
                     "debt already classified as long-term (ASC 470-10-45-14) gets no further adjustment; debt "
                     "refinanced or extended after the year-end moves to its new maturity; debt repaid from cash "
                     "comes out with the cash used; debt repaid from new borrowing or equity comes out, the new debt "
                     "going in at its maturity; a refinancing only planned escalates liquidity. An analyst's "
                     "override of an escalated "
                     "measure replaces its score.",
            "gaps": "The weights are judgement-based and provisional: back-test them against agency ratings before "
                    "relying on absolute levels. Scores inherit each measure's simplifications (see each section)."}


margin_settings = cash_flow_settings  # the EBITDAR margin is scored in points, as RCF / debt
