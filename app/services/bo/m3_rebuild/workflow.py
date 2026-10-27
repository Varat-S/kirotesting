"""Two independent saved components and a deterministic manifest; no synthesis call."""
from hashlib import sha256
import json
from pathlib import Path
import re
import uuid
from .contracts import SCHEMA_VERSION, ADAPTER_VERSION, validate_extraction, validate_management
from .management import assess_management, missing_management, QuotationError
from .provider import PROMPT_VERSION
from .sources import locate_sources
from .structure import convert_structure, render_svg, structure_filename


def write_json(path, payload):
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def diagnostics():
    return {"request_attempted": False, "http_status": None, "answer_returned": False,
            "model": None, "adapter_version": ADAPTER_VERSION, "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION, "parsing": "not_attempted", "schema_validation": "not_attempted",
            "quotation_validation": "not_attempted", "semantic_validation": "not_attempted",
            "token_usage": None, "error_stage": None, "details": [],
            "unavailable_values": "null HTTP/token values mean unavailable, never fabricated"}


def run_rebuild(output_dir, *, case_id, structure_input=None, filing_pdf=None,
                company=None, filing_period=None, extraction=None, extractor=None,
                filing_sources=None, structure_provenance=None,
                evidence_context=None, filing_packet=None, reviewer=None):
    """No live extractor is constructed implicitly. Never overwrite an existing run.

    Inputs can be paths or decoded corporate documents / saved extraction objects.
    Both branches catch their own failures; output directory creation is common IO.
    """
    directory = Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": "m3-rebuild-manifest-1.0", "case_id": case_id,
                "run_id": directory.name, "workflow": "m3-rebuild", "components": {}}
    if evidence_context is not None:
        manifest["evidence_context"] = evidence_context
    for component in ("structure", "management"):
        diag = diagnostics()
        if component == "structure":
            diag.update(adapter_version="deterministic-structure-1.0", schema_version="2.0", prompt_version=None)
        result = {"status": "not_attempted", "artifact": None, "schema_version": None,
                  "validation_status": "not_attempted", "artifact_sha256": None}
        stage = "input"
        try:
            if component == "structure" and structure_input is not None:
                stage = "parsing"
                document = json.loads(Path(structure_input).read_text()) if isinstance(structure_input, (str, Path)) else structure_input
                diag.update(parsing="passed", schema_validation="running")
                stage = "schema_validation"
                artifact = convert_structure(document)
                if structure_provenance:
                    artifact["provenance"].update(structure_provenance)
                diag.update(schema_validation="passed", quotation_validation="not_applicable", semantic_validation="passed")
                stage = "render"
                svg = render_svg(artifact)
                name = structure_filename(artifact)
                (directory / "corporate_structure.svg").write_text(svg, encoding="utf-8")
                result["preview"] = "corporate_structure.svg"
            elif component == "management" and (
                    filing_sources is not None or filing_packet is not None
                    or filing_pdf is not None):
                stage = "source_discovery"
                if filing_sources is not None:
                    sources = filing_sources
                elif filing_packet is not None:
                    from .sources import sources_from_evidence_packet
                    sources = sources_from_evidence_packet(filing_packet)
                else:
                    sources = locate_sources(filing_pdf, company, filing_period)
                write_json(directory / "management_sources.json", sources.to_dict())
                if not sources.section_ids:
                    artifact = missing_management(sources)
                    diag.update(parsing="not_applicable", schema_validation="not_applicable",
                                quotation_validation="unavailable", semantic_validation="information_insufficiency")
                else:
                    stage = "extraction"
                    if extraction is not None:
                        diag["extraction_mode"] = "saved_offline_extraction"
                        raw = Path(extraction).read_text() if isinstance(extraction, (str, Path)) else extraction
                    elif extractor is not None:
                        diag["extraction_mode"] = getattr(
                            extractor, "extraction_mode", "model"
                        )
                        raw = extractor.extract(sources, diag)
                        # Preserve raw returned answer for replay, even if malformed.
                        (directory / "management_response.txt").write_text(raw or "", encoding="utf-8")
                    else:
                        reason = "Executive section found; extraction not attempted because no saved extraction or explicitly authorized extractor was supplied"
                        result["reason"] = reason
                        diag["details"].append({"stage": "extraction", "detail": reason})
                        continue
                    stage = "parsing"
                    if isinstance(raw, str):
                        raw = json.loads(raw)
                    diag["parsing"] = "passed"
                    write_json(directory / "management_extraction.json", raw)
                    stage = "schema_validation"
                    validate_extraction(raw)
                    diag["schema_validation"] = "passed"
                    write_json(directory / "management_validated_extraction.json", raw)
                    stage = "evidence_validation"
                    artifact = assess_management(raw, sources, reviewer=reviewer)
                    write_json(directory/'management_claim_validation.json',artifact['validation'])
                    if hasattr(extractor,'record_validation'):
                        extractor.record_validation(artifact)
                    claims=artifact['validation']['claim_results']
                    diag["quotation_validation"] = "partial" if any(
                        c['outcome']=='unsupported' for claim in claims for c in claim['citation_results']) else "passed"
                    diag["semantic_validation"] = "claim_level_"+artifact['completion_status']
                    diag['validator7_mode']=artifact['validation']['validator_7']['mode']
                stage = "artifact_validation"
                validate_management(artifact)
                name = "Senior Management Assessment.json"
            else:
                reason = "Corporate onboarding JSON unavailable" if component == "structure" else "Selected company Form 10-K unavailable"
                result["reason"] = reason
                diag["details"].append({"stage": "input", "detail": reason})
                continue
            stage = "artifact_write"
            write_json(directory / name, artifact)
            result.update(status=artifact["completion_status"], artifact=name,
                          schema_version=artifact["schema_version"],
                          validation_status="passed" if artifact["completion_status"] == "complete" else "partial",
                          artifact_sha256=sha256((directory / name).read_bytes()).hexdigest())
        except Exception as exc:
            result.update(status="failed", validation_status="failed")
            diag["error_stage"] = diag["error_stage"] or stage
            if stage in ("parsing", "schema_validation"):
                diag[stage] = "failed"
            if stage == "evidence_validation":
                diag["quotation_validation"] = "failed" if isinstance(exc, QuotationError) else "passed_or_not_applicable"
                diag["semantic_validation"] = "not_attempted" if isinstance(exc, QuotationError) else "failed"
            diag["details"].append({"error_type": type(exc).__name__, "detail": str(exc)[:2500]})
        finally:
            diag_name = component + "_diagnostics.json"
            write_json(directory / diag_name, diag)
            result["diagnostics"] = diag_name
            manifest["components"][component] = result
    write_json(directory / "m3_rebuild_manifest.json", manifest)
    return manifest


