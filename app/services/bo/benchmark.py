"""Baseline replay and old-versus-new comparison for a specialists run.

A baseline is a previous run's recorded model responses (``specialists_calls.json``
from the transcript helper, or ``specialists_run.json``). Two views of it are
compared with a new run:

* AS RECORDED — what the validator of that time accepted;
* RE-VALIDATED — the SAME model responses replayed, without calling a model,
  through the current validators and the same case evidence. This isolates what
  the validator changes do from what the prompt and retrieval changes do.

Replaying requires the baseline's evidence ids to exist in the current case,
which holds when the same filing is processed under the same case id.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.bo.offline import ScriptedBoBackend
from app.services.bo.plans import BO_NARROW_AGENT_IDS
from app.services.llm.client import LLMRawResult, LLMRequest


class ReplayBackend(ScriptedBoBackend):
    """Replays recorded responses, re-pointing indicator ids to the current run.

    Deterministic indicators get a fresh ``parameter_result_id`` in every run.
    A recorded response that quoted one is re-pointed to the indicator with the
    SAME ``parameter_id`` in the current request, so the quote is re-validated
    against the current calculation. If the current packet no longer carries
    that indicator the old id is left in place and validation rejects it.
    """

    def __init__(self, script, id_to_parameter: dict[str, str]) -> None:
        super().__init__(script, model_id="baseline-replay")
        self._id_to_parameter = id_to_parameter

    def generate(self, request: LLMRequest) -> LLMRawResult:
        result = super().generate(request)
        current = {i["parameter_id"]: i["parameter_result_id"]
                   for i in request.inputs.get("indicators") or []}
        raw = result.raw_response
        for old_id, parameter_id in self._id_to_parameter.items():
            if old_id in raw and parameter_id in current:
                raw = raw.replace(old_id, current[parameter_id])
        return LLMRawResult(raw_response=raw, model_id=result.model_id,
                            model_config=result.model_config)


def load_baseline_calls(path: str | Path) -> list[dict[str, Any]]:
    """Recorded calls as ``[{task_type, raw_response, model_config, ...}]``."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    calls = []
    for agent in data["agents"]:
        for call in agent["calls"]:
            calls.append({
                "task_type": agent["agent_id"],
                "raw_response": json.dumps(call["output"]) if call.get("output")
                else call.get("raw_response") or "",
                "model_id": call.get("model_id"),
                "model_config": {**(call.get("usage") or {}),
                                 "latency_ms": call.get("latency_ms")},
                "instructions": call.get("effective_instructions"),
                "inputs": call.get("inputs"),
            })
    return calls


def replay_backend(calls: list[dict[str, Any]]) -> ScriptedBoBackend:
    """A scripted backend that returns the baseline's responses verbatim."""
    script: dict[str, list[str]] = {}
    for call in calls:
        if call["task_type"] in BO_NARROW_AGENT_IDS and call.get("raw_response"):
            script.setdefault(call["task_type"], []).append(call["raw_response"])
    missing = [a for a in BO_NARROW_AGENT_IDS if a not in script]
    for agent_id in missing:
        script[agent_id] = [json.dumps({"parameters": [], "hypotheses": []})]
    id_to_parameter = {
        i["parameter_result_id"]: i["parameter_id"]
        for call in calls for i in (call.get("inputs") or {}).get("indicators") or []}
    return ReplayBackend(script, id_to_parameter)


def recorded_baseline_metrics(calls: list[dict[str, Any]],
                              recorded_hypotheses: list[dict[str, Any]] | None
                              ) -> dict[str, Any]:
    """Metrics of the baseline as its own validator recorded it."""
    generated = 0
    tokens = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    using_indicators = 0
    indicators = {}
    for call in calls:
        try:
            parsed = json.loads(call.get("raw_response") or "")
        except ValueError:
            parsed = {}
        hypotheses = parsed.get("hypotheses") or []
        generated += len(hypotheses)
        using_indicators += sum(bool(h.get("quoted_values")) for h in hypotheses)
        for key in tokens:
            tokens[key] += int((call.get("model_config") or {}).get(key) or 0)
        indicators[call["task_type"]] = len((call.get("inputs") or {}).get("indicators") or [])
    metrics: dict[str, Any] = {
        "agents_validated": len({c["task_type"] for c in calls}),
        "hypotheses_generated": generated,
        "hypotheses_using_indicators": using_indicators,
        "unsupported_inference_flags": "not checked",
        "evidence_sufficiency_flags": "not checked",
        "bo_fo_boundary_violations": "not checked",
        "citations_recovered": 0,
        "coverage_checks": 0, "coverage_addressed": "not checked",
        "parameters_held_back": 0, "fo_handoffs": 0,
        "model_calls": len(calls), "tokens": tokens, "runtime_seconds": "n/a",
    }
    if recorded_hypotheses is not None:
        by_admissibility: dict[str, int] = {}
        failures = 0
        for h in recorded_hypotheses:
            by_admissibility[h["admissibility"]] = by_admissibility.get(
                h["admissibility"], 0) + 1
            failures += sum("not found verbatim" in n for n in h.get("validation_notes", []))
        metrics.update(
            hypotheses_usable=sum(h["admissibility"] != "audit_only"
                                  for h in recorded_hypotheses),
            by_admissibility=by_admissibility,
            citation_verification_failures=failures, citations_unresolved=failures)
    return metrics


