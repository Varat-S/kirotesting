"""Local M3 diagnostics using the existing model-run and immutable-stage registry.

Raw evidence/answers stay in SQLite (.db/.sqlite3, ignored by this repository),
never in the six shareable demo files. No settings dump, SDK object, headers or
credential is serialized. Recording is opt-in at the single-pass seam and used
by the standalone M3 runner only. This module never calls a model.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.core.hashing import content_hash
from app.models.orm import ModelRun
from app.services.llm.client import LLMRawResult
from app.services.pipeline.artifacts import ArtifactIntegrityError, StageRecorder, verified_artifacts
from app.services.llm.vertex_m3_diagnostics import PUBLIC_DIAGNOSTIC_FIELDS, public_diagnostics


_CONFIG_FIELDS = {
    "backend", "provider", "model_tier", "request_inputs_sha256",
    "request_schema_sha256", "canonical_schema_sha256", "system_instruction_sha256",
    "schema_adapter", "max_output_tokens", "temperature", "requested_temperature",
    "effective_temperature", "latency_ms", "input_tokens", "output_tokens",
    "total_tokens", "thinking_tokens", "error_type", "simulation", "api",
    "provider_response_id",
}
_CONFIG_FIELDS |= PUBLIC_DIAGNOSTIC_FIELDS


def ensure_local_store(session) -> None:
    """Fail before generation if raw diagnostics would enter a remote/unignored DB."""
    bind = session.get_bind()
    if bind.dialect.name != "sqlite":
        raise ValueError("Standalone M3 diagnostics require local SQLite storage.")
    database = bind.url.database
    if database and database != ":memory:":
        if Path(database).suffix.lower() not in {".db", ".sqlite3"}:
            raise ValueError("M3 diagnostics require a git-ignored .db or .sqlite3 database.")
        Path(database).chmod(0o600)


def _safe_config(config: dict[str, Any]) -> dict[str, Any]:
    safe = {k: v for k, v in config.items()
            if k in _CONFIG_FIELDS and (v is None or type(v) in {str, int, float, bool})}
    # Snapshot created by our Vertex adapter, not an SDK request/response object.
    # Never accept headers, API keys or arbitrary transport/client configuration.
    request = config.get("provider_request")
    if isinstance(request, dict):
        fields = request.get("config") or {}
        safe["provider_request"] = {
            "model": request.get("model"),
            "config": {k: fields[k] for k in (
                "system_instruction", "response_json_schema", "response_mime_type",
                "temperature", "max_output_tokens") if k in fields},
            "inputs_sha256": request.get("inputs_sha256"),
        }
        if isinstance(request.get("contents"), str):
            safe["provider_request"]["contents"] = request["contents"]
        options = fields.get("http_options")
        if isinstance(options, dict):
            safe["provider_request"]["config"]["http_options"] = {
                "timeout": options.get("timeout"),
                "retry_options": {"attempts": (options.get("retry_options") or {}).get("attempts")},
            }
    error = config.get("private_service_error")
    if isinstance(config.get("provider_request_json"), str):
        safe["provider_request_json"] = config["provider_request_json"]
    if isinstance(error, dict):
        # These fields are private even after sanitisation: a service can echo
        # source text in its message or field descriptions. No headers copied.
        safe["private_service_error"] = {k: error[k] for k in ("message", "details", "http_status", "service_status") if k in error}
    return safe


def record_provider_preflight(session, request, backend):
    """Capture configured model/schema/content BEFORE any provider I/O."""
    ensure_local_store(session)
    prepared = backend.prepare_diagnostics(request)
    if not prepared:
        return None
    name = "m3_provider_preflight_" + uuid4().hex
    StageRecorder(session, request.case_id, request.case_version).record(name, {
        "model_config": _safe_config(prepared), "phase": "before_provider_io",
    })
    return name


def record_generation(session, client, result, *, started_at: str, preflight_stage=None) -> dict[str, Any]:
    """Store the exact raw answer and canonical outcome BEFORE specialist checks."""
    raw = result.raw
    config = _safe_config(raw.model_config)
    if preflight_stage:
        config["provider_preflight_stage"] = preflight_stage
    recorded = replace(result, raw=LLMRawResult(
        raw_response=raw.raw_response, model_id=raw.model_id,
        model_config=config))
    run = client.log_run(recorded)
    metadata_stage = "m3_request_metadata_" + run.run_id
    StageRecorder(session, run.case_id, run.case_version).record(metadata_stage, {
        "model_config": recorded.raw.model_config,
        "model_run_id": run.run_id,
    })
    parsed_status = "not_available"
    if raw.raw_response:
        try:
            json.loads(raw.raw_response)
            parsed_status = "valid"
        except (ValueError, TypeError):
            parsed_status = "invalid"
    attempted = raw.model_config.get("request_attempted")
    if "request_attempted" not in raw.model_config and raw.raw_response:
        attempted = False if raw.model_config.get("backend") == "fake" else True
    execution = ("response_received" if raw.raw_response else "provider_rejected" if
                 raw.model_config.get("service_accepted") is False else "not_attempted" if attempted is False else "failed")
    from app.services.bo.m3_contract import is_draft_schema
    draft_contract = is_draft_schema(result.request.prompt.response_schema)
    if draft_contract:
        from app.prompts.bo_prompts import BO_M3_JSON_SCHEMA
    return {
        **public_diagnostics(config),
        "model_run_id": run.run_id,
        "storage": "local_sqlite_model_runs_and_stage_artifacts",
        "request_stage": "model_run_" + run.run_id,
        "request_metadata_stage": metadata_stage,
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "model_execution": execution,
        "request_attempted": attempted,
        "model_response_received": bool(raw.raw_response),
        "canonical_validation_occurred": parsed_status == "valid",
        "json_parsing": parsed_status,
        "canonical_schema_validation": (
            result.validation_outcome if parsed_status == "valid" and not draft_contract else "not_run"),
        **({"generation_draft_validation": result.validation_outcome,
            "canonical_validation_occurred": False,
            "generation_schema_sha256": content_hash(result.request.prompt.response_schema)} if draft_contract else {}),
        "raw_response_sha256": sha256(raw.raw_response.encode("utf-8")).hexdigest(),
        "request_inputs_sha256": content_hash(result.request.inputs),
        "canonical_schema_sha256": content_hash(BO_M3_JSON_SCHEMA if draft_contract else result.request.prompt.response_schema),
        "model_id": raw.model_id,
        "provider": raw.model_config.get("backend", "unknown"),
    }


def record_final_validation(session, *, case_id, snapshot_version, diagnostics,
                            specialist_valid, specialist_issues, assessment_present,
                            validation_issues, rejected_items, run_status,
                            artifact_status, conclusion, governed_assessment,
                            assessment_failure=None, claim_reviews=None) -> dict[str, Any]:
    """Record distinct validation stages; no response repairs or promotions."""
    counts = {
        "structural_credit": len(conclusion.get("admitted_structural_credit_finding_ids") or []),
        "management": len(conclusion.get("admitted_management_signal_ids") or []),
        "governance": len(conclusion.get("admitted_governance_signal_ids") or []),
    }
    supported = {}
    for kind, bucket, key, admitted_key in (
        ("structural_credit", "structural_credit_findings", "finding_id", "admitted_structural_credit_finding_ids"),
        ("management", "management_assessments", "observation_id", "admitted_management_signal_ids"),
        ("governance", "governance_screening", "finding_id", "admitted_governance_signal_ids"),
    ):
        admitted_ids = set(conclusion.get(admitted_key) or [])
        supported[kind] = sum(
            f.get(key) in admitted_ids and f.get("disposition") in {"supported", "qualified"}
            and f.get("admissibility") in {"established", "attributed", "qualified_narrative"}
            for f in (governed_assessment or {}).get(bucket, []))
    stages = {
        **diagnostics,
        "specialist_validation": (
            "valid" if specialist_valid else "rejected" if
            (diagnostics.get("canonical_schema_validation") == "valid" or
             diagnostics.get("canonical_input_validation") == "valid") else "not_run"),
        "assessment_governance": ("rejected" if assessment_failure else
                                  "performed" if assessment_present else "not_run"),
        "assessment_failure": assessment_failure,
        "claim_specific_review_count": len(claim_reviews or []),
        "assessment_issue_count": len(validation_issues),
        "rejected_item_count": len(rejected_items),
        "run_status": run_status,
        "artifact_availability_status": artifact_status,
        "admitted_analytical_findings": counts,
        "evidence_supported_findings": supported,
        "analytical_status": "evidence_supported_findings_for_review" if any(supported.values()) else
                             "no_evidence_supported_findings",
        "requires_human_review": True,
    }
    if diagnostics.get("model_execution") != "response_received":
        completion = "failed_model_execution"
    elif diagnostics.get("generation_draft_validation") == "rejected":
        completion = "failed_generation_contract"
    elif diagnostics.get("canonical_input_validation") == "valid" and not specialist_valid:
        completion = "failed_specialist_validation"
    elif diagnostics.get("canonical_schema_validation") != "valid":
        completion = "failed_canonical_validation"
    elif not specialist_valid:
        completion = "failed_specialist_validation"
    elif assessment_failure:
        completion = "failed_assessment_governance"
    elif artifact_status != "complete":
        completion = "partial_artifacts"
    elif validation_issues or rejected_items or not all((supported["structural_credit"], supported["management"])):
        completion = "completed_with_evidence_gaps"
    else:
        completion = "complete_for_human_review"
    stages["module_completion"] = completion
    stage_name = "m3_validation_" + diagnostics["model_run_id"]
    StageRecorder(session, case_id, snapshot_version).record(stage_name, {
        "stages": stages, "specialist_issues": specialist_issues,
        "validation_issues": validation_issues, "rejected_items": rejected_items,
        "claim_specific_reviews": claim_reviews or [],
    })
    return {**stages, "validation_stage": stage_name}


def load_recorded_m3_run(session, model_run_id: str) -> dict[str, Any]:
    """Load a private, integrity-checked replay bundle WITHOUT making a model call.

    The original raw text and recorded canonical schema can be replayed through
    a FakeLLMBackend/local validator. Never publish this evidence-bearing bundle.
    """
    run = session.get(ModelRun, model_run_id)
    if run is None or run.method != "m3_corporate_management":
        raise ValueError("No recorded M3 model run with that ID.")
    stages = verified_artifacts(session, run.case_id, run.case_version)
    stage = next((r for r in stages if r.name == "model_run_" + run.run_id), None)
    if stage is None:
        raise ArtifactIntegrityError("Recorded M3 request stage is missing.")
    payload = json.loads(stage.payload_json)
    if payload["raw_response"] != run.raw_response:
        raise ArtifactIntegrityError("M3 model response differs from its immutable stage.")
    metadata_stage = next((r for r in stages if r.name == "m3_request_metadata_" + run.run_id), None)
    if metadata_stage is None:
        raise ArtifactIntegrityError("Recorded M3 provider metadata stage is missing.")
    metadata = json.loads(metadata_stage.payload_json)["model_config"]
    if metadata != run.model_config_json or payload["validation_outcome"] != run.validation_outcome:
        raise ArtifactIntegrityError("M3 model metadata differs from its immutable stage.")
    from app.services.bo.m3_contract import is_draft_schema
    draft_contract = is_draft_schema(payload.get("response_schema", {}))
    return {"recorded_request": payload,
            "provider_metadata": metadata,
            "raw_response": run.raw_response,
            "canonical_validation_outcome": ("not_applicable_to_raw_generation_draft" if draft_contract else run.validation_outcome),
            **({"generation_validation_outcome": run.validation_outcome,
                "validation_target": "generation_draft"} if draft_contract else {})}
