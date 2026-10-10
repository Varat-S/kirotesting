"""Deterministic gates around the Business Orchestrator and Business Challenger.

Nothing here calls a model. This module:

* derives the two rubric risk signals the BusinessRiskScore needs from accepted
  categorical parameters, using the EXISTING ``RubricEngine`` and scoring config;
* builds the structured inputs both agents receive (validated artifacts only);
* validates the orchestrator's answer (admissible hypotheses only,
  claim-specific evidence, quote-not-calculate, no score, no double counting);
* validates the challenger's answer and derives the recorded challenge outcome
  from its findings.
"""

from __future__ import annotations

import re
from typing import Any

from app.prompts.bo_prompts import BO_CHALLENGE_JSON_SCHEMA, BO_ORCHESTRATOR_JSON_SCHEMA
from app.schemas.agentic import (
    ChallengeSeverity,
    ParameterResult,
    ParameterStatus,
    RiskScore,
    Topic,
)
from app.schemas.bo import Admissibility, HypothesisRecord
from app.services.agents.validation import DeterministicValidator
from app.services.bo.hypotheses import score_key_paths, unbound_numbers
from app.services.bo.indicators import IndicatorSet
from app.services.bo.plans import BO_ACTIVE_AGENT_IDS, BO_AGENT_SPECS
from app.services.bo.reconcile import Reconciliation, similarity
from app.services.scoring.rubric import RubricError

# Accepted categorical parameter -> (rubric signal, rubric dimension). The
# rubric tables themselves live in the versioned scoring config, unchanged.
RUBRIC_SOURCES: dict[str, tuple[str, str]] = {
    "competitive_position_assessment": ("competitive_position_risk_signal", "market_position"),
    "switching_cost_assessment": ("competitive_position_risk_signal", "switching_costs"),
    "pricing_power_assessment": ("competitive_position_risk_signal", "pricing_power"),
    "competitive_pressure_assessment": ("competitive_position_risk_signal", "competitive_pressure"),
    "management_quality_assessment": ("management_governance_risk_signal", "management_quality"),
    "key_person_dependency_assessment": ("management_governance_risk_signal", "key_person_dependency"),
    "governance_concern_assessment": ("management_governance_risk_signal", "governance_concern"),
}

CLAIM_LISTS = ("strengths", "weaknesses", "key_drivers", "material_risks")
_USABLE_STATUS = (ParameterStatus.OK, ParameterStatus.PROVISIONAL)
_DEFINITIVE = re.compile(
    r"\brecession[- ]proof\b|\bno (?:material |meaningful )?risk\b|\bguarantee[sd]?\b|"
    r"\bwill (?:certainly|always)\b|\bimmune\b|\bshould be approved\b|"
    r"\bcan (?:comfortably )?service (?:its|the) debt\b",
    re.I,
)


def parameter_value_sets(scoring_content: dict[str, Any]) -> dict[str, list[str]]:
    """Allowed categorical values for parameters that feed a rubric dimension."""
    rubrics = scoring_content.get("rubrics", {})
    return {
        parameter_id: sorted(rubrics[signal]["dimensions"][dimension])
        for parameter_id, (signal, dimension) in RUBRIC_SOURCES.items()
        if dimension in rubrics.get(signal, {}).get("dimensions", {})
    }


def rubric_signals(scoring_cfg, parameters: list[ParameterResult], *,
                   analysis_run_id: str) -> tuple[list[ParameterResult], list[str]]:
    """Derive rubric risk signals; an out-of-rubric value is skipped and noted."""
    engine = scoring_cfg.rubric_engine()
    values = parameter_value_sets(scoring_cfg.content)
    grouped: dict[str, dict[str, Any]] = {}
    sources: dict[str, list[str]] = {}
    evidence: dict[str, set[str]] = {}
    notes: list[str] = []
    for p in parameters:
        target = RUBRIC_SOURCES.get(p.parameter_id)
        if target is None or p.status not in _USABLE_STATUS or p.value is None:
            continue
        signal, dimension = target
        if str(p.value) not in values.get(p.parameter_id, []):
            notes.append(
                f"{p.parameter_id}={p.value!r} is not a rubric value for "
                f"{signal}.{dimension}; the dimension was left unscored.")
            continue
        grouped.setdefault(signal, {})[dimension] = p.value
        sources.setdefault(signal, []).append(p.parameter_result_id)
        evidence.setdefault(signal, set()).update(p.evidence_ids)
    results = []
    for signal in sorted(engine.available() & {s for s, _ in RUBRIC_SOURCES.values()}):
        try:
            results.append(engine.score(
                signal, grouped.get(signal, {}), analysis_run_id=analysis_run_id,
                topic=Topic.BUSINESS,
                source_parameter_ids=sorted(sources.get(signal, [])),
                evidence_ids=sorted(evidence.get(signal, set()))))
        except RubricError as exc:  # defensive: values are pre-filtered above
            notes.append(str(exc))
    return results, notes


