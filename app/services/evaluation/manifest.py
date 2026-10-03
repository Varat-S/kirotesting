"""GroundTruthManifest object + loader (task 9.1, Req 23.4-23.6).

A :class:`GroundTruthManifest` is the FIRST-CLASS declaration of truth for a
scored evaluation case. **No scored evaluation metric may be reported without a
declared manifest** (Req 23.5): the scorers in this package require a manifest
and refuse (raise) to score without one.

The manifest carries the fields required by Req 23.4:

* ``case_id``
* ``as_of_date``
* ``evidence_cutoff_timestamp``
* ``approved_document_ids``
* ``verified_facts``
* ``expected_normalized_values``
* ``expected_metric_outputs``
* ``known_missing_items``
* ``known_contradictions``
* ``expected_rule_triggers``
* ``expected_escalations``
* ``adjudicated_material_risks`` (optional; for qualitative recall)
* ``contemporaneous_ground_truth``  -- knowable/documented AS OF time T
* ``future_outcome`` (optional)     -- what happened AFTER T
* ``annotator_metadata``
* ``manifest_version``

Critically, ``contemporaneous_ground_truth`` is stored SEPARATELY from
``future_outcome`` (Req 23.6). ``future_outcome`` must NEVER leak into model
inputs at time T, must NEVER be treated as evidence the model was expected to
know, and must NEVER be mixed into contemporaneous factual-accuracy scoring. The
data model enforces this separation structurally: accuracy scorers only ever
read :attr:`GroundTruthManifest.contemporaneous_ground_truth` /
``verified_facts``; a dedicated predictive-usefulness study reads
``future_outcome`` and is never wired into accuracy scoring.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExpectedField(BaseModel):
    """One expected extraction field in the golden dataset (Req 23.2).

    Each field declares its value, entity, period, currency, scale, source
    location, allowed numeric tolerance, expected status, and (for recall-by-
    type reporting) a ``field_type``.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    field_type: str  # e.g. "financial" | "qualitative" | "operating"
    expected_value: float | str | None = None
    entity_id: str | None = None
    period_end: date | None = None
    currency: str | None = None
    scale: str | None = None
    source_document_id: str | None = None
    source_location: dict[str, Any] | None = None
    tolerance: float = 0.0
    expected_status: str = "verified"
    required: bool = True


class ExpectedMetric(BaseModel):
    """An expected deterministic metric output (Req 23.4)."""

    model_config = ConfigDict(extra="forbid")

    metric_id: str
    expected_result: float | None = None
    expected_state: str = "ok"
    tolerance: float = 0.0


class ExpectedEscalation(BaseModel):
    """An expected escalation keyed on its exact ``rule_id`` (Req 14.4)."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    category: str | None = None
    severity: str | None = None
    mandatory: bool = False


class FutureOutcome(BaseModel):
    """What happened AFTER time T (Req 23.6).

    This is held in its OWN sub-object precisely so it can never be confused
    with contemporaneous evidence. The accuracy scorers do not accept this type.
    """

    model_config = ConfigDict(extra="forbid")

    # Common forward outcomes studied for predictive usefulness only.
    default_occurred: bool | None = None
    rating_downgrade: bool | None = None
    liquidity_shock: bool | None = None
    covenant_breach: bool | None = None
    observed_at: date | None = None
    notes: str | None = None


class GroundTruthManifest(BaseModel):
    """First-class declaration of truth for a scored evaluation case (Req 23.4)."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    as_of_date: date
    evidence_cutoff_timestamp: datetime
    approved_document_ids: list[str] = Field(default_factory=list)

    verified_facts: list[ExpectedField] = Field(default_factory=list)
    expected_normalized_values: dict[str, float] = Field(default_factory=dict)
    expected_metric_outputs: list[ExpectedMetric] = Field(default_factory=list)

    known_missing_items: list[str] = Field(default_factory=list)
    known_contradictions: list[str] = Field(default_factory=list)

    expected_rule_triggers: list[str] = Field(default_factory=list)
    expected_escalations: list[ExpectedEscalation] = Field(default_factory=list)

    adjudicated_material_risks: list[str] = Field(default_factory=list)

    # STRICT SEPARATION (Req 23.6): contemporaneous truth vs future outcome.
    contemporaneous_ground_truth: dict[str, Any] = Field(default_factory=dict)
    future_outcome: FutureOutcome | None = None

    annotator_metadata: dict[str, Any] = Field(default_factory=dict)
    manifest_version: str

    # -- convenience views ----------------------------------------------------

    def fields_by_type(self) -> dict[str, list[ExpectedField]]:
        """Group expected extraction fields by ``field_type`` (recall-by-type)."""
        grouped: dict[str, list[ExpectedField]] = {}
        for field in self.verified_facts:
            grouped.setdefault(field.field_type, []).append(field)
        return grouped

    def required_fields(self) -> list[ExpectedField]:
        return [f for f in self.verified_facts if f.required]


class ManifestError(ValueError):
    """Raised when a manifest cannot be loaded or is structurally invalid."""


def load_manifest(path: str | Path) -> GroundTruthManifest:
    """Load and validate a :class:`GroundTruthManifest` from a JSON file.

    Raises :class:`ManifestError` for a missing file or invalid structure (no
    silent defaults, Req 21.1).
    """
    p = Path(path)
    if not p.exists():
        raise ManifestError(f"Ground-truth manifest not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise ManifestError(f"Manifest {p} is not valid JSON: {exc}") from exc
    return load_manifest_dict(data)


def load_manifest_dict(data: dict[str, Any]) -> GroundTruthManifest:
    """Validate a manifest from an already-parsed dict."""
    try:
        return GroundTruthManifest.model_validate(data)
    except Exception as exc:  # pydantic ValidationError
        raise ManifestError(f"Invalid ground-truth manifest: {exc}") from exc
