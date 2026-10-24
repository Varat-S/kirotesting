"""Versioned artifact manifest — a lightweight index over BO / FO / (future SO)
pipeline outputs.

The manifest does NOT replace or rewrite the existing governed JSON artifacts
(BO memo/audit/handoff, M3 demo files, FO overview, etc.). It *indexes* them so
the Pipeline Explorer and any downstream consumer (notably SO) can discover
every output of a case/run, see its producer, schema, hash, evidence lineage,
dependencies and status, and tell apart absent / skipped / failed / incomplete /
qualified / validated executions WITHOUT trusting file naming.

Design rules:

* Every entry carries an explicit :class:`ExecutionStatus`. An absent or skipped
  producer is still represented — with ``status`` and ``status_reason`` — so a
  missing output shows *why* rather than being silently dropped or faked.
* ``content_hash`` is the canonical SHA-256 of the on-disk JSON payload (via
  :func:`app.core.hashing.content_hash`) when the artifact exists; ``None`` for
  absent/skipped entries. A fabricated or placeholder output must NEVER be
  registered with ``status=VALIDATED``.
* Edges (``depends_on``) reference other ``artifact_id`` values and come from
  *declared* dependencies (agent registry, run manifests), never guessed naming.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

ARTIFACT_MANIFEST_VERSION = "artifact-manifest-1.0"


class ExecutionStatus(str, Enum):
    """The lifecycle state of a producer's output.

    Distinguishes the six states the handoff requires plus ``error`` for an
    indexer/read failure. These are deliberately coarse and auditable.
    """

    ABSENT = "absent"            # producer never ran; no artifact exists
    SKIPPED = "skipped"          # producer deliberately not run for this case/run
    FAILED = "failed"            # producer ran and failed (e.g. validator failure)
    INCOMPLETE = "incomplete"    # ran partially; some sections unavailable
    QUALIFIED = "qualified"      # produced, accepted only with qualification
    VALIDATED = "validated"      # produced and passed its deterministic validation
    ERROR = "error"              # the manifest could not read/parse the artifact


class HumanReviewStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class ArtifactType(str, Enum):
    MEMO = "memo"
    AUDIT = "audit"
    HANDOFF = "handoff"
    MODULE_OUTPUT = "module_output"
    CORPORATE_STRUCTURE = "corporate_structure"
    VALIDATION_REPORT = "validation_report"
    EVALUATION = "evaluation"
    RUN_MANIFEST = "run_manifest"
    FINANCIAL_OVERVIEW = "financial_overview"
    TRANSCRIPT = "transcript"
    EVIDENCE_SNAPSHOT = "evidence_snapshot"
    AGENT_OUTPUT = "agent_output"
    OTHER = "other"


class SnapshotEquivalence(str, Enum):
    """Whether an artifact is proven to derive from the canonical snapshot.

    ``verified`` requires a recorded canonical snapshot identity (version +
    content hash). ``unverified`` means the producer used its own evidence path
    (e.g. FO's filing loader) with no recorded link to the canonical snapshot —
    equivalence must NOT be assumed from a shared case id. ``not_applicable`` for
    artifacts that are not evidence-derived (run manifests, evaluations).
    """

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    NOT_APPLICABLE = "not_applicable"


class ProvenanceRecord(BaseModel):
    """Verifiable provenance for one artifact, distinguishing the five levels.

    These answer, in increasing strength:
      1. same borrower/case            -> ``case_id``
      2. same filing & fiscal period   -> ``filing_identifier`` + ``reporting_period``
      3. same underlying source docs   -> ``source_signature`` / ``source_document_ids``
      4. same canonical snapshot       -> ``canonical_snapshot_version`` + ``..._hash``
                                          and ``snapshot_equivalence``
      5. same numerical metric defn    -> carried per-fact by the consistency check,
                                          not on the artifact itself
    """

    case_id: Optional[str] = None
    reporting_period: Optional[str] = None
    reporting_entity: Optional[str] = None
    filing_identifier: Optional[str] = None       # e.g. FO filing folder
    source_signature: Optional[str] = None        # e.g. FO folder_signature hash
    source_document_ids: list[str] = Field(default_factory=list)
    canonical_snapshot_version: Optional[int] = None
    canonical_snapshot_hash: Optional[str] = None
    snapshot_equivalence: SnapshotEquivalence = SnapshotEquivalence.NOT_APPLICABLE
    equivalence_note: Optional[str] = None


class ArtifactEntry(BaseModel):
    """One indexed artifact (or a deliberately absent/skipped producer)."""

    artifact_id: str = Field(..., description="Stable id unique within the manifest")
    artifact_type: ArtifactType = ArtifactType.OTHER

    # Case / run association.
    case_id: str
    run_id: Optional[str] = None

    # Producer identity: module/agent/validator that owns this artifact.
    producer_module: Optional[str] = None       # e.g. "BO", "M3", "M4", "FO"
    producer_agent: Optional[str] = None         # e.g. "m3_corporate_management"
    producer_validator: Optional[str] = None     # e.g. "validator_7"

    # Schema governance.
    schema_name: Optional[str] = None            # e.g. "bo-memo-1.0"
    schema_version: Optional[str] = None

    # Immutable location + integrity.
    file_location: Optional[str] = None          # repo-relative path, if on disk
    content_hash: Optional[str] = None           # canonical SHA-256 of the payload

    # Lineage.
    source_evidence_snapshot: Optional[str] = None   # "case_id@vN" or snapshot hash
    provenance: Optional[ProvenanceRecord] = None     # verifiable five-level provenance
    depends_on: list[str] = Field(default_factory=list)  # parent artifact_ids

    # Execution + governance status.
    status: ExecutionStatus = ExecutionStatus.ABSENT
    status_reason: Optional[str] = None
    validation_status: Optional[str] = None      # free-form validator summary
    human_review_status: HumanReviewStatus = HumanReviewStatus.NOT_REQUIRED
    escalations: list[str] = Field(default_factory=list)

    # Model / prompt provenance (when applicable).
    model_id: Optional[str] = None
    prompt_version: Optional[str] = None
    model_invoked: Optional[bool] = None

    created_at: Optional[str] = None

    def is_successful(self) -> bool:
        """True only for genuinely-produced, non-failed outputs.

        ``qualified`` counts as successful-with-caveat; ``incomplete`` does not.
        """
        return self.status in {ExecutionStatus.VALIDATED, ExecutionStatus.QUALIFIED}


class ArtifactManifest(BaseModel):
    """The versioned manifest for one case (optionally one run)."""

    manifest_version: str = ARTIFACT_MANIFEST_VERSION
    case_id: str
    run_id: Optional[str] = None
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    artifacts: list[ArtifactEntry] = Field(default_factory=list)

    def by_id(self, artifact_id: str) -> Optional[ArtifactEntry]:
        for entry in self.artifacts:
            if entry.artifact_id == artifact_id:
                return entry
        return None

    def successful(self) -> list[ArtifactEntry]:
        return [a for a in self.artifacts if a.is_successful()]

    def counts_by_status(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for entry in self.artifacts:
            out[entry.status.value] = out.get(entry.status.value, 0) + 1
        return out
