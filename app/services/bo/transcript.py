"""Human-readable transcript of the BO specialists: prompt, numbers, output.

Two small pieces:

* :class:`RecordingBackend` wraps any ``LLMBackend`` and keeps, per call, exactly
  what was sent (instruction text and input payload) and what came back (raw
  response, usage). It changes nothing about the call.
* :func:`render_transcript` turns those recorded calls, plus the run's recorded
  validation outcome, into one HTML page a reviewer can read top to bottom.

The transcript shows inputs and structured outputs only; it does not contain or
reconstruct any model-private reasoning.
"""

from __future__ import annotations

import html
import json
import threading
from typing import Any

from app.services.bo.plans import BO_AGENT_SPECS
from app.services.llm.client import LLMBackend, LLMRawResult, LLMRequest


class RecordingBackend(LLMBackend):
    """Delegate to ``inner`` and record each request/response pair."""

    def __init__(self, inner: LLMBackend) -> None:
        self._inner = inner
        self._lock = threading.Lock()
        self.calls: list[dict[str, Any]] = []

    @property
    def model_id(self) -> str:
        return getattr(self._inner, "model_id", "unknown")

    def cache_identity(self, model_tier: str | None = None):
        return self._inner.cache_identity(model_tier)

    def generate(self, request: LLMRequest) -> LLMRawResult:
        entry: dict[str, Any] = {
            "task_type": request.method, "prompt_id": request.prompt.prompt_id,
            "prompt_name": request.prompt.name, "prompt_version": request.prompt.version,
            "prompt_hash": request.prompt.content_hash,
            "instructions": request.prompt.template, "inputs": request.inputs,
            "model_tier": request.model_tier,
        }
        if request.alias_evidence and request.evidence_ids:
            from app.services.bo.citations import evidence_aliases

            # The inputs carry short evidence labels; this maps them back.
            entry["evidence_aliases"] = {
                alias: eid for eid, alias in evidence_aliases(request.evidence_ids).items()}
        try:
            result = self._inner.generate(request)
        except Exception as exc:
            entry.update(error=f"{type(exc).__name__}: {getattr(exc, 'public_message', exc)}")
            with self._lock:
                self.calls.append(entry)
            raise
        entry.update(raw_response=result.raw_response, model_id=result.model_id,
                     model_config=result.model_config)
        with self._lock:
            self.calls.append(entry)
        return result


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _fmt(value: Any, unit: str | None) -> str:
    if value is None:
        return "not available"
    if unit in ("ratio", "pure") or (isinstance(value, float) and abs(value) < 5
                                     and unit != "index"):
        return f"{value:.4f} ({value * 100:.2f}%)"
    return f"{value:,.2f}" if isinstance(value, float) else str(value)


_CSS = """
body{font-family:Arial,Helvetica,sans-serif;color:#1c2430;max-width:1100px;margin:1.5rem auto;padding:0 1.2rem;font-size:14px;line-height:1.5}
h1{font-size:1.5rem;margin-bottom:.2rem} h2{font-size:1.15rem;margin:2.2rem 0 .4rem;padding:.4rem .6rem;background:#1c2430;color:#fff}
h3{font-size:1rem;margin:1.2rem 0 .3rem;border-bottom:1px solid #c9d1da;padding-bottom:.15rem}
table{border-collapse:collapse;width:100%;margin:.4rem 0 .8rem} th,td{text-align:left;vertical-align:top;padding:.3rem .5rem;border:1px solid #d5dbe2}
th{background:#eef1f5} pre{white-space:pre-wrap;background:#f6f7f9;border:1px solid #d5dbe2;padding:.6rem;font-size:12px}
.muted{color:#68727f} .ok{color:#2f6b45;font-weight:bold} .bad{color:#9a3324;font-weight:bold} .warn{color:#8a6d1c;font-weight:bold}
.banner{padding:.5rem .7rem;margin:.6rem 0;border-left:4px solid #8a6d1c;background:#fbf6e6}
details{margin:.3rem 0} summary{cursor:pointer;color:#23527c} .q{font-style:italic}
nav a{margin-right:.8rem}
"""


