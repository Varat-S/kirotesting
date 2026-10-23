"""Standalone Module 3 execution: one specialist, Validator 7, governed outputs.

A minimal, reusable OFFLINE pathway that runs Module 3 (Corporate Structure &
Senior Management) on its own:

    case-associated evidence
        -> m3_corporate_management specialist (single pass)
           + deterministic corporate-structure processing
        -> Validator 7 (``validate_m3_assessment``)
        -> governed Module 3 JSON deliverables (A de-wrapped + B + sidecar).

It mirrors :func:`app.services.bo.single_specialist.run_m1s2` but targets
``V3_SPEC_BY_CODE['M3']`` and invokes ONLY the Module 3 specialist and its
applicable validation. It NEVER calls any other BO specialist, Agent 6, the
orchestrator, challenger, synthesis, scoring, memo, handoff or any downstream /
cross-module agent, and it is not a general-purpose orchestration framework.

Nothing is fabricated: when no valid case-associated corporate-structure JSON is
admitted, Deliverable A fails closed to the mandated ``unavailable`` artifact
(verbatim :data:`NO_JSON_MESSAGE`) while the senior-management assessment still
proceeds from admitted narrative evidence when supported. A wrong case
association fails closed. The run is an OFFLINE SIMULATION when a scripted
backend is used; the manifest records whether a model was called.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config_registry import ConfigRegistry
from app.models.orm import AgenticAnalysisRun as RunRow, EvidencePacketRow
from app.schemas.bo_m3_structure import CORP_STRUCTURE_TRANSFORM_VERSION
from app.services.agents.registry import default_registry
from app.services.audit.log import ActorType, AuditLog
from app.services.bo.comparison import prepare_specialist, persist_arm, run_single_pass
from app.services.bo.completion import (
    admit_corporate_structure_source,
    build_m3_corporate_structure,
)
from app.services.bo.m3_artifacts import build_m3_deliverables
from app.services.bo.m3_output import assemble_m3_output
from app.services.bo.m3_structure_context import build_structural_facts
from app.services.bo.m3_validation import validate_m3_assessment
from app.services.bo.roster_v3 import ROSTER_V3_VERSION, V3_SPEC_BY_CODE


M3_STANDALONE_POLICY_VERSION = "bo-m3-standalone-1.0"


@dataclass
class M3StandaloneResult:
    """The governed, inspectable output of a standalone Module 3 run."""

    case_id: str
    analysis_run_id: str
    packet_hash: str
    # Validator 7 governed assessment (model_dump) or None when the specialist
    # produced no m3_assessment proposal.
    governed_assessment: dict[str, Any] | None
    # Deliverable A export (de-wrapped v2.0 at root; None when unavailable),
    # Deliverable B analytical record, and the governed structure sidecar.
    m3_corporate_structure: dict[str, Any] | None
    m3_analysis: dict[str, Any]
    m3_structure_sidecar: dict[str, Any] | None
    # The full three-section M3 output (section A/B/C + completion).
    m3_output: dict[str, Any]
    corporate_structure_status: str
    completion_status: str
    validation_issues: list[dict[str, Any]] = field(default_factory=list)
    rejected_items: list[dict[str, Any]] = field(default_factory=list)
    specialist_validated: bool = False
    specialist_failure: str | None = None
    model_called: bool = False
    run_manifest: dict[str, Any] = field(default_factory=dict)


def run_m3(
    session: Session,
    *,
    case_id: str,
    evidence,
    metrics,
    m3_responses: dict[str, Any],
    corporate_structure_document: dict[str, Any] | None = None,
    corporate_structure_source_document_id: str = "corp-onboarding-1",
    transaction_borrower_entity_id: str | None = None,
    sector: str = "auto",
) -> M3StandaloneResult:
    """Run ONLY Module 3 (specialist + Validator 7) and build the governed JSON.

    ``m3_responses`` is the scripted response map, mirroring the single-pass arm
    of the controlled runner: ``m3_responses['single_pass']`` is the scripted
    M3 specialist response (hypotheses + parameters + an ``m3_assessment``).

    When ``corporate_structure_document`` is provided it is admitted for THIS
    case via :func:`admit_corporate_structure_source` (explicit case association,
    content hash) and attached to the specialist call inputs under
    ``corporate_structure_source`` so the deterministic structure builder can
    read it; otherwise Deliverable A fails closed to the unavailable artifact.
    """
    spec = V3_SPEC_BY_CODE["M3"]
    prep = prepare_specialist(
        session, case_id=case_id, evidence=evidence, metrics=metrics, spec=spec,
        sector=sector, run_identity=M3_STANDALONE_POLICY_VERSION)

    # --- Admit the (optional) case-associated corporate-structure document ---
    structure_source: dict[str, Any] | None = None
    if corporate_structure_document is not None:
        structure_source = admit_corporate_structure_source(
            corporate_structure_document, case_id=case_id,
            source_document_id=corporate_structure_source_document_id)

    registry = default_registry("v3")
    scoring = ConfigRegistry(session).latest("scoring")
    if scoring is None:
        raise RuntimeError("Scoring configuration was not bootstrapped.")

    model_configuration = {
        "scope": "M3_only",
        "execution_mode": "offline_simulation",
        "runner_policy_version": M3_STANDALONE_POLICY_VERSION,
        "roster_version": ROSTER_V3_VERSION,
        "transform_version": CORP_STRUCTURE_TRANSFORM_VERSION,
        "validator_id": spec.validator_id,
        "packet_hash": prep.packet.packet_hash,
    }
    row = RunRow(
        analysis_run_id=prep.analysis_run_id, case_id=case_id,
        evidence_snapshot_version=prep.snapshot_version, status="created",
        analysis_mode="agentic_bo_m3_standalone", started_at=datetime.now(timezone.utc),
        router_version=prep.packet.router_version,
        router_hash=prep.packet.packet_hash,
        agent_registry_version=registry.version,
        agent_registry_hash=registry.registry_hash(),
        scoring_config_version=scoring.version,
        scoring_config_hash=scoring.content_hash,
        config_versions=dict(evidence.config_versions),
        model_configuration=model_configuration)
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
        evidence_ids=list(prep.packet.evidence_ids)))
    session.flush()

    # --- CRITICAL ORDERING FIX: build + validate the corporate structure and
    #     inject it into the specialist's input context BEFORE the pass runs. --
    # The structure is built from the admitted, case-associated source (not from
    # a synthetic record created after the fact). Its `status` is set to
    # "validated" provisionally only so the structure builder reads the admitted
    # source; structure validity is independent of the specialist's own
    # validation (re-derived below) and never feeds the structure graph.
    structure_calls = [{"inputs": (
        {"corporate_structure_source": structure_source}
        if structure_source is not None else {})}]
    pre_structure_agent_calls = [{
        "agent_id": "m3_corporate_management", "code": "M3",
        "status": "validated", "coverage": [], "calls": structure_calls,
    }]
    structure_artifact, structure_svg = build_m3_corporate_structure(
        pre_structure_agent_calls, case_id=case_id)

    # Compact, provenance-preserving representation (validated nodes + links with
    # stable ids, borrower, guarantor indicators, grouping classifications,
    # source doc id/hash, simulation status, deterministic observations). None
    # when no structure is available -> no invented facts enter the context.
    structural_facts = build_structural_facts(structure_artifact)
    prep = replace(prep, structural_facts=structural_facts)

    # --- The ONE specialist call (single pass; scripted offline) -------------
    # The specialist now genuinely has the validated structure in its input
    # context (via prep.structural_facts -> _single_pass_inputs) when reasoning.
    arm = run_single_pass(session, prep, dict(m3_responses["single_pass"]))

    def _is_offline_call(call: dict[str, Any]) -> bool:
        config = call.get("model_config") or {}
        return bool(config.get("simulation")) or config.get("backend") == "fake"

    # A model was called only if some recorded call did not come from an offline
    # fake/scripted backend. The standalone path is offline by design.
    model_called = any(not _is_offline_call(c) for c in arm.calls)
    specialist_validated = bool(arm.validation and arm.validation.valid)
    if arm.validation is not None:
        persist_arm(session, prep, arm)

    # --- Validator 7 over the m3_assessment proposal (no live model) ---------
    allowed = set(prep.packet.evidence_ids)
    evidence_texts = {e: prep.passage_text[e] for e in allowed
                      if e in prep.passage_text}
    governed_assessment: dict[str, Any] | None = None
    validation_issues: list[dict[str, Any]] = []
    rejected_items: list[dict[str, Any]] = []
    proposal = (arm.parsed or {}).get("m3_assessment")
    if isinstance(proposal, dict):
        governed = validate_m3_assessment(
            proposal, allowed_evidence_ids=allowed,
            validated_borrower_entity_ids=(
                {transaction_borrower_entity_id}
                if transaction_borrower_entity_id else set()),
            evidence_texts=evidence_texts,
            structure_facts=structural_facts)
        governed_assessment = governed.model_dump(mode="json")
        governed_assessment["replay_provenance"] = {
            "kind": "offline_deterministic_standalone_validation",
            "model_called": model_called,
            "source_analysis_run_id": prep.analysis_run_id,
            "transaction_borrower_entity_ids": (
                [transaction_borrower_entity_id]
                if transaction_borrower_entity_id else []),
        }
        validation_issues = list(governed_assessment.get("validation_issues") or [])
        rejected_items = list(governed_assessment.get("rejected_items") or [])

    # NOTE: the corporate-structure artifact + SVG were already built ABOVE,
    # before the specialist pass, and injected into prep.structural_facts. The
    # structure graph is independent of the specialist's own validation status,
    # so it is NOT rebuilt here.

    # --- Assemble sections + build the two linked deliverables ---------------
    m3_output = assemble_m3_output(
        structure=structure_artifact, governed_m3=governed_assessment,
        structure_svg=structure_svg)
    deliverables = build_m3_deliverables(
        structure_artifact, governed_assessment, m3_output)

    # --- Finalise the run row ------------------------------------------------
    completion_status = m3_output["completion"]["status"]
    row.status = "completed" if specialist_validated else "partially_failed"
    row.completed_at = datetime.now(timezone.utc)
    row.failure_state = arm.failure
    usage = arm.usage()
    if usage["input_tokens"] or usage["output_tokens"]:
        row.total_input_tokens = usage["input_tokens"]
        row.total_output_tokens = usage["output_tokens"]
        row.total_tokens = usage["input_tokens"] + usage["output_tokens"]

    manifest = {
        "document": "bo_m3_standalone_run",
        "version": M3_STANDALONE_POLICY_VERSION,
        "case_id": case_id,
        "analysis_run_id": prep.analysis_run_id,
        "scope": "M3_only",
        "invoked_specialist_agent_id": spec.agent_id,
        "invoked_specialist_code": spec.code,
        "validator_id": spec.validator_id,
        "cross_module_or_downstream_invoked": False,
        "evidence": {
            "snapshot_version": prep.snapshot_version,
            "packet_hash": prep.packet.packet_hash,
            "packet_evidence_ids": list(prep.packet.evidence_ids),
        },
        "source_documents": {
            "corporate_structure": (
                {
                    "source_document_id": structure_source["source_document_id"],
                    "document_hash": structure_source["document_hash"],
                    "registered_case_id": structure_source["registered_case_id"],
                }
                if structure_source is not None else None),
        },
        "versions": {
            "roster_version": ROSTER_V3_VERSION,
            "transform_version": CORP_STRUCTURE_TRANSFORM_VERSION,
            "agent_registry_version": registry.version,
            "agent_registry_hash": registry.registry_hash(),
            "scoring_config_version": scoring.version,
            "prompt_agent_id": spec.agent_id,
        },
        "execution": {
            "execution_mode": "offline_simulation",
            "model_called": model_called,
            "model_id": (arm.calls[-1].get("model_id") if arm.calls else None),
            "specialist_validated": specialist_validated,
            "specialist_failure": arm.failure,
            # Auditability: whether the validated corporate structure was present
            # in the specialist's ACTUAL input context before the pass ran.
            "structure_in_specialist_context": structural_facts is not None,
        },
        "validation": {
            "specialist_valid": specialist_validated,
            "m3_assessment_present": governed_assessment is not None,
            "validation_issue_count": len(validation_issues),
            "rejected_item_count": len(rejected_items),
            "completion_status": completion_status,
            "requires_human_review": True,
            "forced_outcome": False,
        },
        "artifacts": {
            "corporate_structure_status": structure_artifact.status,
            "structure_artifact_id": (deliverables["m3_structure_sidecar"] or {}).get(
                "artifact_id"),
            "analysis_schema_version": deliverables["m3_analysis"].get("schema_version"),
        },
    }

    AuditLog(session).record(
        "bo_m3_standalone_executed", case_id=case_id, actor_type=ActorType.SYSTEM,
        actor_id="M3StandaloneRunner", after={
            "analysis_run_id": prep.analysis_run_id,
            "packet_hash": prep.packet.packet_hash,
            "corporate_structure_status": structure_artifact.status,
            "completion_status": completion_status,
            "model_called": model_called,
            "cross_module_or_downstream_invoked": False,
        },
        reason="Standalone Module 3 execution (specialist + Validator 7 only).",
        linked_objects=[prep.analysis_run_id, prep.packet.packet_hash])
    session.flush()

    return M3StandaloneResult(
        case_id=case_id,
        analysis_run_id=prep.analysis_run_id,
        packet_hash=prep.packet.packet_hash,
        governed_assessment=governed_assessment,
        m3_corporate_structure=deliverables["m3_corporate_structure"],
        m3_analysis=deliverables["m3_analysis"],
        m3_structure_sidecar=deliverables["m3_structure_sidecar"],
        m3_output=m3_output,
        corporate_structure_status=structure_artifact.status,
        completion_status=completion_status,
        validation_issues=validation_issues,
        rejected_items=rejected_items,
        specialist_validated=specialist_validated,
        specialist_failure=arm.failure,
        model_called=model_called,
        run_manifest=manifest)
