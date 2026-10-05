"""Read-only projections of saved evidence, including reproducible exports."""

from app.core.hashing import canonical_json, content_hash


def metric_review(preview, definitions):
    facts = preview["evidence"]["facts"]
    available, unavailable = [], []
    for name, metric in sorted(preview["metrics"].items()):
        definition = definitions.get(
            (metric["metric_definition_id"], metric["metric_definition_version"]), {}
        )
        required = definition.get("inputs", [])
        missing = []
        for field in required:
            supplied = metric.get("inputs", {}).get(field)
            if (
                supplied is not None
                and supplied.get("value") is not None
                and supplied.get("status")
                not in {"missing", "not_disclosed", "not_applicable", "conflicting"}
            ):
                continue
            observed_name = "revenue" if field == "revenue_prior" else field
            candidates = [
                f
                for f in facts
                if f["name"] == observed_name
                or observed_name in f.get("mapping_candidates", [])
            ]
            if supplied is not None:
                reason = (
                    "Supplied input is missing or conflicting; review reconciliation."
                )
            elif any(f.get("mapping_status") == "mapped" for f in candidates):
                reason = "Observed in the case, but not supplied for this calculation. Check its period, scope, dimensions and reconciliation."
            elif candidates:
                reason = "Candidate observations exist, but mapping requires review."
            else:
                reason = "Not extracted as a canonical input in this case; it may exist in the source documents."
            missing.append(
                {
                    "name": field,
                    "reason": reason,
                    "candidate_fact_ids": [f["fact_id"] for f in candidates],
                }
            )
        item = {
            "name": name,
            "metric": metric,
            "required_inputs": required,
            "missing_inputs": missing,
        }
        (available if metric["result"] is not None else unavailable).append(item)
    return {
        "available": available,
        "unavailable": unavailable,
        "total": len(preview["metrics"]),
        "mapped_observations": sum(f.get("mapping_status") == "mapped" for f in facts),
        "total_observations": len(facts),
        "extracted_fields": sorted(
            {f["name"] for f in facts if f.get("mapping_status") == "mapped"}
        ),
    }


def stage_outputs(preview):
    """Combined extraction/mapping is stored; do not invent separate intermediates."""
    evidence = preview["evidence"]
    return {
        "admission": (
            "Source admission and cutoff",
            {
                "cutoff": evidence["evidence_cutoff_timestamp"],
                "admitted_sources": evidence["documents"],
                "rejected_sources": preview["rejected_sources"],
            },
        ),
        "extraction_mapping": (
            "Extracted and mapped evidence (combined)",
            {
                "facts": evidence["facts"],
                "sec_filings": evidence["sec_filings"],
                "narrative_evidence": evidence["narrative_evidence"],
                "mapping_issues": preview["mapping_issues"],
            },
        ),
        "reconciliation": (
            "Reconciled financial values and quality",
            {
                "financials": evidence["financials"],
                "data_quality": evidence["data_quality"],
                "provenance": evidence["provenance"],
            },
        ),
        "canonical_evidence": ("Canonical evidence snapshot", evidence),
        "metrics": ("Deterministic metric outputs", preview["metrics"]),
        "context": (
            "Trends and benchmarks",
            {"trends": preview["trends"], "benchmarks": preview["benchmarks"]},
        ),
        "review": (
            "Coverage and review issues",
            {
                "coverage": preview["coverage"],
                "mapping_issues": preview["mapping_issues"],
                "escalations": preview["escalations"],
            },
        ),
        "llm_input": ("Exact prepared LLM input", preview["next_llm_input"]),
        "full_preview": ("Complete saved pre-LLM result", preview),
    }


def output_manifest(preview):
    base = f"/inspect/{preview['case_id']}/{preview['evidence_version']}"
    return {
        "schema_version": "preview-exports-1.0",
        "case_id": preview["case_id"],
        "evidence_version": preview["evidence_version"],
        "algorithm": "SHA-256",
        "serialization": "UTF-8 canonical_json: sorted keys, no insignificant whitespace, ensure_ascii=False; no trailing newline",
        "origin": "Read-only views derived from the saved pre-LLM result. Checksums are computed at export; they are not hashes recorded at each processing stage or a finalization signature.",
        "artifacts": [
            {
                "name": name,
                "label": label,
                "sha256": content_hash(payload),
                "size_bytes": len(canonical_json(payload).encode("utf-8")),
                "url": f"{base}/artifacts/{name}.json",
            }
            for name, (label, payload) in stage_outputs(preview).items()
        ],
    }
