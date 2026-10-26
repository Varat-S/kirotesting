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
from app.services.bo.m3_diagnostics import ensure_local_store, record_final_validation
from app.services.bo.m3_grounding import M3_GROUNDING_VERSION, strengthen_m3_assessment
from app.services.bo.m3_output import assemble_m3_output
from app.services.bo.m3_structure_context import build_structural_facts
from app.services.bo.m3_validation import M3AssessmentError, validate_m3_assessment
from app.services.bo.roster_v3 import ROSTER_V3_VERSION, V3_SPEC_BY_CODE


M3_STANDALONE_POLICY_VERSION = "bo-m3-standalone-1.4"


def _canonical_metrics_from_prep(prep) -> dict[str, dict[str, Any]]:
    """Build Validator 7's canonical-metric verification map from the prepared
    deterministic indicators (ParameterResult + their CalculationTrace).

    Reuses the repo's canonical calculation layer; it never recomputes a metric.
    Keyed by the canonical ``metric_definition_id`` (the indicator parameter_id).
    A ratio-unit value is ALSO exposed as a percentage so a claim phrased in per
    cent ("12%") can be compared to a stored ratio (0.12) within tolerance. Only
    usable numeric indicators are included; nothing is fabricated.
    """
    out: dict[str, dict[str, Any]] = {}
    for trace in prep.indicators.traces:
        value = trace.value
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        units = trace.units
        # Expose a percentage view for ratio metrics so per-cent claims verify.
        display_value = value * 100 if units == "ratio" else value
        out[trace.parameter_id] = {
            "value": display_value,
            "raw_value": value,
            "units": units,
            "period": trace.period,
            "accounting_basis": None,
            "currency": None,
            "reporting_scope": "consolidated",
            "formula_id": trace.formula_id,
            "definition_version": trace.definition_version,
            "calculation_state": trace.calculation_state,
        }
    return out


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
    validation_stages: dict[str, Any] = field(default_factory=dict)