def score_explanation(score: RiskScore, notes: list[str]) -> dict[str, Any]:
    return {
        "score_id": score.score_id,
        "kind": score.kind.value,
        "status": score.status.value,
        "band": score.band,
        "coverage_weight": score.coverage_weight,
        "contributing_parameter_ids": list(score.contributing_parameter_ids),
        "missing_required_parameter_ids": list(score.missing_required_parameter_ids),
        "critical_missing_parameter_ids": list(score.critical_missing_parameter_ids),
        "components": score.detail.get("components", {}),
        "notes": notes,
        "method": "deterministic",
    }


def hypothesis_view(h: HypothesisRecord) -> dict[str, Any]:
    """The bounded hypothesis fields an agent downstream may read."""
    return {
        "hypothesis_id": h.hypothesis_id,
        "agent_id": h.agent_id,
        "section": h.section,
        "claim": h.claim,
        "claim_type": h.claim_type.value,
        "direction": h.direction.value,
        "disposition": h.disposition.value,
        "admissibility": h.admissibility.value,
        "confidence": h.confidence.value,
        "materiality": h.materiality.value,
        "credit_mechanism": h.credit_mechanism,
        "supporting_evidence_ids": h.supporting_evidence_ids,
        "contradicting_evidence_ids": h.contradicting_evidence_ids,
        "evidence_quotes": h.evidence_quotes,
        "quoted_values": h.quoted_values,
        "missing_information": h.missing_information,
        "corrects_hypothesis_id": h.corrects_hypothesis_id,
        "mitigant": h.mitigant.model_dump(mode="json") if h.mitigant else None,
        "component_hypothesis_ids": h.component_hypothesis_ids,
        "interaction_kind": h.interaction_kind,
        "validation_notes": h.validation_notes,
    }


