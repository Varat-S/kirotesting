"""Controlled v3 M1S2 execution through ``BusinessOverviewRunner``.

This is intentionally limited to one specialist.  It proves mode identity,
sealed-packet execution, validation, persistence, failure propagation and the
Agent 6 boundary without changing the default v2 six-agent run.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config_registry import ConfigRegistry
from app.models.orm import AgenticAnalysisRun as RunRow, EvidencePacketRow
from app.services.agents.registry import default_registry
from app.services.audit.log import ActorType, AuditLog
from app.services.bo.comparison import (
    ArmResult,
    persist_arm,
    prepare_specialist,
    run_hybrid,
    run_investigation,
    run_single_pass,
)
from app.services.bo.hybrid import HYBRID_SELECTION_POLICY_VERSION, InvestigationMode
from app.services.bo.investigation import INVESTIGATION_VERSION
from app.services.bo.ownership import OWNERSHIP_POLICY_VERSION
from app.services.bo.roster_v3 import ROSTER_V3_VERSION, V3_SPEC_BY_CODE
from app.services.bo.semantic_entailment import SEMANTIC_ENTAILMENT_POLICY_VERSION
from app.services.orchestration.promotion import ParameterPromoter


M1S2_RUNNER_POLICY_VERSION = "bo-m1s2-runner-1"


@dataclass(frozen=True)
class Agent6Eligibility:
    eligible: bool
    status: str
    reasons: tuple[str, ...]
    hypothesis_ids: tuple[str, ...]


@dataclass
class SingleSpecialistResult:
    mode: InvestigationMode
    arm: ArmResult
    analysis_run_id: str
    packet_hash: str
    complete: bool
    completeness_status: str
    agent6: Agent6Eligibility
    metadata: dict[str, Any]

    @property
    def records(self):
        return list(self.arm.validation.records if self.arm.validation else [])

    def agent6_payload(self) -> list[dict[str, Any]]:
        if not self.agent6.eligible:
            return []
        return [record.model_dump(mode="json") for record in self.records if record.usable]


def agent6_eligibility(*, records, validation_valid: bool, complete: bool,
                       failure: str | None) -> Agent6Eligibility:
    reasons = []
    if failure:
        reasons.append("specialist_execution_failed")
    if not validation_valid:
        reasons.append("specialist_validation_failed")
    if not complete:
        reasons.append("specialist_completeness_not_satisfied")
    usable = tuple(r.hypothesis_id for r in records if r.usable)
    if not usable:
        reasons.append("no_usable_validated_findings")
    return Agent6Eligibility(not reasons, "eligible" if not reasons else "restricted",
                             tuple(reasons), usable if not reasons else ())


def _completeness(arm: ArmResult, required_checks: list) -> tuple[bool, str]:
    if arm.failure or arm.validation is None or not arm.validation.valid:
        return False, "failed_or_invalid"
    if required_checks:
        responses = {r.get("check_id"): r for r in
                     ((arm.parsed or {}).get("checklist_responses") or [])
                     if isinstance(r, dict)}
        missing = [c.check_id for c in required_checks
                   if responses.get(c.check_id, {}).get("status") not in
                   {"addressed", "not_supported_by_evidence"}]
        if missing:
            return False, "mandatory_coverage_unresolved:" + ",".join(missing)
    assessment = getattr(arm.investigation, "completeness", None)
    if assessment is None:
        # The single-pass path has no stage-12 proof and remains an explicitly
        # reviewable draft in this experimental v3 runner.
        return False, "stage_12_not_run"
    progression = getattr(getattr(assessment, "progression", None), "value", "unknown")
    gaps = getattr(assessment, "material_gaps", []) or []
    complete = progression == "ready_for_red_challenge" and not gaps
    return complete, progression


def run_m1s2(
    session: Session,
    *,
    case_id: str,
    evidence,
    metrics,
    mode: InvestigationMode,
    responses: dict[str, Any],
    coverage_checks=(),
    sector="auto",
) -> SingleSpecialistResult:
    spec = V3_SPEC_BY_CODE["M1S2"]
    prep = prepare_specialist(
        session, case_id=case_id, evidence=evidence, metrics=metrics, spec=spec,
        coverage_checks=coverage_checks, sector=sector,
        run_identity=f"{M1S2_RUNNER_POLICY_VERSION}:{mode.value}")
    registry = default_registry("v3")
    scoring = ConfigRegistry(session).latest("scoring")
    if scoring is None:
        raise RuntimeError("Scoring configuration was not bootstrapped.")
    row = RunRow(
        analysis_run_id=prep.analysis_run_id, case_id=case_id,
        evidence_snapshot_version=prep.snapshot_version, status="created",
        analysis_mode="agentic_bo_m1s2_experiment", started_at=datetime.now(timezone.utc),
        router_version=prep.packet.router_version,
        router_hash=prep.packet.packet_hash,
        agent_registry_version=registry.version,
        agent_registry_hash=registry.registry_hash(),
        scoring_config_version=scoring.version,
        scoring_config_hash=scoring.content_hash,
        config_versions=dict(evidence.config_versions),
        model_configuration={
            "scope": "M1S2_only", "execution_mode": mode.value,
            "runner_policy_version": M1S2_RUNNER_POLICY_VERSION,
            "roster_version": ROSTER_V3_VERSION,
            "investigation_version": INVESTIGATION_VERSION,
            "selection_policy_version": HYBRID_SELECTION_POLICY_VERSION,
            "ownership_policy_version": OWNERSHIP_POLICY_VERSION,
            "semantic_policy_version": SEMANTIC_ENTAILMENT_POLICY_VERSION,
            "packet_hash": prep.packet.packet_hash,
        })
    session.add(row)
    session.flush()
    session.add(EvidencePacketRow(
        packet_hash=prep.packet.packet_hash,
        analysis_run_id=prep.analysis_run_id,
        case_id=case_id,
        snapshot_version=prep.snapshot_version,
        router_version=prep.packet.router_version,
        agent_id=prep.spec.agent_id,
        payload=prep.packet.model_dump(mode="json"),
        evidence_ids=list(prep.packet.evidence_ids),
    ))
    session.flush()

    if mode is InvestigationMode.SINGLE_PASS:
        arm = run_single_pass(session, prep, responses["single_pass"])
    elif mode is InvestigationMode.FULL:
        arm = run_investigation(session, prep, responses["investigation"],
                                arm_name="full_investigation")
    else:
        arm = run_hybrid(session, prep, broad_response=responses["single_pass"],
                         investigation_responses=responses["investigation"])

    if arm.validation is not None:
        persist_arm(session, prep, arm)
        promoter = ParameterPromoter(session)
        for parameter in arm.validation.parameters:
            promoter.persist(parameter, case_id=case_id,
                             snapshot_version=prep.snapshot_version)
    complete, completeness_status = _completeness(arm, prep.checks)
    records = list(arm.validation.records if arm.validation else [])
    eligibility = agent6_eligibility(
        records=records,
        validation_valid=bool(arm.validation and arm.validation.valid),
        complete=complete, failure=arm.failure)
    row.status = "completed" if complete else "partially_failed"
    row.completed_at = datetime.now(timezone.utc)
    row.failure_state = arm.failure or (None if complete else completeness_status)
    usage = arm.usage()
    if usage["input_tokens"] or usage["output_tokens"]:
        row.total_input_tokens = usage["input_tokens"]
        row.total_output_tokens = usage["output_tokens"]
        row.total_tokens = usage["input_tokens"] + usage["output_tokens"]
    metadata = {
        **row.model_configuration,
        "analysis_run_id": prep.analysis_run_id,
        "packet_evidence_ids": list(prep.packet.evidence_ids),
        "candidate_ids": [r.hypothesis_id for r in records],
        "lineage": arm.metadata.get("lineage", []),
        "stages": [s.__dict__ for s in (arm.investigation.stages
                                         if arm.investigation else [])],
        "semantic_judgements": list(arm.investigation.judgements
                                    if arm.investigation else []),
        "completeness_status": completeness_status,
        "complete": complete,
        "agent6": eligibility.__dict__,
        "usage": {**usage, "cost": "not_measured", "latency": "not_measured"},
        "failure": arm.failure,
    }
    AuditLog(session).record(
        "bo_m1s2_mode_executed", case_id=case_id, actor_type=ActorType.SYSTEM,
        actor_id="BusinessOverviewRunner", after=metadata,
        reason="Controlled single-specialist v3 execution.",
        linked_objects=[prep.analysis_run_id, prep.packet.packet_hash,
                        *[r.hypothesis_id for r in records]])
    session.flush()
    return SingleSpecialistResult(mode, arm, prep.analysis_run_id,
                                  prep.packet.packet_hash, complete,
                                  completeness_status, eligibility, metadata)
