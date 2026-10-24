"""Index governed pipeline outputs on disk into an :class:`ArtifactManifest`.

The indexer is **read-only and non-destructive**. It opens each recorded JSON
artifact, re-computes its canonical content hash, reads the governance fields the
producers already wrote (``schema_version``, ``completion_status``,
``validator_id``, ``requires_human_review``, run manifests, etc.), and maps them
onto :class:`ArtifactEntry` records. It never writes to, mutates or fabricates a
source artifact, and it never registers a missing producer as a successful run —
an absent file becomes an ``ABSENT`` entry, not a dropped one.

Layout understood (matches the BO/M3/FO writers):

* ``<output>/bo/<case>/<run>/``  -> business_overview.json, audit_report.json,
  handoff.json
* ``<output>/fo/<case>/<run>/``  -> financial_overview.json,
  precheck_transcript.json, trigger_transcript.json
* an M3 demo directory          -> m3_corporate_structure.json, m3_analysis.json,
  m3_structure_provenance.json, validation_report.json,
  evaluation_scorecard.json, run_manifest.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from app.core.hashing import content_hash
from app.schemas.artifact_manifest import (
    ArtifactEntry,
    ArtifactManifest,
    ArtifactType,
    ExecutionStatus,
    HumanReviewStatus,
)
from app.services.registry.provenance import provenance_from_payload


def _load_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _review_status(flag: Any) -> HumanReviewStatus:
    if flag is True:
        return HumanReviewStatus.PENDING
    return HumanReviewStatus.NOT_REQUIRED


def _status_from_completion(
    completion: Any, *, requires_review: bool, validated: Any
) -> tuple[ExecutionStatus, Optional[str]]:
    """Map a producer's ``completion_status`` onto :class:`ExecutionStatus`.

    Conservative: anything not explicitly complete/validated is reported as the
    nearest honest state, never silently promoted to ``VALIDATED``.
    """
    c = (completion or "").lower() if isinstance(completion, str) else ""
    if c in {"unavailable", "not_executed", "absent"}:
        return ExecutionStatus.ABSENT, completion
    if c in {"skipped"}:
        return ExecutionStatus.SKIPPED, completion
    if c in {"failed", "rejected"}:
        return ExecutionStatus.FAILED, completion
    if c in {"partial", "incomplete", "partially_complete"}:
        return ExecutionStatus.INCOMPLETE, completion
    if requires_review or c in {"qualified", "accepted_with_qualification"}:
        return ExecutionStatus.QUALIFIED, completion or "requires_human_review"
    if validated is False:
        return ExecutionStatus.FAILED, "specialist_validated=false"
    if c in {"complete", "completed", "accepted", "validated"} or validated is True:
        return ExecutionStatus.VALIDATED, completion or None
    # Unknown -> incomplete rather than a false green.
    return ExecutionStatus.INCOMPLETE, completion or "unknown_completion_status"


def _entry_for_file(
    path: Path,
    root: Path,
    *,
    artifact_id: str,
    artifact_type: ArtifactType,
    case_id: str,
    run_id: Optional[str],
    producer_module: Optional[str] = None,
    producer_agent: Optional[str] = None,
    producer_validator: Optional[str] = None,
    depends_on: Optional[list[str]] = None,
) -> ArtifactEntry:
    """Build one entry for an on-disk JSON file (or ABSENT if missing).

    Status is NEVER defaulted to ``validated`` merely because the file exists:
    absent explicit authoritative status, the entry is ``incomplete``. Only a
    recorded completion/validation signal can raise it to ``validated`` /
    ``qualified`` (see :func:`_status_from_completion`).
    """
    if not path.is_file():
        return ArtifactEntry(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            case_id=case_id,
            run_id=run_id,
            producer_module=producer_module,
            producer_agent=producer_agent,
            producer_validator=producer_validator,
            depends_on=depends_on or [],
            status=ExecutionStatus.ABSENT,
            status_reason="no recorded artifact at expected location",
        )

    payload = _load_json(path)
    if payload is None:
        return ArtifactEntry(
            artifact_id=artifact_id,
            artifact_type=artifact_type,
            case_id=case_id,
            run_id=run_id,
            producer_module=producer_module,
            producer_agent=producer_agent,
            producer_validator=producer_validator,
            file_location=_rel(path, root),
            depends_on=depends_on or [],
            status=ExecutionStatus.ERROR,
            status_reason="artifact present but not valid JSON",
        )

    doc = payload if isinstance(payload, dict) else {}
    requires_review = bool(doc.get("requires_human_review"))
    completion = doc.get("completion_status") or (
        doc.get("completion", {}).get("status")
        if isinstance(doc.get("completion"), dict)
        else None
    )
    validated = doc.get("specialist_validated")

    status, reason = _status_from_completion(
        completion, requires_review=requires_review, validated=validated
    )

    provenance = provenance_from_payload(
        payload, case_id=case_id, producer_module=producer_module)
    source_snapshot = None
    if provenance.canonical_snapshot_version is not None:
        source_snapshot = f"{case_id}@v{provenance.canonical_snapshot_version}"

    return ArtifactEntry(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        case_id=case_id,
        run_id=run_id or doc.get("analysis_run_id") or doc.get("run_id"),
        producer_module=producer_module,
        producer_agent=producer_agent or doc.get("invoked_specialist_agent_id"),
        producer_validator=producer_validator or doc.get("validator_id"),
        schema_name=doc.get("schema_version") or doc.get("version"),
        schema_version=doc.get("schema_version"),
        file_location=_rel(path, root),
        content_hash=content_hash(payload),
        source_evidence_snapshot=source_snapshot,
        provenance=provenance,
        status=status,
        status_reason=reason,
        validation_status=(
            "validated" if validated is True
            else "failed" if validated is False
            else None
        ),
        human_review_status=_review_status(requires_review),
        created_at=doc.get("generated_at") or doc.get("created_at"),
    )


def _index_m3_dir(dir_path: Path, root: Path, case_id: str) -> list[ArtifactEntry]:
    """Index an M3 standalone demo directory, reading its run_manifest."""
    manifest = _load_json(dir_path / "run_manifest.json") or {}
    run_id = manifest.get("analysis_run_id")
    model_id = manifest.get("execution", {}).get("model_id") if isinstance(
        manifest.get("execution"), dict) else None
    model_invoked = manifest.get("execution", {}).get("real_model_invoked") if isinstance(
        manifest.get("execution"), dict) else None
    snapshot_v = None
    if isinstance(manifest.get("evidence"), dict):
        snapshot_v = manifest["evidence"].get("snapshot_version")
    snapshot_ref = f"{case_id}@v{snapshot_v}" if snapshot_v is not None else None
    # The run_manifest is the AUTHORITATIVE status record for the standalone M3
    # run. Its real status lives in the ``validation`` sub-block (top-level keys
    # are absent). Derived files (structure, provenance, the manifest itself)
    # carry no completion metadata of their own, so their status is inherited
    # from here rather than judged from file existence (Priority 4: no false
    # "validated").
    val_block = manifest.get("validation") if isinstance(
        manifest.get("validation"), dict) else {}
    authoritative = _status_from_completion(
        val_block.get("completion_status"),
        requires_review=bool(val_block.get("requires_human_review")),
        validated=val_block.get("specialist_valid",
                                val_block.get("specialist_validated")),
    )

    entries: list[ArtifactEntry] = []

    def _apply_snapshot(entry: ArtifactEntry) -> None:
        entry.source_evidence_snapshot = snapshot_ref or entry.source_evidence_snapshot
        entry.model_id, entry.model_invoked = model_id, model_invoked
        if entry.provenance is not None and snapshot_v is not None:
            entry.provenance.canonical_snapshot_version = snapshot_v
            from app.schemas.artifact_manifest import SnapshotEquivalence
            entry.provenance.snapshot_equivalence = SnapshotEquivalence.VERIFIED
            entry.provenance.equivalence_note = (
                "Standalone M3 run recorded its canonical snapshot version.")

    def _inherit_status(entry: ArtifactEntry) -> None:
        # Only inherit when the file carries no self-governing completion signal.
        if entry.status == ExecutionStatus.INCOMPLETE and \
                entry.status_reason == "unknown_completion_status":
            entry.status, entry.status_reason = authoritative

    struct = _entry_for_file(
        dir_path / "m3_corporate_structure.json", root,
        artifact_id="m3.corporate_structure", artifact_type=ArtifactType.CORPORATE_STRUCTURE,
        case_id=case_id, run_id=run_id, producer_module="M3",
        producer_agent="m3_corporate_management",
    )
    _apply_snapshot(struct)
    _inherit_status(struct)
    entries.append(struct)

    analysis = _entry_for_file(
        dir_path / "m3_analysis.json", root,
        artifact_id="m3.analysis", artifact_type=ArtifactType.MODULE_OUTPUT,
        case_id=case_id, run_id=run_id, producer_module="M3",
        producer_agent="m3_corporate_management",
        depends_on=["m3.corporate_structure"],
    )
    _apply_snapshot(analysis)
    entries.append(analysis)

    provenance_entry = _entry_for_file(
        dir_path / "m3_structure_provenance.json", root,
        artifact_id="m3.structure_provenance", artifact_type=ArtifactType.OTHER,
        case_id=case_id, run_id=run_id, producer_module="M3",
        producer_agent="m3_corporate_management", depends_on=["m3.corporate_structure"],
    )
    _apply_snapshot(provenance_entry)
    _inherit_status(provenance_entry)
    entries.append(provenance_entry)

    val = _entry_for_file(
        dir_path / "validation_report.json", root,
        artifact_id="m3.validator_7", artifact_type=ArtifactType.VALIDATION_REPORT,
        case_id=case_id, run_id=run_id, producer_module="M3",
        producer_validator="validator_7", depends_on=["m3.analysis"],
    )
    entries.append(val)

    entries.append(_entry_for_file(
        dir_path / "evaluation_scorecard.json", root,
        artifact_id="m3.evaluation", artifact_type=ArtifactType.EVALUATION,
        case_id=case_id, run_id=run_id, producer_module="M3",
        depends_on=["m3.validator_7"],
    ))

    run_manifest_entry = _entry_for_file(
        dir_path / "run_manifest.json", root,
        artifact_id="m3.run_manifest", artifact_type=ArtifactType.RUN_MANIFEST,
        case_id=case_id, run_id=run_id, producer_module="M3",
    )
    _apply_snapshot(run_manifest_entry)
    _inherit_status(run_manifest_entry)
    entries.append(run_manifest_entry)
    return entries


def _index_bo_run(run_dir: Path, root: Path, case_id: str) -> list[ArtifactEntry]:
    run_id = run_dir.name
    entries: list[ArtifactEntry] = []
    entries.append(_entry_for_file(
        run_dir / "business_overview.json", root,
        artifact_id=f"bo.memo.{run_id}", artifact_type=ArtifactType.MEMO,
        case_id=case_id, run_id=run_id, producer_module="BO",
        producer_agent="business_orchestrator",
    ))
    entries.append(_entry_for_file(
        run_dir / "audit_report.json", root,
        artifact_id=f"bo.audit.{run_id}", artifact_type=ArtifactType.AUDIT,
        case_id=case_id, run_id=run_id, producer_module="BO",
        depends_on=[f"bo.memo.{run_id}"],
    ))
    handoff = _entry_for_file(
        run_dir / "handoff.json", root,
        artifact_id=f"bo.handoff.{run_id}", artifact_type=ArtifactType.HANDOFF,
        case_id=case_id, run_id=run_id, producer_module="BO",
        depends_on=[f"bo.memo.{run_id}"],
    )
    handoff.human_review_status = HumanReviewStatus.PENDING
    entries.append(handoff)
    return entries


def _index_fo_run(run_dir: Path, root: Path, case_id: str) -> list[ArtifactEntry]:
    run_id = run_dir.name
    entries: list[ArtifactEntry] = []
    entries.append(_entry_for_file(
        run_dir / "financial_overview.json", root,
        artifact_id=f"fo.overview.{run_id}", artifact_type=ArtifactType.FINANCIAL_OVERVIEW,
        case_id=case_id, run_id=run_id, producer_module="FO",
    ))
    entries.append(_entry_for_file(
        run_dir / "precheck_transcript.json", root,
        artifact_id=f"fo.precheck.{run_id}", artifact_type=ArtifactType.TRANSCRIPT,
        case_id=case_id, run_id=run_id, producer_module="FO",
        producer_agent="fo_precheck", depends_on=[f"fo.overview.{run_id}"],
    ))
    entries.append(_entry_for_file(
        run_dir / "trigger_transcript.json", root,
        artifact_id=f"fo.triggers.{run_id}", artifact_type=ArtifactType.TRANSCRIPT,
        case_id=case_id, run_id=run_id, producer_module="FO",
        producer_agent="fo_triggers", depends_on=[f"fo.overview.{run_id}"],
    ))
    return entries


def index_case_from_dir(
    output_root: str | Path,
    case_id: str,
    *,
    m3_dirs: Optional[list[str | Path]] = None,
    repo_root: Optional[str | Path] = None,
) -> ArtifactManifest:
    """Index all recorded BO / FO / M3 artifacts for ``case_id`` under ``output_root``.

    ``m3_dirs`` optionally names standalone M3 demo directories (which live
    outside ``<output>/bo`` — e.g. ``examples/bo_m3/.../demo_output/<x>``).
    ``repo_root`` is used only to make ``file_location`` repo-relative.
    """
    output_root = Path(output_root)
    root = Path(repo_root) if repo_root is not None else output_root
    manifest = ArtifactManifest(case_id=case_id)

    bo_case = output_root / "bo" / case_id
    if bo_case.is_dir():
        for run_dir in sorted(p for p in bo_case.iterdir() if p.is_dir()):
            manifest.artifacts.extend(_index_bo_run(run_dir, root, case_id))

    fo_case = output_root / "fo" / case_id
    if fo_case.is_dir():
        for run_dir in sorted(p for p in fo_case.iterdir() if p.is_dir()):
            manifest.artifacts.extend(_index_fo_run(run_dir, root, case_id))

    for m3_dir in m3_dirs or []:
        m3_path = Path(m3_dir)
        if m3_path.is_dir():
            manifest.artifacts.extend(_index_m3_dir(m3_path, root, case_id))

    return manifest


def build_manifest(
    output_root: str | Path,
    case_id: str,
    *,
    m3_dirs: Optional[list[str | Path]] = None,
    repo_root: Optional[str | Path] = None,
) -> ArtifactManifest:
    """Public entry point. Alias of :func:`index_case_from_dir`."""
    return index_case_from_dir(
        output_root, case_id, m3_dirs=m3_dirs, repo_root=repo_root
    )
