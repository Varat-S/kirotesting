"""Presentation of a recorded Financial Overview: scorecard view and the standalone HTML page.

Pure formatting over the recorded payload; nothing is recomputed except the headline
lookup the FO project also does at render time. Amounts are shown in millions of the
reporting currency.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

from .acquisitions import DECIDED_NOTE, UNRESOLVED_NOTE
from .dscr_t2 import RULES as T2_RULES
from .dscr_t2 import T2_MULTIPLE
from .dscr_t3 import RULES as T3_RULES
from .dscr_t3 import T3_SHARE
from .dscr_triggers import DECIDED_NOTE as T1_DECIDED_NOTE
from .dscr_triggers import ESCALATED_NOTE as T1_ESCALATED_NOTE
from .dscr_triggers import REFINANCING_OUTCOMES, ROLLOVER_OUTCOMES, T1_THRESHOLD
from .dscr_triggers import RULES as T1_RULES
from .icr_t2 import LABELS as ICR_T2_LABELS
from .icr_t3 import LABELS as ICR_T3_LABELS
from .icr_t3 import RULES as ICR_T3_RULES
from .icr_t3 import items_evidence as icr_t3_evidence
from .icr_t2 import RULES as ICR_T2_RULES
from .icr_triggers import ICR_T1_THRESHOLD
from .icr_triggers import RULES as ICR_T1_RULES
from .leverage_t2 import LEVERAGE_T2_GAP
from .leverage_t2 import RULES as LEVERAGE_T2_RULES
from .leverage_t3 import KIND_TEXT as LEVERAGE_T3_KIND_TEXT
from .leverage_t3 import LEVERAGE_T3_GAP, LEVERAGE_T3_UPLIFT
from .leverage_t3 import RULES as LEVERAGE_T3_RULES
from .leverage_t4 import KIND_TEXT as LEVERAGE_T4_KIND_TEXT
from .leverage_t4 import RULES as LEVERAGE_T4_RULES
from .leverage_t4 import TIE_SHARE as LEVERAGE_T4_TIE
from .leverage_triggers import LEVERAGE_T1_SHARE
from .leverage_triggers import RULES as LEVERAGE_T1_RULES
from .prepaid import DECIDED_NOTE as PP_DECIDED_NOTE
from .rcf_t2 import KIND_TEXT as RCF_T2_KIND_TEXT
from .rcf_t2 import RCF_T2_EXCESS, RCF_T2_GROWTH, RCF_T2_SHARE
from .rcf_t2 import RULES as RCF_T2_RULES
from .rcf_t3 import RCF_T3_SHARE
from .rcf_t3 import RULES as RCF_T3_RULES
from .rcf_t4 import RULES as RCF_T4_RULES
from .margin_triggers import LOCATION_TEXT as MARGIN_LOCATION_TEXT
from .margin_triggers import RULES as MARGIN_T1_RULES
from .margin_t2 import DIRECTION_TEXT as MARGIN_T2_DIRECTION_TEXT
from .margin_t2 import RULES as MARGIN_T2_RULES
from .rcf_triggers import RCF_T1_THRESHOLD
from .dso_triggers import DSO_T1_DAYS, DSO_T1_RELIEF
from .dso_triggers import KIND_TEXT as DSO_T1_KIND_TEXT
from .dso_triggers import RULES as DSO_T1_RULES
from .liquidity_triggers import FIGURE_TEXT as LIQUIDITY_T1_FIGURE_TEXT
from .liquidity_triggers import ROUTE_TEXT as LIQUIDITY_T1_ROUTE_TEXT
from .liquidity_triggers import RULES as LIQUIDITY_T1_RULES
from .liquidity_t2 import BUCKET_TEXT as LIQUIDITY_T2_BUCKET_TEXT
from .liquidity_t2 import KIND_TEXT as LIQUIDITY_T2_KIND_TEXT
from .liquidity_t2 import LIQUIDITY_T2_THRESHOLD
from .liquidity_t2 import ROUTE_TEXT as LIQUIDITY_T2_ROUTE_TEXT
from .liquidity_t2 import RULES as LIQUIDITY_T2_RULES
from .liquidity_t3 import KIND_TEXT as LIQUIDITY_T3_KIND_TEXT
from .liquidity_t3 import RULES as LIQUIDITY_T3_RULES
from .rcf_triggers import RULES as RCF_T1_RULES
from .prepaid import UNRESOLVED_NOTE as PP_UNRESOLVED_NOTE
from .measures import LIQUIDITY_BANDS, REFINANCING_BANDS, credit_rating, risk_level
from .scoring import GC_SUPERSEDED, SCORING_DEFAULTS, headline_score

_TEMPLATES = Path(__file__).resolve().parents[2] / "api" / "templates"
_env = Environment(loader=FileSystemLoader(str(_TEMPLATES)), autoescape=True)

DASH = "—"


def _num(v):
    """A number from the payload, or None ("inf" stays a string)."""
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


# What an escalated measure is tagged once the analyst decides: their own score, or the engine's kept.
DECISION_TAG = {"override": "Overridden by analyst", "accept": "Engine score"}


def _g(score):
    """A score as written, or "none" (an escalated measure the engine couldn't score)."""
    return "none" if _num(score) is None else f"{score:g}"


def money(v, divisor=1e6):
    if v in ("inf", "-inf", "nan"):
        return "∞" if v == "inf" else "−∞" if v == "-inf" else DASH
    if _num(v) is None:
        return DASH
    s = f"{abs(v) / divisor:,.1f}"
    return f"({s})" if v < 0 else s


def times(v):
    if v == "inf":
        return "∞"
    return f"{v:.2f}x" if _num(v) is not None else DASH


def pct(v):
    return f"{v * 100:.1f}%" if _num(v) is not None else DASH


def days(v, signed=False):
    if _num(v) is None:
        return DASH
    return f"{v:+.1f} days" if signed else f"{v:.1f} days"


def score_cell(score):
    if _num(score) is None:
        return {"text": DASH, "css": "none", "label": None}
    label, css = risk_level(score)
    return {"text": f"{score:g}", "css": css, "label": label}


def _text(v, fmt):
    return {"text": fmt(v), "css": None, "label": None}


def bands_line(bands):
    return " · ".join(f"{r} → {s:g}" for _, s, r in bands)


# Result rows per measure: (label, kind, getter); kind picks the formatter.
RESULTS = {
    "dscr": [("DSCR", "x", lambda c: c.get("ratio")), ("Risk score", "score", lambda c: c.get("score"))],
    "icr": [("ICR", "x", lambda c: c.get("ratio")), ("Risk score", "score", lambda c: c.get("score"))],
    "leverage": [
        ("Debt / EBITDAR", "lev", lambda c: c["debt_to_ebitdar"]),
        ("Debt / EBITDAR score", "score", lambda c: c["debt_to_ebitdar"].get("score")),
        ("Net debt / EBITDAR", "lev", lambda c: c["net_debt_to_ebitdar"]),
        ("Net debt / EBITDAR score", "score", lambda c: c["net_debt_to_ebitdar"].get("score")),
        ("Composite leverage score", "score", lambda c: (c.get("composite") or {}).get("score")),
    ],
    "rcf": [
        ("RCF / debt", "pct", lambda c: c["rcf_to_debt"]["ratio"]),
        ("Risk score", "score", lambda c: c["rcf_to_debt"].get("score")),
        ("RCF / debt (CFO-based, for comparison)", "pct", lambda c: c.get("rcf_to_debt_cfo")),
    ],
    "liquidity": [
        ("Liquidity coverage", "x", lambda c: c.get("coverage")),
        ("Coverage score", "score", lambda c: c.get("coverage_score")),
        ("Refinancing coverage", "x", lambda c: c.get("refinancing")),
        ("Refinancing score", "score", lambda c: c.get("refinancing_score")),
        ("Section score (worse of the two)", "score", lambda c: c.get("score")),
    ],
    "margin": [("EBITDAR margin", "pct", lambda c: c.get("ratio")), ("Risk score", "score", lambda c: c.get("score"))],
    "dso": [
        ("DSO", "days", lambda c: c.get("dso")),
        ("Prior-year DSO", "days", lambda c: c.get("prior_dso")),
        ("Change in DSO (scored)", "days+", lambda c: c.get("change")),
        ("Risk score", "score", lambda c: c.get("score")),
    ],
}
def leverage_ratio(mc: dict) -> str:
    """A Debt/EBITDAR ratio cell: no ratio is computed for EBITDAR ≤ 0 or debt ≤ 0 (they still score); "—" only when
    an input is missing."""
    if mc.get("flag"):
        return f"n/m: {mc['flag']}"
    if mc.get("net_cash"):
        return "n/m: net cash"
    return times(mc.get("ratio"))


FORMATS = {"x": times, "pct": pct, "days": days, "days+": lambda v: days(v, signed=True), "lev": leverage_ratio}

# Why a column has no result, and any scoring note.
REASONS = {
    "leverage": lambda c: [r for r in (c["debt_to_ebitdar"].get("reason"), c["net_debt_to_ebitdar"].get("reason"),
                                       (c.get("composite") or {}).get("reason")) if r],
    "rcf": lambda c: [r for r in (c["rcf_to_debt"].get("reason"),) if r],
    "liquidity": lambda c: [r for r in (c.get("coverage_reason"), c.get("refinancing_reason"), c.get("reason")) if r],
}
NOTES = {
    "leverage": lambda c: [n for n in (c.get("ebitdar_basis") and f"EBITDAR is the {c['ebitdar_basis']} (trigger "
                                       "Debt/EBITDAR T1)",
                                       c["debt_to_ebitdar"].get("note"), c["net_debt_to_ebitdar"].get("note")) if n]
    + (["Composite: net score + 1 (gross score is 2 or more worse)"] if (c.get("composite") or {}).get("uplift") else [])
    + ([f"Composite: + {LEVERAGE_T3_UPLIFT} for a master lease, cross-default or parent guarantee (trigger Debt/EBITDAR "
        "T3)"] if (c.get("composite") or {}).get("lease_uplift") else []),
    "margin": lambda c: [n for n in (c.get("rent_basis") and f"Rent: {c['rent_basis']}",) if n],
    "rcf": lambda c: [n for n in (c["rcf_to_debt"].get("note"),
                                  c.get("nci_distributions_basis") and
                                  f"Distributions to NCI: {c['nci_distributions_basis']}",
                                  c.get("ffo_basis") if c.get("ffo_basis") != "FFO" else None) if n],
}
# The benchmark each benchmark-scored measure is compared with: (label, key, formatter).
BENCHMARKS = {
    "leverage": [("Debt / EBITDAR", "debt_to_ebitdar", times), ("Net debt / EBITDAR", "net_debt_to_ebitdar", times)],
    "rcf": [("RCF / debt", "rcf_to_debt", pct)],
    "margin": [("EBITDAR margin", "ebitdar_margin", pct)],
    "dso": [("Change in DSO", "days_sales_outstanding", lambda v: days(v, signed=True))],
}
DEAL_REPORTED_ROWS = {"dso": [("revenue_reported", "Net revenue as reported (before the deal adjustment)"),
                              ("revenue_total", "Total revenue (DSO uses billed revenue: prepaid-model pre-check)"),
                              ("receivables_before_trigger", "Net receivables as reported (before trigger DSO T1)")],
                      "leverage": [("ebitdar_reported", "EBITDAR as reported (before the deal adjustment)"),
                                   ("adj_debt_reported", "Debt + op. leases as reported (before the deal adjustment)"),
                                   ("debt_before_trigger", "Total debt as calculated (before trigger Debt/EBITDAR T4)"),
                                   ("ebitdar_before_trigger", "EBITDAR as calculated (before trigger Debt/EBITDAR T1)"),
                                   ("cash_before_trigger", "Unrestricted cash as counted (before trigger Debt/EBITDAR "
                                                           "T2)")],
                      "margin": [("ebitdar_before_trigger", "EBITDAR as calculated (before trigger EBITDAR Margin T1)"),
                                 ("revenue_before_trigger", "Net operating revenue as reported (before trigger EBITDAR "
                                                            "Margin T1)")],
                      "rcf": [("cfo_before_trigger", "Operating cash flow as tagged (before trigger RCF/Debt T2)"),
                              ("ffo_before_trigger", "FFO as calculated (before the RCF/Debt triggers)"),
                              ("dividends_before_trigger", "Dividends paid as tagged (before trigger RCF/Debt T1)"),
                              ("nci_distributions_before_trigger",
                               "Distributions to NCI as tagged (before trigger RCF/Debt T3 or T4)"),
                              ("rcf_before_trigger", "RCF as calculated (before the RCF/Debt triggers)")],
                      "dscr": [("capex_reported", "Total CapEx as tagged (before trigger T2)"),
                               ("cash_taxes_reported", "Cash taxes paid as tagged (before trigger T3)"),
                               ("cash_flow_reported", "Cash flow for debt service as tagged (before triggers T2 "
                                                      "and T3)"),
                               ("principal_reported", "Principal due as tagged (before trigger T1)"),
                               ("debt_service_reported", "Debt service + rent as tagged (before trigger T1)")],
                      "icr": [("ebit_reported", "EBIT as tagged (before triggers ICR T3 and T1)"),
                              ("interest_reported", "Interest incurred as tagged (before triggers ICR T2 and T1)")]}
LADDER = [("y1", "Year 1"), ("y2", "Year 2"), ("y3", "Year 3"), ("y4", "Year 4"), ("y5", "Year 5"),
          ("after", "After year 5")]


def _unit(m):
    return next((c.get("unit") for c in m["columns"] if c.get("unit")), None) or "USD"


def _changed(a, b) -> bool:
    if a is None or b is None:
        return (a is None) != (b is None)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) > 1e-9 * max(1.0, abs(a), abs(b))
    return a != b


def _result_text(kind, value):
    return score_cell(value)["text"] if kind == "score" else _text(value, FORMATS[kind])["text"]


def recomputation(before: list[dict], after: list[dict], step: str, by: str) -> list[dict]:
    """What one step (a pre-check or a trigger) changed: for each measure and column it touched, every input row that
    changed (before → after) and every result row (ratios and scores) that changed, as the page shows them. ``step``
    is the check's key, ``by`` its name. The composite risk score is compared too."""
    out = []
    old = {m["key"]: m for m in before}
    for m in after:
        mb = old.get(m["key"])
        if mb is None:
            continue
        if m["key"] == "overall":
            sb, sa = mb["columns"][0].get("score"), m["columns"][0].get("score")
            if _changed(sb, sa):
                out.append({"step": step, "by": by, "measure": "overall", "measure_name": "Composite risk score",
                            "column": m["columns"][0].get("heading") or "", "inputs": [],
                            "results": [{"label": "Composite risk score", "before": score_cell(sb)["text"],
                                         "after": score_cell(sa)["text"], "score": True}]})
            continue
        for col in m["columns"]:
            cb = next((c for c in mb["columns"] if c.get("heading") == col.get("heading")), None)
            if cb is None:
                continue
            inputs = [{"label": label, "before": money(cb.get(k)), "after": money(col.get(k))}
                      for k, label in m.get("rows", []) if _changed(cb.get(k), col.get(k))]
            results, scores = [], []
            for label, kind, get in RESULTS.get(m["key"], []):
                tb, ta = _result_text(kind, get(cb)), _result_text(kind, get(col))
                if kind == "score":  # the revised score is always shown, changed or not
                    scores.append({"label": label, "before": tb, "after": ta, "score": True, "unchanged": tb == ta})
                elif tb != ta:
                    results.append({"label": label, "before": tb, "after": ta, "score": False})
            if inputs or results or any(not x["unchanged"] for x in scores):
                results += [x for x in scores if not (x["unchanged"] and x["before"] == "—")]
                out.append({"step": step, "by": by, "measure": m["key"], "measure_name": m.get("title"),
                            "column": col.get("heading"), "inputs": inputs, "results": results})
    return out


