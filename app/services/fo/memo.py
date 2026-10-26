"""Financial Overview memo: the analyst-facing write-up of one recorded FO run.

The FO counterpart of the Business Overview memo. Same rule: the JSON is the source of truth and the
HTML is a pure rendering of it. Unlike the BO memo, nothing here comes from a model: every sentence is
assembled from the recorded run (the scored measures, the pre-checks, the triggers, the analyst's
review and overrides), through the same view the scorecard uses, so the memo and the scorecard always
agree. Nothing is recomputed.

* The EXECUTIVE part states the composite and rating (or why they are withheld), the measures that
  score low and high risk, and what still needs an analyst.
* Each MEASURE section gives the ratio by period, its score and risk level, the triggers that fired on
  it, any analyst override and the notes that qualify it.
* OUTSTANDING lists escalations, unscored measures and data limitations, so a gap is never silent.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .measures import credit_rating, risk_level
from .reporting import overview_view

MEMO_VERSION = "fo-memo-1.0"
_TEMPLATES = Path(__file__).resolve().parents[1] / "reporting" / "templates"

MEASURE_COUNT = 7
STRENGTH_MAX = 3.5   # "Very low" and "Low" risk
WEAKNESS_MIN = 5     # above this: "Elevated", "High" and "Very high" risk

PRECHECKS = [("going_concern", "Going concern"), ("covenants", "Covenants"),
             ("acquisitions", "Acquisitions and divestitures"), ("prepaid", "Prepaid business model")]
TRIGGER_MEASURE = {"dscr": "DSCR", "icr": "ICR", "leverage": "Debt/EBITDAR", "rcf": "RCF/Debt",
                   "margin": "EBITDAR Margin", "dso": "DSO", "liquidity": "Liquidity"}
# The result row that is scored, when it is not the first (DSO is scored on its change).
PRIMARY_ROW = {"dso": "Change in DSO (scored)"}
# The score a column contributes, as the scorecard's headline uses it.
COLUMN_SCORE = {"leverage": lambda c: (c.get("composite") or {}).get("score"),
                "rcf": lambda c: (c.get("rcf_to_debt") or {}).get("score")}
STATUS_TEXT = {
    "completed": "Scored. The composite and rating are provisional until an analyst reviews the run.",
    "awaiting_review": "Escalated: an analyst must decide before the composite can be relied on.",
    "stopped": "Not scored: the analysis was stopped after a going-concern fail.",
}
MODE_TEXT = {
    "scripted": "OFFLINE RUN: the Pre-check and Trigger Agent answers were scripted, not produced by a live model. "
                "The figures are computed from the filing; the agent readings demonstrate the pipeline only.",
    "live": "The Pre-check and Trigger Agents ran on a live model; every figure is computed by code from the filing.",
}


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _score_text(score) -> dict:
    if _num(score) is None:
        return {"score": None, "text": "—", "label": "not scored", "css": "none"}
    label, css = risk_level(score)
    return {"score": score, "text": f"{score:g}", "label": label, "css": css}


def _trend(scores: list[tuple[str, Any]]) -> dict | None:
    """Direction of the risk score between the headline column and the next scored one (lower is better)."""
    scored = [(h, s) for h, s in scores if _num(s) is not None]
    if len(scored) < 2:
        return None
    (now_h, now), (then_h, then) = scored[0], scored[1]
    word = "improved" if now < then else "deteriorated" if now > then else "unchanged"
    return {"direction": word, "text": f"Risk score {word}: {then:g} ({then_h}) to {now:g} ({now_h})."}


def _triggers(payload: dict, measure: str) -> list[dict]:
    rows = []
    for key, t in (payload.get("triggers") or {}).items():
        if not key.startswith(f"{measure}_t") or not t.get("fired"):
            continue
        rows.append({"trigger": f"{TRIGGER_MEASURE[measure]} {key.rsplit('_', 1)[1].upper()}",
                     "status": t.get("status"), "reason": t.get("reason"),
                     "unresolved": bool(t.get("unresolved"))})
    return rows


def _measure(card: dict, metric: dict, payload: dict) -> dict:
    get = COLUMN_SCORE.get(card["key"], lambda c: c.get("score"))
    columns = metric.get("columns") or []
    headline = card["headline"]
    score = None if card.get("not_applicable") else _num(
        card["override"]["analyst_score"] if card.get("override") and card["override"]["decision"] == "override"
        else next((get(c) for c in columns if get(c) is not None), None))
    rows = card["result_rows"]
    primary = next((r for r in rows if r["label"] == PRIMARY_ROW.get(card["key"])), rows[0] if rows else None)
    # Fired triggers are listed under "triggers"; their longer note is on the scorecard.
    qualifications = [n for n in (card.get("covenant_note"), card.get("deal_note"), card.get("prepaid_note"),
                                  None if _triggers(payload, card["key"]) else card.get("trigger_note"),
                                  card.get("data_confidence_note"), card.get("gc_note"), card.get("superseded")) if n]
    qualifications += card.get("flag_notes") or []
    why_unscored = None
    if score is None:
        why_unscored = ("Not applicable: " + str(card.get("not_applicable"))) if card.get("not_applicable") else (
            next((r for c in columns for r in [c.get("reason") or (c.get("composite") or {}).get("reason")
                                                 or (c.get("rcf_to_debt") or {}).get("reason")] if r), None)
            or card.get("empty") or "No score could be computed from the tagged facts.")
    return {
        "key": card["key"], "title": card["title"], "short": card["short"], "formula": card.get("formula"),
        "score": _score_text(score), "heading": headline.get("heading"),
        "primary": {"label": primary["label"], "periods": [
            {"heading": h, "value": cell} for h, cell in zip(card["headings"], primary["cells"])]} if primary else None,
        "results": [{"label": r["label"], "cells": [c["text"] if isinstance(c, dict) else c for c in r["cells"]]}
                    for r in card["result_rows"]],
        "headings": card["headings"],
        "trend": _trend([(c["heading"], get(c)) for c in columns]),
        "benchmarks": [f"{b['label']}: industry benchmark {b['text']}" for b in card.get("benchmarks") or []],
        "bands": [b["text"] for b in card.get("bands") or []],
        "triggers": _triggers(payload, card["key"]),
        "override": card["override"]["text"] if card.get("override") else None,
        "escalated": bool(card.get("deal_tag") or card.get("prepaid_tag") or card.get("trigger_tag"))
                     and not card.get("override"),
        "qualifications": qualifications,
        "why_unscored": why_unscored,
        "gaps": card.get("gaps"),
    }


def _precheck(name: str, label: str, check: dict | None) -> dict | None:
    if not check:
        return None
    return {"check": name, "title": label, "status": check.get("status"), "reason": check.get("reason"),
            "effect": check.get("effect"), "decided_by": check.get("decided_by"), "mode": check.get("mode")}


def build_memo(payload: dict[str, Any], *, run: dict, review: dict | None = None, overrides: list | None = None,
               source: dict | None = None) -> dict[str, Any]:
    """The memo for one recorded run. ``run`` is the run header (case id, run id, status, hash); ``source`` the
    filing's SEC identity (accession, filing date, URL) when its folder has a ``bundle.json``."""
    view = overview_view(payload, run_status=run.get("run_status"), review=review, overrides=overrides)
    meta, gate, composite = view["meta"], view["gate"], view["composite"]
    metrics = {m["key"]: m for m in payload["metrics"]}
    measures = [_measure(card, metrics[card["key"]], payload) for card in view["measures"]]
    scored = [m for m in measures if m["score"]["score"] is not None]
    borrower = meta.get("entity_name") or meta.get("ticker") or run.get("case_id")

    comp_score = _composite_score(composite)  # the scorecard's composite: overrides already applied
    if not gate.get("show_composite"):
        headline = f"No composite is shown for {borrower}: {gate.get('message') or 'the run is withheld.'}"
        comp_score = None
    elif comp_score is None:
        headline = (f"No composite score for {borrower}: {composite.get('reason') or 'too few measures scored.'} "
                    f"{len(scored)} of {MEASURE_COUNT} measures scored.")
    else:
        label, _ = risk_level(comp_score)
        headline = (f"{borrower}'s financial risk reads {label.lower()}: composite risk score {comp_score:g} of 10, "
                    f"indicative rating {credit_rating(comp_score)}, from {len(scored)} of {MEASURE_COUNT} "
                    f"measures{' after analyst overrides' if composite.get('after_override') else ''}.")

    def point(m):
        value = m["primary"]["periods"][0]["value"] if m["primary"] and m["primary"]["periods"] else None
        value = value["text"] if isinstance(value, dict) else value
        return {"key": m["key"], "text": f"{m['short']}: {value or '—'} (score {m['score']['text']}, "
                                         f"{m['score']['label'].lower()} risk)"}

    escalations = payload.get("escalations") or []
    decided = {o["measure"] for o in overrides or []}
    pending = [e for e in escalations if e["type"] == "measure_override" and e.get("measure") not in decided]
    run_decision = [e for e in escalations if e["type"] == "run_decision"] if not review else []
    prechecks = [p for p in (_precheck(k, label, (payload.get("precheck") or {}).get(k)) for k, label in PRECHECKS)
                 if p]
    unresolved_triggers = [t for m in measures for t in m["triggers"] if t["unresolved"]]

    return {
        "document": "financial_overview_memo",
        "version": MEMO_VERSION,
        "case": {
            "case_id": run.get("case_id"), "borrower": borrower, "ticker": meta.get("ticker"), "cik": meta.get("cik"),
            "form": meta.get("document_type") or "10-K", "period_end": meta.get("period_end"),
            "fiscal_year": meta.get("fiscal_year"), "filing_folder": view["filing"]["folder"],
            "industry_group": view["industry"].get("industry_group"),
            "industry_note": None if view["industry"].get("industry_group") else view["industry"].get("note"),
            "source": source,
        },
        "run": {
            "run_id": run.get("run_id"), "started_at": run.get("started_at"), "content_hash": run.get("content_hash"),
            "engine_version": view["engine_version"], "reference_hash": view["reference_hash"],
            "agent_model": payload.get("agent_model"), "agent_mode": _agent_mode(payload),
            "agent_mode_text": MODE_TEXT.get(_agent_mode(payload)),
        },
        "status": {
            "run_status": run.get("run_status"),
            "text": gate.get("message") or STATUS_TEXT.get(run.get("run_status"), ""),
            "provisional": bool(gate.get("provisional", True)),
            "human_review_status": "decided" if review or overrides else "pending",
            "blocking": bool(run_decision or pending) or not gate.get("show_composite"),
        },
        "executive": {
            "headline": headline,
            "composite": {**_score_text(comp_score),
                          "rating": credit_rating(comp_score) if comp_score is not None else None,
                          "after_override": bool(composite.get("after_override"))},
            "measures_scored": len(scored),
            "strengths": [point(m) for m in scored if m["score"]["score"] <= STRENGTH_MAX],
            "weaknesses": [point(m) for m in scored if m["score"]["score"] > WEAKNESS_MIN],
            "watch": [point(m) for m in scored if STRENGTH_MAX < m["score"]["score"] <= WEAKNESS_MIN],
            "prechecks": [{"title": p["title"], "status": p["status"]} for p in prechecks],
        },
        "prechecks": prechecks,
        "measures": measures,
        "composite": {
            "formula": composite.get("formula"), "rows": composite.get("rows") or [],
            "adjustments": composite.get("adjustments") or [], "average": composite.get("average"),
            "notes": composite.get("notes"), "gaps": composite.get("gaps"),
        },
        "outstanding": {
            "run_decision": [{"reason": e.get("reason")} for e in run_decision],
            "pending_overrides": [{"measure": e.get("measure_name") or e.get("measure"), "title": e.get("title"),
                                   "engine_score": e.get("engine_score"), "reason": e.get("reason")} for e in pending],
            "unresolved_triggers": unresolved_triggers,
            "unscored": [{"measure": m["title"], "reason": m["why_unscored"]} for m in measures if m["why_unscored"]],
            "data_limitations": [x for x in [view["industry"].get("note") if not view["industry"].get("industry_group")
                                             else None, *(view["settings"].get("errors") or [])] if x],
        },
        "decisions": {
            "review": review,
            "overrides": [{"measure": o["measure"], "decision": o["decision"], "engine_score": o.get("engine_score"),
                           "analyst_score": o.get("analyst_score"), "reviewer": o.get("reviewer"),
                           "decided_at": o.get("decided_at"), "reason": o.get("reason")} for o in overrides or []],
        },
    }


