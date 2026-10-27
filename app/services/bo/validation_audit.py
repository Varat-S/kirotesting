"""One specialist's validation, under its own validator identity and audit record.

``validate_specialist_output`` applies the same deterministic sequence the
runner applies to a narrow agent (schema/ownership/evidence gate, per-hypothesis
validation, parameter gate, promotion gate) and returns everything needed to
persist or compare it. ``record_validation`` writes the validator's audit event.

Validation code is shared across agents; the validator IDENTITY is not. Each of
the eight primary agents (and Agent 6's governance) writes its own
``agent_output_validated`` event naming its validator id and version, bound to
that agent's sealed packet hash. Nothing here calls a model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.prompts.bo_prompts import BO_NARROW_JSON_SCHEMA
from app.schemas.agentic import ParameterResult
from app.schemas.bo import HypothesisRecord
from app.services.orchestration.promotion import (
    PromotionRejected,
    ValidatedAgentOutput,
    promote_narrow_output,
)
from app.services.agents.validation import DeterministicValidator
from app.services.audit.log import AuditLog, EventType
from app.services.bo.hypotheses import (
    HYPOTHESIS_VALIDATOR_VERSION,
    HypothesisValidator,
    gate_parameters,
    validator_identity,
)
from app.services.bo.indicators import IndicatorSet
from app.services.bo.plans import BoAgentSpec


@dataclass
class SpecialistValidation:
    spec_code: str
    agent_id: str
    validator_id: str
    validator_version: str
    packet_hash: str | None
    valid: bool
    issues: list[str] = field(default_factory=list)
    records: list[HypothesisRecord] = field(default_factory=list)
    adjustments: list[dict[str, str]] = field(default_factory=list)
    parameter_changes: list[dict[str, Any]] = field(default_factory=list)
    parameters: list[ParameterResult] = field(default_factory=list)

    def audit_payload(self) -> dict[str, Any]:
        return {
            "validator_id": self.validator_id,
            "validator_version": self.validator_version,
            "agent_id": self.agent_id,
            "spec_code": self.spec_code,
            "packet_hash": self.packet_hash,
            "valid": self.valid,
            "issues": list(self.issues),
            "hypotheses": [
                {"hypothesis_id": r.hypothesis_id, "disposition": r.disposition.value,
                 "admissibility": r.admissibility.value,
                 "presentation": r.display_priority.value,
                 "presentation_rule": r.presentation_basis.get("rule")}
                for r in self.records],
            "parameters": [
                {"parameter_id": p.parameter_id, "status": p.status.value,
                 "value": p.value} for p in self.parameters],
            "parameter_changes": list(self.parameter_changes),
        }


def validate_specialist_output(
    spec: BoAgentSpec, agent, packet, parsed: dict[str, Any], *,
    indicators: IndicatorSet, passage_text: dict[str, str],
    corpus: list[dict[str, Any]], value_sets: dict[str, list[str]],
    analysis_run_id: str, agent_run_id: str, prompt_id: str | None = None,
    primary: dict[str, HypothesisRecord] | None = None,
    apply_presentation_policy: bool = True, materiality_basis: str | None = None,
) -> SpecialistValidation:
    """Deterministic validation of one specialist output (no persistence)."""
    outcome = DeterministicValidator.for_agent(
        packet, agent, known_parameters=indicators.known_values(),
    ).validate(parsed, schema=BO_NARROW_JSON_SCHEMA)
    issues = [f"{i.check.value}: {i.detail}" for i in outcome.issues]
    allowed = set(packet.evidence_ids)
    validator = HypothesisValidator(
        spec, passages={e: t for e, t in passage_text.items() if e in allowed},
        indicators=indicators, primary_hypotheses=primary, corpus=corpus,
        apply_presentation_policy=apply_presentation_policy,
        materiality_basis=materiality_basis)
    validation = validator.validate(parsed, analysis_run_id=analysis_run_id,
                                    agent_run_id=agent_run_id)
    issues += validation.issues
    result = SpecialistValidation(
        spec_code=spec.code, agent_id=spec.agent_id,
        validator_id=validator_identity(spec),
        validator_version=HYPOTHESIS_VALIDATOR_VERSION,
        packet_hash=packet.packet_hash, valid=not issues, issues=issues,
        records=validation.records, adjustments=validation.adjustments)
    if issues:
        result.records = []
        return result
    if spec.code == "M3":
        # The application sealed these separately labelled case assumptions;
        # never label their quotations as verified actual company disclosure.
        from app.schemas.bo import Admissibility, Disposition
        case_ids = {p["evidence_id"] for p in packet.narrative_evidence
                    if p.get("evidence_category") == "hypothetical_case_assumptions"}
        for record in result.records:
            citations = set(record.supporting_evidence_ids) | {q["evidence_id"] for q in record.evidence_quotes}
            if citations & case_ids:
                record.evidence_basis = ("hypothetical_case_assumptions" if citations <= case_ids
                                         else "mixed_case_and_filing_evidence")
                if record.usable:
                    record.disposition = Disposition.QUALIFIED
                    record.admissibility = Admissibility.QUALIFIED_NARRATIVE
                    record.review_required = True
                record.validation_notes.append("Scenario assumptions only; no real-world legal verification is implied.")
    gated, result.parameter_changes = gate_parameters(
        parsed, validation.records, value_sets=value_sets, primary=primary)
    if spec.code == "M3":
        from app.services.bo.m3_grounding import gate_m3_parameters
        gated, m3_changes = gate_m3_parameters(
            gated, validation.records,
            evidence_texts={e: passage_text[e] for e in allowed if e in passage_text})
        result.parameter_changes.extend(m3_changes)
    if prompt_id is not None:
        try:
            validated = ValidatedAgentOutput.gate(
                agent, gated, outcome, analysis_run_id=analysis_run_id,
                agent_run_id=agent_run_id, prompt_id=prompt_id,
                input_hash=packet.packet_hash)
            result.parameters = list(promote_narrow_output(validated))
        except PromotionRejected as exc:
            result.valid = False
            result.issues.append(str(exc))
            result.records = []
    return result


def record_validation(audit: AuditLog, validation: SpecialistValidation, *,
                      case_id: str | None, agent_run_id: str) -> None:
    """Append the validator's own audit event for this agent output."""
    audit.record(
        EventType.AGENT_OUTPUT_VALIDATED, case_id=case_id,
        actor_id=validation.validator_id,
        after=validation.audit_payload(),
        reason=("validated" if validation.valid else "rejected"),
        linked_objects=[agent_run_id, validation.packet_hash])