def measure_card(m: dict, override: dict | None = None) -> dict:
    cols = m["columns"]
    score, heading = headline_score(m)
    card = {
        "key": m["key"], "title": m["title"], "short": m["short"], "formula": m.get("formula"),
        "superseded": m.get("superseded"), "gc_note": m.get("gc_note"), "covenant_note": m.get("covenant_note"),
        "deal_note": (m.get("deal_note") or "").replace(UNRESOLVED_NOTE, DECIDED_NOTE) if override
        else m.get("deal_note"),
        "deal_unresolved": m.get("deal_unresolved"),
        "deal_tag": None if not m.get("deal_unresolved") else (
            DECISION_TAG[override["decision"]] if override else UNRESOLVED_TAG),
        "prepaid_note": (m.get("prepaid_note") or "").replace(PP_UNRESOLVED_NOTE, PP_DECIDED_NOTE) if override
        else m.get("prepaid_note"),
        "prepaid_tag": None if not m.get("prepaid_unresolved") else (
            DECISION_TAG[override["decision"]] if override else UNRESOLVED_TAG),
        "trigger_note": (m.get("trigger_note") or "").replace(T1_ESCALATED_NOTE, T1_DECIDED_NOTE) if override
        else m.get("trigger_note"),
        "data_confidence_note": m.get("data_confidence_note"),
        "flag_notes": m.get("flag_notes") or [], "not_applicable": m.get("not_applicable"),
        "trigger_tag": None if not m.get("trigger_escalated") else (
            DECISION_TAG[override["decision"]] if override else ESCALATED_TAG),
        "override": None if not override else {
            **override, "score": score_cell(override["analyst_score"]),
            "text": (f"Overridden by analyst: {_g(override['analyst_score'])} (engine {_g(override['engine_score'])})"
                     if override["decision"] == "override" else
                     f"Engine score: {_g(override['engine_score'])}, accepted by the analyst")
            + f", by {override['reviewer']} on {override['decided_at'][:10]}: {override['reason']}"},
        "notes": m.get("notes"), "gaps": m.get("gaps"), "empty": m.get("empty") if not cols else None,
        "unit": _unit(m), "headings": [c["heading"] for c in cols],
        "headline": {**score_cell(score), "heading": heading, **({"text": "n/a"} if m.get("not_applicable") else {})},
        "value_rows": [{"label": label, "cells": [money(c.get(key)) for c in cols]} for key, label in m.get("rows", [])],
        "result_rows": [{"label": label, "cells": [score_cell(get(c)) if kind == "score" else _text(get(c), FORMATS[kind])
                                                   for c in cols]}
                        for label, kind, get in RESULTS.get(m["key"], [])],
        "reasons": [{"heading": c["heading"], "entries": list(dict.fromkeys(
            REASONS.get(m["key"], lambda c: [r for r in (c.get("reason"),) if r])(c)))} for c in cols],
        "column_notes": [{"heading": c["heading"], "entries": NOTES[m["key"]](c)} for c in cols] if m["key"] in NOTES else [],
        "sources": [{"label": label, "entries": m.get("sources", {}).get(part, [])} for part, label in m.get("source_parts", [])],
        "bands": [],
        "benchmarks": [],
        "details": [],
    }
    card["reasons"] = [r for r in card["reasons"] if r["entries"]]
    card["column_notes"] = [n for n in card["column_notes"] if n["entries"]]
    if m.get("bands"):
        card["bands"].append({"label": "Risk bands", "text": bands_line(m["bands"])})
    for key, label in DEAL_REPORTED_ROWS.get(m["key"], []):  # the figures before the deal adjustment
        if any(key in c for c in cols):
            card["value_rows"].append({"label": label, "cells": [money(c.get(key)) if key in c else DASH
                                                                 for c in cols]})
    if m["key"] == "dscr":  # the figures before each trigger that changed them, in run order: T2, T3, then T1
        applied, done = [], []
        for key, t in (("capex_adjustment", "T2"), ("tax_adjustment", "T3"), ("trigger_adjustment", "T1")):
            if any((c.get(key) or {}).get("recomputed") is not None for c in cols) or (
                    t == "T1" and any(c.get(key) for c in cols)):
                applied.append((key, t, f"after trigger {' and '.join(done)}, before trigger {t}" if done
                                else f"as calculated (before trigger {t})"))
                done.append(t)
        for i, (key, _, stage) in enumerate(applied):
            card["result_rows"].insert(1 + i, {
                "label": f"DSCR {stage}",
                "cells": [_text((c.get(key) or {}).get("ratio_calculated"), times) for c in cols]})
            card["result_rows"].insert(len(card["result_rows"]) - 1, {
                "label": f"Risk score {stage}",
                "cells": [score_cell((c.get(key) or {}).get("calculated")) for c in cols]})
        if applied:
            card["result_rows"][-1]["label"] = f"Risk score (after trigger {' and '.join(t for _, t, _ in applied)})"
    if m["key"] == "icr":  # the figures before each trigger that changed them, in run order: ICR T2, T3, then T1
        applied, done = [], []
        for key, t in (("interest_adjustment", "ICR T2"), ("ebit_adjustment", "ICR T3"),
                       ("trigger_adjustment", "ICR T1")):
            if any((c.get(key) or {}).get("recomputed") is not None for c in cols) or (
                    t == "ICR T1" and any(c.get(key) for c in cols)):
                applied.append((key, t, f"after trigger {done[-1]}, before trigger {t}" if done
                                else f"as calculated (before trigger {t})"))
                done.append(t)
        for i, (key, _, stage) in enumerate(applied):
            card["result_rows"].insert(1 + i, {
                "label": f"ICR {stage}",
                "cells": [_text((c.get(key) or {}).get("ratio_calculated"), times) for c in cols]})
            card["result_rows"].insert(len(card["result_rows"]) - 1, {
                "label": f"Risk score {stage}",
                "cells": [score_cell((c.get(key) or {}).get("calculated")) for c in cols]})
        if applied:
            card["result_rows"][-1]["label"] = f"Risk score (after trigger {' and '.join(t for _, t, _ in applied)})"
    if m["key"] == "leverage":  # the figures before each trigger that changed them, in run order: T4, T1, T2, T3
        applied, done = [], []
        for key, t in (("debt_adjustment", "Debt/EBITDAR T4"), ("ebitdar_adjustment", "Debt/EBITDAR T1"), ("cash_adjustment", "Debt/EBITDAR T2"),
                       ("lease_adjustment", "Debt/EBITDAR T3")):
            if any(c.get(key) for c in cols):
                applied.append((key, t, f"after trigger {done[-1]}, before trigger {t}" if done
                                else f"as calculated (before trigger {t})"))
                done.append(t)
        rows = card["result_rows"]
        for key, t, stage in applied:
            if key in ("debt_adjustment", "ebitdar_adjustment"):  # T2 changes cash only, T3 the composite only
                rows.insert(1, {"label": f"Debt / EBITDAR {stage}", "cells": [
                    _text((c.get(key) or {}).get("ratio_calculated"), times) for c in cols]})
            if key == "lease_adjustment":  # T3 changes the composite only, and shows the score without leases
                rows.insert(len(rows) - 1, {"label": "Composite leverage score without leases (trigger Debt/EBITDAR T3)",
                                            "cells": [score_cell((c.get(key) or {}).get("score_without_leases"))
                                                      for c in cols]})
            else:
                net_at = next(i for i, r in enumerate(rows) if r["label"] == "Net debt / EBITDAR score")
                rows.insert(net_at, {"label": f"Net debt / EBITDAR {stage}", "cells": [
                    _text((c.get(key) or {}).get("net_ratio_calculated"), times) for c in cols]})
            rows.insert(len(rows) - 1, {"label": f"Composite leverage score {stage}",
                                        "cells": [score_cell((c.get(key) or {}).get("calculated")) for c in cols]})
        if applied:
            rows[-1]["label"] = f"Composite leverage score (after trigger {' and '.join(t for _, t, _ in applied)})"
    if m["key"] == "rcf":  # the figures before each trigger that changed them, in run order: T1, then T2
        applied, done = [], []
        for key, t in (("rcf_adjustment", "RCF/Debt T1"), ("rcf_t2_adjustment", "RCF/Debt T2"),
                       ("rcf_t3_adjustment", "RCF/Debt T3"), ("rcf_t4_adjustment", "RCF/Debt T4")):
            if any(c.get(key) for c in cols):
                applied.append((key, f"after trigger {done[-1]}, before trigger {t}" if done
                                else f"as calculated (before trigger {t})"))
                done.append(t)
        rows = card["result_rows"]
        for i, (key, stage) in enumerate(applied):
            rows.insert(1 + i, {"label": f"RCF / debt {stage}",
                                "cells": [_text((c.get(key) or {}).get("ratio_calculated"), pct) for c in cols]})
        for i, (key, stage) in enumerate(applied):
            rows.insert(1 + len(applied) + i, {"label": f"Risk score {stage}", "cells": [
                score_cell((c.get(key) or {}).get("calculated")) for c in cols]})
        if applied:
            rows[1 + 2 * len(applied)]["label"] = f"Risk score (after trigger {' and '.join(done)})"
    if m["key"] == "margin" and any(c.get("margin_adjustment") for c in cols):  # before EBITDAR Margin T1
        rows = card["result_rows"]
        rows.insert(1, {"label": "EBITDAR margin as calculated (before trigger EBITDAR Margin T1)",
                        "cells": [_text((c.get("margin_adjustment") or {}).get("ratio_calculated"), pct) for c in cols]})
        rows.insert(2, {"label": "Risk score as calculated (before trigger EBITDAR Margin T1)",
                        "cells": [score_cell((c.get("margin_adjustment") or {}).get("calculated")) for c in cols]})
        rows[3]["label"] = "Risk score (after trigger EBITDAR Margin T1)"
    if m["key"] == "margin" and any((c.get("margin_t2_adjustment") or {}).get("recomputed") is not None
                                    for c in cols):  # before EBITDAR Margin T2 (the score only)
        rows = card["result_rows"]
        t1 = any(c.get("margin_adjustment") for c in cols)
        rows.insert(len(rows) - 1, {
            "label": "Risk score " + ("after trigger EBITDAR Margin T1, before trigger EBITDAR Margin T2" if t1
                                      else "as calculated (before trigger EBITDAR Margin T2)"),
            "cells": [score_cell((c.get("margin_t2_adjustment") or {}).get("calculated")) for c in cols]})
        rows[-1]["label"] = f"Risk score (after trigger {'EBITDAR Margin T1 and T2' if t1 else 'EBITDAR Margin T2'})"
    if m["key"] == "dso" and any((c.get("dso_adjustment") or {}).get("recomputed") is not None for c in cols):
        rows = card["result_rows"]  # DSO, prior-year DSO, change, score: before DSO T1 after the change and score
        rows.insert(3, {"label": "Change in DSO as calculated (before trigger DSO T1)", "cells": [
            _text((c.get("dso_adjustment") or {}).get("change_calculated"), FORMATS["days+"]) for c in cols]})
        rows.insert(4, {"label": "Risk score as calculated (before trigger DSO T1)",
                        "cells": [score_cell((c.get("dso_adjustment") or {}).get("calculated")) for c in cols]})
        rows[5]["label"] = "Risk score (after trigger DSO T1)"
    if m["key"] == "liquidity":
        rows = card["result_rows"]
        done = [name for key, name in (("liquidity_adjustment", "Liquidity T1"), ("maturity_adjustment", "Liquidity T2"),
                                  ("wall_adjustment", "Liquidity T3"))
                if any((c.get(key) or {}).get("recomputed") is not None for c in cols)]
        covenant = any(c.get("covenant_adjustment") for c in cols)
        for key, name in (("liquidity_adjustment", "Liquidity T1"), ("maturity_adjustment", "Liquidity T2"),
                                  ("wall_adjustment", "Liquidity T3")):
            if name in done:  # each trigger's score before it ran (after any covenant relief)
                rows.insert(len(rows) - 1, {
                    "label": f"Section score before trigger {name}",
                    "cells": [score_cell((c.get(key) or {}).get("calculated")) for c in cols]})
        triggers = " and ".join(done)
        if covenant:
            rows.insert(len(rows) - 1, {
                "label": (f"Section score after trigger {triggers}, before the covenant adjustment" if done
                          else "Section score as calculated (before the covenant adjustment)"),
                "cells": [score_cell((c.get("covenant_adjustment") or {}).get("calculated")) for c in cols]})
        if done or covenant:
            rows[-1]["label"] = "Section score (after " + " and ".join(
                ([f"trigger {triggers}"] if done else []) + (["the covenant adjustment"] if covenant else [])) + ")"
        card["bands"] += [{"label": "Liquidity coverage bands", "text": bands_line(LIQUIDITY_BANDS)},
                          {"label": "Refinancing coverage bands", "text": bands_line(REFINANCING_BANDS)}]
        for c in cols:
            card["value_rows"] += [{"label": f"Maturity: {label}", "cells": [money(c.get(key))]} for key, label in LADDER]
            card["value_rows"] += [{"label": "Weighted maturities (yrs 1–3 + ½ × yrs 4–5)", "cells": [money(c.get("weighted_maturities"))]},
                                   {"label": "Free cash flow", "cells": [money(c.get("fcf"))]},
                                   {"label": "Refinancing sources (cash + revolver + 2 × FCF)", "cells": [money(c.get("refi_sources"))]}]
            card["details"] += [{"label": "Revolver availability", "text": c.get("revolver_source") or DASH},
                                {"label": "Maturity table", "text": c.get("ladder_source") or "None tagged"},
                                {"label": "Year 1 maturities", "text": c.get("y1_source") or "None tagged"},
                                {"label": "Weighted maturities / total debt",
                                 "text": pct(c.get("concentration"))}]
    scoring = m.get("scoring")
    if scoring and m["key"] in BENCHMARKS:
        card["benchmarks"] = [{"label": label, "text": fmt(scoring["benchmarks"].get(key))
                               if scoring["benchmarks"].get(key) is not None else "none for this industry group"}
                              for label, key, fmt in BENCHMARKS[m["key"]]]
        card["curve"] = (f"Industry group: {scoring.get('sector') or 'not classified'} · p = {scoring['p']:g} "
                         f"(score at the benchmark) · m = {scoring['m']:g} · Δ = {scoring['delta']:g} · "
                         f"cap = {scoring['cap']:g} · D = {scoring['D']:g} pp · D(DSO) = {scoring['D_dso']:g} days")
    return card


def composite_card(m: dict) -> dict:
    col = m["columns"][0]
    score = col["score"]
    return {
        **score_cell(score),
        "rating": credit_rating(score) if _num(score) is not None else None,
        "reason": col["reason"],
        "heading": col["heading"],
        "average": f"{m['average']:.2f}" if _num(m.get("average")) is not None else DASH,
        "used": m["used"],
        "adjustments": m["adjustments"],
        "gc_status": m["gc_status"],
        "formula": m["formula"], "notes": m["notes"], "gaps": m["gaps"],
        "rows": [{"name": r["name"], "score": score_cell(r["score"]), "heading": r["heading"] or DASH,
                  "weight": f"{r['weight']:g}%", "share": f"{r['share']:.0%}" if r["contribution"] is not None else DASH,
                  "contribution": f"{r['contribution']:.2f}" if r["contribution"] is not None else DASH,
                  "notes": "; ".join(r["notes"])} for r in m["rows"]],
    }


GC_CSS = {"pass": "low", "watch": "elevated", "fail": "very-high"}
DECIDED_BY = {"agent": "Pre-check Agent (findings verified against the filing)",
              "rules": "Keyword rules (an older run, before the agent was required)",
              "no_mentions": "No going-concern language found"}
PASSAGE_KINDS = [("auditor", "The auditor's report (every paragraph)"),
                 ("management", "Going-concern mentions in the filing text"),
                 ("funding", "Funding statements (12 months or more)")]


def precheck_card(gc: dict | None) -> dict | None:
    if not gc:
        return None
    agent = gc.get("agent")
    labels = {p["passage_id"]: p.get("section_label") or "Auditor's report"
              for kind in ("auditor", "management", "funding") for p in (gc.get("passages") or {}).get(kind, [])}
    return {
        "status": gc["status"], "label": gc["status"].upper(), "css": GC_CSS[gc["status"]],
        "decided_by": DECIDED_BY.get(gc["decided_by"], gc["decided_by"]), "mode": gc["mode"],
        "reason": gc["reason"], "effect": gc["effect"], "basis": gc.get("basis") or [], "notes": gc.get("notes") or [],
        "rules": gc["rules"], "audit_reports": gc.get("audit_reports") or [], "tagged": gc.get("tagged") or [],
        "sections": gc.get("sections") or [],
        "passages": [{"label": label, "entries": gc["passages"].get(kind, [])} for kind, label in PASSAGE_KINDS],
        "agent": None if not agent else agent_result(agent, labels, GC_CSS),
        "agent_not_used": None if agent else AGENT_NOT_USED["rules" if gc["mode"] == "rules" else "no_input"],
    }


AGENT_NOT_USED = {
    "rules": "Not used: this run was recorded before the Pre-check Agent was required, so the keyword rules "
             "decided. Run it again to have the agent read the auditor's report.",
    "no_input": "Not called: there was no auditor's report to read and no going-concern language in the filing.",
}
COV_CSS = {"breach": "very-high", "relief": "elevated", "routine": "low", "compliant": "low", "unclear": "moderate"}
COV_LABELS = {"breach": "BREACH", "relief": "RELIEF", "routine": "ROUTINE", "compliant": "IN COMPLIANCE",
              "unclear": "UNCLEAR"}
COV_DECIDED_BY = {"agent": "Pre-check Agent (findings verified against the filing)",
                  "rules": "Keyword rules (an older run, before the agent was required)",
                  "no_hits": "No search term hit: in compliance"}
COV_AGENT_NOT_USED = {
    "rules": "Not used: this run was recorded before the Pre-check Agent was required, so the keyword rules "
             "decided. Run it again to have the agent interpret the covenant passages.",
    "no_input": "Not called: no search term hit in the debt note, MD&A liquidity, the auditor's report or "
                "subsequent events.",
}
COV_OUTCOMES = [("a", "Breach not waived, or MD&A says a breach is reasonably likely within 12 months",
                 "Revolver availability 0 in every measure; liquidity-and-maturity score 10; escalated to an analyst"),
                ("b", "Breach waived for more than 12 months, or a relief amendment obtained",
                 "+1 on the liquidity-and-maturity score after calculation"),
                ("c", "Routine amendment only (repricing, extension, change of benchmark rate)", "No change (resolved)"),
                ("d", "In compliance (or no search hit)", "No change"),
                ("e", "A waiver or breach is mentioned, but its status or duration isn't clear",
                 "No adjustment, no escalation; information flag")]