def run_m3(
    session: Session,
    *,
    case_id: str,
    evidence,
    metrics,
    m3_responses: dict[str, Any] | None = None,
    corporate_structure_document: dict[str, Any] | None = None,
    corporate_structure_source_document_id: str = "corp-onboarding-1",
    transaction_borrower_entity_id: str | None = None,
    sector: str = "auto",
    live_backend=None,
    live_config: dict[str, Any] | None = None,
    management_window_years: int = 3,
    required_assessment_version: str | None = None,
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

    OPT-IN REAL-MODEL PATHWAY (Priority 3). The standalone path is OFFLINE by
    default (``live_backend=None`` -> scripted ``m3_responses``). Passing a
    provider ``live_backend`` (an :class:`~app.services.llm.client.LLMBackend`)
    switches to a bounded, single-execution LIVE run of ONLY the
    ``m3_corporate_management`` specialist:

    * the SAME admitted evidence packet and VALIDATED structural facts are built
      and injected into the specialist context BEFORE it reasons (ordering
      preserved), and the canonical financial map reaches Validator 7;
    * exactly one specialist execution is made (no retries unless an explicitly
      bounded ``live_config['max_retries']`` is configured); timeout + token
      limits come from the provider's own ``Settings`` (never hardcoded);
    * the response is schema-validated by :class:`LLMClient` before use; an
      invalid response fails SAFELY (``partially_failed``) with NO fabricated
      findings and NO silent fallback to the scripted path;
    * the run manifest records ``execution_mode`` and ``model_called`` from the
      ACTUAL (non-fake) call, plus model id + prompt version + source-snapshot
      metadata; credentials are never logged or persisted (``SecretStr``).

    No OTHER module/agent (Modules 1/2/4, synthesizer, challenger, scoring,
    renderer) is ever invoked on either path. This function does not itself make
    a paid call; the caller supplies a configured provider backend.
    """
    if isinstance(management_window_years, bool) or not isinstance(management_window_years, int) or management_window_years < 1:
        raise ValueError("management_window_years must be a positive integer (default three years)")
    ensure_local_store(session)
    live_mode = live_backend is not None
    if not live_mode and m3_responses is None:
        raise ValueError(
            "run_m3 requires either a scripted m3_responses map (offline) or a "
            "live_backend (opt-in real-model path); neither was supplied.")
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

    execution_mode = "live" if live_mode else "offline_simulation"
    model_configuration = {
        "m3_grounding_policy": M3_GROUNDING_VERSION,
        "m3_validation_revision": "bo-m3-v14-correction-1.0",
        "management_stability_policy": "bo-m3-management-stability-1.0",
        "management_recent_change_window_years": management_window_years,
        "scope": "M3_only",
        "execution_mode": execution_mode,
        "runner_policy_version": M3_STANDALONE_POLICY_VERSION,
        "roster_version": ROSTER_V3_VERSION,
        "transform_version": CORP_STRUCTURE_TRANSFORM_VERSION,
        "validator_id": spec.validator_id,
        "packet_hash": prep.packet.packet_hash,
    }
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
    prep = replace(prep, structural_facts=structural_facts, record_model_run=True,
                   management_window_years=management_window_years,
                   m3_case_context={"validated_borrower_entity_ids": (
                       [transaction_borrower_entity_id] if transaction_borrower_entity_id else [])})

    from app.services.bo.m3_case_evidence import attach_case_evidence
    prep = attach_case_evidence(prep)
    # Explicit request contract, independent of backend type, preserves legacy
    # API replays. The live standalone CLI requests 1.2 explicitly.
    prep = replace(prep, required_m3_version=required_assessment_version)
    model_configuration["required_assessment_version"] = required_assessment_version
    structural_facts = prep.structural_facts
    model_configuration["packet_hash"] = prep.packet.packet_hash
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

    # --- The ONE specialist call (single pass) -------------------------------
    # The specialist genuinely has the validated structure in its input context
    # (via prep.structural_facts -> _single_pass_inputs) when reasoning. Offline:
    # a scripted response drives a deterministic FakeLLMBackend inside
    # run_single_pass. Live (opt-in): the configured provider backend is used
    # exactly ONCE; there is NO silent fallback to the scripted path on failure.
    if live_mode:
        arm = run_single_pass(session, prep, backend=live_backend)
    else:
        arm = run_single_pass(session, prep, dict(m3_responses["single_pass"]))

    def _is_offline_call(call: dict[str, Any]) -> bool:
        config = call.get("model_config") or {}
        if bool(config.get("simulation")) or config.get("backend") == "fake":
            return True
        # A FakeLLMBackend that raised (no model_config) still is not a real
        # model; a recorded call with no model_id (the backend raised before
        # returning) or a fake model id is never a real invocation.
        model_id = str(call.get("model_id") or "fake")
        return model_id.startswith("fake")

    # A model was called only if some recorded call did not come from an offline
    # fake/scripted backend. The offline path is scripted by design; the live
    # path records the actual provider identity on each call. A real model is
    # counted ONLY when a genuine (non-fake) provider answered.
    model_called = ((live_mode and arm.metadata.get("diagnostics", {}).get("request_attempted") is True)
                    or any(not _is_offline_call(c) for c in arm.calls))
    specialist_validated = bool(arm.validation and arm.validation.valid)
    if arm.validation is not None and not arm.validation.valid and not arm.failure:
        arm.failure = (f"Specialist evidence validation rejected "
                       f"({len(arm.validation.issues)} issues; see local validation diagnostics).")
    if arm.validation is not None:
        persist_arm(session, prep, arm)

    # --- Validator 7 over the m3_assessment proposal (no live model) ---------
    allowed = set(prep.packet.evidence_ids)
    evidence_texts = {e: prep.passage_text[e] for e in allowed
                      if e in prep.passage_text}
    governed_assessment: dict[str, Any] | None = None
    validation_issues: list[dict[str, Any]] = []
    rejected_items: list[dict[str, Any]] = []
    assessment_failure: str | None = None
    claim_reviews: list[dict[str, Any]] = []
    canonical_metrics = _canonical_metrics_from_prep(prep)
    proposal = (arm.parsed or {}).get("m3_assessment")
    draft_contract = bool(arm.metadata.get("generation_contract"))
    if isinstance(proposal, dict) and (not draft_contract or specialist_validated):
        try:
            governed = validate_m3_assessment(
                proposal, allowed_evidence_ids=allowed,
                validated_borrower_entity_ids=(
                    {transaction_borrower_entity_id}
                    if transaction_borrower_entity_id else set()),
                evidence_texts=evidence_texts,
                structure_facts=structural_facts,
                canonical_metrics=canonical_metrics,
                management_company_aliases=prep.entity_aliases,
                management_window_years=management_window_years,
                usable_hypothesis_ids=({r.hypothesis_id for r in arm.validation.records if r.usable}
                                       if arm.validation else set()),
                hypothesis_proposals=((arm.parsed or {}).get("hypotheses", [])
                    if proposal.get("schema_version") == "bo-m3-corporate-assessment-1.2" else None),
                generation_draft=draft_contract)
        except M3AssessmentError:
            # Pydantic's substantive contract can reject schema-valid JSON.
            # Preserve the raw registry record and fail closed; exception text
            # may echo the proposal, so keep it out of shareable diagnostics.
            assessment_failure = "Validator 7 assessment contract rejected; see the local recorded response."
        else:
            governed, claim_reviews = strengthen_m3_assessment(
                governed, evidence_texts=evidence_texts, structure_facts=structural_facts)
            governed_assessment = governed.model_dump(mode="json")
            if draft_contract:
                from app.services.bo.m3_contract import validate_governed_response
                import jsonschema
                try:
                    validate_governed_response(arm.parsed, governed_assessment)
                except jsonschema.ValidationError:
                    assessment_failure = "Governed response failed final canonical JSON validation."
                    governed_assessment = None
                    arm.metadata["diagnostics"]["canonical_schema_validation"] = "rejected"
                    arm.metadata["diagnostics"]["canonical_validation_occurred"] = True
                else:
                    arm.metadata["diagnostics"]["canonical_schema_validation"] = "valid"
                    arm.metadata["diagnostics"]["canonical_validation_occurred"] = True
            if governed_assessment is not None:
                if governed_assessment.get("management_stability"):
                    source_metadata = governed_assessment["management_stability"]["source_metadata"]
                    governed_assessment["management_stability"]["source_metadata"] = {
                        eid: {**prep.management_source_metadata.get(eid, {}), **source_metadata.get(eid, {})}
                        for eid in prep.management_source_metadata.keys() | source_metadata.keys()}
                governed_assessment["replay_provenance"] = {
                    "kind": ("live_standalone_validation" if live_mode
                             else "offline_deterministic_standalone_validation"),
                    "execution_mode": execution_mode,
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

    if (structural_facts or {}).get("source", {}).get("authority") == "hypothetical_case_assumptions":
        deliverables["m3_analysis"]["scenario_assumptions"] = {
            **structural_facts["source"], "borrower": structural_facts.get("borrower"),
            "guarantor_node_ids": structural_facts.get("guarantor_node_ids", []),
            "scope": "Authoritative hypothetical case assumptions only; not verified real-world legal facts."}

    # --- Finalise the run row ------------------------------------------------
    completion_status = m3_output["completion"]["status"]
    row.status = "completed" if specialist_validated and not assessment_failure else "partially_failed"
    row.completed_at = datetime.now(timezone.utc)
    row.failure_state = arm.failure or assessment_failure
    usage = arm.usage()
    if usage["input_tokens"] or usage["output_tokens"]:
        row.total_input_tokens = usage["input_tokens"]
        row.total_output_tokens = usage["output_tokens"]
        row.total_tokens = usage["input_tokens"] + usage["output_tokens"]

    diagnostics = record_final_validation(
        session, case_id=case_id, snapshot_version=prep.snapshot_version,
        diagnostics=arm.metadata["diagnostics"], specialist_valid=specialist_validated,
        specialist_issues=list(arm.validation.issues) if arm.validation else [],
        assessment_present=governed_assessment is not None,
        validation_issues=validation_issues, rejected_items=rejected_items,
        run_status=row.status, artifact_status=completion_status,
        conclusion=deliverables["m3_analysis"].get("consolidated_conclusion") or {},
        governed_assessment=governed_assessment, assessment_failure=assessment_failure,
        claim_reviews=claim_reviews)

    manifest = {
        "document": "bo_m3_standalone_run",
        "version": M3_STANDALONE_POLICY_VERSION,
        "case_id": case_id,
        "analysis_run_id": prep.analysis_run_id,
        "scope": "M3_only",
        "validation_stages": diagnostics,
        "completion_status_meaning": (
            "Legacy completion_status describes artifact availability only. "
            "Use validation_stages for execution, validation and analytical acceptance."),
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
            # The manifest MUST state whether a real model was invoked.
            "execution_mode": execution_mode,
            "real_model_invoked": live_mode and model_called,
            "model_called": model_called,
            "model_id": (arm.calls[-1].get("model_id") if arm.calls else None),
            "prompt_agent_id": spec.agent_id,
            "specialist_validated": specialist_validated,
            "specialist_failure": arm.failure,
            # Bounded execution: exactly one specialist pass unless explicitly
            # configured, bounded retries are requested (none by default).
            "specialist_pass_count": len(arm.calls),
            "max_retries_configured": int((live_config or {}).get("max_retries", 0)),
            # Auditability: whether the validated corporate structure was present
            # in the specialist's ACTUAL input context before the pass ran.
            "structure_in_specialist_context": structural_facts is not None,
            "canonical_metrics_available": sorted(canonical_metrics),
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
        run_manifest=manifest, validation_stages=diagnostics)
