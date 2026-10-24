"""Pipeline Explorer backing service — discover demonstrable cases and resolve
one into a manifest + dataflow graph, with artifact-access safety.

This layer is deliberately filesystem-driven so the Explorer is demonstrable
with real recorded artifacts even when no live DB run exists. It resolves two
kinds of sources:

* the configured ``output_dir`` (``<output>/bo/<case>`` and ``<output>/fo/<case>``),
* registered M3 standalone demo directories under ``examples/bo_m3`` — these are
  real, byte-stable recorded fixtures shipped in the repo, clearly labelled as
  deterministic demo data (never presented as a live HCA run).

Artifact access is validated: an artifact id must resolve to a file *inside* the
configured roots, and resolved paths are confirmed not to escape them (no
traversal, no following into unrelated directories). Nothing outside the output
root or the registered demo directories can be read through the Explorer, so
credentials, ``.env`` and unrelated files are never exposed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.schemas.artifact_manifest import ArtifactManifest
from app.services.registry.graph import build_pipeline_graph
from app.services.registry.indexer import build_manifest


@dataclass(frozen=True)
class CaseSource:
    """A discoverable case the Explorer can render."""

    case_id: str
    label: str
    kind: str                      # "live" | "demo_fixture"
    output_root: Path
    repo_root: Path
    m3_dirs: tuple[Path, ...] = ()
    note: Optional[str] = None


# Recorded, byte-stable M3 demo fixtures shipped in the repo. These are labelled
# "deterministic fixture" everywhere in the UI, never a live run.
_M3_DEMO_ROOT = Path("examples/bo_m3/structural_credit/demo_output")
_M3_DEMO_CASES = {
    "hca_simulated": ("HCA (simulated M3 fixture)",
                      "Deterministic recorded Module 3 fixture — NOT a live HCA run."),
    "fictional_positive": ("Fictional — positive structure", "Deterministic M3 fixture."),
    "fictional_negative": ("Fictional — negative structure", "Deterministic M3 fixture."),
    "fictional_mixed": ("Fictional — mixed structure", "Deterministic M3 fixture."),
    "fictional_unsupported_guarantee":
        ("Fictional — unsupported guarantee", "Deterministic M3 fixture."),
}


def discover_cases(
    output_dir: str | Path,
    *,
    repo_root: str | Path = ".",
    include_demo: bool = True,
) -> list[CaseSource]:
    """List the cases the Explorer can render (live output + demo fixtures)."""
    output_root = Path(output_dir)
    repo = Path(repo_root)
    found: dict[str, CaseSource] = {}

    for family in ("bo", "fo"):
        base = output_root / family
        if base.is_dir():
            for case_dir in sorted(p for p in base.iterdir() if p.is_dir()):
                cid = case_dir.name
                if cid not in found:
                    found[cid] = CaseSource(
                        case_id=cid, label=cid, kind="live",
                        output_root=output_root, repo_root=repo,
                        note="Recorded output of a pipeline run.",
                    )

    if include_demo:
        for sub, (label, note) in _M3_DEMO_CASES.items():
            demo_dir = repo / _M3_DEMO_ROOT / sub
            if demo_dir.is_dir():
                cid = f"demo:{sub}"
                found[cid] = CaseSource(
                    case_id=cid, label=label, kind="demo_fixture",
                    output_root=repo / _M3_DEMO_ROOT, repo_root=repo,
                    m3_dirs=(demo_dir,), note=note,
                )

    return list(found.values())


def _source_for(
    case_id: str, output_dir: str | Path, repo_root: str | Path
) -> Optional[CaseSource]:
    for source in discover_cases(output_dir, repo_root=repo_root):
        if source.case_id == case_id:
            return source
    return None


def manifest_for_case(
    case_id: str, output_dir: str | Path, *, repo_root: str | Path = "."
) -> Optional[ArtifactManifest]:
    source = _source_for(case_id, output_dir, repo_root)
    if source is None:
        return None
    lookup_id = source.m3_dirs[0].name if source.kind == "demo_fixture" else case_id
    return build_manifest(
        source.output_root, lookup_id,
        m3_dirs=list(source.m3_dirs), repo_root=source.repo_root,
    )


def graph_for_case(
    case_id: str, output_dir: str | Path, *, repo_root: str | Path = "."
) -> dict[str, Any]:
    """Return the dataflow graph for a case; illustration graph if unknown."""
    manifest = manifest_for_case(case_id, output_dir, repo_root=repo_root)
    graph = build_pipeline_graph(manifest)
    graph["case_id"] = case_id
    return graph


class ArtifactAccessError(ValueError):
    """Raised when an artifact id cannot be safely resolved to a readable file."""


def _safe_resolve(candidate: Path, roots: list[Path]) -> Path:
    """Resolve ``candidate`` and confirm it lives under one of ``roots``."""
    resolved = candidate.resolve()
    for root in roots:
        try:
            resolved.relative_to(root.resolve())
            return resolved
        except ValueError:
            continue
    raise ArtifactAccessError("artifact path escapes the permitted roots")


def load_artifact(
    case_id: str,
    artifact_id: str,
    output_dir: str | Path,
    *,
    repo_root: str | Path = ".",
) -> tuple[Any, ArtifactManifest]:
    """Return ``(payload, manifest)`` for an artifact, enforcing access bounds.

    The ``artifact_id`` must appear in the case's manifest and resolve to a file
    under the case's permitted roots. Absent/skipped entries have no payload and
    raise :class:`ArtifactAccessError` with their recorded reason.
    """
    source = _source_for(case_id, output_dir, repo_root)
    if source is None:
        raise ArtifactAccessError(f"unknown case: {case_id}")
    manifest = manifest_for_case(case_id, output_dir, repo_root=repo_root)
    if manifest is None:
        raise ArtifactAccessError(f"no manifest for case: {case_id}")

    entry = manifest.by_id(artifact_id)
    if entry is None:
        raise ArtifactAccessError(f"unknown artifact: {artifact_id}")
    if not entry.file_location:
        raise ArtifactAccessError(
            f"artifact '{artifact_id}' has no payload ({entry.status.value}: "
            f"{entry.status_reason or 'not produced'})"
        )

    roots = [source.repo_root, source.output_root, *source.m3_dirs]
    path = _safe_resolve(source.repo_root / entry.file_location, roots)
    if not path.is_file():
        raise ArtifactAccessError(f"artifact file missing: {entry.file_location}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    # Per-agent entries index a slice of a shared recorded file (the BO audit).
    # Return THAT agent's recorded output, not the whole audit report.
    if entry.audit_subpath:
        payload = _extract_agent_slice(payload, entry)
    return payload, manifest


def load_artifact_bytes(
    case_id: str,
    artifact_id: str,
    output_dir: str | Path,
    *,
    repo_root: str | Path = ".",
) -> tuple[bytes, str, ArtifactManifest]:
    """Return ``(raw_bytes, original_sha256, manifest)`` for a whole-file artifact.

    This is the EXACT recorded bytes on disk (not a re-serialisation), with the
    original SHA-256 of those bytes — so a download preserves byte-for-byte
    integrity. Per-agent slice artifacts (which index a section of a shared file,
    e.g. the BO audit) have no independent original file and raise
    :class:`ArtifactAccessError`; use the normalized export for those.
    """
    source = _source_for(case_id, output_dir, repo_root)
    if source is None:
        raise ArtifactAccessError(f"unknown case: {case_id}")
    manifest = manifest_for_case(case_id, output_dir, repo_root=repo_root)
    if manifest is None:
        raise ArtifactAccessError(f"no manifest for case: {case_id}")
    entry = manifest.by_id(artifact_id)
    if entry is None:
        raise ArtifactAccessError(f"unknown artifact: {artifact_id}")
    if entry.audit_subpath:
        raise ArtifactAccessError(
            f"artifact '{artifact_id}' indexes a slice of a shared file "
            f"({entry.file_location}); it has no independent original bytes. "
            f"Use the normalized export instead.")
    if not entry.file_location:
        raise ArtifactAccessError(
            f"artifact '{artifact_id}' has no payload ({entry.status.value}: "
            f"{entry.status_reason or 'not produced'})")

    import hashlib
    roots = [source.repo_root, source.output_root, *source.m3_dirs]
    path = _safe_resolve(source.repo_root / entry.file_location, roots)
    if not path.is_file():
        raise ArtifactAccessError(f"artifact file missing: {entry.file_location}")
    raw = path.read_bytes()
    original_sha256 = hashlib.sha256(raw).hexdigest()
    return raw, original_sha256, manifest


def _extract_agent_slice(audit: Any, entry) -> dict[str, Any]:
    """Pull one agent/validator/synthesis/challenger slice from a BO audit report.

    Returns the recorded output for that producer only (its agent block + runs,
    its hypotheses and parameters, or the synthesis/challenger block), so the
    inspector shows the agent's actual output rather than the whole memo/audit.
    """
    if not isinstance(audit, dict):
        return {"error": "audit report is not an object"}
    agent_id = entry.producer_agent
    out: dict[str, Any] = {"agent_id": agent_id, "run_id": entry.run_id,
                           "schema_version": "bo-agent-record-1.0"}

    agent_block = next(
        (a for a in (audit.get("agents") or []) if isinstance(a, dict)
         and a.get("agent_id") == agent_id), None)
    if agent_block is not None:
        out["agent"] = agent_block

    if agent_id == "business_orchestrator":
        out["synthesis"] = audit.get("orchestrator")
        out["conclusion"] = audit.get("final_conclusion") or audit.get("conclusions")
    elif agent_id == "business_challenge":
        out["challenge"] = audit.get("challenger")
    else:
        records = (audit.get("hypotheses", {}) or {}).get("records", []) \
            if isinstance(audit.get("hypotheses"), dict) else []
        out["hypotheses"] = [r for r in records if r.get("agent_id") == agent_id]
        out["parameters"] = [p for p in (audit.get("parameters") or [])
                             if p.get("agent_id") == agent_id]
        packets = audit.get("evidence_packets") or []
        out["evidence_packet"] = next(
            (p for p in packets if isinstance(p, dict) and p.get("agent_id") == agent_id),
            None)
    return out


def _collect_evidence_ids(payload: Any) -> list[str]:
    """Best-effort pull of evidence ids referenced anywhere in a payload."""
    ids: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"evidence_ids", "packet_evidence_ids", "source_fact_ids",
                           "evidence_refs"} and isinstance(value, list):
                    ids.update(str(v) for v in value if isinstance(v, (str, int)))
                elif key in {"evidence_id", "fact_id", "source_id"} and isinstance(
                        value, (str, int)):
                    ids.add(str(value))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    return sorted(ids)


def inspect_node(
    case_id: str,
    artifact_id: str,
    output_dir: str | Path,
    *,
    repo_root: str | Path = ".",
) -> dict[str, Any]:
    """Assemble the node-inspector payload for one artifact.

    Returns identity, status, inputs/dependencies, evidence ids, validation and
    escalation signals, model/prompt provenance, a human-readable summary and the
    raw JSON. For an absent/skipped artifact it returns the *reason*, not mock
    output.
    """
    manifest = manifest_for_case(case_id, output_dir, repo_root=repo_root)
    if manifest is None:
        raise ArtifactAccessError(f"no manifest for case: {case_id}")
    entry = manifest.by_id(artifact_id)
    if entry is None:
        raise ArtifactAccessError(f"unknown artifact: {artifact_id}")

    info: dict[str, Any] = {
        "artifact_id": entry.artifact_id,
        "artifact_type": entry.artifact_type.value,
        "case_id": entry.case_id,
        "run_id": entry.run_id,
        "producer": {
            "module": entry.producer_module,
            "agent": entry.producer_agent,
            "validator": entry.producer_validator,
        },
        "status": entry.status.value,
        "status_reason": entry.status_reason,
        "schema_name": entry.schema_name,
        "content_hash": entry.content_hash,       # canonical SHA-256 of the payload
        "canonical_content_hash": entry.content_hash,
        "original_file_sha256": None,             # set below for whole-file artifacts
        "is_slice": bool(entry.audit_subpath),
        "audit_subpath": entry.audit_subpath,
        "source_evidence_snapshot": entry.source_evidence_snapshot,
        "provenance": entry.provenance.model_dump(mode="json") if entry.provenance else None,
        "depends_on": entry.depends_on,
        "validation_status": entry.validation_status,
        "validator_issues_count": entry.validator_issues_count,
        "human_review_status": entry.human_review_status.value,
        "escalations": entry.escalations,
        "model_id": entry.model_id,
        "prompt_version": entry.prompt_version,
        "model_invoked": entry.model_invoked,
        "file_location": entry.file_location,
        "payload_available": bool(entry.file_location),
        "payload": None,
        "evidence_ids": list(entry.evidence_ids),
    }

    if not entry.file_location:
        # Absent/skipped/failed-without-file: report the reason, never fabricate.
        return info

    # Original-bytes hash only for whole-file artifacts (slices have no own file).
    if not entry.audit_subpath:
        try:
            _, original_sha, _ = load_artifact_bytes(
                case_id, artifact_id, output_dir, repo_root=repo_root)
            info["original_file_sha256"] = original_sha
        except ArtifactAccessError:
            pass

    payload, _ = load_artifact(case_id, artifact_id, output_dir, repo_root=repo_root)
    info["payload"] = payload
    if not info["evidence_ids"]:
        info["evidence_ids"] = _collect_evidence_ids(payload)
    if isinstance(payload, dict):
        issues = payload.get("validation_issues")
        if isinstance(issues, list):
            info["validator_issues"] = issues
        if payload.get("rejected_items"):
            info["rejected_items"] = payload["rejected_items"]
    return info