def covenant_card(cov: dict | None) -> dict | None:
    if not cov:
        return None
    agent = cov.get("agent")
    labels = {p["passage_id"]: p["section_label"] for p in cov.get("passages") or []}
    return {
        "status": cov["status"], "label": COV_LABELS[cov["status"]], "css": COV_CSS[cov["status"]],
        "outcome": cov["outcome"], "decided_by": COV_DECIDED_BY.get(cov["decided_by"], cov["decided_by"]),
        "mode": cov["mode"], "reason": cov["reason"], "effect": cov["effect"], "basis": cov.get("basis") or [],
        "notes": cov.get("notes") or [], "info_flags": cov.get("info_flags") or [],
        "rules": {**cov["rules"], "label": COV_LABELS[cov["rules"]["status"]]},
        "waivers": cov.get("waivers") or [], "headroom": cov.get("headroom") or [],
        "sections": cov.get("sections") or [], "passages": cov.get("passages") or [],
        "outcomes": [{"letter": k, "when": w, "effect": e, "current": k == cov["outcome"]} for k, w, e in COV_OUTCOMES],
        "agent": None if not agent else agent_result(agent, labels, COV_CSS, COV_LABELS),
        "agent_not_used": None if agent else COV_AGENT_NOT_USED["rules" if cov["mode"] == "rules" else "no_input"],
    }


AD_CSS = {"none": "low", "no_adjustment": "low", "adjusted": "moderate", "unresolved": "very-high"}
AD_LABELS = {"none": "NO DEAL", "no_adjustment": "NO ADJUSTMENT", "adjusted": "ADJUSTED", "unresolved": "UNRESOLVED"}
AD_DECIDED_BY = {"agent": "Pre-check Agent (evidence verified against the filing)",
                 "no_hits": "No deal note or deal mention found"}
AD_OUTCOME_CSS = {"not_for_the_year": "none", "pro_forma_revenue": "moderate", "discontinued_operations": "low",
                  "remove_divested_revenue": "moderate", "not_material": "low", "unresolved": "very-high"}
AD_RULES = [("1", "Acquisition with pro forma revenue disclosed", "Recompute DSO using pro forma revenue"),
            ("2", "Divestiture presented as discontinued operations",
             "No adjustment: revenue and receivables already exclude it"),
            ("3", "Divestiture not in discontinued operations, revenue disclosed",
             "Remove the divested revenue, recompute DSO"),
            ("4", "Acquisition or divestiture not material (no pro forma required)", "No adjustment"),
            ("5", "Material deal, but its revenue isn't disclosed", "Unresolved: the measure is escalated to an analyst"),
            ("", "A divestiture after the year-end",
             "Debt (proceeds repaid) and EBITDAR (divested EBITDAR) adjusted together, never one side; if the divested "
             "EBITDAR can't be built from the 10-K, neither is adjusted and Debt/EBITDAR is unresolved")]
FACT_LABELS = {"deal": "Deal", "date": "Date", "discontinued_operations": "Discontinued operations",
               "held_for_sale": "Held for sale", "not_material": "Not material", "material": "Material",
               "pro_forma_revenue": "Pro forma revenue", "divested_revenue": "Divested revenue",
               "consideration": "Price or proceeds", "debt_repaid": "Debt repaid", "ebitdar": "EBITDAR",
               "ebitda": "EBITDA", "ebit": "EBIT", "da": "D&A", "rent": "Rent"}


def _fact_text(f: dict) -> str:
    bits = []
    if f.get("amount"):
        bits.append(f"{f['amount']} = {money(f.get('value'))} million")
    if f.get("date"):
        bits.append(f["date"])
    return " · ".join(bits)


UNRESOLVED_TAG = "Unresolved"  # an escalated measure no analyst has decided yet
ESCALATED_TAG = "Escalated"  # a measure a trigger escalated, not decided yet
TAG_CSS = {UNRESOLVED_TAG: "very-high", ESCALATED_TAG: "very-high", "Overridden by analyst": "moderate",
           "Engine score": "low"}


def unresolved_tags(ad: dict | None, overrides: list | None) -> dict:
    """{measure: tag} for each measure an unresolved deal escalated: "Unresolved" until the analyst decides, then
    "Overridden by analyst" (their own score) or "Engine score" (the engine's score kept)."""
    decided = {o["measure"]: o["decision"] for o in overrides or [] if o.get("source") == "acquisitions"}
    return {m: DECISION_TAG[decided[m]] if m in decided else UNRESOLVED_TAG
            for m in (ad or {}).get("unresolved_measures") or []}


def _tag_for(tags: dict, measures) -> str | None:
    """One tag for these measures: theirs when they agree, else each measure's ("DSO: … · Debt/EBITDAR: …")."""
    picked = {m: tags[m] for m in measures if m in tags}
    if not picked:
        return None
    if UNRESOLVED_TAG in picked.values():
        return UNRESOLVED_TAG
    if len(set(picked.values())) == 1:
        return next(iter(picked.values()))
    return " · ".join(f"{MEASURE_NAME.get(m, m)}: {t}" for m, t in picked.items())


def acquisitions_status(ad: dict | None, overrides: list | None) -> str | None:
    """The pre-check's status as the runs list shows it: an unresolved deal reads as the analyst's decision once
    every escalated measure is decided."""
    if not ad:
        return None
    tag = _tag_for(*[unresolved_tags(ad, overrides)] * 2)
    return ad["status"] if tag in (None, UNRESOLVED_TAG) else tag


def acquisitions_card(ad: dict | None, overrides: list | None = None) -> dict | None:
    if not ad:
        return None
    agent = ad.get("agent")
    adj = ad.get("adjustments") or {}
    tags = unresolved_tags(ad, overrides)
    scores = {o["measure"]: o for o in overrides or [] if o.get("source") == "acquisitions"}
    deals = []
    for d in ad.get("deals") or []:
        tag = _tag_for(tags, d["effects"]["unresolved"]) if d["outcome"] == "unresolved" else None
        decided = tag not in (None, UNRESOLVED_TAG)
        deals.append({
            **d, "kind_label": d["kind"].title(), "timing_label": (d["timing"] or "unknown").replace("_", " "),
            "presentation_label": d["presentation"].replace("_", " "),
            "outcome_label": tag if decided else d["outcome"].replace("_", " "),
            "outcome_css": TAG_CSS.get(tag, "moderate") if decided else AD_OUTCOME_CSS[d["outcome"]],
            "outcome_text": d["outcome_text"] + (" The analyst has decided the escalated score." if decided else ""),
            "effects_text": "; ".join(
                ([f"DSO: {'pro forma revenue' if d['effects']['dso']['action'] == 'pro_forma' else 'divested revenue removed'}"]
                 if d["effects"]["dso"] else [])
                + ([f"Debt/EBITDAR: {d['effects']['leverage']['how']}"] if d["effects"]["leverage"] else [])
                + [f"{MEASURE_NAME.get(u, u)}: {tags.get(u, UNRESOLVED_TAG)}" for u in d["effects"]["unresolved"]])
            or "None",
            "facts": [{"label": FACT_LABELS.get(e["fact"], e["fact"]), "passage_id": e["passage_id"],
                       "section": e["section"], "quote": e["quote"], "text": _fact_text(e)} for e in d["evidence"]]})
    headline = _tag_for(tags, tags)
    label, css = ((AD_LABELS[ad["status"]], AD_CSS[ad["status"]]) if headline in (None, UNRESOLVED_TAG)
                  else (headline.upper(), TAG_CSS.get(headline, "moderate")))
    return {
        "status": ad["status"], "label": label, "css": css,
        "escalated": [{"measure": MEASURE_NAME.get(m, m), "tag": t, "css": TAG_CSS.get(t, "moderate"),
                       "score": (f"{_g(scores[m]['analyst_score'])} (engine {_g(scores[m]['engine_score'])})"
                                 if m in scores and scores[m]["decision"] == "override" else
                                 _g(scores[m]["engine_score"]) if m in scores else None)}
                      for m, t in tags.items()],
        "decided_by": AD_DECIDED_BY.get(ad["decided_by"], ad["decided_by"]), "mode": ad["mode"],
        "reason": ad["reason"], "effect": ad["effect"], "notes": ad.get("notes") or [],
        "info_flags": ad.get("info_flags") or [], "deals": deals, "reported": ad.get("reported") or {},
        "revenue_reported": money((ad.get("reported") or {}).get("revenue")),
        "total_assets": money((ad.get("reported") or {}).get("total_assets")),
        "dso": None if not adj.get("dso") else {
            "reported": money(adj["dso"]["revenue_reported"]), "added": money(adj["dso"]["added"]),
            "removed": money(adj["dso"]["removed"]), "revenue": money(adj["dso"]["revenue"]),
            "deals": ", ".join(adj["dso"]["deals"])},
        "leverage": None if not adj.get("leverage") else {
            "ebitdar": money(adj["leverage"]["ebitdar"]), "debt": money(adj["leverage"]["debt"]),
            "how": adj["leverage"]["how"]},
        "unresolved": [MEASURE_NAME.get(u, u) for u in ad.get("unresolved_measures") or []],
        "signals": (ad.get("rules") or {}).get("signals") or [],
        "sections": ad.get("sections") or [], "passages": ad.get("passages") or [],
        "rules_table": [{"n": n, "when": w, "effect": e} for n, w, e in AD_RULES],
        "agent": None if not agent else {
            "model_id": agent.get("model_id"), "prompt_id": agent.get("prompt_id"),
            "prompt_hash": (agent.get("prompt_hash") or "")[:16], "model_run_id": agent.get("model_run_id"),
            "validation_outcome": agent.get("validation_outcome"),
            "stated": {"label": AD_LABELS.get(agent.get("stated_status"), str(agent.get("stated_status")).upper()),
                       "css": AD_CSS.get(agent.get("stated_status"), "none")} if agent.get("stated_status") else None,
            "consistent": agent.get("status_consistent"), "rationale": agent.get("rationale"),
            "held_back_deals": agent.get("held_back_deals") or [],
            "held_back_evidence": agent.get("held_back_evidence") or [],
            "missing_information": agent.get("missing_information") or [],
            "passages_sent": len((agent.get("inputs") or {}).get("passages") or [])},
        "agent_not_used": None if agent else "Not called: no acquisitions, dispositions, discontinued-operations or "
                                              "held-for-sale note, and no deal mention in the notes.",
    }


MEASURE_NAME = {"dso": "DSO", "leverage": "Debt/EBITDAR", "dscr": "DSCR"}

PP_LABELS = {"not_applicable": "NOT APPLICABLE", "billed_revenue": "BILLED REVENUE", "weight_zero": "DSO WEIGHT 0",
             "weight_half": "DSO WEIGHT 50%", "unresolved": "UNRESOLVED"}
PP_CSS = {"not_applicable": "low", "billed_revenue": "moderate", "weight_zero": "elevated", "weight_half": "elevated",
          "unresolved": "very-high"}
PP_DECIDED_BY = {"agent": "Pre-check Agent (evidence verified against the filing)",
                 "no_text": "Code: no Item 1 or revenue note to read; tagged contract liabilities vs receivables only"}
PP_RULES = [("Revenue split by payer disclosed", "Recompute DSO on billed revenue, factor 1 (normal weight)"),
            ("Not disclosed, and contract liabilities ≥ receivables", "Factor 0: DSO weight 0, spread pro rata"),
            ("Not disclosed, mixed model", "Factor 0.5: DSO weight 50%, spread pro rata"),
            ("Otherwise (a prepaid model, no payer split, contract liabilities below receivables)",
             "Unresolved: DSO as calculated, escalated to an analyst")]


def prepaid_status(pp: dict | None, overrides: list | None) -> str | None:
    """The runs list's status: an unresolved check reads as the analyst's decision on DSO once made."""
    if not pp:
        return None
    decided = next((o for o in overrides or [] if o["measure"] == "dso"), None)
    return DECISION_TAG[decided["decision"]] if pp["status"] == "unresolved" and decided else pp["status"]


def prepaid_card(pp: dict | None, overrides: list | None = None) -> dict | None:
    if not pp:
        return None
    agent = pp.get("agent")
    tag = prepaid_status(pp, overrides)
    decided = tag in DECISION_TAG.values()
    rep = pp.get("reported") or {}
    factor = pp.get("factor")
    return {
        "status": pp["status"], "label": tag.upper() if decided else PP_LABELS[pp["status"]],
        "css": TAG_CSS[tag] if decided else PP_CSS[pp["status"]],
        "decided_by": PP_DECIDED_BY.get(pp["decided_by"], pp["decided_by"]), "mode": pp["mode"],
        "reason": pp["reason"], "effect": pp["effect"], "notes": pp.get("notes") or [],
        "model": pp.get("model") or "not established", "model_basis": pp.get("model_basis"),
        "prepaid_model": pp.get("model") in ("prepaid", "mixed"), "model_evidence": pp.get("model_evidence") or [],
        "contract_liabilities": money(pp.get("contract_liabilities")),
        "contract_liabilities_source": pp.get("contract_liabilities_source") or "not tagged or disclosed",
        "receivables": money(pp.get("receivables")), "cl_ge_receivables": pp.get("cl_ge_receivables"),
        "payer_split": pp.get("payer_split_disclosed"),
        "payers": [{**p, "text": money(p["value"])} for p in pp.get("payer_revenue") or []],
        "billed": {y: money(v) for y, v in (pp.get("billed_revenue") or {}).items()},
        "factor": None if factor is None else f"{factor:g}",
        "dso_weight": None if factor is None else f"{SCORING_DEFAULTS['w_dso'] * factor:g}%",
        "revenue_note_found": pp.get("revenue_note_found"), "passages": pp.get("passages") or [],
        "rules": [{"when": w, "effect": e} for w, e in PP_RULES],
        "agent": None if not agent else {
            "model_id": agent.get("model_id"), "prompt_id": agent.get("prompt_id"),
            "prompt_hash": (agent.get("prompt_hash") or "")[:16], "model_run_id": agent.get("model_run_id"),
            "validation_outcome": agent.get("validation_outcome"),
            "stated": {"label": PP_LABELS.get(agent.get("stated_status"), str(agent.get("stated_status")).upper()),
                       "css": PP_CSS.get(agent.get("stated_status"), "none")} if agent.get("stated_status") else None,
            "stated_model": agent.get("stated_model"), "consistent": agent.get("status_consistent"),
            "rationale": agent.get("rationale"), "held_back": agent.get("held_back") or [],
            "missing_information": agent.get("missing_information") or [],
            "passages_sent": len((agent.get("inputs") or {}).get("passages") or [])},
        "agent_not_used": None if agent else "Not called: there was no Item 1 or revenue note to read.",
    }


T1_LABELS = {"adjusted": "ADJUSTED", "plan_only": "PLAN ONLY: NO ADJUSTMENT", "no_adjustment": "NO ADJUSTMENT",
             "not_triggered": "NOT TRIGGERED", "skipped": "SKIPPED"}
T1_CSS = {"adjusted": "moderate", "plan_only": "very-high", "no_adjustment": "very-high", "not_triggered": "low",
          "skipped": "none"}
T1_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                 "no_text": "Code: no debt note, MD&A liquidity or subsequent-events passage to read",
                 "score": "Code: the DSCR score", "gate": "Code: the going-concern gate"}
OUTCOME_CSS = {"remove_from_principal": "moderate", "executed": "moderate", "plan": "elevated",
               "not_in_principal": "none", "not_rolled_over": "none", "no_backstop": "none",
               "backstop_too_small": "none", "reclassified": "none", "not_year1": "none"}


def trigger_status(t1: dict | None, overrides: list | None) -> str | None:
    """The runs list's status: a trigger that fired reads as the analyst's decision on its measure once made."""
    if not t1:
        return None
    decided = next((o for o in overrides or [] if o["measure"] == t1["measure"]), None)
    return DECISION_TAG[decided["decision"]] if t1.get("fired") and decided else t1["status"]


def _quotes(e: dict, keys) -> list[dict]:
    return [{"fact": label, **e[k]} for k, label in keys if e.get(k)]


