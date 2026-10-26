"""Scalar public M3 states and opt-in, redacted private service diagnostics.

Never serialize SDK exceptions, responses or headers. Service messages may echo
filing text, so even redacted message/details must remain in the private store.
"""
from __future__ import annotations

import re

PUBLIC_DIAGNOSTIC_FIELDS = {
    "backend", "provider", "model_id", "model_tier", "schema_adapter", "sdk_version",
    "request_schema_sha256", "canonical_schema_sha256", "system_instruction_sha256",
    "request_inputs_sha256", "request_attempted", "sdk_invocation_started",
    "service_accepted", "http_response_received", "model_response_received",
    "http_status", "service_status", "error_phase", "latency_ms", "provider_preflight_stage",
    "api", "max_output_tokens", "temperature", "requested_temperature", "effective_temperature",
    "input_tokens", "output_tokens", "total_tokens", "thinking_tokens",
}
SERVICE_STATUSES = {
    "INVALID_ARGUMENT", "FAILED_PRECONDITION", "OUT_OF_RANGE", "UNAUTHENTICATED",
    "PERMISSION_DENIED", "NOT_FOUND", "ALREADY_EXISTS", "ABORTED", "RESOURCE_EXHAUSTED",
    "CANCELLED", "DATA_LOSS", "UNKNOWN", "INTERNAL", "UNIMPLEMENTED", "UNAVAILABLE", "DEADLINE_EXCEEDED",
}


def public_diagnostics(metadata):
    return {k: v for k, v in metadata.items() if k in PUBLIC_DIAGNOSTIC_FIELDS
            and (v is None or type(v) in {str, int, float, bool})}


def redact(text, *, secrets=()):
    if not isinstance(text, str):
        return None
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[REDACTED PRIVATE KEY]", text, flags=re.S)
    text = re.sub(r"\b(?:AIza[\w-]{20,}|sk-[\w-]{15,}|ya29\.[\w.-]+|eyJ[\w-]+\.[\w-]+\.[\w-]+)\b", "[REDACTED]", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s,\"'}]+", "Bearer [REDACTED]", text)
    text = re.sub(r"(?i)([\"']?(?:api[_-]?key|access_token|refresh_token|client_secret|authorization)[\"']?\s*[:=]\s*[\"']?)[^\s,\"'}]+", r"\1[REDACTED]", text)
    return text[:4000]


def failure_metadata(exc, prepared, *, sdk_started, private=False, secrets=()):
    """Do not guess transport success for an ambiguous SDK/network failure."""
    metadata = dict(prepared)
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(code, str) and code.isascii() and code.isdigit():
        code = int(code)
    code = code if type(code) is int and 400 <= code <= 599 else None
    status = getattr(exc, "status", None)
    status = status if isinstance(status, str) and status in SERVICE_STATUSES else None
    local = isinstance(exc, (ValueError, TypeError))
    attempted = True if code is not None else False if not sdk_started or local else None
    metadata.update(request_attempted=attempted, sdk_invocation_started=sdk_started,
        service_accepted=False if code is not None else None,
        http_response_received=True if code is not None else False if not sdk_started or local else None,
        model_response_received=False, http_status=code, service_status=status,
        error_phase="provider_rejection" if code is not None else "local_preparation" if not sdk_started or local else "transport_or_sdk_failure")
    if private:
        envelope = getattr(exc, "details", None)
        error = envelope.get("error", envelope) if isinstance(envelope, dict) else {}
        message = getattr(exc, "message", None)
        message = message if isinstance(message, str) else error.get("message")
        details = []
        raw_details = error.get("details")
        for item in (raw_details if isinstance(raw_details, list) else [])[:12]:
            if not isinstance(item, dict):
                continue
            detail = {k: redact(item[k], secrets=secrets) for k in ("@type", "reason", "domain") if isinstance(item.get(k), str)}
            for key, fields in (("fieldViolations", ("field", "description")), ("violations", ("type", "subject", "description"))):
                if isinstance(item.get(key), list):
                    detail[key] = [{k: redact(v[k], secrets=secrets) for k in fields if isinstance(v.get(k), str)} for v in item[key][:20] if isinstance(v, dict)]
            if detail:
                details.append(detail)
        metadata["private_service_error"] = {"message": redact(message, secrets=secrets), "details": details,
                                              "http_status": code, "service_status": status}
    return metadata