def iter_claims(parsed: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    claims = []
    if isinstance(parsed.get("overall_assessment"), dict):
        claims.append(("overall_assessment", parsed["overall_assessment"]))
    for key in CLAIM_LISTS:
        claims.extend((key, c) for c in parsed.get(key) or [] if isinstance(c, dict))
    return claims


def validate_orchestrator_output(
    parsed: dict[str, Any],
    *,
    hypotheses: dict[str, HypothesisRecord],
    reconciliation: Reconciliation,
    indicators: IndicatorSet,
    accepted_parameter_ids: set[str],
) -> list[str]:
    """Return the issues that prevent the answer becoming a TopicConclusion."""
    known = indicators.known_values()
    admitted = {e for h in hypotheses.values() for e in h.evidence_ids()}
    outcome = DeterministicValidator(
        admitted_evidence_ids=admitted, known_parameters=known,
    ).validate(parsed, schema=BO_ORCHESTRATOR_JSON_SCHEMA)
    issues = [f"{i.check.value}: {i.detail}" for i in outcome.issues]
    if any(i.check.value == "schema" for i in outcome.issues):
        return issues
    issues += [
        f"official_score_assignment: {path!r} is not permitted; the "
        "BusinessRiskScore is deterministic."
        for path in score_key_paths(parsed)
    ]

    claims = iter_claims(parsed)
    ids = [c.get("claim_id") for _, c in claims]
    for cid in sorted({c for c in ids if ids.count(c) > 1}, key=str):
        issues.append(f"duplicate_claim_id: {cid!r} is used more than once.")

    for _, claim in claims:
        cid = claim.get("claim_id", "?")
        refs = list(claim.get("hypothesis_ids") or [])
        used = []
        for hid in refs:
            h = hypotheses.get(hid)
            if h is None:
                issues.append(f"unknown_hypothesis: claim {cid!r} cites {hid!r}.")
            elif not h.usable:
                issues.append(
                    f"inadmissible_hypothesis: claim {cid!r} relies on {hid!r}, "
                    f"which is {h.disposition.value} ({h.admissibility.value}).")
            else:
                used.append(h)
        if claim.get("category") != "limitation" and not used:
            issues.append(
                f"no_validated_basis: claim {cid!r} rests on no validated hypothesis.")
        allowed_evidence = {e for h in used for e in h.evidence_ids()}
        for eid in claim.get("evidence_ids") or []:
            if eid in admitted and eid not in allowed_evidence:
                issues.append(
                    f"claim_evidence_mismatch: claim {cid!r} cites {eid[:12]}, "
                    "which none of its hypotheses rely on.")
        for prid in claim.get("parameter_result_ids") or []:
            if prid not in accepted_parameter_ids:
                issues.append(
                    f"unaccepted_parameter: claim {cid!r} references {prid!r}.")
        quoted = [float(q["value"]) for q in claim.get("quoted_values") or []
                  if isinstance(q, dict) and isinstance(q.get("value"), (int, float))]
        quotes = [q["quote"] for h in used for q in h.evidence_quotes]
        quoted += [float(q["value"]) for h in used for q in h.quoted_values
                   if isinstance(q.get("value"), (int, float))]
        for token in sorted(set(unbound_numbers(claim.get("text") or "", quoted, quotes))):
            issues.append(
                f"unquoted_number: claim {cid!r} states {token!r} with no "
                "deterministic result or verbatim quote behind it.")
        if used:
            kinds = {h.admissibility for h in used}
            stated = claim.get("claim_type")
            if Admissibility.ESTABLISHED not in kinds and stated in (
                    "fact", "calculated_fact"):
                issues.append(
                    f"overstated_claim_type: claim {cid!r} is presented as "
                    f"{stated} but rests only on "
                    f"{sorted(k.value for k in kinds)} hypotheses.")
            if kinds == {Admissibility.QUALIFIED_NARRATIVE} and not (
                    claim.get("uncertainty") or "").strip():
                issues.append(
                    f"missing_qualification: claim {cid!r} rests only on "
                    "qualified hypotheses but states no uncertainty.")

    # One risk must not be counted twice across the adverse lists.
    adverse = [c for key, c in claims if key in ("weaknesses", "material_risks")]
    for i, a in enumerate(adverse):
        for b in adverse[i + 1:]:
            ha, hb = set(a.get("hypothesis_ids") or []), set(b.get("hypothesis_ids") or [])
            ga = {reconciliation.group_of(h) for h in ha} - {None}
            gb = {reconciliation.group_of(h) for h in hb} - {None}
            if (ha and ha == hb) or (ga & gb):
                issues.append(
                    f"double_counted_risk: claims {a.get('claim_id')!r} and "
                    f"{b.get('claim_id')!r} restate the same finding.")
    texts = [(c.get("claim_id"), c.get("text") or "") for _, c in claims]
    for i, (ida, ta) in enumerate(texts):
        for idb, tb in texts[i + 1:]:
            if similarity(ta, tb) >= 0.8:
                issues.append(
                    f"duplicate_claim_text: claims {ida!r} and {idb!r} repeat "
                    "the same statement.")
    return issues


def conclusion_prechecks(parsed: dict[str, Any],
                         hypotheses: dict[str, HypothesisRecord]) -> list[dict[str, Any]]:
    """Advisory deterministic observations handed to the challenger."""
    notes = []
    for _, claim in iter_claims(parsed):
        cid = claim.get("claim_id")
        match = _DEFINITIVE.search(claim.get("text") or "")
        if match:
            notes.append({"claim_id": cid, "check": "definitive_language",
                          "detail": f"Contains {match.group(0)!r}."})
        used = [hypotheses[h] for h in claim.get("hypothesis_ids") or []
                if h in hypotheses]
        counter = sorted({e for h in used for e in h.contradicting_evidence_ids})
        if counter and not (claim.get("uncertainty") or "").strip():
            notes.append({"claim_id": cid, "check": "counter_evidence_unstated",
                          "detail": "Underlying hypotheses cite counter-evidence "
                                    "but the claim states no uncertainty."})
        unproven = [h.hypothesis_id for h in used if h.mitigant is not None
                    and h.mitigant.effectiveness.value != "demonstrated"]
        if unproven and claim.get("category") in ("strength", "mitigant"):
            notes.append({"claim_id": cid, "check": "unproven_mitigant_as_strength",
                          "detail": f"Relies on unproven mitigant(s) {unproven}."})
    return notes


def validate_challenger_output(parsed: dict[str, Any], *, claim_ids: set[str],
                               admitted_evidence_ids: set[str],
                               known_parameter_ids: set[str]) -> list[str]:
    import jsonschema

    try:
        jsonschema.validate(instance=parsed, schema=BO_CHALLENGE_JSON_SCHEMA)
    except jsonschema.ValidationError as exc:
        return [f"schema: {exc.message}"]
    issues = [
        f"official_score_assignment: {path!r}; a challenger never sets a score."
        for path in score_key_paths(parsed)
    ]
    owned = {p for spec in BO_AGENT_SPECS for p in spec.owned_parameters}
    for item in parsed.get("challenges") or []:
        cid = item.get("challenge_id")
        target = item.get("target")
        if target is not None and target not in claim_ids:
            issues.append(f"unknown_target: challenge {cid!r} targets {target!r}.")
        for eid in item.get("evidence_ids") or []:
            if eid not in admitted_evidence_ids:
                issues.append(
                    f"evidence_exists: challenge {cid!r} cites unadmitted {eid!r}.")
        for agent_id in item.get("affected_agent_ids") or []:
            if agent_id not in BO_ACTIVE_AGENT_IDS:
                issues.append(
                    f"out_of_scope_agent: challenge {cid!r} names {agent_id!r}, "
                    "which is not a Business Overview agent.")
        for scope in item.get("requested_rerun_scope") or []:
            if scope not in BO_ACTIVE_AGENT_IDS and scope not in owned \
                    and scope not in known_parameter_ids:
                issues.append(
                    f"out_of_scope_rerun: challenge {cid!r} requests {scope!r}.")
    return issues


def challenge_outcome(findings, decision) -> str:
    """Recorded outcome of one challenge pass, derived from its findings."""
    if decision.escalated:
        return "human_review_required"
    if decision.requires_rerun or decision.deterministic_only:
        return "rerun_required"
    if any(f.severity is ChallengeSeverity.MEDIUM for f in findings):
        return "accepted_with_qualification"
    return "accepted"