def trigger_card(t1: dict | None, overrides: list | None = None) -> dict | None:
    """The DSCR T1 trigger as the page shows it."""
    if not t1:
        return None
    agent = t1.get("agent")
    tag = trigger_status(t1, overrides)
    decided = tag in DECISION_TAG.values()
    decision = next((o for o in overrides or [] if o["measure"] == t1["measure"]), None)
    pr = t1.get("principal") or {}
    return {
        "status": t1["status"], "fired": t1.get("fired"), "label": tag.upper() if decided else T1_LABELS[t1["status"]],
        "css": TAG_CSS[tag] if decided else T1_CSS[t1["status"]], "threshold": T1_THRESHOLD,
        "score": score_cell(t1.get("score")), "column": t1.get("column") or DASH,
        "decided_by": T1_DECIDED_BY.get(t1["decided_by"], t1["decided_by"]), "mode": t1["mode"],
        "reason": t1["reason"], "effect": t1["effect"], "notes": t1.get("notes") or [],
        "decision": None if not decision else (
            f"Overridden by analyst: {_g(decision['analyst_score'])} (engine {_g(decision['engine_score'])})"
            if decision["decision"] == "override" else f"Engine score {_g(decision['engine_score'])}, accepted")
        + (f", by {decision['reviewer']} on {decision['decided_at'][:10]}: {decision['reason']}" if decision else ""),
        "principal": None if not pr else {
            "amount": money(pr.get("principal")), "as_at": pr.get("as_at") or DASH,
            "source": pr.get("concept") or "not tagged", "short_term": pr.get("includes_short_term_borrowings"),
            "ratio": times(pr.get("ratio")), "removed": money(t1.get("principal_removed")),
            "after": money(pr["principal"] - t1["principal_removed"])
            if _num(pr.get("principal")) is not None and t1.get("principal_removed") else None},
        "relief": t1.get("refinancing_relief") or 0,
        "resolution": None if not t1.get("fired") else (
            "Unresolved: the agent's answer and the verified evidence disagree, so DSCR is escalated to an analyst"
            if t1.get("unresolved", True) else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)" + ("" if t1["status"] == "adjusted" else "; the DSCR score stays as calculated")),
        "unresolved": bool(t1.get("fired") and t1.get("unresolved", True)),
        "shared_with": [s.replace("liquidity_t2", "Liquidity T2") for s in t1.get("shared_with") or []],
        "principal_basis": {"maturity_table": "debt maturity table, year 1",
                            "current_portion": "current portion of debt (no maturity table tagged)"}.get(
            pr.get("principal_basis"), "not tagged"),
        "rollover": [{**e, "kind_label": (e["kind"] or "").replace("364_day", "364-day").replace("_", " "),
                      "balance_text": money(e["balance"]),
                      "removed_text": money(e["removed"]) if e["removed"] else DASH,
                      "backstop_text": (f"{money(e['backstop'])} million undrawn, matures {e['backstop_matures']}"
                                        if e.get("backstop") is not None else "none qualifying"),
                      "rolled_over": bool(e.get("rollover_evidence")), "outcome_css": OUTCOME_CSS[e["outcome"]],
                      "quotes": _quotes(e, (("balance_evidence", "Balance"), ("rollover_evidence", "Rolled over"),
                                            ("backstop_evidence", "Backstop")))}
                     for e in t1.get("rollover") or []],
        "refinancings": [{**e, "amount_text": money(e["amount"]) if e.get("amount") is not None else DASH,
                          "share_text": pct(e.get("share_of_principal")), "outcome_css": OUTCOME_CSS[e["outcome"]],
                          "quotes": _quotes(e, (("event_evidence", "Refinancing"),
                                                ("reclassified_evidence", "Classified as long-term"),
                                                ("maturity_evidence", "Maturity"), ("amount_evidence", "Amount")))}
                         for e in t1.get("refinancings") or []],
        "rules": [{"when": w, "effect": e} for w, e in T1_RULES],
        "rollover_outcomes": ROLLOVER_OUTCOMES, "refinancing_outcomes": REFINANCING_OUTCOMES,
        "signals": (t1.get("rules") or {}).get("signals") or [],
        "sections": t1.get("sections") or [], "passages": t1.get("passages") or [],
        "agent": None if not agent else {
            "model_id": agent.get("model_id"), "prompt_id": agent.get("prompt_id"),
            "prompt_hash": (agent.get("prompt_hash") or "")[:16], "model_run_id": agent.get("model_run_id"),
            "validation_outcome": agent.get("validation_outcome"),
            "stated": {"label": T1_LABELS.get(agent.get("stated_status"), str(agent.get("stated_status")).upper()),
                       "css": T1_CSS.get(agent.get("stated_status"), "none")} if agent.get("stated_status") else None,
            "consistent": agent.get("status_consistent"), "rationale": agent.get("rationale"),
            "held_back": agent.get("held_back") or [], "missing_information": agent.get("missing_information") or [],
            "passages_sent": len((agent.get("inputs") or {}).get("passages") or [])},
        "agent_not_used": None if agent else (
            "Not called: no debt note, MD&A liquidity or subsequent-events passage to read." if t1.get("fired")
            else "Not called: the trigger wasn't set off."),
    }


T2_LABELS = {"committed_growth": "COMMITTED GROWTH: NO ADJUSTMENT", "split_disclosed": "SPLIT DISCLOSED: RECOMPUTED",
             "growth_not_quantified": "INPUT CORRECTION: RECOMPUTED", "no_growth": "NO GROWTH: SCORE HELD",
             "not_triggered": "NOT TRIGGERED", "skipped": "SKIPPED"}
T2_CSS = {"committed_growth": "very-high", "split_disclosed": "moderate", "growth_not_quantified": "moderate",
          "no_growth": "very-high", "not_triggered": "low", "skipped": "none"}
T2_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                 "no_text": "Code: no MD&A liquidity and capital resources or segment-note passage to read",
                 "score": "Code: the DSCR score", "capex": "Code: CapEx and depreciation as tagged",
                 "gate": "Code: the going-concern gate"}


def t2_card(t2: dict | None, overrides: list | None = None) -> dict | None:
    """The DSCR T2 trigger as the page shows it."""
    if not t2:
        return None
    agent = t2.get("agent")
    tag = trigger_status(t2, overrides)
    decided = tag in DECISION_TAG.values()
    cx = t2.get("capex") or {}
    split = t2.get("split")
    return {
        "status": t2["status"], "fired": t2.get("fired"), "label": tag.upper() if decided else T2_LABELS[t2["status"]],
        "css": TAG_CSS[tag] if decided else T2_CSS[t2["status"]], "multiple_threshold": T2_MULTIPLE,
        "score": score_cell(t2.get("score")), "column": t2.get("column") or DASH,
        "decided_by": T2_DECIDED_BY.get(t2["decided_by"], t2["decided_by"]), "mode": t2["mode"],
        "reason": t2["reason"], "effect": t2["effect"], "notes": t2.get("notes") or [],
        "capex": None if not cx else {
            "capex": money(cx.get("capex")), "capex_source": cx.get("capex_concept") or "not tagged",
            "depreciation": money(cx.get("depreciation")),
            "depreciation_source": cx.get("depreciation_concept") or "not tagged",
            "multiple": times(cx.get("multiple")), "ratio": times(cx.get("ratio"))},
        "maintenance": money(t2.get("maintenance_capex")) if t2.get("maintenance_capex") is not None else None,
        "maintenance_basis": None if t2.get("maintenance_capex") is None else (
            "depreciation (growth described but not quantified)" if t2["status"] == "growth_not_quantified" else
            "disclosed maintenance CapEx" if (split or {}).get("basis") == "maintenance" else
            f"total CapEx less the disclosed growth CapEx of {money((split or {}).get('growth'))} million"),
        "resolution": None if not t2.get("fired") else (
            "Unresolved: the agent's answer and the verified evidence disagree, so DSCR is escalated to an analyst"
            if t2.get("unresolved", True) else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)" + ("" if t2["status"] in ("split_disclosed", "growth_not_quantified")
                           else "; the DSCR score stays as calculated")),
        "unresolved": bool(t2.get("fired") and t2.get("unresolved", True)),
        "evidence": [{"fact": "Committed or under construction", **e} for e in t2.get("committed") or []]
        + ([{"fact": "Maintenance CapEx" if split["basis"] == "maintenance" else "Growth CapEx", **split["evidence"]}]
           if split else [])
        + [{"fact": "Growth described", **e} for e in t2.get("growth") or []],
        "rules": [{"when": w, "effect": e} for w, e in T2_RULES],
        "sections": t2.get("sections") or [], "passages": t2.get("passages") or [],
        "agent": _trigger_agent(agent, T2_LABELS, T2_CSS),
        "agent_not_used": None if agent else (
            "Not called: no MD&A liquidity and capital resources or segment-note passage to read." if t2.get("fired")
            else "Not called: the trigger wasn't set off."),
    }


def _trigger_agent(agent: dict | None, labels: dict, css: dict) -> dict | None:
    """A trigger's agent record as the page shows it: the model run and the agent's stated answer."""
    if not agent:
        return None
    stated = agent.get("stated_status")
    return {"model_id": agent.get("model_id"), "prompt_id": agent.get("prompt_id"),
            "prompt_hash": (agent.get("prompt_hash") or "")[:16], "model_run_id": agent.get("model_run_id"),
            "validation_outcome": agent.get("validation_outcome"),
            "stated": {"label": labels.get(stated, str(stated).upper()), "css": css.get(stated, "none")}
            if stated else None,
            "consistent": agent.get("status_consistent"), "rationale": agent.get("rationale"),
            "held_back": agent.get("held_back") or [], "missing_information": agent.get("missing_information") or [],
            "passages_sent": len((agent.get("inputs") or {}).get("passages") or [])}


T3_LABELS = {"one_off_quantified": "ONE-OFF QUANTIFIED: RECOMPUTED", "structural": "STRUCTURAL: NO ADJUSTMENT",
             "low_confidence": "LOW DATA CONFIDENCE: SCORE HELD", "not_triggered": "NOT TRIGGERED",
             "skipped": "SKIPPED"}
T3_CSS = {"one_off_quantified": "moderate", "structural": "low", "low_confidence": "elevated", "not_triggered": "low",
          "skipped": "none"}
T3_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                 "no_text": "Code: no income tax note or MD&A tax passage to read",
                 "score": "Code: the DSCR score", "taxes": "Code: cash taxes paid and current tax expense as tagged",
                 "gate": "Code: the going-concern gate"}


def t3_card(t3: dict | None, overrides: list | None = None) -> dict | None:
    """The DSCR T3 trigger as the page shows it."""
    if not t3:
        return None
    agent = t3.get("agent")
    tag = trigger_status(t3, overrides)
    decided = tag in DECISION_TAG.values()
    tx = t3.get("taxes") or {}
    return {
        "status": t3["status"], "fired": t3.get("fired"), "label": tag.upper() if decided else T3_LABELS[t3["status"]],
        "css": TAG_CSS[tag] if decided else T3_CSS[t3["status"]], "share_threshold": pct(T3_SHARE),
        "score": score_cell(t3.get("score")), "column": t3.get("column") or DASH,
        "decided_by": T3_DECIDED_BY.get(t3["decided_by"], t3["decided_by"]), "mode": t3["mode"],
        "reason": t3["reason"], "effect": t3["effect"], "notes": t3.get("notes") or [],
        "data_confidence": t3.get("data_confidence"),
        "taxes": None if not tx else {
            "cash_taxes": money(tx.get("cash_taxes")), "cash_taxes_source": tx.get("cash_taxes_concept") or "not tagged",
            "current_tax": money(tx.get("current_tax")),
            "current_tax_source": tx.get("current_tax_concept") or "not tagged",
            "gap": money(tx.get("gap")), "cfads": money(tx.get("cfads")), "gap_share": pct(tx.get("gap_share"))},
        "backed_out": money(t3.get("backed_out")) if t3.get("backed_out") else None,
        "cash_taxes_after": money(tx["cash_taxes"] - t3["backed_out"]) if t3.get("backed_out") else None,
        "resolution": None if not t3.get("fired") else (
            "Unresolved: the agent's answer and the verified evidence disagree, so DSCR is escalated to an analyst"
            if t3.get("unresolved", True) else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)" + ("" if t3["status"] == "one_off_quantified" else "; the DSCR score stays as calculated")),
        "unresolved": bool(t3.get("fired") and t3.get("unresolved", True)),
        "evidence": [{"fact": f"{e['kind'].replace('_', ' ').capitalize()}: {money(e['amount'])} million "
                              + ("subtracted from" if e["kind"] == "refund" else "added back to") + " cash flow",
                      **e["evidence"]} for e in t3.get("one_offs") or []]
        + [{"fact": "Paid in cash", **e["paid_in_cash_evidence"]} for e in t3.get("one_offs") or []
           if e.get("paid_in_cash_evidence")]
        + [{"fact": f"Structural: {e['label']}", **e} for e in t3.get("structural") or []]
        + [{"fact": "One-off mentioned, not quantified", **e} for e in t3.get("unquantified") or []],
        "rules": [{"when": w, "effect": e} for w, e in T3_RULES],
        "sections": t3.get("sections") or [], "passages": t3.get("passages") or [],
        "agent": _trigger_agent(agent, T3_LABELS, T3_CSS),
        "agent_not_used": None if agent else (
            "Not called: no income tax note or MD&A tax passage to read." if t3.get("fired")
            else "Not called: the trigger wasn't set off."),
    }


ICR_T1_LABELS = {"adjusted": "ADJUSTED", "nothing_found": "NOTHING FOUND: UNRESOLVED", "not_triggered": "NOT TRIGGERED",
                 "skipped": "SKIPPED"}
ICR_T1_CSS = {"adjusted": "moderate", "nothing_found": "very-high", "not_triggered": "low", "skipped": "none"}
ICR_T1_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                     "no_text": "Code: no MD&A or note passage mentions a one-off, a swap or capitalised interest",
                     "score": "Code: the ICR score", "gate": "Code: the going-concern gate"}


def icr_t1_card(t: dict | None, overrides: list | None = None) -> dict | None:
    """The ICR T1 trigger as the page shows it."""
    if not t:
        return None
    agent = t.get("agent")
    tag = trigger_status(t, overrides)
    decided = tag in DECISION_TAG.values()
    fx = t.get("inputs") or {}
    swap = t.get("swap")
    return {
        "status": t["status"], "fired": t.get("fired"),
        "label": tag.upper() if decided else ICR_T1_LABELS[t["status"]],
        "css": TAG_CSS[tag] if decided else ICR_T1_CSS[t["status"]], "threshold": _g(ICR_T1_THRESHOLD),
        "score": score_cell(t.get("score")), "column": t.get("column") or DASH,
        "decided_by": ICR_T1_DECIDED_BY.get(t["decided_by"], t["decided_by"]), "mode": t["mode"],
        "reason": t["reason"], "effect": t["effect"], "notes": t.get("notes") or [],
        "inputs": None if not fx else {
            "ratio": times(fx.get("ratio")), "ebit": money(fx.get("ebit")), "rent": money(fx.get("rent")),
            "interest": money(fx.get("interest")), "interest_source": fx.get("interest_concept") or "not tagged"},
        "ebit_change": money(t.get("ebit_change")) if t.get("ebit_change") else None,
        "interest_change": money(t.get("interest_change")) if t.get("interest_change") else None,
        "resolution": None if not t.get("fired") else (
            f"Unresolved: {t.get('unresolved_why') or 'nothing found to explain the weak cover'}, so ICR is "
            "escalated to an analyst" if t.get("unresolved", True) else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)"),
        "unresolved": bool(t.get("fired") and t.get("unresolved", True)),
        "evidence": [{"fact": f"One-off {e['kind']}: {money(e['amount'])} million "
                              + ("added back to" if e["kind"] == "charge" else "removed from") + " EBIT",
                      **e["evidence"]} for e in t.get("one_offs") or []]
        + ([{"fact": f"Undesignated swap mark-to-market {swap['direction']}: {money(swap['amount'])} million out of "
                     "interest expense", **swap["evidence"]}] if swap else [])
        + ([{"fact": "Not designated as a hedge", **swap["undesignated_evidence"]}]
           if swap and swap.get("undesignated_evidence") else [])
        + ([{"fact": f"Capitalised interest (cross-check): {money(t['capitalised']['amount'])} million",
             **t["capitalised"]["evidence"]}] if t.get("capitalised") else []),
        "rules": [{"when": w, "effect": e} for w, e in ICR_T1_RULES],
        "sections": t.get("sections") or [], "passages": t.get("passages") or [],
        "agent": _trigger_agent(agent, ICR_T1_LABELS, ICR_T1_CSS),
        "agent_not_used": None if agent else (
            "Not called: no MD&A or note passage mentions a one-off, a swap or capitalised interest." if t.get("fired")
            else "Not called: the trigger wasn't set off."),
    }


ICR_T2_CSS = {"gross_disclosed": "moderate", "gross_from_income": "moderate", "kept_as_tagged": "elevated",
              "unresolved": "very-high", "not_applicable": "none", "not_triggered": "low", "skipped": "none"}
ICR_T2_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                     "no_text": "Code: no passage mentions interest expense or income",
                     "tags": "Code: the interest tags", "gate": "Code: the going-concern gate"}


def icr_t2_card(t: dict | None, overrides: list | None = None) -> dict | None:
    """The ICR T2 trigger as the page shows it."""
    if not t:
        return None
    agent = t.get("agent")
    tag = trigger_status(t, overrides)
    decided = tag in DECISION_TAG.values()
    fx = t.get("inputs") or {}
    swap = t.get("swap")
    return {
        "status": t["status"], "fired": t.get("fired"),
        "label": tag.upper() if decided else ICR_T2_LABELS[t["status"]],
        "css": TAG_CSS[tag] if decided else ICR_T2_CSS[t["status"]],
        "score": score_cell(t.get("score")), "column": t.get("column") or DASH,
        "decided_by": ICR_T2_DECIDED_BY.get(t["decided_by"], t["decided_by"]), "mode": t["mode"],
        "reason": t["reason"], "effect": t["effect"], "notes": t.get("notes") or [],
        "flags": [{"net_interest_income": "ICR n/a: net interest is income",
                   "operating_interest": "ICR less meaningful: operating interest (often a captive finance arm)"}[f]
                  for f in t.get("flags") or []],
        "inputs": None if not fx else {
            "kind": {"net": "net interest (net of interest income)", "operating": "operating interest",
                     "net_income": "net interest, which is income"}.get(fx.get("kind"), "gross"),
            "concept": fx.get("concept") or "interest costs incurred",
            "tagged": money(fx.get("tagged_expense") if fx.get("kind") != "net_income"
                            else fx.get("net_interest_income")),
            "capitalised": money(fx.get("capitalised")), "ratio": times(fx.get("ratio"))},
        "gross_used": money(t.get("gross_used")) if t.get("interest_change") else None,
        "resolution": None if not t.get("fired") else (
            f"Unresolved: {t.get('unresolved_why')}, so ICR is escalated to an analyst" if t.get("unresolved")
            else "Resolved: ICR is n/a (no analyst decision)" if t["status"] == "not_applicable" else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)"),
        "unresolved": bool(t.get("fired") and t.get("unresolved")),
        "evidence": ([{"fact": f"Gross interest expense: {money(t['gross']['amount'])} million",
                       **t["gross"]["evidence"]}] if t.get("gross") else [])
        + ([{"fact": f"Interest income: {money(t['income']['amount'])} million", **t["income"]["evidence"]}]
           if t.get("income") else [])
        + ([{"fact": f"Undesignated swap mark-to-market {swap['direction']}: {money(swap['amount'])} million "
                     "removed", **swap["evidence"]}] if swap else [])
        + ([{"fact": "Not designated as a hedge", **swap["undesignated_evidence"]}]
           if swap and swap.get("undesignated_evidence") else []),
        "rules": [{"when": w, "effect": e} for w, e in ICR_T2_RULES],
        "sections": t.get("sections") or [], "passages": t.get("passages") or [],
        "agent": _trigger_agent(agent, ICR_T2_LABELS, ICR_T2_CSS),
        "agent_not_used": None if agent else (
            "Not called: net interest is income, so ICR is n/a." if t["status"] == "not_applicable" else
            "Not called: no passage mentions interest expense or income." if t.get("fired")
            else "Not called: the trigger wasn't set off."),
    }


