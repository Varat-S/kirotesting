"""Versioned JSON Schema registry + validate-on-write for snapshots.

The snapshot schemas (Req 5.5 / 16.1) must validate against a *versioned* JSON
Schema. We derive the JSON Schema from the Pydantic snapshot models and key it
by ``snapshot_type`` + ``schema_version``, then validate instances with the
``jsonschema`` library ("validate on write").
"""

from __future__ import annotations

from typing import Any

import jsonschema

from app.schemas.snapshots import (
    SCHEMA_VERSION,
    CanonicalEvidenceSnapshot,
    FinalCaseSnapshot,
)


class SchemaValidationError(ValueError):
    """Raised when a snapshot payload fails JSON Schema validation."""


# Registry of versioned JSON Schemas, keyed by (snapshot_type, schema_version).
_SCHEMAS: dict[tuple[str, str], dict[str, Any]] = {
    ("canonical_evidence", SCHEMA_VERSION): CanonicalEvidenceSnapshot.model_json_schema(),
    ("final_case", SCHEMA_VERSION): FinalCaseSnapshot.model_json_schema(),
}


def get_json_schema(snapshot_type: str, schema_version: str = SCHEMA_VERSION) -> dict[str, Any]:
    """Return the registered JSON Schema for a snapshot type + version."""
    key = (snapshot_type, schema_version)
    if key not in _SCHEMAS:
        raise SchemaValidationError(
            f"No JSON Schema registered for snapshot_type={snapshot_type!r} "
            f"schema_version={schema_version!r}."
        )
    return _SCHEMAS[key]


def _to_jsonable(payload: Any) -> dict[str, Any]:
    if isinstance(payload, (CanonicalEvidenceSnapshot, FinalCaseSnapshot)):
        return payload.model_dump(mode="json")
    if isinstance(payload, dict):
        return payload
    raise SchemaValidationError(
        f"Cannot validate payload of type {type(payload)!r}; expected a snapshot "
        "model or dict."
    )


def validate_snapshot(payload: Any) -> dict[str, Any]:
    """Validate a snapshot (model or dict) against its versioned JSON Schema.

    Returns the JSON-able dict on success; raises :class:`SchemaValidationError`
    on failure. This is the "validate on write" entrypoint (Req 5.5 / 16.1).
    """
    data = _to_jsonable(payload)
    snapshot_type = data.get("snapshot_type")
    schema_version = data.get("schema_version", SCHEMA_VERSION)
    if not snapshot_type:
        raise SchemaValidationError("Snapshot payload missing 'snapshot_type'.")

    schema = get_json_schema(snapshot_type, schema_version)
    try:
        jsonschema.validate(instance=data, schema=schema)
    except jsonschema.ValidationError as exc:  # pragma: no cover - message passthrough
        raise SchemaValidationError(str(exc)) from exc
    return data