def render_transcript(*, title: str, case: dict[str, Any], calls: list[dict[str, Any]],
                      outcomes: dict[str, dict[str, Any]],
                      hypotheses: dict[str, list[dict[str, Any]]],
                      note: str = "") -> str:
    """One HTML page: for each specialist, the prompt, its numbers and the output."""
    by_agent: dict[str, list[dict[str, Any]]] = {}
    for call in calls:
        by_agent.setdefault(call["task_type"], []).append(call)
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{_e(title)}</title>"
           f"<style>{_CSS}</style></head><body><h1>{_e(title)}</h1>",
           f"<div class='muted'>{_e(case.get('borrower'))} &middot; case {_e(case.get('case_id'))}"
           f" &middot; as of {_e(case.get('as_of_date'))} &middot; analysis run "
           f"{_e(case.get('analysis_run_id'))}</div>"]
    if note:
        out.append(f"<div class='banner'>{_e(note)}</div>")
    out.append("<nav>" + "".join(
        f"<a href='#{s.code}'>{s.code} {_e(s.title)}</a>" for s in BO_AGENT_SPECS) + "</nav>")

    for spec in BO_AGENT_SPECS:
        outcome = outcomes.get(spec.agent_id, {})
        status = outcome.get("status", "not_executed")
        cls = "ok" if status == "validated" else "bad" if status in ("rejected", "failed") else "warn"
        out.append(f"<h2 id='{spec.code}'>{spec.code} &mdash; {_e(spec.title)}</h2>")
        out.append(f"<p>Agent <code>{spec.agent_id}</code> &middot; recorded status: "
                   f"<span class='{cls}'>{_e(status)}</span>"
                   + (f" &middot; {_e(outcome.get('detail'))}" if outcome.get("detail") else "")
                   + "</p>")
        agent_calls = by_agent.get(spec.agent_id, [])
        if not agent_calls:
            out.append("<p class='warn'>No model call was made for this agent.</p>")
            continue
        for index, call in enumerate(agent_calls, 1):
            if len(agent_calls) > 1:
                out.append(f"<p><strong>Call {index} of {len(agent_calls)}</strong></p>")
            out.append(_call_section(call))
        if outcome.get("issues"):
            out.append("<h3>Deterministic validation: output rejected</h3><ul>" + "".join(
                f"<li class='bad'>{_e(i)}</li>" for i in outcome["issues"]) + "</ul>"
                "<p class='muted'>A rejected output promotes nothing: none of its "
                "parameters or hypotheses is accepted.</p>")
        if outcome.get("adjustments"):
            out.append("<h3>Deterministic validation: adjustments</h3><ul>" + "".join(
                f"<li><code>{_e(a['hypothesis_id'])}</code>: {_e(a['note'])}</li>"
                for a in outcome["adjustments"]) + "</ul>")
        accepted = hypotheses.get(spec.agent_id) or []
        if accepted:
            out.append("<h3>After validation: recorded hypothesis dispositions</h3>"
                       "<table><tr><th>Id</th><th>Type</th><th>Disposition</th>"
                       "<th>How it may be used</th><th>Verified quotes</th></tr>" + "".join(
                           f"<tr><td><code>{_e(h['hypothesis_id'])}</code></td>"
                           f"<td>{_e(h['claim_type'])}</td><td>{_e(h['disposition'])}</td>"
                           f"<td>{_e(h['admissibility'])}</td>"
                           f"<td>{len(h['evidence_quotes'])}</td></tr>" for h in accepted)
                       + "</table>")
    out.append("</body></html>")
    return "\n".join(out)