ICR_T3_CSS = {"pretax_found": "moderate", "pretax_not_found": "very-high", "not_triggered": "low", "skipped": "none"}
ICR_T3_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                     "no_text": "Code: no passage mentions pre-tax income or non-operating items",
                     "tags": "Code: the EBIT source", "gate": "Code: the going-concern gate"}


def icr_t3_card(t: dict | None, overrides: list | None = None) -> dict | None:
    """The ICR T3 trigger as the page shows it."""
    if not t:
        return None
    agent = t.get("agent")
    tag = trigger_status(t, overrides)
    decided = tag in DECISION_TAG.values()
    fx = t.get("inputs") or {}
    return {
        "status": t["status"], "fired": t.get("fired"),
        "label": tag.upper() if decided else ICR_T3_LABELS[t["status"]],
        "css": TAG_CSS[tag] if decided else ICR_T3_CSS[t["status"]],
        "score": score_cell(t.get("score")), "column": t.get("column") or DASH,
        "decided_by": ICR_T3_DECIDED_BY.get(t["decided_by"], t["decided_by"]), "mode": t["mode"],
        "reason": t["reason"], "effect": t["effect"], "notes": t.get("notes") or [],
        "inputs": None if not fx else {"ebit": money(fx.get("ebit")), "source": fx.get("ebit_concept"),
                                       "ratio": times(fx.get("ratio"))},
        "ebit": money(t.get("ebit")) if t.get("ebit") is not None else None,
        "resolution": None if not t.get("fired") else (
            f"Unresolved: {t.get('unresolved_why')}, so ICR is escalated to an analyst" if t.get("unresolved")
            else "Resolved: EBIT is pre-tax income + interest expense; the agent and the verified evidence agree"),
        "unresolved": bool(t.get("fired") and t.get("unresolved")),
        "evidence": icr_t3_evidence(t),
        "rules": [{"when": w, "effect": e} for w, e in ICR_T3_RULES],
        "sections": t.get("sections") or [], "passages": t.get("passages") or [],
        "agent": _trigger_agent(agent, ICR_T3_LABELS, ICR_T3_CSS),
        "agent_not_used": None if agent else (
            "Not called: no passage mentions pre-tax income or non-operating items." if t.get("fired")
            else "Not called: the trigger wasn't set off."),
    }


LEVERAGE_T1_LABELS = {"normalised": "NORMALISED: RECOMPUTED", "operational": "OPERATIONAL: NO ADJUSTMENT",
                      "average_ebitdar": "AVERAGE EBITDAR: RECOMPUTED", "unresolved": "UNRESOLVED",
                      "not_triggered": "NOT TRIGGERED", "skipped": "SKIPPED"}
LEVERAGE_T1_CSS = {"normalised": "moderate", "operational": "low", "average_ebitdar": "elevated",
                   "unresolved": "very-high", "not_triggered": "low", "skipped": "none"}
LEVERAGE_T1_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                          "no_text": "Code: no MD&A or note passage mentions a one-off or an operating driver",
                          "score": "Code: the Debt/EBITDAR score",
                          "one_offs": "Code: EBITDAR and the one-offs as tagged", "gate": "Code: the going-concern gate"}


def leverage_t1_card(t: dict | None, overrides: list | None = None) -> dict | None:
    """The Debt/EBITDAR T1 trigger as the page shows it."""
    if not t:
        return None
    agent = t.get("agent")
    tag = trigger_status(t, overrides)
    decided = tag in DECISION_TAG.values()
    fx = t.get("inputs") or {}
    average = t.get("average")
    return {
        "status": t["status"], "fired": t.get("fired"),
        "label": tag.upper() if decided else (f"{average['n']}-YEAR AVERAGE: RECOMPUTED" if average and t["status"]
                                              == "average_ebitdar" else LEVERAGE_T1_LABELS[t["status"]]),
        "css": TAG_CSS[tag] if decided else LEVERAGE_T1_CSS[t["status"]], "share_threshold": pct(LEVERAGE_T1_SHARE),
        "smaller_reporting_company": t.get("smaller_reporting_company"),
        "score": score_cell(t.get("score")), "column": t.get("column") or DASH,
        "decided_by": LEVERAGE_T1_DECIDED_BY.get(t["decided_by"], t["decided_by"]), "mode": t["mode"],
        "reason": t["reason"], "effect": t["effect"], "notes": t.get("notes") or [], "fired_by": t.get("fired_by"),
        "inputs": None if not fx else {
            "ebitdar": money(fx.get("ebitdar")), "charges": money(fx.get("one_off_charges")),
            "gains": money(fx.get("one_off_gains")), "net": money(fx.get("net_one_offs")),
            "share": pct(fx.get("share")), "source": fx.get("net_one_offs_concept") or "none tagged",
            "ratio": times(fx.get("debt_ratio")), "net_ratio": times(fx.get("net_debt_ratio"))},
        "ebitdar_used": money(t.get("ebitdar_used")) if t.get("ebitdar_used") is not None else None,
        "ebitdar_basis": None if t.get("ebitdar_used") is None else (
            "normalised: reported + one-off charges − one-off gains" if t["status"] == "normalised"
            else average["label"]),
        "average": None if not average else {
            "label": average["label"], "average": money(average["average"]),
            "years": [{"heading": y["heading"], "ebitdar": money(y["ebitdar"])} for y in average["years"]]},
        "resolution": None if not t.get("fired") else (
            f"Unresolved: {t.get('unresolved_why')}, so Debt/EBITDAR is escalated to an analyst"
            if t.get("unresolved", True) else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)"),
        "unresolved": bool(t.get("fired") and t.get("unresolved", True)),
        "evidence": [{"fact": f"One-off {e['kind']} ({e['category'].replace('_', ' ')}): {money(e['amount'])} million "
                              + ("added back to" if e["kind"] == "charge" else "removed from") + " EBITDAR",
                      **e["evidence"]} for e in t.get("one_offs") or []]
        + [{"fact": f"Recurring {e['kind']} ({e['category'].replace('_', ' ')}): {money(e['amount'])} million, not "
                    "adjusted", **e["evidence"]} for e in t.get("recurring") or []]
        + [{"fact": "Recurs", **e["recurring_evidence"]} for e in t.get("recurring") or []]
        + [{"fact": f"One-off identified, not quantified ({e['category'].replace('_', ' ')})", **e["evidence"]}
           for e in t.get("unquantified") or []]
        + [{"fact": "Operational explanation", **e} for e in t.get("operational") or []],
        "rules": [{"when": w, "effect": e} for w, e in LEVERAGE_T1_RULES],
        "sections": t.get("sections") or [], "passages": t.get("passages") or [],
        "agent": _trigger_agent(agent, LEVERAGE_T1_LABELS, LEVERAGE_T1_CSS),
        "agent_not_used": None if agent else (
            "Not called: no MD&A or note passage mentions a one-off or an operating driver." if t.get("fired")
            else "Not called: the trigger wasn't set off."),
    }


LEVERAGE_T2_LABELS = {"cash_reduced": "CASH REDUCED: RECOMPUTED", "debt_maturities": "DEBT MATURITIES: NO ADJUSTMENT",
                      "freely_available": "FREELY AVAILABLE: NO ADJUSTMENT", "unresolved": "UNRESOLVED",
                      "not_triggered": "NOT TRIGGERED", "skipped": "SKIPPED"}
LEVERAGE_T2_CSS = {"cash_reduced": "moderate", "debt_maturities": "low", "freely_available": "low",
                   "unresolved": "very-high", "not_triggered": "low", "skipped": "none"}
LEVERAGE_T2_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                          "no_text": "Code: no MD&A or note passage mentions trapped or earmarked cash",
                          "score": "Code: the Debt/EBITDAR score",
                          "scores": "Code: the Debt / EBITDAR and Net debt / EBITDAR scores",
                          "gate": "Code: the going-concern gate"}
LEVERAGE_T2_KIND_TEXT = {"acquisition": "Acquisition earmark", "payout": "Payout earmark", "trapped": "Trapped cash",
                         "debt_maturity": "Earmarked for debt maturities"}


def leverage_t2_card(t: dict | None, overrides: list | None = None) -> dict | None:
    """The Debt/EBITDAR T2 trigger as the page shows it."""
    if not t:
        return None
    agent = t.get("agent")
    tag = trigger_status(t, overrides)
    decided = tag in DECISION_TAG.values()
    fx = t.get("inputs") or {}

    def fact(e, tail):
        return {"fact": f"{LEVERAGE_T2_KIND_TEXT[e['kind']]}{tail}", **e["evidence"]}

    return {
        "status": t["status"], "fired": t.get("fired"),
        "label": tag.upper() if decided else LEVERAGE_T2_LABELS[t["status"]],
        "css": TAG_CSS[tag] if decided else LEVERAGE_T2_CSS[t["status"]], "gap_threshold": _g(LEVERAGE_T2_GAP),
        "score": score_cell(t.get("score")), "column": t.get("column") or DASH,
        "decided_by": LEVERAGE_T2_DECIDED_BY.get(t["decided_by"], t["decided_by"]), "mode": t["mode"],
        "reason": t["reason"], "effect": t["effect"], "notes": t.get("notes") or [], "fired_by": t.get("fired_by"),
        "inputs": None if not fx else {
            "gross_score": score_cell(fx.get("gross_score")), "net_score": score_cell(fx.get("net_score")),
            "gap": _g(abs(fx["gap"])) if fx.get("gap") is not None else DASH,
            "cash": money(fx.get("cash")), "cash_source": fx.get("cash_concept") or "not tagged",
            "ratio": times(fx.get("debt_ratio")), "net_ratio": times(fx.get("net_debt_ratio"))},
        "removed": money(t.get("removed")) if t.get("removed") else None,
        "cash_after": money(fx["cash"] - t["removed"]) if t.get("removed") else None,
        "resolution": None if not t.get("fired") else (
            f"Unresolved: {t.get('unresolved_why')}, so Debt/EBITDAR is escalated to an analyst"
            if t.get("unresolved", True) else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)"),
        "unresolved": bool(t.get("fired") and t.get("unresolved", True)),
        "evidence": [fact(e, f": {money(e['amount'])} million out of cash") for e in t.get("quantified") or []]
        + [fact(e, ": described, not quantified") for e in t.get("unquantified") or []]
        + [fact(e, f": {money(e['amount'])} million, no adjustment" if e.get("amount") is not None
                else ": no adjustment") for e in t.get("maturities") or []]
        + [{"fact": "Freely available", **e} for e in t.get("available") or []],
        "rules": [{"when": w, "effect": e} for w, e in LEVERAGE_T2_RULES],
        "sections": t.get("sections") or [], "passages": t.get("passages") or [],
        "agent": _trigger_agent(agent, LEVERAGE_T2_LABELS, LEVERAGE_T2_CSS),
        "agent_not_used": None if agent else (
            "Not called: no MD&A or note passage mentions trapped or earmarked cash." if t.get("fired")
            else "Not called: the trigger wasn't set off."),
    }


LEVERAGE_T3_LABELS = {"disclosed": "DISCLOSED: +1", "not_disclosed": "NOT DISCLOSED: UNRESOLVED",
                      "not_triggered": "NOT TRIGGERED", "skipped": "SKIPPED"}
LEVERAGE_T3_CSS = {"disclosed": "elevated", "not_disclosed": "very-high", "not_triggered": "low", "skipped": "none"}
LEVERAGE_T3_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                          "no_text": "Code: no MD&A or note passage mentions a master lease, a cross-default, a lease "
                                     "guarantee or lease terms",
                          "score": "Code: the Debt/EBITDAR score",
                          "scores": "Code: the composite leverage score with and without leases",
                          "gate": "Code: the going-concern gate"}


def leverage_t3_card(t: dict | None, overrides: list | None = None) -> dict | None:
    """The Debt/EBITDAR T3 trigger as the page shows it."""
    if not t:
        return None
    agent = t.get("agent")
    tag = trigger_status(t, overrides)
    decided = tag in DECISION_TAG.values()
    fx = t.get("inputs") or {}

    def fact(e, tail):
        who = f" with {e['counterparty']}" if e.get("counterparty") else ""
        return {"fact": f"{LEVERAGE_T3_KIND_TEXT[e['kind']]}{who}{tail}", **e["evidence"]}

    return {
        "status": t["status"], "fired": t.get("fired"),
        "label": tag.upper() if decided else LEVERAGE_T3_LABELS[t["status"]],
        "css": TAG_CSS[tag] if decided else LEVERAGE_T3_CSS[t["status"]], "gap_threshold": _g(LEVERAGE_T3_GAP),
        "uplift": _g(LEVERAGE_T3_UPLIFT),
        "score": score_cell(t.get("score")), "column": t.get("column") or DASH,
        "decided_by": LEVERAGE_T3_DECIDED_BY.get(t["decided_by"], t["decided_by"]), "mode": t["mode"],
        "reason": t["reason"], "effect": t["effect"], "notes": t.get("notes") or [], "fired_by": t.get("fired_by"),
        "inputs": None if not fx else {
            "score": score_cell(fx.get("score")), "score_without": score_cell(fx.get("score_without")),
            "gap": _g(fx["gap"]) if fx.get("gap") is not None else DASH,
            "debt": money(fx.get("debt")), "op_leases": money(fx.get("op_leases")), "rent": money(fx.get("rent")),
            "ebitdar": money(fx.get("ebitdar")), "ebitda": money(fx.get("ebitda")),
            "lease_source": fx.get("op_leases_concept") or "not tagged", "rent_source": fx.get("rent_concept") or "not tagged",
            "ratio": times(fx.get("debt_ratio")), "net_ratio": times(fx.get("net_debt_ratio")),
            "ratio_without": times(fx.get("ratio_without")), "net_ratio_without": times(fx.get("net_ratio_without"))},
        "resolution": None if not t.get("fired") else (
            f"Unresolved: {t.get('unresolved_why')}, so Debt/EBITDAR is escalated to an analyst"
            if t.get("unresolved", True) else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)"),
        "unresolved": bool(t.get("fired") and t.get("unresolved", True)),
        "evidence": [fact(e, f": +{_g(LEVERAGE_T3_UPLIFT)}") for e in t.get("debt_like") or []]
        + [fact(e, ": no lease adjustment") for e in t.get("other") or []],
        "rules": [{"when": w, "effect": e} for w, e in LEVERAGE_T3_RULES],
        "sections": t.get("sections") or [], "passages": t.get("passages") or [],
        "agent": _trigger_agent(agent, LEVERAGE_T3_LABELS, LEVERAGE_T3_CSS),
        "agent_not_used": None if agent else (
            "Not called: no MD&A or note passage mentions a master lease, a cross-default, a lease guarantee or lease "
            "terms." if t.get("fired") else "Not called: the trigger wasn't set off."),
    }


LEVERAGE_T4_LABELS = {"reconciles": "RECONCILED: RECOMPUTED", "does_not_reconcile": "DOESN'T RECONCILE: UNRESOLVED",
                      "not_triggered": "NOT TRIGGERED", "skipped": "SKIPPED"}
LEVERAGE_T4_CSS = {"reconciles": "moderate", "does_not_reconcile": "very-high", "not_triggered": "low", "skipped": "none"}
LEVERAGE_T4_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                          "no_text": "Code: no balance-sheet, debt-note or lease-note passage mentions debt",
                          "score": "Code: the Debt/EBITDAR score", "debt": "Code: how total debt was tagged",
                          "gate": "Code: the going-concern gate"}


