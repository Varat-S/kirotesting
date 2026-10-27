"""Tiny synthetic schema probe. Never run without separately authorized cost."""
import argparse
import json
from .sources import sources_from_pages
from .provider import VertexExtractor
from .workflow import diagnostics
from .management import assess_management
from .contracts import validate_management, validate_extraction


def synthetic_sources():
    return sources_from_pages([
        "Form 10-K\nExample Corp\nFor the fiscal year ended December 31, 2024",
        "Information about our Executive Officers\nAs of February 1, 2025, our executive officers were as follows:\n"
        "Alex Sample........ 50 Chief Executive Officer\n"
        "Alex Sample was appointed Chief Executive Officer effective May 1, 2024. "
        "Alex Sample previously served as President of the Company from January 2020 through April 2024.\nItem 1A. Risk Factors"],
        "Example Corp", "2024-12-31")


def main(argv=None):
    p = argparse.ArgumentParser(description="Separately gated small synthetic Vertex schema compatibility probe")
    p.add_argument("--authorize-paid-schema-probe", action="store_true")
    args = p.parse_args(argv)
    if not args.authorize_paid_schema_probe:
        p.error("Separate authorization required; no request attempted")
    diag = diagnostics()
    try:
        sources = synthetic_sources()
        raw = VertexExtractor(authorized=True).extract(sources, diag)
        diag["error_stage"] = "parsing"
        diag["parsing"] = "running"
        payload = json.loads(raw)
        diag["parsing"] = "passed"
        diag["error_stage"] = "schema_validation"
        validate_extraction(payload)
        diag["schema_validation"] = "passed"
        diag["error_stage"] = "evidence_validation"
        artifact = assess_management(payload, sources)
        validate_management(artifact)
        diag["schema_validation"] = "passed"
        diag["quotation_validation"] = "passed" if not artifact["rejected_profiles"] else "partial"
        diag["semantic_validation"] = "passed_bounded_rules" if not artifact["rejected_profiles"] else "partial"
        diag["error_stage"] = None
        print(json.dumps({"synthetic_probe": True, "diagnostics": diag, "assessment": artifact}, indent=2))
        return int(artifact["completion_status"] != "complete")
    except Exception as exc:
        diag["error_stage"] = diag["error_stage"] or "probe_validation"
        if diag["error_stage"] in ("parsing", "schema_validation"):
            diag[diag["error_stage"]] = "failed"
        diag["details"].append(str(exc)[:1500])
        print(json.dumps({"synthetic_probe": True, "diagnostics": diag}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
