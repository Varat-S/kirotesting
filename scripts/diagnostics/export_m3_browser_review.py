"""Offline, allowlisted projection of saved M3 proposals for local browser review.

Never exports raw model/request bundles or opens a provider. Private database is
read-only. Saved analytical artifacts remain unchanged.
"""
import argparse
import json
from pathlib import Path

from sqlalchemy import select

from app.models.base import create_engine_and_session
from app.models.orm import ModelRun
from app.services.bo.m3_diagnostics import load_recorded_m3_run


def export(database: Path, output: Path):
    manifest = json.loads((output / "run_manifest.json").read_text())
    validation = json.loads((output / "validation_report.json").read_text())
    analysis = json.loads((output / "m3_analysis.json").read_text())
    case = manifest["case_id"]
    engine, sessions = create_engine_and_session(
        f"sqlite:///file:{database.resolve()}?mode=ro&uri=true")
    with sessions() as session:
        runs = list(session.scalars(select(ModelRun).where(ModelRun.case_id == case)))
        if len(runs) != 1:
            raise ValueError("Expected exactly one recorded model run for the saved case.")
        bundle = load_recorded_m3_run(session, runs[0].run_id)
    engine.dispose()
    try:
        response = json.loads(bundle["raw_response"])
    except ValueError:
        response = {}
    from app.services.bo.m3_contract import COMPACT_ID, decode_transport
    if response and bundle['recorded_request']['response_schema'].get('$id') == COMPACT_ID:
        response = decode_transport(response, bundle['recorded_request']['response_schema'])  # Labels only; no repair/defaults/validation promotion.
    assessment = response.get("m3_assessment") or {}
    inputs = bundle["recorded_request"]["inputs"]
    evidence = {e["evidence_id"]: e for e in inputs.get("evidence", [])}
    quote_index = {}
    for h in response.get("hypotheses", []):
        for q in h.get("evidence_quotes", []):
            quote_index.setdefault(q["evidence_id"], []).append(q["quote"])

    def citation(eid):
        passage = evidence.get(eid, {})
        quotes = quote_index.get(eid, [])
        quote = "\n\n".join(quotes) if quotes else passage.get("text", "")[:2400]
        return {"evidence_id": eid, "section": passage.get("section"), "quote": quote,
            "quotation_kind": "model_quoted" if quotes else "recorded_source_excerpt",
            "citation_exists": eid in evidence,
            "quoted_text_matches_source": all(q in passage.get("text", "") for q in quotes) if quotes else None}

    admitted = set(analysis.get("admitted_structural_credit_finding_ids", []))
    conclusion = analysis.get("consolidated_conclusion") or {}
    admitted.update(conclusion.get("admitted_management_signal_ids", []))
    admitted.update(conclusion.get("admitted_governance_signal_ids", []))
    dispos = {f["id"]: f for f in validation.get("finding_dispositions", [])}
    # The legacy validation-report table lists management/governance rows only.
    # Structural outcomes already exist in the saved governed analysis; use
    # those actual results rather than displaying a blank validator disposition.
    for finding in analysis.get('ai_structural_credit_findings', []):
        dispos[finding['finding_id']] = finding
    gate_passed = validation["validation_stages"].get("assessment_governance") == "performed"
    proposals = []
    for bucket, kind, id_key in [("structural_credit_findings", "structural", "finding_id"),
        ("management_assessments", "management", "observation_id"),
        ("governance_screening", "governance", "finding_id")]:
        for f in assessment.get(bucket, []):
            fid = f.get(id_key)
            governed = dispos.get(fid, {})
            status = "unverified"
            if gate_passed:
                if fid in admitted:
                    status = "qualified" if governed.get("disposition", f.get("disposition")) == "qualified" else "accepted"
                elif governed.get("disposition") == "rejected":
                    status = "rejected"
                elif governed.get("disposition") == "unverified":
                    status = "unverified"
                elif f.get("assessment_availability") == "insufficient_evidence":
                    status = "unavailable"
            reasons = []
            if not gate_passed:
                reasons.append("The response failed before Validator 7; this proposal is not an admitted finding.")
                if validation.get("specialist_failure"):
                    reasons.append(validation["specialist_failure"])
            reasons += [str(i.get("reason")) for i in validation.get("validation_issues", [])
                        if i.get("item_id") == fid]
            eids = list(dict.fromkeys(f.get("supporting_evidence_ids", []) + f.get("contradicting_evidence_ids", [])))
            proposals.append({"id": fid, "kind": kind, "status": status,
                "proposed_disposition": f.get("proposed_disposition", f.get("disposition")),
                "validator_disposition": governed.get("disposition") if gate_passed else "not_run",
                "credit_direction": f.get("credit_direction"),
                "evidence": f.get("structural_observation") or f.get("evidence_summary") or "See cited passage below.",
                "inference": f.get("credit_hypothesis") or f.get("hypothesis"),
                "credit_implication": f.get("causal_mechanism") or f.get("credit_mechanism"),
                "citations": [citation(eid) for eid in eids], "validation_reasons": reasons,
                "qualifications": f.get("qualifications", []) + f.get("missing_information", [])
                    + f.get("uncertainties", []) + f.get("mitigating_considerations", []),
                "referenced_node_ids": f.get("referenced_node_ids", []),
                "referenced_link_ids": f.get("referenced_link_ids", []),
                "supporting_evidence_investigated": f.get("supporting_evidence_investigated"),
                "contradictory_evidence_investigated": f.get("contradictory_evidence_investigated")})
    stability = assessment.get("management_stability") or {}
    executives = [{k: e.get(k) for k in ("executive_id", "name", "current_role", "facts",
        "prior_company_experience", "transition_type", "recent_appointment")} for e in stability.get("executives", [])]
    parameters = [{k: p.get(k) for k in ("parameter_id", "status", "value", "evidence_ids", "hypothesis_ids")}
                  for p in response.get("parameters", [])]
    hypotheses = [{"id": h.get("hypothesis_id"), "claim": h.get("claim"),
        "proposed_disposition": h.get("disposition"), "direction": h.get("direction"),
        "citations": [citation(q["evidence_id"]) for q in h.get("evidence_quotes", [])]}
        for h in response.get("hypotheses", [])]
    transport_path = output / "transport_summary.json"
    transport = json.loads(transport_path.read_text()) if transport_path.exists() else {}
    review = {"case_id": case, "analysis_run_id": manifest["analysis_run_id"],
        "origin": "allowlisted projection of this case's saved actual Gemini response; not a fallback",
        "assessment_version": assessment.get("schema_version"), "management_version": stability.get("schema_version"),
        "http_status": transport.get("http_status"), "finish_reasons": transport.get("finish_reasons"),
        "proposals": proposals, "executives_proposed": executives, "parameters_proposed": parameters,
        "hypotheses_proposed": hypotheses, "management_review_proposed": {k: stability.get(k)
            for k in ("as_of_date", "as_of_source", "review_scope", "limitations", "recent_change_window_years")}}
    target = output / "m3_browser_review.json"
    with target.open("x", encoding="utf-8") as f:
        json.dump(review, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(json.dumps({"path": str(target), "proposals": len(proposals),
                      "executive_profiles_proposed": len(executives), "provider_calls": 0}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    export(args.database, args.output)