def leverage_t4_card(t: dict | None, overrides: list | None = None) -> dict | None:
    """The Debt/EBITDAR T4 trigger as the page shows it."""
    if not t:
        return None
    agent = t.get("agent")
    tag = trigger_status(t, overrides)
    decided = tag in DECISION_TAG.values()
    fx = t.get("inputs") or {}
    total, fl = t.get("note_total"), t.get("finance_leases_note")
    inside = t.get("finance_leases_inside_evidence")
    return {
        "status": t["status"], "fired": t.get("fired"),
        "label": tag.upper() if decided else LEVERAGE_T4_LABELS[t["status"]],
        "css": TAG_CSS[tag] if decided else LEVERAGE_T4_CSS[t["status"]], "tie_share": pct(LEVERAGE_T4_TIE),
        "score": score_cell(t.get("score")), "column": t.get("column") or DASH,
        "decided_by": LEVERAGE_T4_DECIDED_BY.get(t["decided_by"], t["decided_by"]), "mode": t["mode"],
        "reason": t["reason"], "effect": t["effect"], "notes": t.get("notes") or [], "fired_by": t.get("fired_by"),
        "inputs": None if not fx else {
            "debt": money(fx.get("debt")), "debt_source": fx.get("debt_concept") or "not tagged",
            "borrowings": money(fx.get("borrowings")),
            "total_skipped": None if fx.get("total_skipped") is None else
            f"{money(fx['total_skipped'])} million ({fx.get('total_skipped_concept')})",
            "finance_leases_added": None if fx.get("finance_leases_added") is None else
            f"{money(fx['finance_leases_added'])} million ({fx.get('finance_leases_concept')})",
            "ratio": times(fx.get("debt_ratio"))},
        "reconciled": money(t.get("reconciled_debt")) if t.get("reconciled_debt") is not None else None,
        "instruments_sum": money(t.get("instruments_sum")) if t.get("instruments_sum") is not None else None,
        "resolution": None if not t.get("fired") else (
            f"Unresolved: {t.get('unresolved_why')}, so Debt/EBITDAR is escalated to an analyst"
            if t.get("unresolved", True) else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)"),
        "unresolved": bool(t.get("fired") and t.get("unresolved", True)),
        "evidence": [{"fact": f"{LEVERAGE_T4_KIND_TEXT[e['kind']]}: {e['description']}, {money(e['amount'])} million",
                      **e["evidence"]} for e in t.get("instruments") or []]
        + ([{"fact": f"Debt note total: {money(total['amount'])} million", **total["evidence"]}] if total else [])
        + ([{"fact": f"Finance lease liabilities (lease note): {money(fl['amount'])} million", **fl["evidence"]}]
           if fl else [])
        + ([{"fact": "Finance leases already in the total", **inside}] if inside else []),
        "rules": [{"when": w, "effect": e} for w, e in LEVERAGE_T4_RULES],
        "sections": t.get("sections") or [], "passages": t.get("passages") or [],
        "agent": _trigger_agent(agent, LEVERAGE_T4_LABELS, LEVERAGE_T4_CSS),
        "agent_not_used": None if agent else (
            "Not called: no balance-sheet, debt-note or lease-note passage mentions debt." if t.get("fired")
            else "Not called: the trigger wasn't set off."),
    }


RCF_T1_LABELS = {"added_back": "ADDED BACK: RECOMPUTED", "no_one_offs": "NO ONE-OFFS: NO ADJUSTMENT",
                 "unresolved": "UNRESOLVED", "not_triggered": "NOT TRIGGERED", "skipped": "SKIPPED"}
RCF_T1_CSS = {"added_back": "moderate", "no_one_offs": "low", "unresolved": "very-high", "not_triggered": "low",
              "skipped": "none"}
RCF_T1_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                     "no_text": "Code: no MD&A or note passage mentions a one-off cash item or a special dividend",
                     "score": "Code: the RCF / debt score and ratio", "gate": "Code: the going-concern gate"}


def rcf_t1_card(t: dict | None, overrides: list | None = None) -> dict | None:
    """The RCF/Debt T1 trigger as the page shows it."""
    if not t:
        return None
    agent = t.get("agent")
    tag = trigger_status(t, overrides)
    decided = tag in DECISION_TAG.values()
    fx = t.get("inputs") or {}

    def kind(e):
        return "Special dividend" if e["kind"] == "special_dividend" else "One-off cash cost"

    return {
        "status": t["status"], "fired": t.get("fired"),
        "label": tag.upper() if decided else RCF_T1_LABELS[t["status"]],
        "css": TAG_CSS[tag] if decided else RCF_T1_CSS[t["status"]], "threshold": _g(RCF_T1_THRESHOLD),
        "score": score_cell(t.get("score")), "column": t.get("column") or DASH,
        "decided_by": RCF_T1_DECIDED_BY.get(t["decided_by"], t["decided_by"]), "mode": t["mode"],
        "reason": t["reason"], "effect": t["effect"], "notes": t.get("notes") or [], "fired_by": t.get("fired_by"),
        "inputs": None if not fx else {
            "cfo": money(fx.get("cfo")), "wc": money(fx.get("wc_change")), "ffo": money(fx.get("ffo")),
            "ffo_basis": fx.get("ffo_basis") or DASH, "dividends": money(fx.get("dividends")),
            "nci": money(fx.get("nci_distributions")), "rcf": money(fx.get("rcf")), "debt": money(fx.get("adj_debt")),
            "ratio": pct(fx.get("ratio"))},
        "cash_costs": money(t.get("cash_costs")) if t.get("cash_costs") else None,
        "special_dividends": money(t.get("special_dividends")) if t.get("special_dividends") else None,
        "resolution": None if not t.get("fired") else (
            f"Unresolved: {t.get('unresolved_why')}, so RCF / debt is escalated to an analyst"
            if t.get("unresolved", True) else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)"),
        "unresolved": bool(t.get("fired") and t.get("unresolved", True)),
        "evidence": [{"fact": f"{kind(e)} ({e['category'].replace('_', ' ')}): {money(e['amount'])} million, added "
                              "back", **e["evidence"]} for e in t.get("one_offs") or []]
        + [{"fact": f"Recurring {kind(e).lower()} ({e['category'].replace('_', ' ')}): {money(e['amount'])} million, "
                    "not adjusted", **e["evidence"]} for e in t.get("recurring") or []]
        + [{"fact": "Recurs", **e["recurring_evidence"]} for e in t.get("recurring") or []]
        + [{"fact": f"{kind(e)} identified, not quantified ({e['category'].replace('_', ' ')})", **e["evidence"]}
           for e in t.get("unquantified") or []],
        "rules": [{"when": w, "effect": e} for w, e in RCF_T1_RULES],
        "sections": t.get("sections") or [], "passages": t.get("passages") or [],
        "agent": _trigger_agent(agent, RCF_T1_LABELS, RCF_T1_CSS),
        "agent_not_used": None if agent else (
            "Not called: no MD&A or note passage mentions a one-off cash item or a special dividend." if t.get("fired")
            else "Not called: the trigger wasn't set off."),
    }


DSO_T1_LABELS = {"recomputed": "MECHANICAL: RECOMPUTED", "recovered": f"RECOVERED: −{DSO_T1_RELIEF}",
                 "unresolved": "UNRESOLVED", "no_cause": "NO CAUSE: NO ADJUSTMENT", "not_triggered": "NOT TRIGGERED",
                 "skipped": "SKIPPED"}
DSO_T1_CSS = {"recomputed": "moderate", "recovered": "moderate", "unresolved": "very-high", "no_cause": "low",
              "not_triggered": "low", "skipped": "none"}
DSO_T1_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                     "no_text": "Code: no MD&A, note or subsequent-events passage mentions receivables or collections",
                     "score": "Code: the change in DSO", "gate": "Code: the going-concern gate"}


LIQUIDITY_T1_LABELS = {"stated": "AVAILABILITY STATED", "computed": "AVAILABILITY WORKED OUT",
                       "no_revolver": "NO REVOLVER: NO ADJUSTMENT", "unresolved": "UNRESOLVED",
                       "not_triggered": "NOT TRIGGERED", "skipped": "SKIPPED"}
LIQUIDITY_T1_CSS = {"stated": "moderate", "computed": "moderate", "no_revolver": "low", "unresolved": "very-high",
                    "not_triggered": "low", "skipped": "none"}
LIQUIDITY_T1_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                           "no_text": "Code: no debt-note or MD&A liquidity passage mentions a revolver",
                           "score": "Code: the revolver availability search", "gate": "Code: the going-concern gate"}


def liquidity_t1_card(t: dict | None, overrides: list | None = None) -> dict | None:
    """The Liquidity T1 trigger as the page shows it."""
    if not t:
        return None
    agent = t.get("agent")
    tag = trigger_status(t, overrides)
    decided = tag in DECISION_TAG.values()
    fx = t.get("inputs") or {}
    facilities = [{
        "facility_id": f["facility_id"], "description": f["description"], "asset_based": f["asset_based"],
        "route": LIQUIDITY_T1_ROUTE_TEXT[f["route"]], "unresolved": f["route"] == "unresolved",
        "availability": money(f["availability"]) if f["availability"] is not None else None,
        "figures": [{"label": LIQUIDITY_T1_FIGURE_TEXT[k], "amount": money(x["amount"])}
                    for k, x in f["figures"].items()],
        "maturity": (f["maturity"] or {}).get("date"),
        "matures_soon": bool((f["maturity"] or {}).get("within_12_months")),
        "unresolved_why": f["unresolved_why"]} for f in t.get("facilities") or []]
    return {
        "status": t["status"], "fired": t.get("fired"),
        "label": tag.upper() if decided else LIQUIDITY_T1_LABELS[t["status"]],
        "css": TAG_CSS[tag] if decided else LIQUIDITY_T1_CSS[t["status"]],
        "score": score_cell(t.get("score")), "column": t.get("column") or DASH,
        "decided_by": LIQUIDITY_T1_DECIDED_BY.get(t["decided_by"], t["decided_by"]), "mode": t["mode"],
        "reason": t["reason"], "effect": t["effect"], "notes": t.get("notes") or [], "fired_by": t.get("fired_by"),
        "inputs": None if not fx else {
            "as_at": fx.get("as_at"), "cash": money(fx.get("cash")), "cfo": money(fx.get("cfo")),
            "coverage": times(fx.get("coverage")), "refinancing": times(fx.get("refinancing")),
            "search": fx.get("revolver_found_source")},
        "availability": money(t.get("availability")) if t.get("availability") is not None else None,
        "facilities": facilities,
        "resolution": None if not t.get("fired") else (
            f"Unresolved: {t.get('unresolved_why')}, so liquidity and debt maturity is escalated to an analyst"
            if t.get("unresolved", True) else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)"),
        "unresolved": bool(t.get("fired") and t.get("unresolved", True)),
        "evidence": [q for f in t.get("facilities") or [] for q in (
            [{"fact": f"{f['facility_id']}: revolving facility" + (" (asset-based)" if f["asset_based"] else ""),
              **f["evidence"]}]
            + [{"fact": f"{f['facility_id']}: {LIQUIDITY_T1_FIGURE_TEXT[k].lower()} {money(x['amount'])} million",
                **x["evidence"]} for k, x in f["figures"].items()]
            + ([{"fact": f"{f['facility_id']}: matures {f['maturity']['date']}", **f["maturity"]["evidence"]}]
               if f.get("maturity") else []))],
        "rules": [{"when": w, "effect": e} for w, e in LIQUIDITY_T1_RULES],
        "sections": t.get("sections") or [], "passages": t.get("passages") or [],
        "agent": _trigger_agent(agent, LIQUIDITY_T1_LABELS, LIQUIDITY_T1_CSS),
        "agent_not_used": None if agent else (
            "Not called: no debt-note or MD&A liquidity passage mentions a revolver, credit facility or letters of "
            "credit." if t.get("fired") else "Not called: the trigger wasn't set off."),
    }


LIQUIDITY_T2_LABELS = {"recomputed": "MATURITIES RECOMPUTED", "already_long_term": "ALREADY LONG-TERM: NO ADJUSTMENT",
                       "unresolved": "UNRESOLVED", "none_found": "NOTHING FOUND: NO ADJUSTMENT",
                       "not_triggered": "NOT TRIGGERED", "skipped": "SKIPPED"}
LIQUIDITY_T2_CSS = {"recomputed": "moderate", "already_long_term": "low", "unresolved": "very-high", "none_found": "low",
                    "not_triggered": "low", "skipped": "none"}
LIQUIDITY_T2_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                           "no_text": "Code: no subsequent-events, debt-note, MD&A liquidity or going-concern passage",
                           "score": "Code: the coverage and refinancing scores", "gate": "Code: the going-concern gate"}
_T2_EVIDENCE = {"reclassified": "already long-term (ASC 470-10-45-14)", "event": "event",
                "original_maturity": "original maturity", "amount": "amount", "new_maturity": "new maturity",
                "source": "source of repayment", "cash_used": "cash used"}


def liquidity_t2_card(t: dict | None, overrides: list | None = None) -> dict | None:
    """The Liquidity T2 trigger as the page shows it."""
    if not t:
        return None
    agent = t.get("agent")
    tag = trigger_status(t, overrides)
    decided = tag in DECISION_TAG.values()
    fx = t.get("inputs") or {}
    bucket = lambda k: LIQUIDITY_T2_BUCKET_TEXT.get(k, DASH) if k else DASH  # noqa: E731
    items = [{"item_id": e["item_id"], "description": e["description"],
              "kind": LIQUIDITY_T2_KIND_TEXT.get(e["kind"], str(e["kind"]).capitalize()),
              "route": LIQUIDITY_T2_ROUTE_TEXT[e["route"]], "unresolved": e["route"] in ("planned", "incomplete"),
              "date": e.get("date") or DASH, "amount": money(e["amount"]) if e.get("amount") is not None else DASH,
              "move": f"{bucket(e.get('from'))} → {bucket(e.get('to'))}" if e.get("to") else bucket(e.get("from")),
              "cash_used": money(e["cash_used"]) if e.get("cash_used") else None,
              "dscr_t1_item": e.get("dscr_t1_item"), "unresolved_why": e.get("unresolved_why")}
             for e in t.get("items") or []]
    return {
        "status": t["status"], "fired": t.get("fired"),
        "label": tag.upper() if decided else LIQUIDITY_T2_LABELS[t["status"]],
        "css": TAG_CSS[tag] if decided else LIQUIDITY_T2_CSS[t["status"]],
        "threshold": _g(LIQUIDITY_T2_THRESHOLD), "score": score_cell(t.get("score")), "column": t.get("column") or DASH,
        "decided_by": LIQUIDITY_T2_DECIDED_BY.get(t["decided_by"], t["decided_by"]), "mode": t["mode"],
        "reason": t["reason"], "effect": t["effect"], "notes": t.get("notes") or [], "fired_by": t.get("fired_by"),
        "inputs": None if not fx else {
            "as_at": fx.get("as_at"), "cash": money(fx.get("cash")),
            "coverage": times(fx.get("coverage")), "coverage_score": score_cell(fx.get("coverage_score")),
            "refinancing": times(fx.get("refinancing")), "refinancing_score": score_cell(fx.get("refinancing_score")),
            "ladder": [{"label": LIQUIDITY_T2_BUCKET_TEXT[k], "amount": money(v)}
                       for k, v in (fx.get("ladder") or {}).items()]},
        "debts": items,
        "dscr_t1": [{"item_id": e["item_id"], "outcome": e["outcome"].replace("_", " ")}
                    for e in t.get("dscr_t1") or []],
        "resolution": None if not t.get("fired") else (
            f"Unresolved: {t.get('unresolved_why')}, so liquidity and debt maturity is escalated to an analyst"
            if t.get("unresolved", True) else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)"),
        "unresolved": bool(t.get("fired") and t.get("unresolved", True)),
        "evidence": [{"fact": f"{e['item_id']}: {_T2_EVIDENCE[k]}" + (" (reused from DSCR T1)" if q.get("reused") else ""),
                      "section": q.get("section") or ("DSCR T1" if q.get("reused") else DASH), **q}
                     for e in t.get("items") or [] for k, q in e["evidence"].items()],
        "rules": [{"when": w, "effect": e} for w, e in LIQUIDITY_T2_RULES],
        "sections": t.get("sections") or [], "passages": t.get("passages") or [],
        "agent": _trigger_agent(agent, LIQUIDITY_T2_LABELS, LIQUIDITY_T2_CSS),
        "agent_not_used": None if agent else (
            "Not called: no subsequent-events, debt-note, MD&A liquidity or going-concern passage to read."
            if t.get("fired") else "Not called: the trigger wasn't set off."),
    }


LIQUIDITY_T3_LABELS = {"secured": "SECURED: −1", "unsecured": "UNSECURED: +1", "mixed": "MIXED: NO CHANGE",
                       "not_triggered": "NOT TRIGGERED", "skipped": "SKIPPED"}
LIQUIDITY_T3_CSS = {"secured": "moderate", "unsecured": "elevated", "mixed": "low", "not_triggered": "low",
                    "skipped": "none"}
LIQUIDITY_T3_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                           "no_text": "Code: no debt-note or MD&A liquidity passage describes the debt",
                           "score": "Code: the maturity table", "gate": "Code: the going-concern gate"}


def liquidity_t3_card(t: dict | None, overrides: list | None = None) -> dict | None:
    """The Liquidity T3 trigger as the page shows it."""
    if not t:
        return None
    tag = trigger_status(t, overrides)
    decided = tag in DECISION_TAG.values()
    fx = t.get("inputs") or {}
    return {
        "status": t["status"], "fired": t.get("fired"),
        "label": tag.upper() if decided else LIQUIDITY_T3_LABELS[t["status"]],
        "css": TAG_CSS[tag] if decided else LIQUIDITY_T3_CSS[t["status"]],
        "score": score_cell(t.get("score")), "column": t.get("column") or DASH,
        "decided_by": LIQUIDITY_T3_DECIDED_BY.get(t["decided_by"], t["decided_by"]), "mode": t["mode"],
        "reason": t["reason"], "effect": t["effect"], "notes": t.get("notes") or [], "fired_by": t.get("fired_by"),
        "inputs": None if not fx.get("wall") else {
            "wall": fx["wall_label"], "amount": money(fx["wall_amount"]), "total": money(fx["total"]),
            "share": pct(fx["share"])},
        "shares": None if not t.get("fired") else {"secured": pct(t.get("secured_share")),
                                                   "unsecured": pct(t.get("unsecured_share"))},
        "resolution": None if not t.get("fired") else (
            f"Unresolved: {t.get('unresolved_why')}, so liquidity and debt maturity is escalated to an analyst"
            if t.get("unresolved", True) else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)"),
        "unresolved": bool(t.get("fired") and t.get("unresolved", True)),
        "evidence": [{"fact": f"{e['item_id']}: {LIQUIDITY_T3_KIND_TEXT[e['kind']].lower()}, {money(e['amount'])} "
                              f"million, matures {e['maturity']}", **e["evidence"]} for e in t.get("instruments") or []],
        "rules": [{"when": w, "effect": e} for w, e in LIQUIDITY_T3_RULES],
        "sections": t.get("sections") or [], "passages": t.get("passages") or [],
        "agent": _trigger_agent(t.get("agent"), LIQUIDITY_T3_LABELS, LIQUIDITY_T3_CSS),
    }