def _call_section(call: dict[str, Any]) -> str:
    inputs = call["inputs"]
    parts = []
    config = call.get("model_config") or {}
    usage = ", ".join(f"{k.replace('_', ' ')} {config[k]}" for k in
                      ("input_tokens", "output_tokens", "total_tokens") if k in config)
    parts.append(f"<p class='muted'>Model {_e(call.get('model_id', 'n/a'))} &middot; prompt "
                 f"{_e(call['prompt_id'])} &middot; {_e(usage or 'usage not reported')}"
                 + (f" &middot; latency {config['latency_ms']} ms" if "latency_ms" in config else "")
                 + "</p>")

    parts.append("<h3>1. Prompt: instructions sent to the agent</h3>"
                 f"<pre>{_e(call['instructions'])}</pre>")
    agent = inputs.get("agent") or {}
    if agent:
        parts.append(f"<p>Owned parameters: <code>{_e(', '.join(agent.get('owned_parameters', [])))}"
                     "</code></p>")
    sets = inputs.get("parameter_value_sets") or {}
    if sets:
        parts.append("<p>Allowed values: " + "; ".join(
            f"<code>{_e(k)}</code> = {_e(', '.join(v))}" for k, v in sets.items()) + "</p>")
    if inputs.get("follow_up_note"):
        parts.append(f"<p class='warn'>{_e(inputs['follow_up_note'])}</p>")

    indicators = inputs.get("indicators") or []
    parts.append("<h3>2. Numbers in the prompt (deterministic calculations)</h3>")
    if indicators:
        parts.append("<table><tr><th>Indicator</th><th>Value</th><th>Unit</th><th>Period</th>"
                     "<th>Numerator</th><th>Denominator</th><th>State</th></tr>" + "".join(
                         f"<tr><td>{_e(i['label'])}"
                         + (f"<br><span class='muted'>{_e(i['caveat'])}</span>" if i.get("caveat") else "")
                         + f"</td><td>{_e(_fmt(i['value'], i.get('unit')))}</td><td>{_e(i.get('unit'))}</td>"
                         f"<td>{_e(i.get('period'))}</td><td>{_e(i.get('numerator'))}</td>"
                         f"<td>{_e(i.get('denominator') or 'n/a')}</td>"
                         f"<td>{_e(i.get('calculation_state'))}</td></tr>" for i in indicators)
                     + "</table><p class='muted'>These are the only calculated numbers the "
                       "agent was given. Any other figure must be copied from a quoted passage.</p>")
    else:
        parts.append("<p>No calculated indicator was available.</p>")

    evidence = inputs.get("evidence") or []
    parts.append(f"<h3>3. Evidence in the prompt ({len(evidence)} passages from the filing)</h3>")
    if evidence:
        rows = []
        for p in evidence:
            text = p.get("text") or ""
            rows.append(
                f"<tr><td><code>{_e(p['evidence_id'][:10])}</code></td><td>{_e(p.get('section'))}"
                f"<br><span class='muted'>offset {_e(p.get('location'))}</span></td>"
                f"<td>{_e(', '.join(p.get('retrieved_for', [])))}</td>"
                f"<td><details><summary>{_e(' '.join(text.split())[:150])}&hellip;</summary>"
                f"<pre>{_e(text)}</pre></details></td></tr>")
        parts.append("<table><tr><th>Evidence id</th><th>Section</th><th>Retrieved for</th>"
                     "<th>Passage (click to expand)</th></tr>" + "".join(rows) + "</table>")
    primary = inputs.get("primary_hypotheses")
    if primary is not None:
        parts.append(f"<p>Validated hypotheses from BO1&ndash;BO5 passed to this agent: "
                     f"{len(primary)} (" + _e(", ".join(h["hypothesis_id"] for h in primary)) + ")</p>")

    parts.append("<h3>4. Output returned by the agent</h3>")
    if call.get("error"):
        parts.append(f"<p class='bad'>Call failed: {_e(call['error'])}</p>")
        return "".join(parts)
    raw = call.get("raw_response") or ""
    try:
        parsed = json.loads(raw)
    except ValueError:
        parts.append(f"<p class='bad'>The response was not valid JSON.</p><pre>{_e(raw[:6000])}</pre>")
        return "".join(parts)
    parameters = parsed.get("parameters") or []
    if parameters:
        parts.append("<p><strong>Parameters</strong></p><table><tr><th>Parameter</th><th>Value</th>"
                     "<th>Status</th><th>Evidence cited</th><th>Notes</th></tr>" + "".join(
                         f"<tr><td><code>{_e(p.get('parameter_id'))}</code></td><td>{_e(p.get('value'))}</td>"
                         f"<td>{_e(p.get('status'))}</td>"
                         f"<td>{_e(', '.join(e[:10] for e in p.get('evidence_ids') or []))}</td>"
                         f"<td>{_e(p.get('notes'))}</td></tr>" for p in parameters) + "</table>")
    hyps = parsed.get("hypotheses") or []
    parts.append(f"<p><strong>Hypotheses ({len(hyps)})</strong></p>")
    for h in hyps:
        quotes = "".join(f"<li class='q'>&ldquo;{_e(q.get('quote'))}&rdquo; "
                         f"<span class='muted'>[{_e((q.get('evidence_id') or '')[:10])}]</span></li>"
                         for q in h.get("evidence_quotes") or [])
        numbers = "".join(f"<li>calculation {_e((q.get('parameter_result_id') or '')[:14])} = "
                          f"{_e(q.get('value'))} (denominator {_e(q.get('denominator'))})</li>"
                          for q in h.get("quoted_values") or [])
        extra = []
        if h.get("credit_mechanism"):
            extra.append(f"Credit mechanism: {_e(h['credit_mechanism'])}")
        if h.get("corrects_hypothesis_id"):
            extra.append(f"Corrects {_e(h['corrects_hypothesis_id'])}")
        if h.get("mitigant"):
            m = h["mitigant"]
            extra.append(f"Mitigant: {_e(m.get('capability'))} &mdash; existence "
                         f"{_e(m.get('existence'))}, effectiveness {_e(m.get('effectiveness'))}")
        if h.get("component_hypothesis_ids"):
            extra.append(f"{_e(h.get('interaction_kind'))} of "
                         f"{_e(', '.join(h['component_hypothesis_ids']))}")
        if h.get("missing_information"):
            extra.append("Missing: " + _e("; ".join(h["missing_information"])))
        parts.append(
            f"<table><tr><th style='width:9rem'><code>{_e(h.get('hypothesis_id'))}</code></th>"
            f"<td><strong>{_e(h.get('claim'))}</strong></td></tr>"
            f"<tr><th>As stated by the agent</th><td>{_e(h.get('claim_type'))} &middot; direction "
            f"{_e(h.get('direction'))} &middot; {_e(h.get('disposition'))} &middot; confidence "
            f"{_e(h.get('confidence'))} &middot; materiality {_e(h.get('materiality'))}</td></tr>"
            + (f"<tr><th>Quotes</th><td><ul>{quotes}</ul></td></tr>" if quotes else "")
            + (f"<tr><th>Numbers</th><td><ul>{numbers}</ul></td></tr>" if numbers else "")
            + (f"<tr><th>Counter-evidence</th><td>{_e(', '.join(e[:10] for e in h['contradicting_evidence_ids']))}</td></tr>"
               if h.get("contradicting_evidence_ids") else "")
            + (f"<tr><th>Detail</th><td>{'<br>'.join(extra)}</td></tr>" if extra else "")
            + "</table>")
    requests = parsed.get("retrieval_requests") or []
    if requests:
        parts.append("<p><strong>Follow-up searches requested</strong></p><ul>" + "".join(
            f"<li>{_e(r.get('purpose'))}: {_e(', '.join(r.get('terms') or []))}</li>"
            for r in requests) + "</ul>")
    parts.append(f"<details><summary>Raw JSON response</summary><pre>{_e(json.dumps(parsed, indent=1))}</pre></details>")
    return "".join(parts)