def run_rebuild_from_packet(output_dir, *, case_id, packet, extraction=None,
                            extractor=None, reviewer=None):
    """Run both components solely from one admitted, routed evidence packet."""
    from app.schemas.agentic import EvidencePacket
    from app.services.agents.router import EvidenceRouter

    packet = EvidencePacket.model_validate(
        packet.model_dump(mode="json") if hasattr(packet, "model_dump") else packet
    )
    if packet.agent_id != "m3_corporate_management_input":
        raise ValueError("Packet is not the routed M3 input packet")
    if packet.case_id != case_id:
        raise ValueError("M3 packet belongs to a different case")
    if not packet.packet_hash or EvidenceRouter.hash_packet(packet) != packet.packet_hash:
        raise ValueError("M3 evidence packet integrity check failed")
    context = dict(packet.context)
    records = [
        item for item in packet.structured_evidence
        if item.get("evidence_type") == "corporate_structure_onboarding"
        and item.get("evidence_id") in packet.evidence_ids
    ]
    if len(records) > 1:
        raise ValueError("M3 packet contains multiple corporate structure sources")
    structure = records[0] if records else None
    corporate_document_id = context.get("corporate_document_id")
    corporate_document_sha256 = context.get("corporate_document_sha256")
    if structure is not None:
        if structure["document_id"] != corporate_document_id:
            raise ValueError("Corporate evidence differs from selected document identity")
        if structure["document_sha256"] != corporate_document_sha256:
            raise ValueError("Corporate evidence differs from selected document hash")

        def company_key(value):
            tokens = re.findall(r"[a-z0-9]+", value.casefold())
            while tokens and tokens[-1] in {
                "inc", "incorporated", "corp", "corporation", "llc", "ltd"
            }:
                tokens.pop()
            return tokens

        if (not context.get("company") or company_key(structure["reporting_company"])
                != company_key(context["company"])):
            raise ValueError("Corporate evidence differs from selected reporting company")
    structure_provenance = None
    if structure is not None:
        structure_provenance = {
            "source_sha256": structure["document_sha256"],
            "payload_sha256": sha256(json.dumps(
                structure["payload"], sort_keys=True, ensure_ascii=False
            ).encode()).hexdigest(),
            "input_document_id": structure["document_id"],
            "structured_evidence_id": structure["evidence_id"],
            "reporting_company": structure["reporting_company"],
            "declared_status": structure["declared_status"],
            "assumption_status": structure["assumption_status"],
            "canonical_snapshot_version": packet.snapshot_version,
            "evidence_packet_hash": packet.packet_hash,
        }
    evidence_context = {
        "snapshot_version": packet.snapshot_version,
        "router_version": packet.router_version,
        "packet_hash": packet.packet_hash,
        "company": context.get("company"),
        "filing_period": context.get("filing_period"),
        "evidence_cutoff_timestamp": context.get("evidence_cutoff_timestamp"),
        "corporate_document_id": context.get("corporate_document_id"),
        "management_document_id": context.get("management_document_id"),
    }
    return run_rebuild(
        output_dir, case_id=case_id,
        structure_input=structure["payload"] if structure else None,
        company=context.get("company"), filing_period=context.get("filing_period"),
        extraction=extraction, extractor=extractor,
        filing_packet=packet if context.get("management_document_id") else None,
        structure_provenance=structure_provenance,
        evidence_context=evidence_context,
        reviewer=reviewer,
    )


def run_directory(output_root, case_id):
    if not case_id or Path(case_id).name != case_id or case_id in (".", ".."):
        raise ValueError("case_id must be a single safe path component")
    return Path(output_root) / "m3_rebuild" / case_id / ("run_" + uuid.uuid4().hex[:12])
