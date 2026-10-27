"""Small explicit boundaries: integrity-checked artifacts, registry and synthesis."""
from hashlib import sha256
import json
from pathlib import Path
from jsonschema import ValidationError as SchemaValidationError
from .contracts import validate_management
from .structure import validate_structure


def load_components(manifest_path, *, case_id=None):
    path = Path(manifest_path).resolve()
    manifest = json.loads(path.read_text())
    if manifest.get("schema_version") != "m3-rebuild-manifest-1.0":
        raise ValueError("Unsupported M3 rebuild manifest")
    if case_id is not None and manifest.get("case_id") != case_id:
        raise ValueError("Rebuild manifest belongs to a different case")
    components = {}
    for kind in ("structure", "management"):
        item = manifest["components"][kind]
        try:
            payload = None
            if item["diagnostics"] != kind + "_diagnostics.json":
                raise ValueError("Unexpected diagnostics path")
            if item["artifact"] is not None:
                location = (path.parent / item["artifact"]).resolve()
                if location.parent != path.parent:
                    raise ValueError("Artifact path escapes its run directory")
                if sha256(location.read_bytes()).hexdigest() != item["artifact_sha256"]:
                    raise ValueError(f"{kind} artifact integrity check failed")
                payload = json.loads(location.read_text())
                if kind == "management":
                    validate_management(payload)
                else:
                    validate_structure(payload)
                if payload["schema_version"] != item["schema_version"] or payload["completion_status"] != item["status"]:
                    raise ValueError("Artifact schema/status differs from manifest")
            elif item["status"] in ("complete", "partial"):
                raise ValueError("Successful component is missing its artifact")
            components[kind] = {"status": item["status"], "validation_status": item["validation_status"],
                                "artifact": payload, "error": item.get("reason")}
        except (ValueError, OSError, KeyError, TypeError, SchemaValidationError) as exc:
            components[kind] = {"status": "failed", "validation_status": "failed", "artifact": None,
                                "error": f"{type(exc).__name__}: {str(exc)[:1500]}"}
    return manifest, components


def validate_context(block, *, company=None, filing_period=None, cutoff=None):
    import re
    def identity(value):
        tokens = re.findall(r"[a-z0-9]+", value.casefold())
        while tokens and tokens[-1] in {"inc", "incorporated", "corp", "corporation", "llc", "ltd"}:
            tokens.pop()
        return tokens
    companies = []
    structure = block["m3_rebuild"]["structure"]["artifact"]
    management = block["m3_rebuild"]["management"]["artifact"]
    if structure:
        companies.append(next(iter(structure["companies"].values()))["company_display_name"])
    if management:
        companies.append(management["company"])
        if filing_period is not None and management["filing_period"] != str(filing_period):
            raise ValueError("Rebuild management filing period differs from selected BO case period")
        if cutoff is not None and management["roster_date"] and management["roster_date"] > str(cutoff)[:10]:
            raise ValueError("Executive roster is later than evidence cutoff")
    if company is not None:
        companies.append(company)
    if companies and any(identity(name) != identity(companies[0]) for name in companies):
        raise ValueError("Rebuild artifacts and consuming case refer to different companies")
    return block


def synthesis_block(manifest_path, *, case_id=None):
    manifest, components = load_components(manifest_path, case_id=case_id)
    block = {"module": "M3", "workflow": "m3-rebuild", "schema_version": "m3-rebuild-handoff-1.0",
            "status": "complete" if all(c["status"] == "complete" for c in components.values()) else "incomplete",
            "agent_ids": ["m3_structure_conversion", "m3_management_assessment"],
            "findings": [],
            "m3_rebuild": components,
            "source_manifest": str(Path(manifest_path).resolve()), "source_run_id": manifest["run_id"],
            "missing_information": ["Educational structure is not a verified legal/obligor assessment. Management observations are bounded continuity rules, not competence/governance ratings."],
            "consumption_instruction": "Read the two typed artifacts independently. Preserve directions, gaps, quotations and simulation provenance. Do not convert these into legacy scored hypotheses, verified entities, cash-flow claims or governance ratings."}

    return validate_context(block)


def registry_entries(manifest_path, root, case_id):
    from app.schemas.artifact_manifest import ArtifactEntry, ArtifactType, ExecutionStatus, ProvenanceRecord, SnapshotEquivalence
    from app.core.hashing import content_hash
    path = Path(manifest_path).resolve()
    manifest, components = load_components(path, case_id=case_id)
    entries = []
    for kind, component in components.items():
        item = manifest["components"][kind]
        payload = component["artifact"]
        source = payload.get("source", payload.get("provenance", {})) if payload else {}
        filename = path.parent / (item["artifact"] or item["diagnostics"])
        try:
            location = str(filename.relative_to(Path(root).resolve()))
        except ValueError:
            location = str(filename)
        try:
            diag = json.loads((path.parent / (kind + "_diagnostics.json")).read_text())
        except (OSError, ValueError):
            diag = {}
        entries.append(ArtifactEntry(
            artifact_id=f"m3.rebuild.{kind}.{manifest['run_id']}",
            artifact_type=ArtifactType.CORPORATE_STRUCTURE if kind == "structure" else ArtifactType.MODULE_OUTPUT,
            case_id=case_id, run_id=manifest["run_id"], producer_module="M3",
            producer_agent="m3_structure_conversion" if kind == "structure" else "m3_management_assessment",
            schema_version=item["schema_version"], file_location=location,
            content_hash=content_hash(payload) if payload else None,
            status={"complete": ExecutionStatus.VALIDATED, "partial": ExecutionStatus.INCOMPLETE,
                    "failed": ExecutionStatus.FAILED, "not_attempted": ExecutionStatus.ABSENT}[component["status"]], status_reason=component["error"],
            validation_status=component["validation_status"], model_id=diag.get("model"), model_invoked=diag.get("request_attempted"),
            provenance=ProvenanceRecord(case_id=case_id,
                reporting_period=payload.get("filing_period") if payload else None,
                source_signature=source.get("sha256", source.get("source_sha256")),
                filing_identifier=source.get("path"), snapshot_equivalence=SnapshotEquivalence.UNVERIFIED,
                equivalence_note="Independent selected-filing/submitted-JSON path; no canonical snapshot equivalence asserted.")))
    return entries


def unavailable_block(case_id):
    return {"module": "M3", "workflow": "m3-rebuild", "schema_version": "m3-rebuild-handoff-1.0",
            "status": "incomplete", "findings": [], "agent_ids": ["m3_structure_conversion", "m3_management_assessment"],
            "case_id": case_id, "m3_rebuild": {kind: {"status": "not_attempted", "validation_status": "not_attempted",
                "artifact": None, "error": "Required component input unavailable"} for kind in ("structure", "management")},
            "missing_information": ["Corporate onboarding JSON and/or selected filing/extraction are unavailable." ]}
