"""BO-only demonstration deliverables for the selected specialist roster.

From one specialists run this module produces, all from the SAME structured
payload (so the reports cannot drift from the data):

* ``specialists_run.json``      the complete structured output and run metadata
* ``executive_summary.html``    a business overview assembled from validated
                                findings (no model wrote it; nothing is reworded)
* ``specialists_comparison.html`` (+ ``.pdf`` when reportlab is installed)
                                prompt, evidence, numbers, output and verdict for
                                every selected specialist
* ``validation_audit.html``     every rejected, qualified, corrected,
                                reclassified or unresolved hypothesis
* ``m3_governed_assessment.*``  Validator 7's authoritative post-validation
                                M3 object and a deterministic audit view
* ``benchmark.html``            old-versus-new comparison, when a baseline is given

The executive summary quotes each validated hypothesis as the agent wrote it and
labels how it may be used. It contains no rating and no debt-capacity view.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.core.hashing import canonical_json
from app.models.orm import AgentRunRow, RetrievalAttemptRow
from app.prompts.bo_prompts import SHARED_RULES, specialist_block
from app.services.bo.m3_version import is_m3_v2_block

PAYLOAD_VERSION = "bo-specialists-run-1.0"
M3_EXPORT_VERSION = "bo-m3-governed-export-1.0"
DEBT_RULE = "operating_pressure_to_debt_capacity"


# --------------------------------------------------------------------------- #
# payload
# --------------------------------------------------------------------------- #


def build_payload(run, *, calls: list[dict[str, Any]] | None = None,
                  execution_mode: str, prices: dict[str, float] | None = None
                  ) -> dict[str, Any]:
    """Collect everything recorded for a specialists run into one structure."""
    session = run.session
    calls = calls or []
    calls_by_agent: dict[str, list[dict[str, Any]]] = {}
    for call in calls:
        calls_by_agent.setdefault(call["task_type"], []).append(call)
    run_rows = session.execute(
        select(AgentRunRow).where(AgentRunRow.analysis_run_id == run.analysis_run_id)
        .order_by(AgentRunRow.created_at)).scalars().all()
    attempts = [r.payload for r in session.execute(
        select(RetrievalAttemptRow)
        .where(RetrievalAttemptRow.analysis_run_id == run.analysis_run_id)
        .order_by(RetrievalAttemptRow.created_at)).scalars()]
    borrower = next((e for e in run.evidence.entities if e.get("borrower_flag")), {})
    documents = [
        {k: d.get(k) for k in ("filename", "role", "form", "accession", "cik",
                               "filing_date", "report_date", "sha256", "source_url")}
        for d in run.evidence.documents]

    agents = []
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    latency = 0
    specs = [run.spec_by_agent[agent_id] for agent_id in run.narrow_agent_ids]
    for spec in specs:
        outcome = run.agents[spec.agent_id]
        agent_calls = []
        for call in calls_by_agent.get(spec.agent_id, []):
            config = call.get("model_config") or {}
            for key in totals:
                totals[key] += int(config.get(key) or 0)
            latency += int(config.get("latency_ms") or 0)
            try:
                parsed = json.loads(call.get("raw_response") or "")
            except ValueError:
                parsed = None
            agent_calls.append({
                "prompt_id": call.get("prompt_id"),
                "prompt_hash": call.get("prompt_hash"),
                "effective_instructions": call.get("instructions"),
                "inputs": call.get("inputs"),
                "evidence_aliases": call.get("evidence_aliases"),
                "output": parsed,
                "raw_response": None if parsed is not None else call.get("raw_response"),
                "error": call.get("error"),
                "model_id": call.get("model_id"),
                "usage": {k: config.get(k) for k in totals if k in config},
                "latency_ms": config.get("latency_ms"),
            })
        records = [h.model_dump(mode="json") for h in run.hypotheses.get(spec.agent_id, [])]
        agents.append({
            "agent_id": spec.agent_id, "code": spec.code, "title": spec.title,
            "owned_parameters": list(spec.owned_parameters),
            "indicator_groups": list(spec.indicator_groups),
            "specialist_block": specialist_block(spec),
            "status": outcome.status, "detail": outcome.detail,
            "validation_issues": outcome.issues,
            "validator_adjustments": outcome.adjustments,
            "agent_run_ids": [r.run_id for r in run_rows if r.agent_id == spec.agent_id],
            "calls": agent_calls,
            "parameters": [
                {"parameter_id": p.parameter_id, "value": p.value,
                 "status": p.status.value, "evidence_ids": p.evidence_ids,
                 "notes": p.notes}
                for p in run.promoted.get(spec.agent_id, [])],
            "parameter_adjustments": run.parameter_adjustments.get(spec.agent_id, []),
            "hypotheses": records,
            "coverage": [c.as_payload() for c in run.coverage
                         if c.agent_id == spec.agent_id],
            "retrieval_attempts": [a for a in attempts if a["agent_id"] == spec.agent_id],
        })

    passages = {p["evidence_id"]: {
        "section": p.get("section"), "location": p.get("location"),
        "document_id": p.get("document_id")} for p in run.evidence.narrative_evidence}
    payload = {
        "document": "business_overview_specialists_run",
        "version": PAYLOAD_VERSION,
        "meta": {
            "case_id": run.case_id, "borrower": borrower.get("legal_name"),
            "as_of_date": str(run.evidence.as_of_date),
            "evidence_cutoff": str(run.evidence.evidence_cutoff_timestamp),
            "evidence_snapshot_version": run.snapshot_version,
            "analysis_run_id": run.analysis_run_id,
            "scope": run.scope,
            "execution_mode": execution_mode,
            "models": sorted({c.get("model_id") for c in calls if c.get("model_id")}),
            "sector_profile": run.sector.sector_id if run.sector else None,
            "agent_registry_version": run.registry.version,
            "agent_registry_hash": run.registry.registry_hash(),
            "prompts": {n: {"prompt_id": p.prompt_id, "content_hash": p.content_hash}
                        for n, p in sorted(run.prompts.items())
                        if n in set(run.narrow_agent_ids)},
            "retrieval_budget": vars(run.runner.retriever.budget),
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "runtime_seconds": round(
                (run.completed_at - run.started_at).total_seconds(), 2)
            if run.completed_at else None,
            "documents": documents,
            "narrative_passages": len(run.evidence.narrative_evidence),
            "facts": len(run.evidence.facts),
        },
        "shared_rules": SHARED_RULES,
        "stages": run.stage_log,
        "agents": agents,
        "indicators": [t.model_dump(mode="json") for t in run.indicators.traces],
        "fo_handoffs": run.fo_handoffs(),
        # This is the runner's authoritative state, not a report-side replay.
        "module_conclusions": run.module_conclusions,
        "passages": passages,
        "usage": {**totals, "model_latency_ms_total": latency,
                  "calls": len(calls)},
    }
    payload["usage"]["cost"] = _cost(totals, prices)
    payload["metrics"] = compute_metrics(payload)
    return payload


def _cost(totals, prices) -> dict[str, Any]:
    if not prices:
        return {"determinable": False,
                "reason": "No model price was supplied; token counts are reported instead."}
    amount = (totals["input_tokens"] * prices["input_per_million"]
              + totals["output_tokens"] * prices["output_per_million"]) / 1_000_000
    return {"determinable": True, "currency": prices.get("currency", "USD"),
            "amount": round(amount, 4), "prices_per_million_tokens": prices,
            "note": "Computed from the supplied prices; not read from a provider invoice."}


def _all(payload):
    return [h for a in payload["agents"] for h in a["hypotheses"]]


def compute_metrics(payload: dict[str, Any]) -> dict[str, Any]:
    """Counts used for the old-versus-new comparison. More is not better."""
    hypotheses = _all(payload)
    generated = sum(len((c.get("output") or {}).get("hypotheses") or [])
                    for a in payload["agents"] for c in a["calls"][-1:])
    flags = [f for h in hypotheses for f in h["review_flags"]]
    recoveries = [r for h in hypotheses for r in h["recovery"]]
    coverage = [c for a in payload["agents"] for c in a["coverage"]]
    by_admissibility: dict[str, int] = {}
    for h in hypotheses:
        by_admissibility[h["admissibility"]] = by_admissibility.get(h["admissibility"], 0) + 1
    params = [p for a in payload["agents"] for p in a["parameters"]]
    return {
        "agents_validated": sum(a["status"] == "validated" for a in payload["agents"]),
        "agents_rejected_or_failed": [a["code"] for a in payload["agents"]
                                      if a["status"] != "validated"],
        "hypotheses_generated": generated,
        "hypotheses_recorded": len(hypotheses),
        "hypotheses_usable": sum(h["admissibility"] != "audit_only" for h in hypotheses),
        "by_admissibility": by_admissibility,
        "by_disposition": {d: sum(h["disposition"] == d for h in hypotheses)
                           for d in ("supported", "qualified", "rejected", "unverified")},
        "unsupported_inference_flags": sum(f["layer"].startswith("C") for f in flags),
        "evidence_sufficiency_flags": sum(f["layer"].startswith("D") for f in flags),
        "flags_by_rule": _count(f["rule_id"] for f in flags),
        "citation_verification_failures": len(recoveries),
        "citations_recovered": sum(r["outcome"] != "unresolved" for r in recoveries),
        "citations_unresolved": sum(r["outcome"] == "unresolved" for r in recoveries),
        "bo_fo_boundary_violations": sum(f["rule_id"] == DEBT_RULE for f in flags),
        "fo_handoffs": len(payload["fo_handoffs"]),
        "hypotheses_using_indicators": sum(
            bool(h["parameter_result_ids"]) and h["admissibility"] != "audit_only"
            for h in hypotheses),
        "indicators_supplied_by_agent": {
            a["code"]: len((a["calls"][-1]["inputs"] or {}).get("indicators") or [])
            if a["calls"] else 0 for a in payload["agents"]},
        "coverage_checks": len(coverage),
        "coverage_addressed": sum(c["status"] == "addressed" for c in coverage),
        "coverage_by_status": _count(c["status"] for c in coverage),
        "parameters_accepted": sum(p["status"] in ("ok", "provisional") for p in params),
        # "not_established" is the agent's own answer, not a validator action.
        "parameters_held_back": sum(
            adj["reason"] != "not_established"
            for a in payload["agents"] for adj in a["parameter_adjustments"]),
        "parameters_reported_not_established": sum(
            adj["reason"] == "not_established"
            for a in payload["agents"] for adj in a["parameter_adjustments"]),
        "parameters_held_back_by_reason": _count(
            adj["reason"] for a in payload["agents"] for adj in a["parameter_adjustments"]
            if adj["reason"] != "not_established"),
        "tokens": {k: payload["usage"][k] for k in
                   ("input_tokens", "output_tokens", "total_tokens")},
        "runtime_seconds": payload["meta"]["runtime_seconds"],
        "model_calls": payload["usage"]["calls"],
    }


def _count(values) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items()))


# --------------------------------------------------------------------------- #
# rendering helpers
# --------------------------------------------------------------------------- #

_CSS = """
body{font-family:Arial,Helvetica,sans-serif;color:#1c2430;max-width:1120px;margin:1.6rem auto;padding:0 1.2rem;font-size:14.5px;line-height:1.55}
h1{font-size:1.55rem;margin-bottom:.2rem} h2{font-size:1.15rem;margin:2rem 0 .5rem;padding:.4rem .6rem;background:#1c2430;color:#fff}
h3{font-size:1rem;margin:1.2rem 0 .3rem;border-bottom:1px solid #c9d1da;padding-bottom:.15rem}
table{border-collapse:collapse;width:100%;margin:.4rem 0 1rem} th,td{text-align:left;vertical-align:top;padding:.35rem .55rem;border:1px solid #d5dbe2}
th{background:#eef1f5} pre{white-space:pre-wrap;background:#f6f7f9;border:1px solid #d5dbe2;padding:.6rem;font-size:12.5px}
.muted{color:#68727f;font-size:.88em} .banner{padding:.55rem .75rem;margin:.7rem 0;border-left:4px solid #8a6d1c;background:#fbf6e6}
.tag{display:inline-block;font-size:.72rem;text-transform:uppercase;letter-spacing:.03em;padding:.05rem .4rem;border-radius:3px;border:1px solid #9aa5b1;margin-right:.35rem;white-space:nowrap}
.t-established,.t-calculated{border-color:#2f6b45;color:#2f6b45} .t-management{border-color:#23527c;color:#23527c}
.t-qualified,.t-risk{border-color:#8a6d1c;color:#8a6d1c} .t-held{border-color:#9a3324;color:#9a3324}
.ok{color:#2f6b45;font-weight:bold} .bad{color:#9a3324;font-weight:bold} .warn{color:#8a6d1c;font-weight:bold}
li{margin:.35rem 0} details{margin:.3rem 0} summary{cursor:pointer;color:#23527c} .q{font-style:italic;color:#3d4753}
nav a{margin-right:.9rem}
"""


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _page(title: str, payload: dict[str, Any], body: list[str]) -> str:
    meta = payload["meta"]
    mode = ("LIVE MODEL run (" + ", ".join(meta["models"]) + ")"
            if meta["execution_mode"] == "live_model"
            else "OFFLINE SIMULATION: responses were replayed or scripted, not produced by a model in this run")
    return "\n".join([
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{_e(title)}</title>"
        f"<style>{_CSS}</style></head><body><h1>{_e(title)}</h1>",
        f"<div class='muted'>{_e(meta['borrower'])} &middot; case {_e(meta['case_id'])} &middot; "
        f"as of {_e(meta['as_of_date'])} &middot; run {_e(meta['analysis_run_id'])}</div>",
        f"<div class='banner'>{_e(mode)}. {len(payload['agents'])} Business Overview specialists and their "
        "deterministic validators only: no orchestrator, challenger, score, rating or "
        "debt-capacity view.</div>", *body, "</body></html>"])


def use_label(h: dict[str, Any]) -> tuple[str, str]:
    """(css class, label) describing how a hypothesis may be used."""
    if h["admissibility"] == "audit_only":
        return "held", "Not usable"
    if h["admissibility"] == "attributed":
        return "management", "Management states"
    if h["admissibility"] == "established":
        if h["evidence_basis"] == "deterministic_calculation":
            return "calculated", "Calculated"
        return "established", "Established"
    if any(f["rule_id"] == "generic_risk_disclosure_only" for f in h["review_flags"]):
        return "risk", "Disclosed risk"
    return "qualified", "Qualified"


def _number(value, unit) -> str:
    if value is None:
        return "not available"
    if unit == "ratio":
        return f"{value * 100:.2f}%"
    if unit and "USD_million" in unit:
        return f"USD {value:,.0f} million"
    return f"{value:,.4f}" if abs(value) < 100 else f"{value:,.0f}"


def _source(h, payload) -> str:
    parts = []
    for quote in h["evidence_quotes"][:2]:
        where = payload["passages"].get(quote["evidence_id"], {})
        parts.append(f"<span class='q'>&ldquo;{_e(quote['quote'])}&rdquo;</span> "
                     f"<span class='muted'>[{_e(where.get('section'))}"
                     + (", recovered" if quote.get("recovered") else "") + "]</span>")
    for q in h["quoted_values"]:
        trace = next((t for t in payload["indicators"]
                      if t["parameter_result_id"] == q.get("parameter_result_id")), None)
        if trace:
            parts.append(f"<span class='muted'>Calculation: {_e(trace['label'])} = "
                         f"{_e(_number(trace['value'], trace['units']))}, period "
                         f"{_e(trace['period'])}, denominator "
                         f"{_e((trace.get('denominator') or {}).get('name') or 'n/a')}</span>")
    return "<br>".join(parts)


def _finding(h, payload) -> str:
    css, label = use_label(h)
    notes = [n for n in h["validation_notes"]]
    extra = ""
    if h.get("mitigant"):
        m = h["mitigant"]
        extra += (f"<br><span class='muted'>Mitigant: {_e(m['capability'])} &mdash; "
                  f"existence {_e(m['existence'])}, effectiveness {_e(m['effectiveness'])}</span>")
    if h.get("transmission_mechanism"):
        extra += (f"<br><span class='muted'>Mechanism: {_e(h['transmission_mechanism'])} "
                  f"(built from {_e(', '.join(h['component_hypothesis_ids']))})</span>")
    if notes and css in ("qualified", "risk", "management"):
        extra += "<br><span class='muted'>Why qualified: " + _e(" ".join(notes)) + "</span>"
    source = _source(h, payload)
    return (f"<li><span class='tag t-{css}'>{label}</span>{_e(h['claim'])} "
            f"<span class='muted'>[{_e(h['hypothesis_id'])}]</span>"
            + (f"<br>{source}" if source else "") + extra + "</li>")


# --------------------------------------------------------------------------- #
# A. executive summary
# --------------------------------------------------------------------------- #

_ORDER = {"high": 0, "medium": 1, "low": 2}


def render_executive_summary(payload: dict[str, Any]) -> str:
    usable = [h for h in _all(payload) if h["admissibility"] != "audit_only"]
    usable.sort(key=lambda h: (_ORDER[h["materiality"]], h["hypothesis_id"]))
    taken: set[str] = set()

    def pick(test) -> list[dict[str, Any]]:
        chosen = [h for h in usable if h["hypothesis_id"] not in taken and test(h)]
        taken.update(h["hypothesis_id"] for h in chosen)
        return chosen

    def agent(*codes):
        return lambda h: h["section"] in codes

    codes = {a["code"] for a in payload["agents"]}
    if "M3" in codes:  # specialist-only v3: provisional findings, no synthesis claim
        sections = [
            ("Business model and revenue quality", "", pick(agent("M1S1"))),
            ("Customer, payer and pricing risks", "", pick(agent("M1S2", "M1S3"))),
            ("Operating model and competitive position", "",
             pick(agent("M1S4", "M1S5"))),
            ("Industry context and disclosed risks", "", pick(agent("M2"))),
            ("Corporate structure, management and governance", "", pick(agent("M3"))),
            ("Strategy and capital stewardship",
             "Intentions and ordinary investment activity are not presented as "
             "demonstrated credit resilience.", pick(agent("M4"))),
            ("Qualified cross-specialist interactions",
             "These are provisional specialist findings, not an orchestrator "
             "conclusion or credit recommendation.", pick(agent("M1A6"))),
        ]
    else:
        sections = [
            ("Cross-specialist risk interactions",
             "BO6 combines validated findings of BO1&ndash;BO5. An interaction is "
             "never more certain than the findings it is built from.", pick(agent("BO6"))),
            ("How the company generates revenue", "",
             pick(lambda h: h["section"] == "BO1"
                  and h["direction"] in ("neutral", "mixed"))),
            ("Sources of operating resilience",
             "Favourable findings from any specialist. A mitigant marked unproven "
             "exists but has no measured outcome in the evidence.",
             pick(lambda h: h["direction"] == "positive")),
            ("Material operating risks and vulnerabilities", "",
             pick(lambda h: h["section"] in ("BO1", "BO4")
                  and h["direction"] == "negative" and h["materiality"] == "high")),
            ("Competitive and payer dynamics", "", pick(agent("BO2", "BO3"))),
            ("Management and execution", "", pick(agent("BO4", "BO1"))),
            ("External and regulatory exposures", "", pick(agent("BO5"))),
        ]
        sections.append(sections.pop(0))

    body = ["<p>Every statement below is a hypothesis written by a specialist and "
            "passed by the deterministic validators, quoted as written. The tag "
            "says how it may be used: <span class='tag t-established'>Established</span>"
            "stated in the filing and quote-verified; <span class='tag t-calculated'>"
            "Calculated</span>a deterministic indicator; <span class='tag t-management'>"
            "Management states</span>attributed to management; <span class='tag t-risk'>"
            "Disclosed risk</span>risk-factor language, not an event that occurred; "
            "<span class='tag t-qualified'>Qualified</span>an inference or a finding "
            "with counter-evidence.</p>"]
    for title, note, items in sections:
        body.append(f"<h2>{title}</h2>")
        if note:
            body.append(f"<p class='muted'>{note}</p>")
        body.append("<ul>" + "".join(_finding(h, payload) for h in items) + "</ul>"
                    if items else "<p class='muted'>No validated finding.</p>")

    body.append("<h2>Unresolved evidence gaps</h2>")
    held = [h for h in _all(payload) if h["admissibility"] == "audit_only"
            and h["model_stated"].get("disposition") != "rejected"]
    body.append("<h3>Claims the specialists made that did not pass validation</h3>")
    body.append("<ul>" + "".join(
        f"<li><span class='tag t-held'>Not usable</span>{_e(h['claim'])} "
        f"<span class='muted'>[{_e(h['hypothesis_id'])}] {_e(' '.join(h['validation_notes']))}</span></li>"
        for h in held) + "</ul>" if held else "<p class='muted'>None.</p>")
    unresolved = [c for a in payload["agents"] for c in a["coverage"]
                  if c["status"] != "addressed"]
    body.append("<h3>Coverage questions without a validated answer</h3>")
    body.append("<ul>" + "".join(
        f"<li><strong>{_e(c['check_id'])}</strong> ({_e(c['status'].replace('_', ' '))}): "
        f"{_e(c['question'])}</li>" for c in unresolved) + "</ul>"
        if unresolved else "<p class='muted'>All coverage questions were addressed.</p>")
    held_params = [(a["code"], adj) for a in payload["agents"]
                   for adj in a["parameter_adjustments"]
                   if adj["reason"] != "not_established"]
    body.append("<h3>Classifications held back for lack of support</h3>")
    body.append("<ul>" + "".join(
        f"<li>{_e(code)} <code>{_e(adj['parameter_id'])}</code>: proposed "
        f"{_e(adj['original_value'])!s} &mdash; {_e(adj['detail'])}</li>"
        for code, adj in held_params) + "</ul>"
        if held_params else "<p class='muted'>None.</p>")
    missing = sorted({m for h in usable for m in h["missing_information"]})
    body.append("<h3>Information the specialists reported as missing</h3>")
    body.append("<ul>" + "".join(f"<li>{_e(m)}</li>" for m in missing) + "</ul>"
                if missing else "<p class='muted'>None reported.</p>")
    body.append("<h3>Questions handed to the Financial Overview</h3>")
    body.append("<ul>" + "".join(
        f"<li><strong>{_e(f['variable'])}</strong> ({_e(f['origin'])}, "
        f"{_e(f['hypothesis_id'])}): {_e(f['question'])}</li>"
        for f in payload["fo_handoffs"]) + "</ul>"
        if payload["fo_handoffs"] else "<p class='muted'>None.</p>")
    body.append("<p class='muted'>This summary contains no borrower rating and no "
                "debt-capacity conclusion; those are outside the Business Overview.</p>")
    return _page("Business Overview: executive summary", payload, body)


def build_m3_governed_export(payload: dict[str, Any]) -> dict[str, Any]:
    """Pure view of the runner-owned M3 assessment; never re-validates it."""
    module = (payload.get("module_conclusions") or {}).get("M3") or {}
    assessment = module.get("m3_assessment")
    m3_agent = next((a for a in payload.get("agents", []) if a.get("code") == "M3"), None)
    status = module.get("m3_assessment_status") or "unavailable"
    reason = None
    if assessment is None:
        reason = ("No authoritative post-validation M3 assessment exists. "
                  + " ".join(module.get("missing_information") or [])).strip()
    return {
        "document": "m3_governed_assessment",
        "version": M3_EXPORT_VERSION,
        "case_id": payload.get("meta", {}).get("case_id"),
        "analysis_run_id": payload.get("meta", {}).get("analysis_run_id"),
        "latest_accepted_specialist_attempt": (
            m3_agent["agent_run_ids"][-1]
            if m3_agent and m3_agent.get("status") == "validated"
            and m3_agent.get("agent_run_ids") else None),
        "assessment_status": status,
        "unavailable_reason": reason,
        "authoritative_source": "module_conclusions.M3.m3_assessment",
        "governed_assessment": assessment,
    }


def render_m3_governed_assessment(export: dict[str, Any],
                                  payload: dict[str, Any]) -> str:
    assessment = export.get("governed_assessment")
    if assessment is None:
        body = ["<h2>Governed assessment</h2><p><strong>unavailable</strong></p>",
                f"<p>{_e(export.get('unavailable_reason') or 'No validated M3 output exists.')}</p>"]
        return _page("M3 governed assessment", payload, body)

    excluded = {"model_proposal", "validation_issues", "rejected_items"}
    governed = {k: v for k, v in assessment.items() if k not in excluded}
    borrower = assessment.get("borrower_identification") or {}
    unresolved = {
        "borrower_limitation": borrower.get("limitation"),
        "information_gaps": assessment.get("information_gaps") or [],
        "downstream_dependencies": assessment.get("downstream_dependencies") or [],
    }

    def json_block(value: Any) -> str:
        if value in (None, [], {}):
            return "<p class='muted'>None / unavailable.</p>"
        return "<pre>" + _e(json.dumps(value, indent=2, ensure_ascii=False)) + "</pre>"

    body = [
        "<p>This is a deterministic view of the runner's authoritative "
        "<code>module_conclusions.M3.m3_assessment</code>. It is not reconstructed "
        "from the transcript.</p>",
        "<table><tr><th>Assessment schema</th><td>" + _e(assessment.get("schema_version"))
        + "</td></tr><tr><th>Validator</th><td>" + _e(assessment.get("validator_id"))
        + " / " + _e(assessment.get("validator_version"))
        + "</td></tr><tr><th>Latest accepted attempt</th><td>"
        + _e(export.get("latest_accepted_specialist_attempt") or "unavailable")
        + "</td></tr></table>",
        "<h2>1. Model proposal</h2>", json_block(assessment.get("model_proposal")),
        "<h2>2. Validator findings</h2>", json_block(assessment.get("validation_issues")),
        "<h2>3. Final governed content</h2>", json_block(governed),
        "<h2>4. Excluded material</h2>", json_block(assessment.get("rejected_items")),
        "<h2>5. Unresolved information</h2>", json_block(unresolved),
    ]
    return _page("M3 governed assessment", payload, body)


# --------------------------------------------------------------------------- #
# B. six-specialist comparison
# --------------------------------------------------------------------------- #


def render_comparison(payload: dict[str, Any]) -> str:
    body = ["<nav>" + "".join(f"<a href='#{a['code']}'>{a['code']} {_e(a['title'])}</a>"
                              for a in payload["agents"]) + "<a href='#shared'>Shared rules</a></nav>"]
    usage = payload["usage"]
    cost = usage["cost"]
    body.append(
        "<table><tr><th>Specialist</th><th>Status</th><th>Passages</th><th>Indicators</th>"
        "<th>Hypotheses (usable / recorded)</th><th>Parameters accepted / held back</th>"
        "<th>Coverage addressed</th><th>Tokens in / out</th><th>Latency</th></tr>"
        + "".join(_summary_row(a) for a in payload["agents"]) + "</table>"
        f"<p class='muted'>Total: {usage['calls']} model call(s), {usage['input_tokens']:,} "
        f"tokens in, {usage['output_tokens']:,} out; run time "
        f"{_e(payload['meta']['runtime_seconds'])} s. Cost: "
        + (f"{cost['currency']} {cost['amount']} ({_e(cost['note'])})"
           if cost["determinable"] else _e(cost["reason"])) + "</p>")
    for agent in payload["agents"]:
        body.append(_agent_section(agent, payload))
    count = len(payload["agents"])
    count_label = "six" if count == 6 else str(count)
    body.append(f"<h2 id='shared'>Rules shared by all {count_label} specialists</h2>"
                "<p class='muted'>This block is identical in every prompt and names no "
                "parameter. Each specialist's own parameters are in its role block "
                "above.</p><pre>" + _e(payload["shared_rules"]) + "</pre>")
    return _page(
        f"Business Overview: {len(payload['agents'])}-specialist comparison",
        payload, body)


def _summary_row(agent) -> str:
    call = agent["calls"][-1] if agent["calls"] else None
    inputs = (call or {}).get("inputs") or {}
    usable = sum(h["admissibility"] != "audit_only" for h in agent["hypotheses"])
    accepted = sum(p["status"] in ("ok", "provisional") for p in agent["parameters"])
    held_back = sum(adj["reason"] != "not_established"
                    for adj in agent["parameter_adjustments"])
    cls = "ok" if agent["status"] == "validated" else "bad"
    usage_in = sum((c["usage"].get("input_tokens") or 0) for c in agent["calls"])
    usage_out = sum((c["usage"].get("output_tokens") or 0) for c in agent["calls"])
    latency = sum((c.get("latency_ms") or 0) for c in agent["calls"])
    addressed = sum(c["status"] == "addressed" for c in agent["coverage"])
    return (f"<tr><td><a href='#{agent['code']}'>{agent['code']}</a> {_e(agent['title'])}</td>"
            f"<td class='{cls}'>{_e(agent['status'])}</td>"
            f"<td>{len(inputs.get('evidence') or [])}</td>"
            f"<td>{len(inputs.get('indicators') or [])}</td>"
            f"<td>{usable} / {len(agent['hypotheses'])}</td>"
            f"<td>{accepted} / {held_back}</td>"
            f"<td>{addressed} / {len(agent['coverage'])}</td>"
            f"<td>{usage_in:,} / {usage_out:,}</td><td>{latency:,} ms</td></tr>")


def _agent_section(agent, payload) -> str:
    out = [f"<h2 id='{agent['code']}'>{agent['code']} &mdash; {_e(agent['title'])}</h2>"]
    cls = "ok" if agent["status"] == "validated" else "bad"
    out.append(f"<p>Agent <code>{agent['agent_id']}</code> &middot; validator verdict: "
               f"<span class='{cls}'>{_e(agent['status'])}</span>"
               + (f" &middot; {_e(agent['detail'])}" if agent["detail"] else "") + "</p>")
    if agent["validation_issues"]:
        out.append("<ul>" + "".join(f"<li class='bad'>{_e(i)}</li>"
                                    for i in agent["validation_issues"]) + "</ul>")
    if not agent["calls"]:
        out.append("<p class='warn'>No model call was recorded for this specialist.</p>")
        return "".join(out)
    call = agent["calls"][-1]
    inputs = call["inputs"] or {}
    out.append(f"<p class='muted'>Model {_e(call['model_id'])} &middot; prompt "
               f"{_e(call['prompt_id'])} &middot; calls {len(agent['calls'])} &middot; "
               f"tokens {_e(call['usage'].get('input_tokens'))} in / "
               f"{_e(call['usage'].get('output_tokens'))} out &middot; "
               f"{_e(call.get('latency_ms'))} ms</p>")

    out.append("<h3>Role and exact effective prompt</h3>"
               f"<pre>{_e(agent['specialist_block'])}</pre>"
               "<details><summary>Full instructions exactly as sent (role block + "
               "shared rules)</summary><pre>" + _e(call["effective_instructions"])
               + "</pre></details>")
    sets = inputs.get("parameter_value_sets") or {}
    out.append("<p><strong>Permitted values supplied:</strong> " + ("; ".join(
        f"<code>{_e(k)}</code>: {_e(', '.join(v))}" for k, v in sets.items()) or "none")
        + "</p>")
    checklist = inputs.get("checklist") or []
    if checklist:
        out.append("<p><strong>Coverage questions asked:</strong></p><ul>" + "".join(
            f"<li><code>{_e(c['check_id'])}</code> {_e(c['question'])}</li>"
            for c in checklist) + "</ul>")

    indicators = inputs.get("indicators") or []
    out.append(f"<h3>Deterministic indicators provided ({len(indicators)})</h3>")
    if indicators:
        out.append("<table><tr><th>Indicator</th><th>Value</th><th>Period</th>"
                   "<th>Numerator</th><th>Denominator</th><th>State</th></tr>" + "".join(
                       f"<tr><td>{_e(i['label'])}"
                       + (f"<br><span class='muted'>{_e(i['caveat'])}</span>" if i.get("caveat") else "")
                       + f"</td><td>{_e(_number(i['value'], i.get('unit')))}</td>"
                       f"<td>{_e(i.get('period'))}</td>"
                       f"<td>{_e(i.get('numerator'))}{_inputs(i, 0)}</td>"
                       f"<td>{_e(i.get('denominator') or 'n/a')}{_inputs(i, 1)}</td>"
                       f"<td>{_e(i.get('calculation_state'))}</td></tr>" for i in indicators)
                   + "</table>")
    else:
        out.append("<p class='muted'>None available for this specialist's remit.</p>")

    evidence = inputs.get("evidence") or []
    out.append(f"<h3>Evidence provided ({len(evidence)} filing passages)</h3>"
               "<table><tr><th>Id</th><th>Section</th><th>Retrieved for</th>"
               "<th>Passage (click to expand)</th></tr>" + "".join(
                   f"<tr><td><code>{_e(p['evidence_id'][:10])}</code></td>"
                   f"<td>{_e(p.get('section'))}</td>"
                   f"<td>{_e(', '.join(p.get('retrieved_for') or []))}</td>"
                   f"<td><details><summary>{_e(' '.join((p.get('text') or '').split())[:140])}"
                   f"&hellip;</summary><pre>{_e(p.get('text'))}</pre></details></td></tr>"
                   for p in evidence) + "</table>")
    if inputs.get("primary_hypotheses") is not None:
        out.append("<p><strong>Validated BO1&ndash;BO5 hypotheses passed in:</strong> "
                   + _e(", ".join(f"{h['hypothesis_id']} ({h['admissibility']})"
                                  for h in inputs["primary_hypotheses"])) + "</p>")

    out.append("<h3>Parameter assessments</h3><table><tr><th>Parameter</th>"
               "<th>Model's value</th><th>After validation</th><th>Reason</th></tr>")
    held = {adj["parameter_id"]: adj for adj in agent["parameter_adjustments"]}
    raw = {p.get("parameter_id"): p for p in (call.get("output") or {}).get("parameters") or []}
    for pid in agent["owned_parameters"]:
        model_value = raw.get(pid, {}).get("value")
        final = next((p for p in agent["parameters"] if p["parameter_id"] == pid), None)
        if pid in held and held[pid]["reason"] == "not_established":
            verdict, reason = "not established", held[pid]["detail"]
        elif pid in held:
            verdict = "<span class='bad'>held back</span>"
            reason = f"{held[pid]['reason'].replace('_', ' ')}: {held[pid]['detail']}"
        elif final is not None and final["status"] in ("ok", "provisional"):
            verdict, reason = f"<span class='ok'>{_e(final['value'])}</span>", ""
        elif pid in raw:
            verdict, reason = "unavailable", raw[pid].get("notes") or "reported unavailable by the agent"
        else:
            verdict, reason = "not emitted", ""
        out.append(f"<tr><td><code>{_e(pid)}</code></td><td>{_e(model_value)}</td>"
                   f"<td>{verdict}</td><td>{_e(reason)}</td></tr>")
    out.append("</table>")

    out.append(f"<h3>Hypotheses, citations and validator verdict ({len(agent['hypotheses'])})</h3>")
    for h in agent["hypotheses"]:
        out.append(_hypothesis_table(h, payload))

    if agent["coverage"]:
        out.append("<h3>Coverage questions: outcome</h3><table><tr><th>Check</th>"
                   "<th>Passages located</th><th>Agent's answer</th><th>Validated answer</th>"
                   "<th>Status</th></tr>" + "".join(
                       f"<tr><td><code>{_e(c['check_id'])}</code></td>"
                       f"<td>{len(c['passages_located'])}</td>"
                       f"<td>{_e(c['agent_response'])} {_e(', '.join(c['hypothesis_ids_cited']))}"
                       + (f"<br><span class='muted'>{_e(c['note'])}</span>" if c.get("note") else "")
                       + f"</td><td>{_e(', '.join(c['addressed_by_validated_hypotheses']) or 'none')}</td>"
                       f"<td class='{'ok' if c['status'] == 'addressed' else 'warn'}'>"
                       f"{_e(c['status'].replace('_', ' '))}</td></tr>"
                       for c in agent["coverage"]) + "</table>")

    attempts = agent["retrieval_attempts"]
    gaps = [a for a in attempts if a["status"] == "no_results"]
    recoveries = [r for h in agent["hypotheses"] for r in h["recovery"]]
    out.append("<h3>Retrieval requests and retry outcomes</h3>"
               f"<p>{len(attempts)} search(es) over admitted passages: "
               f"{sum(a['origin'] == 'plan' for a in attempts)} from the plan, "
               f"{sum(a['origin'] == 'checklist' for a in attempts)} for coverage questions, "
               f"{sum(a['origin'] == 'agent_request' for a in attempts)} requested by the agent; "
               f"{len(gaps)} returned nothing"
               + (" (" + _e(", ".join(a["query_id"] for a in gaps)) + ")" if gaps else "")
               + f". Quote-recovery attempts: {len(recoveries)}.</p>")
    for r in recoveries:
        out.append(f"<p class='muted'>Recovery for {_e(r['hypothesis_id'])}: "
                   f"<strong>{_e(r['outcome'])}</strong> &mdash; {_e(r['detail'])}</p>")
    return "".join(out)


def _inputs(indicator, index) -> str:
    values = list((indicator.get("inputs") or {}).items())
    if index < len(values):
        return f"<br><span class='muted'>{_e(values[index][1])}</span>"
    return ""


def _hypothesis_table(h, payload) -> str:
    css, label = use_label(h)
    stated = h["model_stated"]
    rows = [
        f"<tr><th style='width:11rem'><code>{_e(h['hypothesis_id'])}</code></th>"
        f"<td><strong>{_e(h['claim'])}</strong></td></tr>",
        f"<tr><th>Model stated</th><td>{_e(stated.get('claim_type'))}, "
        f"{_e(stated.get('disposition'))}, direction {_e(h['direction'])}, confidence "
        f"{_e(h['confidence'])}, materiality {_e(stated.get('materiality'))}, basis "
        f"{_e(stated.get('evidence_basis'))}</td></tr>",
        f"<tr><th>Validator verdict</th><td><span class='tag t-{css}'>{label}</span>"
        f"{_e(h['claim_type'])}, {_e(h['disposition'])} &rarr; {_e(h['admissibility'])}; "
        f"evidence basis: {_e(h['evidence_basis'].replace('_', ' '))}; materiality "
        f"{_e(h['materiality'])}</td></tr>",
    ]
    support = _source(h, payload)
    if support:
        rows.append(f"<tr><th>Supporting evidence</th><td>{support}</td></tr>")
    if h["contradicting_evidence_ids"]:
        rows.append("<tr><th>Contradicting evidence</th><td>" + _e(", ".join(
            f"{e[:10]} ({payload['passages'].get(e, {}).get('section')})"
            for e in h["contradicting_evidence_ids"])) + "</td></tr>")
    if h.get("mitigant"):
        m = h["mitigant"]
        rows.append(f"<tr><th>Mitigant</th><td>{_e(m['capability'])} &mdash; existence "
                    f"{_e(m['existence'])}; effectiveness <strong>{_e(m['effectiveness'])}"
                    "</strong></td></tr>")
    if h.get("transmission_mechanism") or h["component_hypothesis_ids"]:
        rows.append(f"<tr><th>Interaction</th><td>{_e(h.get('interaction_kind'))} of "
                    f"{_e(', '.join(h['component_hypothesis_ids']))}<br>Mechanism: "
                    f"{_e(h.get('transmission_mechanism') or 'not stated')}</td></tr>")
    if h["validation_notes"]:
        rows.append("<tr><th>Validator notes</th><td>" + "<br>".join(
            _e(n) for n in h["validation_notes"]) + "</td></tr>")
    if h["missing_information"]:
        rows.append("<tr><th>Missing information</th><td>"
                    + _e("; ".join(h["missing_information"])) + "</td></tr>")
    if h["fo_handoffs"]:
        rows.append("<tr><th>FO handoff</th><td>" + "<br>".join(
            f"{_e(f['variable'])}: {_e(f['question'])} ({_e(f['origin'])})"
            for f in h["fo_handoffs"]) + "</td></tr>")
    return "<table>" + "".join(rows) + "</table>"


# --------------------------------------------------------------------------- #
# C. validation audit
# --------------------------------------------------------------------------- #


def audit_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Every hypothesis the validator rejected, qualified, corrected or left open."""
    rows = []
    for h in _all(payload):
        stated = h["model_stated"]
        changed = (h["validation_notes"] or h["review_flags"] or h["recovery"]
                   or h["admissibility"] == "audit_only"
                   or stated.get("disposition") != h["disposition"]
                   or stated.get("claim_type") != h["claim_type"])
        if not changed:
            continue
        if stated.get("disposition") == "rejected":
            category = "rejected by the specialist itself"
        elif h["admissibility"] == "audit_only":
            category = "unresolved / held back"
        elif stated.get("claim_type") != h["claim_type"]:
            category = "reclassified"
        elif h["recovery"] and all(r["outcome"] != "unresolved" for r in h["recovery"]):
            category = "evidence recovered"
        elif stated.get("disposition") != h["disposition"]:
            category = "qualified"
        else:
            category = "noted"
        css, label = use_label(h)
        rows.append({
            "hypothesis_id": h["hypothesis_id"], "agent": h["section"],
            "category": category, "claim": h["claim"],
            "model_stated": f"{stated.get('claim_type')}, {stated.get('disposition')}",
            "issues": h["validation_notes"],
            "rules": [f["rule_id"] for f in h["review_flags"]],
            "heuristic": any(f.get("heuristic") for f in h["review_flags"]),
            "supporting_quotes": [q["quote"] for q in h["evidence_quotes"]],
            "counter_evidence": h["contradicting_evidence_ids"],
            "recovery": h["recovery"],
            "final": f"{h['claim_type']}, {h['disposition']} -> {h['admissibility']}",
            "can_reach_memo_as_fact": h["admissibility"] == "established",
            "use": label,
        })
    return rows


def render_audit(payload: dict[str, Any]) -> str:
    rows = audit_rows(payload)
    metrics = payload["metrics"]
    total = metrics["hypotheses_recorded"]
    body = [
        f"<p>{total} hypotheses were recorded; {len(rows)} appear here because the "
        "validator rejected, qualified, corrected, reclassified or annotated them, or "
        "because the specialist rejected its own hypothesis. "
        f"{total - len(rows)} passed unchanged and are not listed. "
        f"Usable after validation: {metrics['hypotheses_usable']} of {total}.</p>",
        "<table><tr><th>Outcome</th><th>Count</th></tr>" + "".join(
            f"<tr><td>{_e(k)}</td><td>{v}</td></tr>"
            for k, v in _count(r["category"] for r in rows).items()) + "</table>",
        "<p class='muted'>Rules marked <em>heuristic</em> are pattern checks over the "
        "wording of a claim. They hold back known over-readings; they are not proof "
        "that a claim that passes is logically sound.</p>",
    ]
    rejected_agents = [a for a in payload["agents"] if a["status"] != "validated"]
    if rejected_agents:
        body.append("<h2>Specialist outputs rejected as a whole</h2>")
        for agent in rejected_agents:
            body.append(f"<p><strong>{agent['code']}</strong> {_e(agent['status'])}: "
                        + _e("; ".join(agent["validation_issues"]) or agent["detail"])
                        + "</p>")
    for category in ("unresolved / held back", "reclassified", "evidence recovered",
                     "qualified", "noted", "rejected by the specialist itself"):
        chosen = [r for r in rows if r["category"] == category]
        if not chosen:
            continue
        body.append(f"<h2>{category.capitalize()} ({len(chosen)})</h2>")
        for r in chosen:
            recovery = "".join(
                f"<br>Original quote: <span class='q'>&ldquo;{_e(x['original_quote'])}&rdquo;</span>"
                f"<br>Search terms: {_e(', '.join(x['retrieval_request']['terms']))}; "
                f"candidates examined: {len(x['candidates'])}; outcome "
                f"<strong>{_e(x['outcome'])}</strong> &mdash; {_e(x['detail'])}"
                + (f"<br>Replacement (source text): <span class='q'>&ldquo;"
                   f"{_e(x['replacement_quote'])}&rdquo;</span>" if x["replacement_quote"] else "")
                for x in r["recovery"])
            body.append(
                "<table>"
                f"<tr><th style='width:12rem'><code>{_e(r['hypothesis_id'])}</code></th>"
                f"<td><strong>{_e(r['claim'])}</strong></td></tr>"
                f"<tr><th>Original classification</th><td>{_e(r['model_stated'])}</td></tr>"
                "<tr><th>Validation issue</th><td>" + ("<br>".join(_e(i) for i in r["issues"])
                                                      or "none recorded") +
                (f"<br><span class='muted'>Rules: {_e(', '.join(r['rules']))}"
                 + (" (heuristic)" if r["heuristic"] else "") + "</span>" if r["rules"] else "")
                + "</td></tr>"
                "<tr><th>Supporting / counter-evidence</th><td>" + ("<br>".join(
                    f"<span class='q'>&ldquo;{_e(q)}&rdquo;</span>"
                    for q in r["supporting_quotes"]) or "no verified quote")
                + (f"<br>Counter-evidence passages: {len(r['counter_evidence'])}"
                   if r["counter_evidence"] else "") + "</td></tr>"
                + (f"<tr><th>Recovery attempt</th><td>{recovery[4:]}</td></tr>" if recovery
                   else "<tr><th>Recovery attempt</th><td>none needed</td></tr>")
                + f"<tr><th>Final disposition</th><td>{_e(r['final'])} ({_e(r['use'])})</td></tr>"
                "<tr><th>Can reach the memo as a fact?</th><td>"
                + ("<span class='ok'>Yes</span>" if r["can_reach_memo_as_fact"]
                   else "<span class='bad'>No</span>") + "</td></tr></table>")
    held = [(a["code"], adj) for a in payload["agents"] for adj in a["parameter_adjustments"]
            if adj["reason"] != "not_established"]
    body.append(f"<h2>Parameter classifications held back ({len(held)})</h2>")
    if held:
        body.append("<table><tr><th>Specialist</th><th>Parameter</th><th>Model's value</th>"
                    "<th>Reason</th><th>Detail</th></tr>" + "".join(
                        f"<tr><td>{code}</td><td><code>{_e(adj['parameter_id'])}</code></td>"
                        f"<td>{_e(adj['original_value'])}</td>"
                        f"<td>{_e(adj['reason'].replace('_', ' '))}</td>"
                        f"<td>{_e(adj['detail'])}</td></tr>" for code, adj in held) + "</table>")
    else:
        body.append("<p class='muted'>None.</p>")
    return _page("Business Overview: validation audit", payload, body)


# --------------------------------------------------------------------------- #
# benchmark
# --------------------------------------------------------------------------- #

_BENCH_ROWS = [
    ("Specialists validated (of 6)", "agents_validated"),
    ("Hypotheses generated by the model", "hypotheses_generated"),
    ("Hypotheses usable after validation", "hypotheses_usable"),
    ("... established facts", ("by_admissibility", "established")),
    ("... attributed to management", ("by_admissibility", "attributed")),
    ("... qualified", ("by_admissibility", "qualified_narrative")),
    ("... not usable (audit only)", ("by_admissibility", "audit_only")),
    ("Unsupported-inference flags (Layer C)", "unsupported_inference_flags"),
    ("Evidence-sufficiency flags (Layer D)", "evidence_sufficiency_flags"),
    ("BO/FO boundary violations detected", "bo_fo_boundary_violations"),
    ("Citation verification failures", "citation_verification_failures"),
    ("Citations recovered from the filing", "citations_recovered"),
    ("Citations left unresolved", "citations_unresolved"),
    ("Usable hypotheses bound to a deterministic indicator", "hypotheses_using_indicators"),
    ("Coverage questions asked", "coverage_checks"),
    ("Coverage questions with a validated answer", "coverage_addressed"),
    ("Parameter values accepted", "parameters_accepted"),
    ("Parameter values held back by validation", "parameters_held_back"),
    ("Parameter values the agent reported as not established",
     "parameters_reported_not_established"),
    ("FO handoffs raised", "fo_handoffs"),
    ("Model calls", "model_calls"),
    ("Tokens in", ("tokens", "input_tokens")),
    ("Tokens out", ("tokens", "output_tokens")),
    ("Run time (s)", "runtime_seconds"),
]


def _metric(metrics, key):
    if metrics is None:
        return "n/a"
    if isinstance(key, tuple):
        value = metrics.get(key[0], {})
        return value.get(key[1], 0) if isinstance(value, dict) else "n/a"
    return metrics.get(key, "n/a")


def render_benchmark(payload: dict[str, Any], columns: list[tuple[str, str, dict | None]],
                     notes: list[str], checks: list[dict[str, Any]]) -> str:
    """``columns`` = [(title, description, metrics)], last one being this run."""
    body = ["<p>An increase in accepted hypotheses is not treated as an improvement. "
            "The aim is fewer unsupported inferences reaching a memo and better "
            "coverage of material topics.</p>",
            "<table><tr><th>Measure</th>" + "".join(
                f"<th>{_e(title)}<br><span class='muted'>{_e(desc)}</span></th>"
                for title, desc, _ in columns) + "</tr>"]
    for label, key in _BENCH_ROWS:
        body.append(f"<tr><td>{_e(label)}</td>" + "".join(
            f"<td>{_e(_metric(m, key))}</td>" for _, _, m in columns) + "</tr>")
    body.append("</table>")
    if checks:
        body.append("<h2>Known problems of the baseline run: status now</h2>"
                    "<table><tr><th>Problem in the baseline</th><th>Baseline</th>"
                    "<th>This run</th><th>Status</th></tr>" + "".join(
                        f"<tr><td>{_e(c['problem'])}</td><td>{_e(c['before'])}</td>"
                        f"<td>{_e(c['after'])}</td>"
                        f"<td class='{'ok' if c['resolved'] else 'warn'}'>"
                        f"{'addressed' if c['resolved'] else 'NOT resolved'}</td></tr>"
                        for c in checks) + "</table>")
    if notes:
        body.append("<h2>Notes on comparability</h2><ul>" + "".join(
            f"<li>{_e(n)}</li>" for n in notes) + "</ul>")
    return _page("Business Overview: old versus new", payload, body)


# --------------------------------------------------------------------------- #
# PDF (optional) and writer
# --------------------------------------------------------------------------- #


def write_comparison_pdf(payload: dict[str, Any], path: Path) -> bool:
    """A3-landscape table, one column per specialist. False if reportlab is absent."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A3, landscape
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
        )
    except ImportError:
        return False
    translate = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                               "–": "-", "—": "-", "…": "...",
                               " ": " ", "→": "->"})

    def clean(text) -> str:
        return ("" if text is None else str(text)).translate(translate).encode(
            "latin-1", "replace").decode("latin-1")

    body = ParagraphStyle("b", fontName="Helvetica", fontSize=7.2, leading=8.9)
    head = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=8.5, leading=10,
                          textColor=colors.white)
    label = ParagraphStyle("l", fontName="Helvetica-Bold", fontSize=7.2, leading=8.9)

    def para(text, style=body):
        markup = html.escape(clean(text)).replace("\n", "<br/>")
        for marker in ("VERDICT:", "HELD BACK", "Validator:"):
            markup = markup.replace(marker, f"<b>{marker}</b>")
        return Paragraph(markup, style)

    agents = payload["agents"]
    if len(agents) > 6:
        doc = SimpleDocTemplate(
            str(path), pagesize=landscape(A3), leftMargin=12 * mm,
            rightMargin=12 * mm, topMargin=10 * mm, bottomMargin=10 * mm,
            title="Business Overview specialist live transcript")
        title = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=15, leading=18)
        heading = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=11,
                                 leading=14, spaceBefore=7, spaceAfter=3)
        mono = ParagraphStyle("mono", fontName="Courier", fontSize=6.6, leading=8.2,
                              borderColor=colors.HexColor("#d5dbe2"), borderWidth=.4,
                              borderPadding=5, backColor=colors.HexColor("#f6f7f9"))

        def shortened(value: Any) -> Any:
            if isinstance(value, dict):
                return {key: shortened(item) for key, item in value.items()}
            if isinstance(value, list):
                return [shortened(item) for item in value]
            if isinstance(value, str) and len(value) > 1200:
                return value[:1200] + (
                    f" ... [{len(value) - 1200} more characters in specialists_comparison.html]")
            return value

        def block(label_text: str, value: Any):
            if not isinstance(value, str):
                value = json.dumps(shortened(value), indent=2, ensure_ascii=False,
                                   default=str)
            rendered = html.escape(clean(shortened(value))).replace("\n", "<br/>")
            return [Paragraph(label_text, heading), Paragraph(rendered, mono)]

        meta = payload["meta"]
        story = [
            Paragraph(f"Business Overview: {len(agents)}-specialist live transcript", title),
            Paragraph(html.escape(clean(
                f"{meta['borrower']}, case {meta['case_id']}, run {meta['analysis_run_id']}, "
                f"{meta['execution_mode'].replace('_', ' ')}, models "
                f"{', '.join(meta['models']) or 'n/a'}.")), body),
        ]
        for index, agent in enumerate(agents):
            if index:
                story.append(PageBreak())
            story.append(Paragraph(
                f"{agent['code']} - {html.escape(clean(agent['title']))}", title))
            story.append(Paragraph(html.escape(clean(
                f"Agent {agent['agent_id']}; status {agent['status']}; "
                f"validator issues {len(agent['validation_issues'])}; "
                f"adjustments {len(agent['validator_adjustments'])}.")), body))
            for call_index, call in enumerate(agent["calls"], 1):
                usage = call.get("usage") or {}
                story.append(Paragraph(html.escape(clean(
                    f"Call {call_index}/{len(agent['calls'])}; model {call.get('model_id')}; "
                    f"{usage.get('input_tokens')} input, {usage.get('output_tokens')} output, "
                    f"{usage.get('total_tokens')} total tokens; {call.get('latency_ms')} ms.")),
                    body))
                story += block("Effective instructions", call.get("effective_instructions"))
                story += block("Inputs", call.get("inputs"))
                story += block("Model output", call.get("output") or call.get("raw_response")
                               or call.get("error"))
            story += block("Deterministic validator result", {
                "status": agent["status"], "detail": agent["detail"],
                "issues": agent["validation_issues"],
                "adjustments": agent["validator_adjustments"],
                "parameters_after_validation": agent["parameters"],
                "hypotheses_after_validation": agent["hypotheses"],
                "coverage": agent["coverage"],
            })
        doc.build(story)
        return True

    cells: dict[str, dict[str, Any]] = {}
    depth = 0
    for agent in agents:
        call = agent["calls"][-1] if agent["calls"] else {}
        inputs = call.get("inputs") or {}
        cell: dict[str, Any] = {"role": agent["specialist_block"]}
        cell["numbers"] = "\n".join(
            f"- {i['label']}: {_number(i['value'], i.get('unit'))} [{i.get('period')}]"
            for i in inputs.get("indicators") or []) or "none"
        sections: dict[str, int] = {}
        for p in inputs.get("evidence") or []:
            sections[p.get("section") or "?"] = sections.get(p.get("section") or "?", 0) + 1
        cell["evidence"] = (f"{len(inputs.get('evidence') or [])} passages: " + ", ".join(
            f"{k} x{v}" for k, v in sorted(sections.items())) + "\nCoverage questions: "
            + (", ".join(c["check_id"] for c in inputs.get("checklist") or []) or "none"))
        held = {adj["parameter_id"]: adj for adj in agent["parameter_adjustments"]}
        final = {p["parameter_id"]: p for p in agent["parameters"]}
        lines = []
        for pid in agent["owned_parameters"]:
            if pid in held and held[pid]["reason"] == "not_established":
                lines.append(f"- {pid}: not established")
            elif pid in held:
                lines.append(f"- {pid}: HELD BACK (model said {held[pid]['original_value']}; "
                             f"{held[pid]['reason'].replace('_', ' ')})")
            elif pid in final and final[pid]["status"] in ("ok", "provisional"):
                lines.append(f"- {pid}: {final[pid]['value']}")
            else:
                lines.append(f"- {pid}: unavailable")
        cell["parameters"] = "\n".join(lines)
        cell["hypotheses"] = []
        for h in agent["hypotheses"]:
            _, use = use_label(h)
            text = [f"{h['hypothesis_id']}: {h['claim']}",
                    f"VERDICT: {use} ({h['claim_type']}, {h['disposition']}; "
                    f"basis {h['evidence_basis'].replace('_', ' ')})"]
            for q in h["evidence_quotes"][:1]:
                text.append(f'Quote: "{q["quote"][:260]}"')
            if h.get("mitigant"):
                text.append(f"Mitigant: {h['mitigant']['capability']} - "
                            f"{h['mitigant']['effectiveness']}")
            if h.get("transmission_mechanism"):
                text.append(f"Mechanism: {h['transmission_mechanism']}")
            for note in h["validation_notes"][:3]:
                text.append(f"Validator: {note[:240]}")
            cell["hypotheses"].append("\n".join(text))
        depth = max(depth, len(cell["hypotheses"]))
        cell["coverage"] = "\n".join(
            f"- {c['check_id']}: {c['status'].replace('_', ' ')}"
            for c in agent["coverage"]) or "none"
        cell["usage"] = (f"{agent['status']}; {call.get('model_id')}\n"
                         f"{(call.get('usage') or {}).get('input_tokens')} tokens in, "
                         f"{(call.get('usage') or {}).get('output_tokens')} out, "
                         f"{call.get('latency_ms')} ms")
        cells[agent["agent_id"]] = cell

    def row(name, key):
        return [para(name, label)] + [para(cells[a["agent_id"]].get(key, "")) for a in agents]

    data = [[para("", head)] + [para(f"{a['code']}\n{a['title']}", head) for a in agents],
            row("PROMPT\nrole, parameters", "role"),
            row("PROMPT\nindicators", "numbers"),
            row("PROMPT\nevidence", "evidence"),
            row("OUTPUT\nparameters after validation", "parameters")]
    for index in range(depth):
        data.append([para(f"OUTPUT\nhypothesis {index + 1}", label)] + [
            para(cells[a["agent_id"]]["hypotheses"][index]
                 if index < len(cells[a["agent_id"]]["hypotheses"]) else "")
            for a in agents])
    data += [row("COVERAGE\nquestions", "coverage"), row("RUN", "usage")]
    doc = SimpleDocTemplate(str(path), pagesize=landscape(A3), leftMargin=9 * mm,
                            rightMargin=9 * mm, topMargin=9 * mm, bottomMargin=9 * mm,
                            title="Business Overview specialists")
    first = 22 * mm
    table = Table(data, colWidths=[first] + [(doc.width - first) / len(agents)] * len(agents),
                  repeatRows=1, splitByRow=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1c2430")),
        ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#eef1f5")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b9c2cc")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    meta = payload["meta"]
    title = ParagraphStyle("t", fontName="Helvetica-Bold", fontSize=14, leading=17)
    small = ParagraphStyle("s", fontName="Helvetica", fontSize=8, leading=10)
    doc.build([
        Paragraph(f"Business Overview: {len(agents)}-specialist comparison", title),
        Paragraph(html.escape(clean(
            f"{meta['borrower']}, case {meta['case_id']}, run {meta['analysis_run_id']}, "
            f"{meta['execution_mode'].replace('_', ' ')}, model(s) "
            f"{', '.join(meta['models']) or 'n/a'}. {len(agents)} specialists and validators only.")),
            small),
        Spacer(1, 3 * mm), table, Spacer(1, 5 * mm),
        Paragraph(f"Rules shared by all {len(agents)} specialists (identical in every prompt)",
                  ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=9, leading=11)),
        para(payload["shared_rules"],
             ParagraphStyle("r", fontName="Helvetica", fontSize=7.5, leading=9.5))])
    return True


def write_reports(payload: dict[str, Any], folder: Path, *, benchmark: str | None = None
                  ) -> dict[str, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = {"specialists_run.json": folder / "specialists_run.json"}
    paths["specialists_run.json"].write_text(
        canonical_json(payload) + "\n", encoding="utf-8", newline="\n")
    pages = [
        ("executive_summary.html", render_executive_summary(payload)),
        ("specialists_comparison.html", render_comparison(payload)),
        ("validation_audit.html", render_audit(payload)),
    ]
    # The governed-assessment view is M3-v1's Validator 7 record; M3-v2 has none.
    if not is_m3_v2_block((payload.get("module_conclusions") or {}).get("M3")):
        m3_export = build_m3_governed_export(payload)
        paths["m3_governed_assessment.json"] = folder / "m3_governed_assessment.json"
        paths["m3_governed_assessment.json"].write_text(
            canonical_json(m3_export) + "\n", encoding="utf-8", newline="\n")
        pages.append(("m3_governed_assessment.html",
                      render_m3_governed_assessment(m3_export, payload)))
    for name, content in pages:
        paths[name] = folder / name
        paths[name].write_text(content, encoding="utf-8", newline="\n")
    if benchmark is not None:
        paths["benchmark.html"] = folder / "benchmark.html"
        paths["benchmark.html"].write_text(benchmark, encoding="utf-8", newline="\n")
    pdf = folder / "specialists_comparison.pdf"
    if write_comparison_pdf(payload, pdf):
        paths["specialists_comparison.pdf"] = pdf
    return paths