def dso_t1_card(t: dict | None, overrides: list | None = None) -> dict | None:
    """The DSO T1 trigger as the page shows it."""
    if not t:
        return None
    agent = t.get("agent")
    tag = trigger_status(t, overrides)
    decided = tag in DECISION_TAG.values()
    fx = t.get("inputs") or {}
    return {
        "status": t["status"], "fired": t.get("fired"),
        "label": tag.upper() if decided else DSO_T1_LABELS[t["status"]],
        "css": TAG_CSS[tag] if decided else DSO_T1_CSS[t["status"]], "threshold": _g(DSO_T1_DAYS),
        "score": score_cell(t.get("score")), "column": t.get("column") or DASH,
        "decided_by": DSO_T1_DECIDED_BY.get(t["decided_by"], t["decided_by"]), "mode": t["mode"],
        "reason": t["reason"], "effect": t["effect"], "notes": t.get("notes") or [], "fired_by": t.get("fired_by"),
        "inputs": None if not fx else {
            "receivables": money(fx.get("receivables")), "revenue": money(fx.get("revenue")), "dso": days(fx.get("dso")),
            "prior_receivables": money(fx.get("prior_receivables")), "prior_revenue": money(fx.get("prior_revenue")),
            "prior_dso": days(fx.get("prior_dso")), "change": days(fx.get("change"), signed=True),
            "deal_adjusted": fx.get("deal_adjusted")},
        "added": money(t.get("added")) if t.get("added") else None,
        "prior_added": money(t.get("prior_added")) if t.get("prior_added") else None,
        "resolution": None if not t.get("fired") else (
            f"Unresolved: {t.get('unresolved_why')}, so DSO is escalated to an analyst"
            if t.get("unresolved", True) else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)"),
        "unresolved": bool(t.get("fired") and t.get("unresolved", True)),
        "evidence": [{"fact": f"{DSO_T1_KIND_TEXT[e['kind']]} ({e['year']} year): {money(e['amount'])} million, added "
                              "back", **e["evidence"]} for e in t.get("mechanical") or []]
        + [{"fact": "Unusual", **e["unusual_evidence"]} for e in t.get("mechanical") or [] if e.get("unusual_evidence")]
        + [{"fact": f"{DSO_T1_KIND_TEXT[e['kind']]}: mentioned, not quantified", **e["evidence"]}
           for e in t.get("unquantified") or []]
        + [{"fact": f"Cause: {DSO_T1_KIND_TEXT[e['kind']].lower()}", **e["evidence"]}
           for e in t.get("operational") or []]
        + ([{"fact": "Collections recovered after the year-end", **t["recovery"]}] if t.get("recovery") else []),
        "rules": [{"when": w, "effect": e} for w, e in DSO_T1_RULES],
        "sections": t.get("sections") or [], "passages": t.get("passages") or [],
        "agent": _trigger_agent(agent, DSO_T1_LABELS, DSO_T1_CSS),
        "agent_not_used": None if agent else (
            "Not called: no MD&A, note or subsequent-events passage mentions receivables or collections."
            if t.get("fired") else "Not called: the trigger wasn't set off."),
    }


RCF_T2_LABELS = {"removed": "INFLOWS REMOVED: RECOMPUTED", "no_adjustment": "NO ADJUSTMENT",
                 "unresolved": "UNRESOLVED", "not_triggered": "NOT TRIGGERED", "skipped": "SKIPPED"}
RCF_T2_CSS = {"removed": "moderate", "no_adjustment": "low", "unresolved": "very-high", "not_triggered": "low",
              "skipped": "none"}
RCF_T2_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                     "no_text": "Code: no cash flow statement, MD&A or note passage mentions a cash receipt",
                     "score": "Code: the RCF / debt score",
                     "ffo": "Code: the tagged one-off inflows and FFO's change on the prior year",
                     "gate": "Code: the going-concern gate"}
RCF_T2_OUTCOME = {"remove": "removed from CFO and FFO", "inside": "inside the working-capital lines: no adjustment",
                  "recurring": "recurs: no adjustment", "not_one_time": "not described as one-time: no adjustment",
                  "outside_cash_flow": "investing or financing: no adjustment", "unquantified": "not quantified",
                  "unknown_composition": "can't be placed (no working-capital lines tagged)"}


def rcf_t2_card(t: dict | None, overrides: list | None = None) -> dict | None:
    """The RCF/Debt T2 trigger as the page shows it."""
    if not t:
        return None
    agent = t.get("agent")
    tag = trigger_status(t, overrides)
    decided = tag in DECISION_TAG.values()
    fx = t.get("inputs") or {}
    return {
        "status": t["status"], "fired": t.get("fired"),
        "label": tag.upper() if decided else RCF_T2_LABELS[t["status"]],
        "css": TAG_CSS[tag] if decided else RCF_T2_CSS[t["status"]], "share_threshold": pct(RCF_T2_SHARE),
        "growth_threshold": pct(RCF_T2_GROWTH), "excess_threshold": f"{RCF_T2_EXCESS * 100:.0f} points",
        "score": score_cell(t.get("score")), "column": t.get("column") or DASH,
        "decided_by": RCF_T2_DECIDED_BY.get(t["decided_by"], t["decided_by"]), "mode": t["mode"],
        "reason": t["reason"], "effect": t["effect"], "notes": t.get("notes") or [], "fired_by": t.get("fired_by"),
        "inputs": None if not fx else {
            "cfo": money(fx.get("cfo")), "wc": money(fx.get("wc_change")), "ffo": money(fx.get("ffo")),
            "ffo_basis": fx.get("ffo_basis") or DASH, "prior_ffo": money(fx.get("prior_ffo")),
            "prior_heading": fx.get("prior_heading") or DASH, "growth": pct(fx.get("growth")),
            "revenue_growth": pct(fx.get("revenue_growth")) if fx.get("revenue_growth") is not None else "not tagged",
            "tagged": money(fx.get("tagged_total")), "share": pct(fx.get("share")),
            "tagged_concepts": ", ".join(e["concept"] for e in fx.get("tagged_inflows") or []) or "none tagged",
            "wc_lines": ", ".join(x["label"] for x in fx.get("working_capital_lines") or []) or "none tagged"},
        "removed": money(t.get("removed")) if t.get("removed") else None,
        "resolution": None if not t.get("fired") else (
            f"Unresolved: {t.get('unresolved_why')}, so RCF / debt is escalated to an analyst"
            if t.get("unresolved", True) else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)"),
        "unresolved": bool(t.get("fired") and t.get("unresolved", True)),
        "evidence": [{"fact": f"{RCF_T2_KIND_TEXT.get(e['kind'], 'Inflow')} ({(e.get('category') or 'other').replace('_', ' ')})"
                              + (f": {money(e['amount'])} million" if e.get("amount") is not None else "")
                              + f", {RCF_T2_OUTCOME.get(e['outcome'], e['outcome'])}"
                              + (f" ({e['recurrence']})" if e.get("recurrence") else ""), **e["evidence"]}
                     for e in t.get("inflows") or []]
        + [{"fact": "Described as one-time or timing", **e["one_time_evidence"]}
           for e in t.get("inflows") or [] if e.get("one_time_evidence")],
        "rules": [{"when": w, "effect": e} for w, e in RCF_T2_RULES],
        "sections": t.get("sections") or [], "passages": t.get("passages") or [],
        "agent": _trigger_agent(agent, RCF_T2_LABELS, RCF_T2_CSS),
        "agent_not_used": None if agent else (
            "Not called: no cash flow statement, MD&A or note passage mentions a cash receipt." if t.get("fired")
            else "Not called: the trigger wasn't set off."),
    }


RCF_T3_LABELS = {"cash_paid": "CASH PAID: RECOMPUTED", "declared": "DECLARED: RECOMPUTED (TIMING)",
                 "none_in_year": "NONE IN THE YEAR: NO ADJUSTMENT", "unresolved": "UNRESOLVED",
                 "not_triggered": "NOT TRIGGERED", "skipped": "SKIPPED"}
RCF_T3_CSS = {"cash_paid": "moderate", "declared": "elevated", "none_in_year": "low", "unresolved": "very-high",
              "not_triggered": "low", "skipped": "none"}
RCF_T3_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                     "no_text": "Code: no cash flow statement, equity statement or note passage mentions NCI",
                     "score": "Code: the RCF / debt score",
                     "nci": "Code: the NCI-distributions tags and NCI's size", "gate": "Code: the going-concern gate"}


def rcf_t3_card(t: dict | None, overrides: list | None = None) -> dict | None:
    """The RCF/Debt T3 trigger as the page shows it."""
    if not t:
        return None
    agent = t.get("agent")
    tag = trigger_status(t, overrides)
    decided = tag in DECISION_TAG.values()
    fx = t.get("inputs") or {}
    return {
        "status": t["status"], "fired": t.get("fired"),
        "label": tag.upper() if decided else RCF_T3_LABELS[t["status"]],
        "css": TAG_CSS[tag] if decided else RCF_T3_CSS[t["status"]], "share_threshold": pct(RCF_T3_SHARE),
        "score": score_cell(t.get("score")), "column": t.get("column") or DASH,
        "decided_by": RCF_T3_DECIDED_BY.get(t["decided_by"], t["decided_by"]), "mode": t["mode"],
        "reason": t["reason"], "effect": t["effect"], "notes": t.get("notes") or [], "fired_by": t.get("fired_by"),
        "inputs": None if not fx else {
            "tagged": "yes" if fx.get("tagged") else "no", "nci_income": money(fx.get("nci_income")),
            "net_income": money(fx.get("net_income")), "nci_equity": money(fx.get("nci_equity")),
            "total_equity": money(fx.get("total_equity")), "share": pct(fx.get("share")),
            "test": {"income": "share of consolidated net income", "equity": "share of total equity"}.get(
                fx.get("test"), "no NCI tagged"),
            "counted": money(fx.get("nci_distributions"))},
        "amount": money(t.get("amount")) if t.get("amount") else None,
        "resolution": None if not t.get("fired") else (
            f"Unresolved: {t.get('unresolved_why')}, so RCF / debt is escalated to an analyst"
            if t.get("unresolved", True) else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)"),
        "unresolved": bool(t.get("fired") and t.get("unresolved", True)),
        "evidence": [{"fact": f"Distribution to {'redeemable NCI' if e['holder'] == 'redeemable_nci' else 'NCI'} ("
                              + ("cash paid" if e["basis"] == "cash_paid" else "declared") + f"): {money(e['amount'])} "
                              "million", **e["evidence"]} for e in t.get("distributions") or []]
        + [{"fact": "Purchase of NCI ownership (excluded)", **q} for q in t.get("purchases") or []]
        + [{"fact": "No distribution in the year", **q} for q in t.get("none_evidence") or []]
        + [{"fact": "Lumped with other financing or buyouts", **q} for q in t.get("lumped") or []],
        "rules": [{"when": w, "effect": e} for w, e in RCF_T3_RULES],
        "sections": t.get("sections") or [], "passages": t.get("passages") or [],
        "agent": _trigger_agent(agent, RCF_T3_LABELS, RCF_T3_CSS),
        "agent_not_used": None if agent else (
            "Not called: no cash flow statement, equity statement or note passage mentions NCI." if t.get("fired")
            else "Not called: the trigger wasn't set off."),
    }


RCF_T4_LABELS = {"buyouts_excluded": "BUYOUTS EXCLUDED: RECOMPUTED", "distributions_only": "DISTRIBUTIONS ONLY",
                 "unresolved": "UNRESOLVED", "not_triggered": "NOT TRIGGERED", "skipped": "SKIPPED"}
RCF_T4_CSS = {"buyouts_excluded": "moderate", "distributions_only": "low", "unresolved": "very-high",
              "not_triggered": "low", "skipped": "none"}
RCF_T4_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                     "no_text": "Code: no cash flow statement, equity statement or note passage mentions NCI",
                     "score": "Code: the RCF / debt score", "tag": "Code: the NCI-distributions tag used",
                     "tags": "Code: the equity statement's distributions to NCI and the purchase-of-NCI tags",
                     "gate": "Code: the going-concern gate"}


def rcf_t4_card(t: dict | None, overrides: list | None = None) -> dict | None:
    """The RCF/Debt T4 trigger as the page shows it."""
    if not t:
        return None
    agent = t.get("agent")
    tag = trigger_status(t, overrides)
    decided = tag in DECISION_TAG.values()
    fx = t.get("inputs") or {}
    return {
        "status": t["status"], "fired": t.get("fired"),
        "label": tag.upper() if decided else RCF_T4_LABELS[t["status"]],
        "css": TAG_CSS[tag] if decided else RCF_T4_CSS[t["status"]], "concept": t.get("concept"),
        "score": score_cell(t.get("score")), "column": t.get("column") or DASH,
        "decided_by": RCF_T4_DECIDED_BY.get(t["decided_by"], t["decided_by"]), "mode": t["mode"],
        "reason": t["reason"], "effect": t["effect"], "notes": t.get("notes") or [], "fired_by": t.get("fired_by"),
        "inputs": None if not fx else {"concept": fx.get("concept") or "none tagged",
                                       "tagged": money(fx.get("nci_distributions"))},
        "excluded": money(t.get("excluded")) if t.get("excluded") else None,
        "resolution": None if not t.get("fired") else (
            f"Unresolved: {t.get('unresolved_why')}, so RCF / debt is escalated to an analyst"
            if t.get("unresolved", True) else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)"),
        "unresolved": bool(t.get("fired") and t.get("unresolved", True)),
        "evidence": [{"fact": "Buyout of NCI in the tagged amount" + (f": {money(e['amount'])} million, excluded"
                                                                     if e.get("amount") is not None
                                                                     else ": not quantified"), **e["evidence"]}
                     for e in t.get("buyouts") or []]
        + [{"fact": "No purchase of NCI in the year", **q} for q in t.get("no_purchase_evidence") or []],
        "rules": [{"when": w, "effect": e} for w, e in RCF_T4_RULES],
        "sections": t.get("sections") or [], "passages": t.get("passages") or [],
        "agent": _trigger_agent(agent, RCF_T4_LABELS, RCF_T4_CSS),
        "agent_not_used": None if agent else (
            "Not called: no cash flow statement, equity statement or note passage mentions NCI." if t.get("fired")
            else "Not called: the trigger wasn't set off."),
    }


MARGIN_T1_LABELS = {"revenue_adjusted": "REVENUE ADJUSTED: RECOMPUTED", "outside_revenue": "OUTSIDE REVENUE: EBITDAR ONLY",
                    "unresolved": "UNRESOLVED", "not_triggered": "NOT TRIGGERED", "skipped": "SKIPPED"}
MARGIN_T1_CSS = {"revenue_adjusted": "moderate", "outside_revenue": "low", "unresolved": "very-high",
                 "not_triggered": "low", "skipped": "none"}
MARGIN_T1_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                        "no_text": "Code: no income statement, MD&A or note passage to read",
                        "score": "Code: the EBITDAR margin score",
                        "leverage_t1": "Code: whether Debt/EBITDAR T1 removed one-off items",
                        "gate": "Code: the going-concern gate"}


def margin_t1_card(t: dict | None, overrides: list | None = None) -> dict | None:
    """The EBITDAR Margin T1 trigger as the page shows it."""
    if not t:
        return None
    agent = t.get("agent")
    tag = trigger_status(t, overrides)
    decided = tag in DECISION_TAG.values()
    fx = t.get("inputs") or {}
    return {
        "status": t["status"], "fired": t.get("fired"),
        "label": tag.upper() if decided else MARGIN_T1_LABELS[t["status"]],
        "css": TAG_CSS[tag] if decided else MARGIN_T1_CSS[t["status"]],
        "score": score_cell(t.get("score")), "column": t.get("column") or DASH,
        "decided_by": MARGIN_T1_DECIDED_BY.get(t["decided_by"], t["decided_by"]), "mode": t["mode"],
        "reason": t["reason"], "effect": t["effect"], "notes": t.get("notes") or [], "fired_by": t.get("fired_by"),
        "inputs": None if not fx else {
            "ebitdar": money(fx.get("ebitdar")), "revenue": money(fx.get("revenue")), "ratio": pct(fx.get("ratio")),
            "normalisation": money(fx.get("normalisation")), "rent_basis": fx.get("rent_basis")},
        "revenue_adjustment": money(t.get("revenue_adjustment")) if t.get("revenue_adjustment") else None,
        "placed": [{"item_id": e["item_id"], "kind": e["kind"], "category": e["category"].replace("_", " "),
                   "amount": money(e["amount"]), "location": MARGIN_LOCATION_TEXT[e["location"]]}
                  for e in t.get("items") or []],
        "resolution": None if not t.get("fired") else (
            f"Unresolved: {t.get('unresolved_why')}, so the margin is escalated to an analyst"
            if t.get("unresolved", True) else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)"),
        "unresolved": bool(t.get("fired") and t.get("unresolved", True)),
        "evidence": [{"fact": f"{e['item_id']} ({e['kind']}, {money(e['amount'])} million): booked "
                              f"{MARGIN_LOCATION_TEXT[e['location']]}", **e["location_evidence"]}
                     for e in t.get("items") or [] if e.get("location_evidence")],
        "rules": [{"when": w, "effect": e} for w, e in MARGIN_T1_RULES],
        "sections": t.get("sections") or [], "passages": t.get("passages") or [],
        "agent": _trigger_agent(agent, MARGIN_T1_LABELS, MARGIN_T1_CSS),
        "agent_not_used": None if agent else (
            "Not called: no income statement, MD&A or note passage to read." if t.get("fired")
            else "Not called: the trigger wasn't set off."),
    }


