"""Business Overview outputs: analyst memo, audit report and downstream handoff.

Same rule as the main memo generator: JSON is the source of truth and the HTML
(and optional PDF) is a pure rendering of it.

* The MEMO carries only what the validated Business conclusion says: material,
  accepted or appropriately qualified findings with citations. Rejected and
  unverified hypotheses are not in it.
* The AUDIT REPORT is assembled from RECORDED rows (agent runs, evidence
  packets, retrieval attempts, hypothesis versions, parameter results, scores,
  conclusions, challenge findings). Nothing is reconstructed after the fact: an
  agent with no recorded run reads ``not_executed`` and a step that did not
  happen reads ``not_performed``. It shows structured outputs, validations and
  evidence decisions, not model-private reasoning.
* The HANDOFF is the typed ``AcceptedBusinessOverview`` for later Structuring /
  Credit Orchestrator use; it stays ``pending`` until a human accepts it.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.hashing import canonical_json, content_hash
from app.models.orm import (
    AcceptedBusinessOverviewRow,
    AgentRunRow,
    ChallengeFindingRow,
    EvidencePacketRow,
    HypothesisRecordRow,
    ParameterResultRow,
    RetrievalAttemptRow,
    RiskScoreRow,
    TopicConclusionRow,
)
from app.schemas.bo import AcceptedBusinessOverview
from app.services.bo.plans import (
    BO_ACTIVE_AGENT_IDS,
    BO_NARROW_AGENT_IDS,
    BO_RESILIENCE_AGENT_ID,
    BO_SECTIONS,
    BO_SPEC_BY_AGENT,
    BO_V1_TO_V2_AGENT,
)
from app.services.bo.synthesis import iter_claims, score_explanation
from app.services.llm.client import FakeLLMBackend
from app.services.pipeline.artifacts import StageRecorder

MEMO_VERSION = "bo-memo-1.0"
AUDIT_VERSION = "bo-audit-1.0"
_TEMPLATES = Path(__file__).parents[1] / "reporting" / "templates"

STATUS_TEXT = {
    "accepted": "Challenged without material finding. Human review is still "
                "required before this section is approved.",
    "accepted_with_qualification": "Accepted subject to the qualifications listed "
                                   "under Outstanding Questions. Human review required.",
    "human_review_required": "Unresolved material challenge. This is NOT a final "
                             "Business Overview; mandatory human review.",
    "unavailable": "No validated Business conclusion was produced. See the audit "
                   "report for the reason.",
}
MODE_TEXT = {
    "offline_simulation": "OFFLINE SIMULATION: agent responses came from a scripted "
                          "fake backend, not a live model. This demonstrates the "
                          "pipeline, not model analysis.",
    "live_model": "Agent responses were produced by a live model provider.",
}


def execution_mode(backend) -> str:
    return "offline_simulation" if isinstance(backend, FakeLLMBackend) else "live_model"


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True, lstrip_blocks=True,
    )


def render_memo_html(memo: dict[str, Any]) -> str:
    return _env().get_template("bo_memo.html.j2").render(memo=memo)


def render_audit_html(audit: dict[str, Any]) -> str:
    return _env().get_template("bo_audit.html.j2").render(audit=audit)


class BoReportBuilder:
    def __init__(self, session: Session, *, output_root="output") -> None:
        self.session = session
        self.output_root = Path(output_root)

    # -- entry ----------------------------------------------------------------

    def finish(self, run, *, write_outputs: bool = True, produce_pdf: bool = False):
        from app.services.bo.runner import BoRunResult

        mode = execution_mode(run.runner.backend)
        sources = self._sources(run)
        memo = self._memo(run, mode, sources)
        audit = self._audit(run, mode, sources)
        handoff = self._handoff(run, mode)
        record = {
            "analysis_run_id": run.analysis_run_id,
            "conclusion_status": run.conclusion_status,
            "execution_mode": mode,
            "agents": {a: o.as_payload() for a, o in run.agents.items()},
            "stages": run.stage_log,
            "challenge_rounds": run.challenge_rounds,
            "remediation": run.remediation,
        }
        recorder = StageRecorder(self.session, run.case_id, run.snapshot_version)
        recorder.record(f"bo_memo:{run.analysis_run_id}", memo)
        recorder.record(f"bo_audit:{run.analysis_run_id}", audit)

        paths: dict[str, Path] = {}
        if write_outputs:
            folder = self.output_root / "bo" / run.case_id / run.analysis_run_id
            folder.mkdir(parents=True, exist_ok=True)
            for name, payload in (("business_overview.json", memo),
                                  ("audit_report.json", audit),
                                  ("handoff.json", handoff)):
                paths[name] = folder / name
                paths[name].write_text(canonical_json(payload) + "\n",
                                       encoding="utf-8", newline="\n")
            for name, html in (("business_overview.html", render_memo_html(memo)),
                               ("audit_report.html", render_audit_html(audit))):
                paths[name] = folder / name
                paths[name].write_text(html, encoding="utf-8", newline="\n")
            if produce_pdf:
                from app.services.reporting.memo import (
                    _render_pdf_with,
                    detect_pdf_backend,
                )

                backend = detect_pdf_backend()
                if backend is not None:
                    paths["business_overview.pdf"] = folder / "business_overview.pdf"
                    paths["business_overview.pdf"].write_bytes(
                        _render_pdf_with(backend, render_memo_html(memo)))

        self.session.add(AcceptedBusinessOverviewRow(
            handoff_id=handoff["handoff_id"], analysis_run_id=run.analysis_run_id,
            case_id=run.case_id, snapshot_version=run.snapshot_version,
            human_review_status="pending", payload=handoff,
            content_hash=handoff["content_hash"],
            artifacts={k: str(v) for k, v in paths.items()}))
        self.session.flush()
        return BoRunResult(
            analysis_run_id=run.analysis_run_id, case_id=run.case_id,
            snapshot_version=run.snapshot_version,
            conclusion_status=run.conclusion_status, execution_mode=mode,
            record=record, memo=memo, audit=audit, handoff=handoff,
            output_paths=paths)

    # -- shared ---------------------------------------------------------------

    def _rows(self, model, run_id, order=None):
        stmt = select(model).where(model.analysis_run_id == run_id)
        if order is not None:
            stmt = stmt.order_by(order)
        return self.session.execute(stmt).scalars().all()

    def _sources(self, run) -> dict[str, dict[str, Any]]:
        documents = {d.get("document_id"): d for d in run.evidence.documents}
        out = {}
        for passage in run.evidence.narrative_evidence:
            doc = documents.get(passage.get("document_id"), {})
            out[passage["evidence_id"]] = {
                "evidence_id": passage["evidence_id"],
                "document_id": passage.get("document_id"),
                "filename": doc.get("filename"),
                "form": doc.get("form"),
                "sec_accession": passage.get("sec_accession") or doc.get("accession"),
                "source_url": doc.get("source_url"),
                "section": passage.get("section"),
                "heading": passage.get("heading"),
                "location": passage.get("location"),
                "page": passage.get("page"),
                "excerpt": (passage.get("text") or "")[:400],
            }
        return out

    def _case(self, run) -> dict[str, Any]:
        borrower = next((e for e in run.evidence.entities if e.get("borrower_flag")), {})
        cutoff = run.evidence.evidence_cutoff_timestamp
        return {
            "case_id": run.case_id,
            "borrower": borrower.get("legal_name"),
            "borrower_entity_id": borrower.get("entity_id"),
            "as_of_date": run.evidence.as_of_date.isoformat()
            if run.evidence.as_of_date else None,
            "evidence_cutoff": cutoff.isoformat() if cutoff else None,
            "evidence_snapshot_version": run.snapshot_version,
        }

    def _run_block(self, run, mode) -> dict[str, Any]:
        return {
            "analysis_run_id": run.analysis_run_id,
            "scope": "business_overview_only",
            "execution_mode": mode,
            "execution_label": MODE_TEXT[mode],
            "agent_registry_version": run.registry.version,
            "agent_registry_hash": run.registry.registry_hash(),
            "started_at": run.started_at.isoformat(),
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        }

    def _score_block(self, run) -> dict[str, Any]:
        block = score_explanation(run.score, run.rubric_notes)
        block["label"] = run.scoring_cfg.content.get("label")
        block["scale"] = run.scoring_cfg.content.get("scale")
        block["scoring_config_version"] = run.scoring_cfg.version
        block["scoring_config_hash"] = run.scoring_cfg.content_hash
        weights = run.scoring_cfg.content.get("business_weights", {})
        block["weights"] = weights
        reasons = []
        for pid in block["missing_required_parameter_ids"]:
            reasons.append(
                f"{pid} is not available"
                + (" (critical dimension)" if pid in block["critical_missing_parameter_ids"]
                   else "")
                + (": no customer-level revenue disclosure was admitted, so no "
                   "customer concentration index was computed."
                   if pid == "customer_hhi" else "."))
        block["explanation"] = (
            "The BusinessRiskScore is computed deterministically from rubric "
            "signals; no model assigns it. " + " ".join(reasons)).strip()
        return block

    # -- memo -----------------------------------------------------------------

    def _memo(self, run, mode, sources) -> dict[str, Any]:
        by_id = {h.hypothesis_id: h for a in run.narrow_agent_ids
                 for h in run.hypotheses.get(a, [])}
        traces = {t.parameter_result_id: t for t in run.indicators.traces}
        cited: list[str] = []
        used_calcs: list[str] = []

        def cite(evidence_ids) -> list[int]:
            numbers = []
            for eid in evidence_ids:
                if eid not in sources:
                    continue
                if eid not in cited:
                    cited.append(eid)
                numbers.append(cited.index(eid) + 1)
            return sorted(set(numbers))

        def render(claim: dict[str, Any]) -> dict[str, Any]:
            hyps = [by_id[h] for h in claim.get("hypothesis_ids") or [] if h in by_id]
            quoted = []
            for q in claim.get("quoted_values") or []:
                trace = traces.get(q.get("parameter_result_id"))
                if trace is not None:
                    used_calcs.append(trace.parameter_result_id)
                    quoted.append({
                        "parameter_result_id": trace.parameter_result_id,
                        "label": trace.label, "value": trace.value,
                        "unit": trace.units, "period": trace.period,
                        "denominator": (trace.denominator or {}).get("name")})
            for h in hyps:
                used_calcs.extend(h.parameter_result_ids)
            directions = sorted({h.direction.value for h in hyps})
            return {
                "claim_id": claim.get("claim_id"),
                "title": claim.get("title"),
                "text": claim.get("text"),
                "category": claim.get("category"),
                "claim_type": claim.get("claim_type"),
                "materiality": claim.get("materiality", "medium"),
                "uncertainty": claim.get("uncertainty"),
                "signal": ("mixed" if len(directions) > 1 else
                           directions[0] if directions else "neutral"),
                "hypothesis_ids": [h.hypothesis_id for h in hyps],
                "citations": cite(claim.get("evidence_ids") or []),
                "quoted_values": quoted,
            }

        parsed = run.orchestrator_output or {}
        claims = [(name, render(c), c.get("section")) for name, c in iter_claims(parsed)]
        overall = next((c for name, c, _ in claims if name == "overall_assessment"), None)
        strengths = [c for name, c, _ in claims if name == "strengths"]
        weaknesses = [c for name, c, _ in claims if name == "weaknesses"]

        sections = []
        for agent_id, code, title in run.sections:
            body = [c for name, c, section in claims
                    if section == code and name in ("key_drivers", "material_risks")]
            # Headline strengths/weaknesses of this section are shown once, in
            # the strengths-and-vulnerabilities part, and referenced from here.
            headline = [
                {"claim_id": c["claim_id"], "title": c["title"] or c["text"],
                 "list": name}
                for name, c, section in claims
                if section == code and name in ("strengths", "weaknesses")]
            sections.append({
                "code": code, "title": title, "agent_id": agent_id,
                "agent_status": run.agents[agent_id].status,
                "claims": body,
                "headline_refs": headline,
                "parameters": [
                    {"parameter_id": p.parameter_id, "value": p.value,
                     "status": p.status.value}
                    for p in run.promoted.get(agent_id, [])],
                "notice": None if body or headline else (
                    "No material validated finding was selected for this section."
                    if run.agents[agent_id].status == "validated" else
                    f"Specialist status: {run.agents[agent_id].status}. "
                    "This section is a coverage gap."),
            })

        last = run.challenge_rounds[-1] if run.challenge_rounds else None
        open_findings = [
            {"challenge_id": f["challenge_id"], "severity": f["severity"],
             "issue_type": f["issue_type"], "reason": f["reason"],
             "target": f["target"]}
            for f in (last or {}).get("findings", [])
            if f["severity"] in ("medium", "high", "material")]
        limitations = [
            f"{run.spec_by_agent[a].code} {run.spec_by_agent[a].title}: "
            f"specialist {o.status}."
            for a, o in run.agents.items()
            if a in run.spec_by_agent and o.status != "validated"]
        limitations += [
            f"No admitted passage matched the {g.purpose} search "
            f"'{g.query_id}' ({run.spec_by_agent[a].code})."
            for a in run.narrow_agent_ids for g in run.retrieval[a].gaps]
        used = {h for _, c, _ in claims for h in c["hypothesis_ids"]}
        missing = sorted({m for hid in used for m in by_id[hid].missing_information})
        uncertainties = list(dict.fromkeys(
            [*(parsed.get("unresolved_contradictions") or []),
             *(c["uncertainty"] for _, c, _ in claims
               if c["uncertainty"] and c["materiality"] == "high")]))

        return {
            "document": "business_overview_memo",
            "version": MEMO_VERSION,
            "case": self._case(run),
            "run": self._run_block(run, mode),
            "status": {
                "conclusion_status": run.conclusion_status,
                "text": STATUS_TEXT[run.conclusion_status],
                "human_review_status": "pending",
                "challenge": (last or {}).get("outcome", "not_executed"),
                "remediation_round_performed": run.remediation is not None,
            },
            "executive": {
                "provisional_view": overall,
                "strengths": [{"claim_id": c["claim_id"],
                               "title": c["title"] or c["text"]} for c in strengths],
                "weaknesses": [{"claim_id": c["claim_id"],
                                "title": c["title"] or c["text"]} for c in weaknesses],
                "principal_uncertainties": uncertainties,
                "follow_up": list(parsed.get("follow_up") or []),
            },
            "sections": sections,
            "module_conclusions": run.module_conclusions,
            "cross_module_risk_interactions": [
                c for name, c, _ in claims
                if name in ("material_risks", "key_drivers")
                and len(c.get("hypothesis_ids") or []) > 1],
            "strengths_vulnerabilities": {"strengths": strengths,
                                          "weaknesses": weaknesses},
            "presentation": {
                "display": [h.hypothesis_id for h in by_id.values()
                            if h.display_priority.value == "display"],
                "on_demand": [h.hypothesis_id for h in by_id.values()
                              if h.display_priority.value == "on_demand"],
                "suppressed_count": sum(
                    h.display_priority.value == "suppress" for h in by_id.values()),
            },
            "outstanding": {
                "open_questions": list(parsed.get("open_questions") or []),
                "unresolved_contradictions":
                    list(parsed.get("unresolved_contradictions") or []),
                "missing_information": missing,
                "data_limitations": limitations,
                "unresolved_challenges": open_findings,
            },
            "score": self._score_block(run),
            "calculations": [
                traces[i].model_dump(mode="json")
                for i in dict.fromkeys(used_calcs) if i in traces],
            "sources": [{"n": i + 1, **sources[eid]} for i, eid in enumerate(cited)],
        }

    # -- audit ----------------------------------------------------------------

    def _audit(self, run, mode, sources) -> dict[str, Any]:
        rid = run.analysis_run_id
        agent_runs = self._rows(AgentRunRow, rid, AgentRunRow.created_at)
        runs_by_agent: dict[str, list] = {}
        for row in agent_runs:
            runs_by_agent.setdefault(row.agent_id, []).append(row)

        agents = []
        for agent_id in run.active_agent_ids:
            definition = run.registry.get(agent_id)
            spec = run.spec_by_agent.get(agent_id)
            rows = runs_by_agent.get(agent_id, [])
            outcome = run.agents[agent_id]
            agents.append({
                "agent_id": agent_id,
                "code": spec.code if spec else None,
                "role": spec.title if spec else definition.task_type,
                "task_type": definition.task_type,
                "model_tier": definition.model_tier.value,
                "dependencies": list(definition.dependencies),
                "owned_parameters": list(definition.owned_parameters),
                "task_sections": list(spec.task_sections) if spec else [],
                "agent_definition_hash": definition.definition_hash(),
                "prompt_name": definition.prompt,
                "status": outcome.status if rows or outcome.status != "not_executed"
                else "not_executed",
                "detail": outcome.detail,
                "validation_issues": outcome.issues,
                "validator_adjustments": outcome.adjustments,
                "runs": [
                    {"agent_run_id": r.run_id, "execution_wave": r.execution_wave,
                     "validation_status": r.validation_status,
                     "validation_detail": r.validation_detail,
                     "model_id": r.model_id, "prompt_id": r.prompt_id,
                     "prompt_version": r.prompt_version, "prompt_hash": r.prompt_hash,
                     "input_hash": r.input_hash, "cache_key": r.cache_key,
                     "reused_from_cache": r.reused_from_cache,
                     "rerun_reason": r.rerun_reason, "error_state": r.error_state,
                     "latency_ms": r.latency_ms, "usage": r.usage,
                     "parent_run_ids": r.parent_run_ids,
                     "input_evidence_count": len(r.input_evidence_ids or []),
                     "created_at": r.created_at.isoformat()}
                    for r in rows],
            })

        packets = [
            {"agent_id": p.agent_id, "packet_hash": p.packet_hash,
             "router_version": p.router_version,
             "passage_count": len(p.payload.get("narrative_evidence", [])),
             "indicator_count": len(p.payload.get("parameters", [])),
             "evidence": [
                 {k: sources[e].get(k) for k in
                  ("evidence_id", "filename", "section", "location", "sec_accession")}
                 for e in p.evidence_ids if e in sources],
             "instruction_like_passages": [
                 n["evidence_id"] for n in p.payload.get("narrative_evidence", [])
                 if n.get("instruction_like")]}
            for p in self._rows(EvidencePacketRow, rid, EvidencePacketRow.id)]

        attempts = [a.payload for a in self._rows(
            RetrievalAttemptRow, rid, RetrievalAttemptRow.created_at)]
        hypothesis_rows = self._rows(HypothesisRecordRow, rid,
                                     HypothesisRecordRow.created_at)
        hypotheses = [
            {**h.payload, "record_id": h.id, "acceptance_state": h.acceptance_state,
             "supersedes_id": h.supersedes_id}
            for h in hypothesis_rows]
        current = [h for h in hypotheses if h["acceptance_state"] == "accepted"]

        def tally(items):
            out: dict[str, int] = {"supported": 0, "qualified": 0, "rejected": 0,
                                   "unverified": 0}
            for h in items:
                out[h["disposition"]] += 1
            return out

        statistics = {
            "overall": tally(current),
            "by_agent": {a: tally([h for h in current if h["agent_id"] == a])
                         for a in run.narrow_agent_ids},
        }
        parameters = [
            {"parameter_result_id": p.parameter_result_id,
             "parameter_id": p.parameter_id, "topic": p.topic, "method": p.method,
             "value": (p.value or {}).get("v"), "status": p.status,
             "agent_id": p.agent_id, "agent_run_id": p.agent_run_id,
             "formula_id": p.formula_id, "formula_version": p.formula_version,
             "evidence_ids": p.evidence_ids, "source_fact_ids": p.source_fact_ids,
             "source_parameter_ids": p.source_parameter_ids,
             "supersedes_id": p.supersedes_id, "acceptance_state": p.acceptance_state}
            for p in self._rows(ParameterResultRow, rid, ParameterResultRow.created_at)]
        scores = [
            {"score_id": s.score_id, "kind": s.kind, "status": s.status,
             "band": s.band, "coverage_weight": s.coverage_weight,
             "contributing_parameter_ids": s.contributing_parameter_ids,
             "missing_required_parameter_ids": s.missing_required_parameter_ids,
             "critical_missing_parameter_ids": s.critical_missing_parameter_ids,
             "supersedes_id": s.supersedes_id, "acceptance_state": s.acceptance_state}
            for s in self._rows(RiskScoreRow, rid, RiskScoreRow.created_at)]
        conclusions = [
            {"conclusion_id": c.id, "orchestrator_run_id": c.orchestrator_run_id,
             "challenge_status": c.challenge_status, "supersedes_id": c.supersedes_id,
             "acceptance_state": c.acceptance_state, "payload": c.payload}
            for c in self._rows(TopicConclusionRow, rid, TopicConclusionRow.created_at)]
        findings = [
            {"challenge_id": f.challenge_id, "target": f.target,
             "issue_type": f.issue_type, "severity": f.severity, "reason": f.reason,
             "affected_agent_ids": f.affected_agent_ids,
             "requires_reanalysis": f.requires_reanalysis,
             "requested_rerun_scope": f.requested_rerun_scope,
             "evidence_ids": f.evidence_ids}
            for f in self._rows(ChallengeFindingRow, rid, ChallengeFindingRow.created_at)]

        by_id = {h["hypothesis_id"]: h for h in current}
        final = next((c for c in conclusions if c["acceptance_state"] == "accepted"), None)
        provenance = []
        if final is not None and run.orchestrator_output is not None:
            for name, claim in iter_claims(run.orchestrator_output):
                provenance.append({
                    "list": name, "claim_id": claim.get("claim_id"),
                    "section": claim.get("section"),
                    "claim_type": claim.get("claim_type"),
                    "text": claim.get("text"),
                    "hypotheses": [
                        {"hypothesis_id": h, "agent_id": by_id[h]["agent_id"],
                         "disposition": by_id[h]["disposition"],
                         "admissibility": by_id[h]["admissibility"]}
                        for h in claim.get("hypothesis_ids") or [] if h in by_id],
                    "parameter_result_ids": claim.get("parameter_result_ids") or [],
                    "quoted_values": claim.get("quoted_values") or [],
                    "evidence": [
                        {k: sources[e].get(k) for k in
                         ("evidence_id", "filename", "section", "location",
                          "sec_accession")}
                        for e in claim.get("evidence_ids") or [] if e in sources],
                })

        followups = [a for a in attempts if a["origin"] == "agent_request"]
        missing = sorted({m for h in current for m in h.get("missing_information", [])})
        token_totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        token_seen = False
        for row in agent_runs:
            for key in token_totals:
                if row.usage and row.usage.get(key) is not None:
                    token_totals[key] += int(row.usage[key])
                    token_seen = True
        calls = [r for r in agent_runs if not r.reused_from_cache
                 and r.error_state != "TimeoutError"]

        return {
            "document": "business_overview_audit_report",
            "version": AUDIT_VERSION,
            "notice": (
                "This report lists recorded events: structured agent outputs, "
                "tool actions, validations and evidence decisions. It does not "
                "expose or reconstruct model-private reasoning."),
            "case": self._case(run),
            "run": self._run_block(run, mode),
            "conclusion_status": run.conclusion_status,
            "sources": {
                "documents": [
                    {k: d.get(k) for k in
                     ("document_id", "filename", "role", "form", "accession", "cik",
                      "filing_date", "report_date", "available_at", "source_url",
                      "sha256", "parser")}
                    for d in run.evidence.documents],
                "sec_filings": run.evidence.sec_filings,
                "rejected_sources": run.rejected_sources,
                "prior_escalations": run.prior_escalations,
            },
            "evidence_snapshot": {
                "case_id": run.case_id,
                "snapshot_version": run.snapshot_version,
                "schema_version": run.evidence.schema_version,
                "content_hash": content_hash(run.evidence.model_dump(mode="json")),
                "config_versions": run.evidence.config_versions,
                "fact_count": len(run.evidence.facts),
                "narrative_passage_count": len(run.evidence.narrative_evidence),
                "human_verified_fact_count": len(
                    run.evidence.provenance.get("human_verified_fact_ids", [])),
            },
            "registry": {
                "version": run.registry.version,
                "hash": run.registry.registry_hash(),
                "total_registered_jobs": len(run.registry),
                "active_agent_ids": list(run.active_agent_ids),
                "inactive_registered_agent_ids": sorted(
                    d.agent_id for d in run.registry.all()
                    if d.agent_id not in run.active_agent_ids),
                "v1_to_v2_agent_map": BO_V1_TO_V2_AGENT,
            },
            "prompts": {
                name: {"prompt_id": p.prompt_id, "version": p.version,
                       "content_hash": p.content_hash,
                       "response_schema_hash": content_hash(p.response_schema)}
                for name, p in sorted(run.prompts.items())},
            "agents": agents,
            "execution": {
                "stages": run.stage_log,
                "ai_calls": len(calls),
                "cache_reuses": sum(1 for r in agent_runs if r.reused_from_cache),
                "agent_run_records": len(agent_runs),
                "token_usage": token_totals if token_seen else "not_reported",
                "runtime_seconds": round(
                    (run.completed_at - run.started_at).total_seconds(), 3)
                if run.completed_at else None,
            },
            "evidence_packets": packets,
            "retrieval": {
                "attempts": attempts,
                "gaps": [a for a in attempts if a["status"] == "no_results"],
                "follow_up": followups if followups else "not_performed",
                "budget": vars(run.runner.retriever.budget),
            },
            "hypotheses": {
                "records": hypotheses,
                "statistics": statistics,
                "rejected": [h for h in current if h["disposition"] == "rejected"],
                "unverified": [h for h in current if h["disposition"] == "unverified"],
                "corrections": [
                    {"rejected": h["corrects_hypothesis_id"],
                     "corrected_by": h["hypothesis_id"]}
                    for h in current if h.get("corrects_hypothesis_id")],
            },
            "calculations": [t.model_dump(mode="json") for t in run.indicators.traces],
            "parameters": parameters,
            "reconciliation": {
                **run.reconciliation.as_payload(),
                "orchestrator_merged_duplicates":
                    (run.orchestrator_output or {}).get("merged_duplicates") or [],
            },
            "resilience_analysis": (
                [h for h in current if h["agent_id"] == run.resilience_agent_id]
                if run.agents[run.resilience_agent_id].status == "validated"
                else run.agents[run.resilience_agent_id].status),
            "module_conclusions": run.module_conclusions,
            "orchestrator": {
                "history": run.orchestrator_history or "not_executed",
                "claims": provenance,
            },
            "challenger": {
                "rounds": run.challenge_rounds or "not_executed",
                "recorded_findings": findings,
            },
            "rerun": run.remediation or "not_performed",
            "missing_information": missing,
            "score": self._score_block(run),
            "scores_history": scores,
            "conclusions": conclusions,
            "final_conclusion": (
                {"conclusion_id": final["conclusion_id"],
                 "orchestrator_run_id": final["orchestrator_run_id"],
                 "score_reference": final["payload"].get("score_reference"),
                 "challenge_status": final["challenge_status"],
                 "supersedes_id": final["supersedes_id"]}
                if final else "unavailable"),
        }

    # -- handoff --------------------------------------------------------------

    def _handoff(self, run, mode) -> dict[str, Any]:
        by_id = {h.hypothesis_id: h for a in run.narrow_agent_ids
                 for h in run.hypotheses.get(a, [])}
        parsed = run.orchestrator_output or {}
        claims = iter_claims(parsed)

        def brief(claim):
            return {"claim_id": claim.get("claim_id"), "title": claim.get("title"),
                    "text": claim.get("text"), "section": claim.get("section"),
                    "materiality": claim.get("materiality", "medium"),
                    "hypothesis_ids": claim.get("hypothesis_ids") or [],
                    "evidence_ids": claim.get("evidence_ids") or []}

        mitigants = [h for h in by_id.values() if h.mitigant is not None and h.usable]
        last = run.challenge_rounds[-1] if run.challenge_rounds else None
        unresolved = [
            {"challenge_id": f["challenge_id"], "severity": f["severity"],
             "issue_type": f["issue_type"], "reason": f["reason"],
             "target": f["target"]}
            for f in (last or {}).get("findings", [])
            if f["severity"] in ("medium", "high", "material")]
        if run.conclusion is None:
            history = run.orchestrator_history[-1] if run.orchestrator_history else {}
            unresolved.append({
                "challenge_id": None, "severity": "material",
                "issue_type": "no_validated_conclusion",
                "reason": "; ".join(history.get("issues", []))
                or "The Business Orchestrator did not produce a validated conclusion.",
                "target": None})

        def mitigant_brief(h):
            return {"hypothesis_id": h.hypothesis_id,
                    "capability": h.mitigant.capability,
                    "existence": h.mitigant.existence,
                    "effectiveness": h.mitigant.effectiveness.value,
                    "evidence_ids": h.evidence_ids()}

        handoff = AcceptedBusinessOverview(
            handoff_id=f"bo_{uuid.uuid4().hex[:16]}",
            analysis_run_id=run.analysis_run_id,
            case_id=run.case_id,
            evidence_snapshot_version=run.snapshot_version,
            business_topic_conclusion_id=run.conclusion_row_id
            if run.conclusion is not None else None,
            conclusion_status=run.conclusion_status,
            business_risk_score_id=run.score.score_id,
            business_risk_score_status=run.score.status.value,
            business_risk_band=run.score.band,
            accepted_finding_ids=sorted(
                {h for _, c in claims for h in c.get("hypothesis_ids") or []
                 if h in by_id}),
            material_risk_ids=[c.get("claim_id") for name, c in claims
                               if name == "material_risks"],
            business_strengths=[brief(c) for name, c in claims if name == "strengths"],
            operational_vulnerabilities=[
                brief(c) for name, c in claims
                if name in ("weaknesses", "material_risks")],
            validated_mitigants=[
                mitigant_brief(h) for h in mitigants
                if h.mitigant.effectiveness.value == "demonstrated"],
            unproven_mitigants=[
                mitigant_brief(h) for h in mitigants
                if h.mitigant.effectiveness.value != "demonstrated"],
            unresolved_challenges=unresolved,
            open_questions=list(parsed.get("open_questions") or []),
            module_conclusions=run.module_conclusions,
            compound_business_risks=[
                brief(c) for name, c in claims
                if name in ("material_risks", "key_drivers")
                and len(c.get("hypothesis_ids") or []) > 1],
            score_limitations=list(run.rubric_notes),
            evidence_limitations=sorted({
                m for h in by_id.values() for m in h.missing_information}),
            escalations=unresolved,
            canonical_references={
                "analysis_run_id": run.analysis_run_id,
                "evidence_snapshot_version": run.snapshot_version,
                "topic_conclusion_id": run.conclusion_row_id,
                "score_id": run.score.score_id,
                "audit_stage": f"bo_audit:{run.analysis_run_id}",
            },
            execution_mode=mode,
            agent_registry_version=run.registry.version,
            agent_registry_hash=run.registry.registry_hash(),
            prompt_hashes={n: p.content_hash for n, p in sorted(run.prompts.items())},
            scoring_config_version=run.scoring_cfg.version,
            scoring_config_hash=run.scoring_cfg.content_hash,
            config_versions=dict(run.evidence.config_versions),
        )
        payload = handoff.model_dump(mode="json")
        payload["content_hash"] = handoff_content_hash(payload)
        return payload


def handoff_content_hash(payload: dict[str, Any]) -> str:
    """Hash of the analytical content (review fields and the hash excluded)."""
    excluded = {"content_hash", "human_review_status", "reviewer", "reviewed_at"}
    return content_hash({k: v for k, v in payload.items() if k not in excluded})