def known_problem_checks(baseline: dict[str, Any] | None, new: dict[str, Any]
                         ) -> list[dict[str, Any]]:
    """Status of generic problem classes, read from the two payloads' data."""
    def hyps(payload):
        return [h for a in payload["agents"] for h in a["hypotheses"]] if payload else []

    def flagged(payload, rule):
        return [h["hypothesis_id"] for h in hyps(payload)
                if any(f["rule_id"] == rule for f in h["review_flags"])]

    def coverage(payload, check_id):
        found = [c for a in (payload or {}).get("agents", []) for c in a["coverage"]
                 if c["check_id"] == check_id]
        return found[0] if found else None

    checks = []
    debt_old, debt_new = (flagged(baseline, "operating_pressure_to_debt_capacity"),
                          flagged(new, "operating_pressure_to_debt_capacity"))
    usable_debt_new = [h["hypothesis_id"] for h in hyps(new)
                       if h["hypothesis_id"] in debt_new and h["admissibility"] != "audit_only"]
    checks.append({
        "problem": "A Business Overview claim concluded on debt service or liquidity",
        "before": f"present in {', '.join(debt_old) or 'none'} and accepted at the time",
        "after": (f"asserted in {', '.join(debt_new)}; held back and handed to FO"
                  if debt_new else "not asserted by any specialist"),
        "resolved": not usable_debt_new})
    old_fail = [r for h in hyps(baseline) for r in h["recovery"]]
    new_fail = [r for h in hyps(new) for r in h["recovery"]]
    checks.append({
        "problem": "Genuine quotations failed verification because of page-layout text",
        "before": f"{len(old_fail)} quote(s) failed exact matching",
        "after": (f"{len(new_fail)} failed exact matching; "
                  f"{sum(r['outcome'] != 'unresolved' for r in new_fail)} recovered from "
                  f"the filing, {sum(r['outcome'] == 'unresolved' for r in new_fail)} unresolved"),
        "resolved": all(r["outcome"] != "unresolved" for r in new_fail)})
    # Every coverage question of the new run, sector-level or case-specific.
    seen: set[str] = set()
    for outcome in [c for a in new["agents"] for c in a["coverage"]]:
        key = outcome["check_id"]
        addressed = any(c["status"] == "addressed" for a in new["agents"]
                        for c in a["coverage"] if c["check_id"] == key)
        if key in seen:
            continue
        seen.add(key)
        before = coverage(baseline, key)
        answers = sorted({h for a in new["agents"] for c in a["coverage"]
                          if c["check_id"] == key
                          for h in c["addressed_by_validated_hypotheses"]})
        checks.append({
            "problem": f"Coverage question '{key}' ({outcome['origin']}): "
                       + outcome["question"][:110],
            "before": ("no validated answer" if before is None
                       or before["status"] != "addressed" else "addressed"),
            "after": (("addressed by " + ", ".join(answers)) if addressed
                      else outcome["status"].replace("_", " "))
                     + f" ({len(outcome['passages_located'])} passage(s) supplied)",
            "resolved": addressed})
    using = [h["hypothesis_id"] for h in hyps(new)
             if h["parameter_result_ids"] and h["admissibility"] != "audit_only"]
    checks.append({
        "problem": "No hypothesis used a deterministic indicator",
        "before": "0 hypotheses bound to an indicator",
        "after": f"{len(using)} usable hypotheses bound to an indicator",
        "resolved": bool(using)})
    held = sum(adj["reason"] in ("value_not_permitted", "label_not_concise")
               for a in new["agents"] for adj in a["parameter_adjustments"])
    sentences = sum(len(str(p["value"] or "").split()) > 6
                    for a in new["agents"] for p in a["parameters"]
                    if p["status"] in ("ok", "provisional"))
    checks.append({
        "problem": "Parameter values were free-text sentences",
        "before": "sentence-length values were accepted",
        "after": f"{sentences} sentence-length values accepted; {held} non-permitted "
                 "values held back",
        "resolved": sentences == 0})
    return checks