def _composite_score(composite: dict):
    """The composite score the scorecard shows (its text is "—" when there is none)."""
    try:
        return float(composite.get("text"))
    except (TypeError, ValueError):
        return None


def _agent_mode(payload: dict) -> str | None:
    modes = {c.get("mode") for c in (payload.get("precheck") or {}).values() if isinstance(c, dict) and c.get("mode")}
    return "scripted" if "scripted" in modes else "live" if "live" in modes else None


def filing_source(folder: str | None) -> dict | None:
    """The filing's SEC identity from its folder's ``bundle.json`` (a shared filing folder), else None. Read as is:
    the memo only names the source; the BO is what checks the hashes."""
    import json

    from . import loader

    try:
        manifest = json.loads((loader.resolve_folder(folder) / "bundle.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    primary = next((f for f in manifest.get("files") or [] if f.get("role") == "primary_inline_xbrl"), {})
    return {"accession": manifest.get("accession"), "filing_date": manifest.get("filing_date"),
            "url": primary.get("source_url")}


def _env() -> Environment:
    return Environment(loader=FileSystemLoader(str(_TEMPLATES)), autoescape=select_autoescape(["html", "j2"]),
                       trim_blocks=True, lstrip_blocks=True)


def render_memo_html(memo: dict[str, Any]) -> str:
    return _env().get_template("fo_memo.html.j2").render(memo=memo)
