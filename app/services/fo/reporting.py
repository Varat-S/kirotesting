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
from .dscr_triggers import DECIDED_NOTE as T1_DECIDED_NOTE
from .dscr_triggers import ESCALATED_NOTE as T1_ESCALATED_NOTE
from .dscr_triggers import REFINANCING_OUTCOMES, ROLLOVER_OUTCOMES, T1_THRESHOLD
from .dscr_triggers import RULES as T1_RULES
from .prepaid import DECIDED_NOTE as PP_DECIDED_NOTE
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
        ("Debt / EBITDAR", "x", lambda c: c["debt_to_ebitdar"]["ratio"]),
        ("Debt / EBITDAR score", "score", lambda c: c["debt_to_ebitdar"].get("score")),
        ("Net debt / EBITDAR", "x", lambda c: c["net_debt_to_ebitdar"]["ratio"]),
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
FORMATS = {"x": times, "pct": pct, "days": days, "days+": lambda v: days(v, signed=True)}

# Why a column has no result, and any scoring note.
REASONS = {
    "leverage": lambda c: [r for r in (c["debt_to_ebitdar"].get("reason"), c["net_debt_to_ebitdar"].get("reason"),
                                       (c.get("composite") or {}).get("reason")) if r],
    "rcf": lambda c: [r for r in (c["rcf_to_debt"].get("reason"),) if r],
    "liquidity": lambda c: [r for r in (c.get("coverage_reason"), c.get("refinancing_reason"), c.get("reason")) if r],
}
NOTES = {
    "leverage": lambda c: [n for n in (c["debt_to_ebitdar"].get("note"), c["net_debt_to_ebitdar"].get("note")) if n]
    + (["Composite: net score + 1 (gross score is 2 or more worse)"] if (c.get("composite") or {}).get("uplift") else []),
    "rcf": lambda c: [n for n in (c["rcf_to_debt"].get("note"),
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
                              ("revenue_total", "Total revenue (DSO uses billed revenue: prepaid-model pre-check)")],
                      "leverage": [("ebitdar_reported", "EBITDAR as reported (before the deal adjustment)"),
                                   ("adj_debt_reported", "Debt + op. leases as reported (before the deal adjustment)")],
                      "dscr": [("principal_reported", "Principal due as tagged (before trigger T1)"),
                               ("debt_service_reported", "Debt service + rent as tagged (before trigger T1)")]}
LADDER = [("y1", "Year 1"), ("y2", "Year 2"), ("y3", "Year 3"), ("y4", "Year 4"), ("y5", "Year 5"),
          ("after", "After year 5")]


def _unit(m):
    return next((c.get("unit") for c in m["columns"] if c.get("unit")), None) or "USD"


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
        "headline": {**score_cell(score), "heading": heading},
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
    if m["key"] == "dscr" and any(c.get("trigger_adjustment") for c in cols):
        card["result_rows"][1:1] = [
            {"label": "DSCR as calculated (before trigger T1)",
             "cells": [_text((c.get("trigger_adjustment") or {}).get("ratio_calculated"), times) for c in cols]}]
        card["result_rows"].insert(len(card["result_rows"]) - 1, {
            "label": "Risk score as calculated (before trigger T1)",
            "cells": [score_cell((c.get("trigger_adjustment") or {}).get("calculated")) for c in cols]})
        card["result_rows"][-1]["label"] = "Risk score (after trigger T1)"
    if m["key"] == "liquidity":
        if any(c.get("covenant_adjustment") for c in cols):
            card["result_rows"].insert(len(card["result_rows"]) - 1, {
                "label": "Section score as calculated (before the covenant adjustment)",
                "cells": [score_cell((c.get("covenant_adjustment") or {}).get("calculated")) for c in cols]})
            card["result_rows"][-1]["label"] = "Section score (after the covenant adjustment)"
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
        return {"state": "clear", "show_composite": True, "show_measures": True, "message": None}
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
        "escalations": escalations,
        "gate": gate_view(gc, run_status, review, escalations, overrides),
        "composite": {**composite_card(composite), "after_override": bool(overrides) or sided_with_agent},
        "measures": [measure_card(m, by_measure.get(m["key"])) for m in metrics[1:]],
    }


def render_overview_html(payload: dict[str, Any], *, run: dict | None = None, review: dict | None = None,
                         overrides: list | None = None) -> str:
    """A standalone scorecard page for one recorded run."""
    view = overview_view(payload, run_status=(run or {}).get("run_status"), review=review, overrides=overrides)
    return _env.get_template("fo_overview.html.j2").render(view=view, run=run)