MARGIN_T2_LABELS = {"moved_toward_p": "MOVED 1 POINT TOWARD P", "no_adjustment": "NO ADJUSTMENT",
                    "unresolved": "UNRESOLVED", "not_triggered": "NOT TRIGGERED", "skipped": "SKIPPED"}
MARGIN_T2_CSS = {"moved_toward_p": "moderate", "no_adjustment": "low", "unresolved": "very-high",
                 "not_triggered": "low", "skipped": "none"}
MARGIN_T2_DECIDED_BY = {"agent": "Trigger Agent (evidence verified against the filing)",
                        "no_text": "Code: no segment note or MD&A passage to read",
                        "score": "Code: the EBITDAR margin score",
                        "segments": "Code: the segment note's tags (segment revenue and profit)",
                        "gate": "Code: the going-concern gate"}
MARGIN_T2_FINDINGS = {"reportable_segments": ("Reportable segments", {"multiple": "more than one", "single": "a single "
                                                                       "reportable segment"}),
                      "profit_measure": ("Segment profit", {"same_measure": "measured the same way across segments",
                                                            "not_comparable": "not comparable across segments",
                                                            "unclear": "not shown to be measured the same way"}),
                      "outlier_explanation": ("The outlier's margin", {"business_model": "explained by how that "
                                                                         "business works",
                                                                         "year_performance": "explained by the year's "
                                                                         "performance",
                                                                         "none": "not explained"})}


def margin_t2_card(t: dict | None, overrides: list | None = None) -> dict | None:
    """The EBITDAR Margin T2 trigger as the page shows it."""
    if not t:
        return None
    agent = t.get("agent")
    tag = trigger_status(t, overrides)
    decided = tag in DECISION_TAG.values()
    fx = t.get("inputs") or {}
    o = fx.get("outlier") or {}
    th = t.get("thresholds") or {}
    findings = t.get("findings") or {}
    return {
        "status": t["status"], "fired": t.get("fired"),
        "label": tag.upper() if decided else MARGIN_T2_LABELS[t["status"]],
        "css": TAG_CSS[tag] if decided else MARGIN_T2_CSS[t["status"]],
        "score": score_cell(t.get("score")), "column": t.get("column") or DASH,
        "high": f"{th.get('high', 9):g}", "low": f"{th.get('low', 2):g}", "share": pct(th.get("share", 0.3)),
        "gap": f"{th.get('gap', 0.05) * 100:g} points",
        "decided_by": MARGIN_T2_DECIDED_BY.get(t["decided_by"], t["decided_by"]), "mode": t["mode"],
        "reason": t["reason"], "effect": t["effect"], "notes": t.get("notes") or [], "fired_by": t.get("fired_by"),
        "segments": [{"name": s["name"], "revenue": money(s["revenue"]), "profit": money(s.get("profit")),
                      "margin": pct(s.get("margin")), "share": pct(s.get("share")),
                      "outlier": s["member"] == o.get("member")} for s in fx.get("segments") or []],
        "profit_measure": fx.get("profit_label"), "profit_concept": fx.get("profit_concept"),
        "segment_margin": pct(fx.get("segment_margin")), "ratio": pct(fx.get("ratio")),
        "outlier": None if not o else {
            "name": o["name"], "margin": pct(o["margin"]), "share": pct(o["share"]),
            "rest_margin": pct(o.get("rest_margin")), "p": f"{fx['p']:g}",
            "implied_ratio": pct(fx.get("implied_ratio")), "implied_score": score_cell(fx.get("implied_score")),
            "direction": MARGIN_T2_DIRECTION_TEXT.get(fx.get("direction"), fx.get("direction"))},
        "recomputed": score_cell(t.get("recomputed")) if t.get("recomputed") is not None else None,
        "findings": [{"label": MARGIN_T2_FINDINGS[k][0], "text": MARGIN_T2_FINDINGS[k][1].get(v, v)}
                     for k, v in findings.items()],
        "resolution": None if not t.get("fired") else (
            f"Unresolved: {t.get('unresolved_why')}, so the margin is escalated to an analyst"
            if t.get("unresolved", True) else
            "Resolved: the agent and the verified evidence agree, so the verified outcome stands (no analyst "
            "decision)"),
        "unresolved": bool(t.get("fired") and t.get("unresolved", True)),
        "evidence": [{"fact": f"{MARGIN_T2_FINDINGS[k][0]}: {MARGIN_T2_FINDINGS[k][1].get(findings.get(k), '')}",
                      **e} for k, e in (t.get("evidence") or {}).items()],
        "rules": [{"when": w, "effect": e} for w, e in MARGIN_T2_RULES],
        "sections": t.get("sections") or [], "passages": t.get("passages") or [],
        "agent": _trigger_agent(agent, MARGIN_T2_LABELS, MARGIN_T2_CSS),
        "agent_not_used": None if agent else (
            "Not called: no segment profit tagged for each segment." if t.get("decided_by") == "segments" and
            t.get("fired") else "Not called: no segment note or MD&A passage to read." if t.get("fired")
            else "Not called: the trigger wasn't set off."),
    }


def agent_result(agent: dict, labels: dict, css: dict, names: dict | None = None) -> dict:
    """The Pre-check Agent's answer as recorded: its stated status, the verified status, and every finding it
    gave with the validator's verdict on it. ``labels`` maps passage ids (in document order) to their section."""
    names = names or {}
    findings = [{**f, "section": f.get("section") or labels.get(f["passage_id"], "—"), "verified": True,
                 "verdict": "Verified" if not f.get("stated_classification") else
                 f"Verified, read as {f['classification'].replace('_', ' ')} (agent said "
                 f"{f['stated_classification'].replace('_', ' ')})"} for f in agent.get("accepted") or []]
    findings += [{**f, "section": labels.get(f["passage_id"], "—"), "verified": False,
                  "verdict": f"Held back: {f['reason']}"} for f in agent.get("held_back") or []]
    order = {p: i for i, p in enumerate(labels)}
    findings.sort(key=lambda f: order.get(f["passage_id"], len(order)))
    valid = agent.get("validation_outcome") == "valid"
    stated, derived = agent.get("stated_status"), agent.get("derived_status")
    return {
        "model_id": agent.get("model_id"), "prompt_id": agent.get("prompt_id"),
        "prompt_hash": (agent.get("prompt_hash") or "")[:16], "model_run_id": agent.get("model_run_id"),
        "validation_outcome": agent.get("validation_outcome"), "validation_detail": agent.get("validation_detail"),
        "stated": {"label": names.get(stated, str(stated).upper()), "css": css.get(stated, "none")} if stated else None,
        "verified": ({"label": names.get(derived, derived.upper()), "css": css[derived]}
                     if valid and derived else None),
        "headroom": agent.get("headroom") or [], "headroom_held_back": agent.get("headroom_held_back") or [],
        "consistent": agent.get("status_consistent"),
        "rationale": agent.get("rationale"), "findings": findings,
        "held_back": agent.get("held_back") or [], "missing_information": agent.get("missing_information") or [],
        "passages_sent": len((agent.get("inputs") or {}).get("passages") or []),
    }


def gate_view(gc: dict | None, run_status: str | None, review: dict | None, escalations: list | None = None,
              overrides: list | None = None) -> dict:
    """Whether the composite and the measures are shown: a going-concern fail waits for the analyst; an escalated
    measure leaves the composite provisional until the analyst accepts or overrides its score. When the agent read
    going concern as FAIL against its verified evidence, the composite is provisional until the analyst decides; a
    continue / stop then reads as a going-concern fail, "use verified" as the verified result."""
    gc_decision = next((e for e in escalations or [] if e["type"] == "run_decision" and e.get("disagreement")), None)
    sided_with_agent = bool(gc_decision and review and review["decision"] in ("continue", "stop"))
    if not (gc and gc["status"] == "fail") and not sided_with_agent:
        decided = {o["measure"]: o for o in overrides or []}
        pending = [e for e in escalations or [] if e["type"] == "measure_override" and e["measure"] not in decided]
        if gc_decision and not review and run_status in (None, "awaiting_review"):
            return {"state": "awaiting_gc_decision", "show_composite": True, "show_measures": True,
                    "provisional": True, "pending": pending, "gc_decision": gc_decision,
                    "checks": sorted({"going_concern", *(c for e in pending for c in e.get("checks") or [e["check"]])}),
                    "message": f"{gc_decision['reason']}: escalated to an analyst, who keeps the verified result or "
                               "sides with the agent's FAIL (continue at an overall score of 10, or stop). The composite "
                               "is provisional until then."}
        if pending and run_status in (None, "awaiting_review"):
            names = "; ".join(f"{e['measure_name']} (engine score {_g(e['engine_score'])}, "
                              f"{e.get('title') or 'covenant breach'})" for e in pending)
            return {"state": "awaiting_override", "show_composite": True, "show_measures": True, "provisional": True,
                    "pending": pending, "checks": sorted({c for e in pending for c in e.get("checks") or [e["check"]]}),
                    "message": f"Escalated to an analyst, who accepts the engine's score or overrides it for {names}. "
                               "The composite is provisional until then."}
        if decided:
            lines = [f"{e['measure_name']}: " + (f"overridden by analyst to {_g(o['analyst_score'])} (engine "
                                                  f"{_g(o['engine_score'])})" if o["decision"] == "override"
                                                  else f"engine score {_g(o['engine_score'])}, accepted")
                     + f" by {o['reviewer']} on {o['decided_at'][:10]}: {o['reason']}"
                     for e in escalations or [] if e.get("measure") in decided for o in [decided[e["measure"]]]]
            return {"state": "overridden", "show_composite": True, "show_measures": True, "message": " · ".join(lines),
                    "checks": sorted({c for e in escalations or [] if e.get("measure") in decided
                                      for c in e.get("checks") or [e["check"]]})}
        # A completed, scored run with no escalation is still awaiting the
        # standard human approval: its composite/rating is PROVISIONAL until an
        # analyst accepts it. The score is retained and shown; it is simply
        # labelled provisional (no scoring change).
        if run_status in (None, "completed", "awaiting_review") and not review:
            return {"state": "clear", "show_composite": True, "show_measures": True,
                    "provisional": True, "awaiting_approval": True,
                    "message": "Composite and rating are PROVISIONAL pending human "
                               "approval. The calculated score is shown but is not "
                               "final until an analyst accepts it."}
        return {"state": "clear", "show_composite": True, "show_measures": True,
                "provisional": False, "message": None}
    if review and review["decision"] == "stop":
        return {"state": "stopped", "show_composite": False, "show_measures": False,
                "message": f"Not scored: going-concern fail. Analysis stopped by {review['reviewer']} on "
                           f"{review['decided_at'][:10]}: {review['reason']}"}
    if review and review["decision"] == "continue":
        return {"state": "continued", "show_composite": True, "show_measures": True,
                "message": f"Analysis continued by {review['reviewer']} on {review['decided_at'][:10]}: "
                           f"{review['reason']}. Overall score 10; the measures are superseded by the going-concern "
                           "fail and not counted; agent triggers are skipped."}
    if run_status in (None, "awaiting_review"):
        return {"state": "awaiting_review", "show_composite": False, "show_measures": True,
                "message": "Going-concern FAIL: escalated to an analyst, who decides whether the analysis continues. "
                           "The composite is withheld until then; the measures are shown for information only."}
    return {"state": run_status, "show_composite": False, "show_measures": True, "message": None}


def agent_reading_view(t: dict, metrics: list[dict]) -> dict:
    """One trigger's agent answer beside its verified result, as the page shows it: the measure's score as calculated
    before the check, as verified (applied) and as the agent's answer would make it (not applied), and its notes."""
    reading = t["agent_reading"]
    sc = reading.get("scores")
    title = next((m["title"] for m in metrics if m["key"] == t.get("measure")), t.get("measure"))
    agent = t.get("agent") or {}
    return {
        "stated": str(reading.get("status") or agent.get("stated_status") or "none").replace("_", " ").upper(),
        "verified": str(t.get("status") or "").replace("_", " ").upper(),
        "agrees": agent.get("status_consistent") is not False,
        "measure_name": title, "column": (sc or {}).get("column") or t.get("column") or DASH,
        "scores": None if not sc else {k: score_cell(sc[k]) for k in ("calculated", "verified", "agent")},
        "same_score": bool(sc) and sc["verified"] == sc["agent"],
        "notes": [f"The agent's answer, as stated: {n}" for n in reading.get("notes") or []],
    }


def overview_view(payload: dict[str, Any], *, run_status: str | None = None, review: dict | None = None,
                  overrides: list | None = None) -> dict[str, Any]:
    from .review import override_decisions, override_scores
    from .runner import composite_for

    metrics = payload["metrics"]
    meta, industry = payload["meta"], payload["industry"]
    gc = (payload.get("precheck") or {}).get("going_concern")
    cov = (payload.get("precheck") or {}).get("covenants")
    escalations = payload.get("escalations") or []
    overrides = overrides or []
    # The analyst sided with the agent's FAIL against the verified evidence: the run reads as a going-concern fail.
    sided_with_agent = bool(review and review["decision"] in ("continue", "stop") and gc and gc["status"] != "fail"
                            and any(e.get("disagreement") and e["type"] == "run_decision" for e in escalations))
    if sided_with_agent:
        composite = composite_for(payload, gc_status="fail")
        metrics = [metrics[0], *({**m, "superseded": GC_SUPERSEDED} for m in metrics[1:])]
    elif overrides:
        composite = composite_for(payload, override_scores(overrides), override_decisions(overrides))
    else:
        composite = metrics[0]
    by_measure = {o["measure"]: o for o in overrides}
    return {
        "meta": meta,
        "industry": industry,
        "filing": payload["filing"],
        "engine_version": payload["engine_version"],
        "reference_hash": payload["reference_hash"],
        "settings": payload["scoring_settings"],
        "precheck": precheck_card(gc),
        "covenants": covenant_card(cov),
        "acquisitions": acquisitions_card((payload.get("precheck") or {}).get("acquisitions"), overrides),
        "prepaid": prepaid_card((payload.get("precheck") or {}).get("prepaid"), overrides),
        "dscr_t1": trigger_card((payload.get("triggers") or {}).get("dscr_t1"), overrides),
        "dscr_t2": t2_card((payload.get("triggers") or {}).get("dscr_t2"), overrides),
        "dscr_t3": t3_card((payload.get("triggers") or {}).get("dscr_t3"), overrides),
        "icr_t1": icr_t1_card((payload.get("triggers") or {}).get("icr_t1"), overrides),
        "icr_t2": icr_t2_card((payload.get("triggers") or {}).get("icr_t2"), overrides),
        "icr_t3": icr_t3_card((payload.get("triggers") or {}).get("icr_t3"), overrides),
        "leverage_t1": leverage_t1_card((payload.get("triggers") or {}).get("leverage_t1"), overrides),
        "leverage_t2": leverage_t2_card((payload.get("triggers") or {}).get("leverage_t2"), overrides),
        "leverage_t3": leverage_t3_card((payload.get("triggers") or {}).get("leverage_t3"), overrides),
        "leverage_t4": leverage_t4_card((payload.get("triggers") or {}).get("leverage_t4"), overrides),
        "rcf_t1": rcf_t1_card((payload.get("triggers") or {}).get("rcf_t1"), overrides),
        "rcf_t2": rcf_t2_card((payload.get("triggers") or {}).get("rcf_t2"), overrides),
        "rcf_t3": rcf_t3_card((payload.get("triggers") or {}).get("rcf_t3"), overrides),
        "rcf_t4": rcf_t4_card((payload.get("triggers") or {}).get("rcf_t4"), overrides),
        "margin_t1": margin_t1_card((payload.get("triggers") or {}).get("margin_t1"), overrides),
        "margin_t2": margin_t2_card((payload.get("triggers") or {}).get("margin_t2"), overrides),
        "dso_t1": dso_t1_card((payload.get("triggers") or {}).get("dso_t1"), overrides),
        "liquidity_t1": liquidity_t1_card((payload.get("triggers") or {}).get("liquidity_t1"), overrides),
        "liquidity_t2": liquidity_t2_card((payload.get("triggers") or {}).get("liquidity_t2"), overrides),
        "liquidity_t3": liquidity_t3_card((payload.get("triggers") or {}).get("liquidity_t3"), overrides),
        "escalations": escalations,
        "recomputations": payload.get("recomputations") or [],
        # What changed the scored measures: without the agent's answers, which are recomputed but never applied.
        "applied_recomputations": [r for r in payload.get("recomputations") or [] if not r["step"].endswith(":agent")],
        "agent_readings": {k: agent_reading_view(t, metrics) for k, t in (payload.get("triggers") or {}).items()
                           if t.get("fired") and t.get("agent_reading")},
        "gate": gate_view(gc, run_status, review, escalations, overrides),
        "composite": {**composite_card(composite), "after_override": bool(overrides) or sided_with_agent},
        "measures": [measure_card(m, by_measure.get(m["key"])) for m in metrics[1:]],
    }


def render_overview_html(payload: dict[str, Any], *, run: dict | None = None, review: dict | None = None,
                         overrides: list | None = None) -> str:
    """A standalone scorecard page for one recorded run."""
    view = overview_view(payload, run_status=(run or {}).get("run_status"), review=review, overrides=overrides)
    return _env.get_template("fo_overview.html.j2").render(view=view, run=run)
