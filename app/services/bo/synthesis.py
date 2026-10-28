"""Deterministic gates around the Business Orchestrator and Business Challenger.

Nothing here calls a model. This module:

* maps rubric-backed categorical parameters to their permitted values, from
  the versioned scoring config;
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
from app.schemas.agentic import ChallengeSeverity
from app.schemas.bo import Admissibility, HypothesisRecord
from app.services.agents.validation import DeterministicValidator
from app.services.bo.hypotheses import score_key_paths, unbound_numbers
from app.services.bo.indicators import IndicatorSet
from app.services.bo.plans import BO_ACTIVE_AGENT_IDS, BO_AGENT_SPECS
from app.services.bo.reconcile import Reconciliation

# Categorical parameter -> (rubric signal, rubric dimension). The rubric tables
# in the versioned scoring config supply each parameter's permitted values.
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


def drop_uncited_claims(parsed: dict[str, Any]) -> list[dict[str, str]]:
    """Remove a listed claim that cites no hypothesis at all (in place).

    A strength, weakness, driver or risk with empty ``hypothesis_ids`` has no
    validated basis, typically a restatement of a debt-specialist or Module 3
    conclusion the orchestrator cannot cite. Dropping it asserts nothing new,
    so it is done here rather than rejecting the whole conclusion. A claim that
    cites unknown or inadmissible hypotheses is left for validation to reject,
    and so is an uncited overall assessment. Returns one adjustment per claim.
    """
    changes = []
    for key in CLAIM_LISTS:
        kept = []
        for claim in parsed.get(key) or []:
            if (isinstance(claim, dict) and claim.get("category") != "limitation"
                    and not claim.get("hypothesis_ids")):
                changes.append({
                    "claim_id": claim.get("claim_id", "?"),
                    "note": (f"Claim {claim.get('title') or claim.get('text', '')[:80]!r} "
                             f"dropped from {key}: it cites no validated hypothesis."),
                })
            else:
                kept.append(claim)
        if key in parsed and isinstance(parsed[key], list):
            parsed[key] = kept
    return changes


def downgrade_overstated_claim_types(
    parsed: dict[str, Any], hypotheses: dict[str, HypothesisRecord],
) -> list[dict[str, str]]:
    """Weaken a fact label that no established hypothesis supports (in place).

    A claim typed ``fact``/``calculated_fact`` whose usable hypotheses are all
    attributed becomes a ``management_claim``; otherwise an ``inference``.
    Weakening a label cannot overstate anything, so it is done here rather
    than rejecting the whole conclusion. Returns one adjustment per change.
    """
    changes = []
    for _, claim in iter_claims(parsed):
        stated = claim.get("claim_type")
        if stated not in ("fact", "calculated_fact"):
            continue
        used = [hypotheses[h] for h in claim.get("hypothesis_ids") or []
                if h in hypotheses and hypotheses[h].usable]
        kinds = {h.admissibility for h in used}
        if not used or Admissibility.ESTABLISHED in kinds:
            continue
        new = ("management_claim" if kinds == {Admissibility.ATTRIBUTED}
               else "inference")
        claim["claim_type"] = new
        changes.append({
            "claim_id": claim.get("claim_id", "?"),
            "note": (f"Claim type {stated} lowered to {new}: it rests only on "
                     f"{sorted(k.value for k in kinds)} hypotheses."),
        })
    return changes


NARRATIVE_ONLY_UNCERTAINTY = (
    "Rests on the company's own narrative disclosure; not independently verified.")


def fill_missing_qualifications(
    parsed: dict[str, Any], hypotheses: dict[str, HypothesisRecord],
) -> list[dict[str, str]]:
    """State the uncertainty a claim resting only on qualified narrative left blank (in place).

    Such a claim must say what is uncertain about it. Adding the standard
    qualification only weakens the claim, so it is done here rather than
    rejecting the whole conclusion. Returns one adjustment per change.
    """
    changes = []
    for _, claim in iter_claims(parsed):
        if (claim.get("uncertainty") or "").strip():
            continue
        used = [hypotheses[h] for h in claim.get("hypothesis_ids") or []
                if h in hypotheses and hypotheses[h].usable]
        if not used or {h.admissibility for h in used} != {Admissibility.QUALIFIED_NARRATIVE}:
            continue
        claim["uncertainty"] = NARRATIVE_ONLY_UNCERTAINTY
        changes.append({
            "claim_id": claim.get("claim_id", "?"),
            "note": ("Standard uncertainty added: the claim rests only on qualified "
                     "narrative hypotheses and stated none."),
        })
    return changes


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
        "Business Overview assigns no score."
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
    # Stylistic repetition and missing uncertainty phrasing are intentionally
    # left to the orchestrator and challenger in this exploratory branch.
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
                               known_parameter_ids: set[str],
                               allowed_agent_ids: set[str] | None = None,
                               owned_parameter_ids: set[str] | None = None) -> list[str]:
    import jsonschema

    try:
        jsonschema.validate(instance=parsed, schema=BO_CHALLENGE_JSON_SCHEMA)
    except jsonschema.ValidationError as exc:
        return [f"schema: {exc.message}"]
    issues = [
        f"official_score_assignment: {path!r}; a challenger never sets a score."
        for path in score_key_paths(parsed)
    ]
    allowed = set(BO_ACTIVE_AGENT_IDS if allowed_agent_ids is None
                  else allowed_agent_ids)
    owned = ({p for spec in BO_AGENT_SPECS for p in spec.owned_parameters}
             if owned_parameter_ids is None else set(owned_parameter_ids))
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
            if agent_id not in allowed:
                issues.append(
                    f"out_of_scope_agent: challenge {cid!r} names {agent_id!r}, "
                    "which is not a Business Overview agent.")
        for scope in item.get("requested_rerun_scope") or []:
            if scope not in allowed and scope not in owned \
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
