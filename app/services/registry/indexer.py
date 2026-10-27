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
    """Read-only unsupported historical artifact discovery; never revalidate as rebuilt M3."""
    return [ArtifactEntry(artifact_id="legacy.m3." + path.stem, artifact_type=ArtifactType.OTHER,
            case_id=case_id, producer_module="M3", file_location=_rel(path, root),
            status=ExecutionStatus.SKIPPED, validation_status="unsupported_legacy",
            status_reason="Unsupported legacy M3 output; retained only for historical inspection")
            for path in sorted(dir_path.glob("*.json"))]


_BO_AGENT_MODULE = {
    "m1_revenue_quality": "M1", "m1_customer_stickiness": "M1",
    "m1_pricing_power": "M1", "m1_operating_model": "M1",
    "m1_competitive_durability": "M1", "m1_resilience_integration": "M1",
    "m2_industry": "M2", "m3_corporate_management": "M3",
    "m4_strategy_capital": "M4",
}



def _bo_status_from_audit(agent_status: Any, validation_status: Any) -> ExecutionStatus:
    """Map a recorded BO agent status onto the manifest status taxonomy."""
    a = str(agent_status or "").lower()
    v = str(validation_status or "").lower()
    if a in {"rejected", "failed"} or v in {"rejected", "invalid"}:
        return ExecutionStatus.FAILED
    if a == "skipped":
        return ExecutionStatus.SKIPPED
    if a == "validated" and v in {"valid", "validated", ""}:
        return ExecutionStatus.VALIDATED
    if a == "validated":
        return ExecutionStatus.QUALIFIED
    # Unknown recorded status -> incomplete, never a false green.
    return ExecutionStatus.INCOMPLETE


def _index_bo_agents(
    audit: dict, run_dir: Path, root: Path, case_id: str, run_id: str
) -> list[ArtifactEntry]:
    """Per-agent / per-validator / synthesis / challenger entries from the audit.

    These index the ALREADY-RECORDED per-agent data inside ``audit_report.json``
    (no new write path). Each entry is run-scoped (``bo.agent.<agent_id>.<run>``)
    so a node-to-artifact mapping always resolves within the same run. The parsed
    output for an agent is its hypotheses + parameters, carried on the inspector
    payload via the shared audit file + sub-locator.
    """
    entries: list[ArtifactEntry] = []
    audit_loc = _rel(run_dir / "audit_report.json", root)
    agents = audit.get("agents") if isinstance(audit.get("agents"), list) else []
    records = (audit.get("hypotheses", {}) or {}).get("records", []) \
        if isinstance(audit.get("hypotheses"), dict) else []
    params = audit.get("parameters") if isinstance(audit.get("parameters"), list) else []

    # Evidence ids per agent from hypothesis records + parameters.
    ev_by_agent: dict[str, set] = {}
    for rec in records:
        aid = rec.get("agent_id")
        if not aid:
            continue
        ids = ev_by_agent.setdefault(aid, set())
        for e in (rec.get("supporting_evidence_ids") or []):
            if isinstance(e, (str, int)):
                ids.add(str(e))
    for par in params:
        aid = par.get("agent_id")
        if aid:
            ev_by_agent.setdefault(aid, set()).update(
                str(e) for e in (par.get("evidence_ids") or []) if isinstance(e, (str, int)))

    synthesis_ids = {"business_orchestrator"}
    challenger_ids = {"business_challenge"}
    for agent in agents:
        aid = agent.get("agent_id")
        if not aid:
            continue
        runs = agent.get("runs") or []
        val_status = runs[-1].get("validation_status") if runs else None
        status = _bo_status_from_audit(agent.get("status"), val_status)
        unsupported_m3 = aid == "m3_corporate_management"
        if unsupported_m3:
            status, val_status = ExecutionStatus.SKIPPED, "unsupported_legacy"
        if aid in synthesis_ids:
            art_type, kind_id = ArtifactType.MODULE_OUTPUT, f"bo.synthesis.{run_id}"
            module = "BO"
        elif aid in challenger_ids:
            art_type, kind_id = ArtifactType.MODULE_OUTPUT, f"bo.challenger.{run_id}"
            module = "BO"
        else:
            art_type = ArtifactType.AGENT_OUTPUT
            kind_id = f"bo.agent.{aid}.{run_id}"
            module = _BO_AGENT_MODULE.get(aid, "BO")
        entry = ArtifactEntry(
            artifact_id=kind_id,
            artifact_type=art_type,
            case_id=case_id,
            run_id=run_id,
            producer_module=module,
            producer_agent=aid,
            schema_name="bo-agent-record-1.0",
            file_location=audit_loc,            # points into the recorded audit
            depends_on=[f"bo.agent.{d}.{run_id}" for d in (agent.get("dependencies") or [])],
            status=status,
            status_reason="Unsupported legacy M3 specialist output" if unsupported_m3 else agent.get("detail"),
            validation_status=val_status,
            validator_issues_count=len(agent.get("validation_issues") or []),
            evidence_ids=sorted(ev_by_agent.get(aid, set())),
            prompt_version=str(runs[-1].get("prompt_version")) if runs else None,
            model_id=runs[-1].get("model_id") if runs else None,
            audit_subpath=f"agents[agent_id={aid}]",
        )
        entries.append(entry)
    return entries


def _module_status(status: Any) -> ExecutionStatus:
    """Map a recorded module/validator/assessment status string onto the taxonomy."""
    s = str(status or "").lower()
    if s in {"validated", "complete", "completed", "accepted", "pass", "passed"}:
        return ExecutionStatus.VALIDATED
    if s in {"qualified", "accepted_with_qualification"}:
        return ExecutionStatus.QUALIFIED
    if s in {"failed", "rejected", "fail"}:
        return ExecutionStatus.FAILED
    if s in {"unavailable", "not_run", "not_executed", "not_attempted", "absent"}:
        return ExecutionStatus.ABSENT
    if s in {"skipped"}:
        return ExecutionStatus.SKIPPED
    if s in {"incomplete", "partial"}:
        return ExecutionStatus.INCOMPLETE
    return ExecutionStatus.INCOMPLETE


def _index_bo_module_artifacts(
    memo: dict, audit: dict, run_dir: Path, root: Path, case_id: str, run_id: str
) -> list[ArtifactEntry]:
    """Extract the governed module/validator/governance/snapshot records.

    These are the already-persisted outputs that live inside the memo's
    ``module_conclusions`` and the audit (M4 debt assessment + its Validator 8,
    the M4 strategy Validator 8, rebuilt M3 components, final governance, canonical
    snapshot). Only stages that ACTUALLY executed and were persisted are indexed;
    an absent stage is either omitted or recorded ``absent`` with a reason — never
    fabricated. Each entry points into the recorded memo/audit file via
    ``audit_subpath`` so it is inspectable from its Pipeline Explorer node.
    """
    entries: list[ArtifactEntry] = []
    memo_loc = _rel(run_dir / "business_overview.json", root)
    audit_loc = _rel(run_dir / "audit_report.json", root)
    mc = memo.get("module_conclusions") if isinstance(
        memo.get("module_conclusions"), dict) else {}

    # --- M4: debt assessment + debt Validator 8 + strategy Validator 8 -------
    m4 = mc.get("M4") if isinstance(mc.get("M4"), dict) else {}
    debt = m4.get("m4_debt_assessment") if isinstance(
        m4.get("m4_debt_assessment"), dict) else None
    if debt is not None:
        status = _module_status(debt.get("status"))
        entries.append(ArtifactEntry(
            artifact_id=f"bo.m4_debt.{run_id}",
            artifact_type=ArtifactType.MODULE_OUTPUT,
            case_id=case_id, run_id=run_id, producer_module="M4",
            producer_agent="module4_debt_structure_specialist",
            schema_name=debt.get("version"),
            file_location=memo_loc, audit_subpath="module_conclusions.M4.m4_debt_assessment",
            status=status,
            status_reason=debt.get("reason"),
            validation_status=("validated" if debt.get("validated") else "failed"),
            evidence_ids=[str(e) for e in (debt.get("evidence_ids") or [])],
            escalations=[debt["escalation_id"]] if debt.get("escalation_id") else [],
            model_invoked=debt.get("model_invoked"),
        ))
        # The debt Validator 8 report (deterministic, five checks).
        report = debt.get("validator_report") if isinstance(
            debt.get("validator_report"), dict) else None
        if report is not None:
            passed = report.get("passed")
            entries.append(ArtifactEntry(
                artifact_id=f"bo.validator8_debt.{run_id}",
                artifact_type=ArtifactType.VALIDATION_REPORT,
                case_id=case_id, run_id=run_id, producer_module="M4",
                producer_validator=debt.get("validator_id") or "validator_8_debt_structure",
                file_location=memo_loc,
                audit_subpath="module_conclusions.M4.m4_debt_assessment.validator_report",
                status=(ExecutionStatus.VALIDATED if passed else ExecutionStatus.FAILED),
                validation_status=("validated" if passed else "failed"),
                validator_issues_count=len(debt.get("validator_issues") or []),
                depends_on=[f"bo.m4_debt.{run_id}"],
            ))

    # Strategy Validator 8 = the m4_strategy_capital agent's own validation, read
    # from the recorded audit agent block (validator_id declared in roster v3).
    strat_agent = next(
        (a for a in (audit.get("agents") or []) if isinstance(a, dict)
         and a.get("agent_id") == "m4_strategy_capital"), None)
    if strat_agent is not None:
        runs = strat_agent.get("runs") or []
        vstat = runs[-1].get("validation_status") if runs else None
        entries.append(ArtifactEntry(
            artifact_id=f"bo.validator8_strategy.{run_id}",
            artifact_type=ArtifactType.VALIDATION_REPORT,
            case_id=case_id, run_id=run_id, producer_module="M4",
            producer_agent="m4_strategy_capital",
            producer_validator="validator_8",
            file_location=audit_loc,
            # ``#validation`` selects the agent's VALIDATION record (status,
            # issues, validator adjustments) — not its hypotheses/parameters —
            # so the strategy Validator 8 node shows its own recorded output and
            # never another producer's data (PR #3 Task 1).
            audit_subpath="agents[agent_id=m4_strategy_capital]#validation",
            status=_bo_status_from_audit(strat_agent.get("status"), vstat),
            validation_status=vstat,
            validator_issues_count=len(strat_agent.get("validation_issues") or []),
            depends_on=[f"bo.agent.m4_strategy_capital.{run_id}"],
        ))

    m3 = mc.get("M3") or {}
    if m3.get("workflow") == "m3-rebuild":
        for kind, component in (m3.get("m3_rebuild") or {}).items():
            payload = component.get("artifact") or {}
            entries.append(ArtifactEntry(
                artifact_id=f"bo.m3.{kind}.{run_id}",
                artifact_type=ArtifactType.CORPORATE_STRUCTURE if kind == "structure" else ArtifactType.MODULE_OUTPUT,
                case_id=case_id, run_id=run_id, producer_module="M3",
                producer_agent="m3_structure_conversion" if kind == "structure" else "m3_management_assessment",
                schema_version=payload.get("schema_version"),
                file_location=memo_loc, audit_subpath=f"module_conclusions.M3.m3_rebuild.{kind}",
                status=_module_status(component.get("status")),
                status_reason=component.get("error"), validation_status=component.get("validation_status")))

    # --- Final deterministic governance --------------------------------------
    fc = audit.get("final_conclusion") if isinstance(audit.get("final_conclusion"), dict) else None
    memo_status = memo.get("status") if isinstance(memo.get("status"), dict) else {}
    conclusion_status = audit.get("conclusion_status") or memo_status.get("conclusion_status")
    if fc is not None or conclusion_status:
        review = str(memo_status.get("human_review_status") or "").lower()
        status = _module_status(conclusion_status)
        # A governed conclusion pending human review is qualified, not validated.
        if status == ExecutionStatus.VALIDATED and review == "pending":
            status = ExecutionStatus.QUALIFIED
        entries.append(ArtifactEntry(
            artifact_id=f"bo.governance.{run_id}",
            artifact_type=ArtifactType.MODULE_OUTPUT,
            case_id=case_id, run_id=run_id, producer_module="BO",
            producer_agent="final_governance",
            file_location=audit_loc, audit_subpath="final_conclusion",
            status=status,
            status_reason=memo_status.get("text"),
            validation_status=conclusion_status,
            human_review_status=(HumanReviewStatus.PENDING if review == "pending"
                                 else HumanReviewStatus.ACCEPTED if review == "accepted"
                                 else HumanReviewStatus.NOT_REQUIRED),
            depends_on=[f"bo.challenger.{run_id}"],
        ))

    # --- Canonical evidence snapshot -----------------------------------------
    snap = audit.get("evidence_snapshot") if isinstance(audit.get("evidence_snapshot"), dict) else None
    if snap is not None:
        ver = snap.get("snapshot_version")
        entries.append(ArtifactEntry(
            artifact_id=f"bo.snapshot.{run_id}",
            artifact_type=ArtifactType.EVIDENCE_SNAPSHOT,
            case_id=case_id, run_id=run_id, producer_module="BO",
            producer_agent="canonical_evidence_snapshot",
            schema_name=snap.get("schema_version"),
            content_hash=snap.get("content_hash"),
            file_location=audit_loc, audit_subpath="evidence_snapshot",
            source_evidence_snapshot=(f"{case_id}@v{ver}" if ver is not None else None),
            status=ExecutionStatus.VALIDATED,
            validation_status="recorded",
        ))
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
    # A BO handoff is recorded with human_review_status="pending" in the DB; the
    # on-disk payload carries conclusion_status. Prefer that; otherwise a produced
    # handoff awaiting review is QUALIFIED, not a false "incomplete"/"validated".
    handoff_doc = _load_json(run_dir / "handoff.json")
    if isinstance(handoff_doc, dict):
        review = str(handoff_doc.get("human_review_status") or "").lower()
        if review in {"", "pending"}:
            handoff.human_review_status = HumanReviewStatus.PENDING
            if handoff.status == ExecutionStatus.INCOMPLETE and \
                    handoff.status_reason == "unknown_completion_status":
                handoff.status = ExecutionStatus.QUALIFIED
                handoff.status_reason = "handoff recorded, pending human review"
        elif review == "accepted":
            handoff.human_review_status = HumanReviewStatus.ACCEPTED
        elif review == "rejected":
            handoff.human_review_status = HumanReviewStatus.REJECTED
    entries.append(handoff)

    # Per-agent / validator / synthesis / challenger entries from the audit.
    audit = _load_json(run_dir / "audit_report.json")
    if isinstance(audit, dict):
        entries.extend(_index_bo_agents(audit, run_dir, root, case_id, run_id))
    # Governed module / validator / governance / snapshot records from the memo
    # + audit (M4 debt + validators 7/8, final governance, canonical snapshot).
    memo = _load_json(run_dir / "business_overview.json")
    if isinstance(memo, dict) and isinstance(audit, dict):
        entries.extend(_index_bo_module_artifacts(
            memo, audit, run_dir, root, case_id, run_id))
    return entries


def _fo_run_state(payload: dict) -> tuple[str, str]:
    """(run_status, human_review_status) derived from the FO payload.

    Mirrors ``app.services.fo.runner.run_state`` without importing it, so the
    registry reads the FO payload the same way the producer does: an escalated
    run (open escalations, or a going-concern fail flagged for review) awaits an
    analyst; otherwise it is completed and pending the standard human approval.
    """
    escalated = payload.get("escalations")
    if escalated is None:
        escalated = (((payload.get("precheck") or {}).get("going_concern") or {})
                     .get("review_required"))
    if escalated:
        return "awaiting_review", "escalated"
    return "completed", "pending"


def _fo_overview_status(payload: dict) -> tuple[ExecutionStatus, Optional[str], str, bool]:
    """Map a completed/scored FO payload onto the taxonomy + provisional flag.

    A completed FO run is NOT ``incomplete``: it is a produced, scored output
    whose rating is awaiting human approval -> ``qualified`` (provisional). An
    escalated run is also ``qualified`` (awaiting an analyst decision). Only a
    genuinely unscored/failed payload is lower. Returns
    ``(status, reason, human_review_status, rating_provisional)``.
    """
    run_status, review = _fo_run_state(payload)
    composite = payload.get("composite") if isinstance(payload.get("composite"), dict) else {}
    has_score = composite.get("score") is not None
    if run_status == "awaiting_review":
        return (ExecutionStatus.QUALIFIED,
                "FO run escalated; composite withheld/provisional pending analyst decision",
                review, True)
    if has_score:
        # Completed and scored, but the rating is provisional until an analyst
        # approves it (default human_review_status is "pending").
        return (ExecutionStatus.QUALIFIED,
                "FO computed; rating provisional pending human approval",
                review, True)
    return (ExecutionStatus.INCOMPLETE, "FO payload carries no composite score",
            review, False)


def _index_fo_run(run_dir: Path, root: Path, case_id: str) -> list[ArtifactEntry]:
    run_id = run_dir.name
    entries: list[ArtifactEntry] = []

    overview_path = run_dir / "financial_overview.json"
    payload = _load_json(overview_path)
    overview = _entry_for_file(
        overview_path, root,
        artifact_id=f"fo.overview.{run_id}", artifact_type=ArtifactType.FINANCIAL_OVERVIEW,
        case_id=case_id, run_id=run_id, producer_module="FO",
    )
    # FO uses its own status fields (run_status / escalations / composite), NOT
    # the BO completion_status fields, so a completed FO run must not be misread
    # as "incomplete". Re-derive the status from the FO payload.
    if isinstance(payload, dict):
        status, reason, review, provisional = _fo_overview_status(payload)
        overview.status = status
        overview.status_reason = reason
        overview.human_review_status = (
            HumanReviewStatus.ESCALATED if review == "escalated"
            else HumanReviewStatus.PENDING if review == "pending"
            else HumanReviewStatus.NOT_REQUIRED)
        overview.rating_provisional = provisional
        composite = payload.get("composite") if isinstance(
            payload.get("composite"), dict) else {}
        overview.composite_score = composite.get("score")
        overview.credit_rating = composite.get("rating")
        overview.risk_level = composite.get("risk_level")
        metrics = payload.get("metrics") if isinstance(payload.get("metrics"), list) else []
        # The seven measures (metrics[0] is the composite row; the rest are measures).
        overview.fo_measures = [m.get("key") for m in metrics[1:]
                                if isinstance(m, dict) and m.get("key")]
        triggers = payload.get("triggers") if isinstance(payload.get("triggers"), dict) else {}
        overview.fo_triggers_fired = sorted(
            k for k, t in triggers.items() if isinstance(t, dict) and t.get("fired"))
        pre = payload.get("precheck") if isinstance(payload.get("precheck"), dict) else {}
        overview.fo_prechecks = sorted(pre.keys())
        overview.validation_status = "scored" if composite.get("score") is not None else "unscored"
    entries.append(overview)

    # Per-stage FO nodes so each executed stage is inspectable from its Explorer
    # node. These index the recorded financial_overview.json (the transcripts are
    # optional). A stage is marked validated only when the payload records it.
    def _stage(aid: str, agent: str, present: bool, subpath: str) -> None:
        entries.append(ArtifactEntry(
            artifact_id=f"{aid}.{run_id}",
            artifact_type=ArtifactType.MODULE_OUTPUT,
            case_id=case_id, run_id=run_id, producer_module="FO",
            producer_agent=agent,
            file_location=(_rel(overview_path, root) if present else None),
            audit_subpath=(subpath if present else None),
            depends_on=[f"fo.overview.{run_id}"],
            status=(ExecutionStatus.VALIDATED if present else ExecutionStatus.ABSENT),
            status_reason=(None if present else "stage not recorded in FO payload"),
        ))

    if isinstance(payload, dict):
        _stage("fo.precheck", "fo_precheck", "precheck" in payload, "precheck")
        _stage("fo.measures", "fo_measures",
               isinstance(payload.get("metrics"), list), "metrics")
        _stage("fo.triggers", "fo_triggers",
               bool(payload.get("triggers")), "triggers")
        # FO scoring stage carries the (provisional) composite.
        composite_present = isinstance(payload.get("composite"), dict)
        scoring = ArtifactEntry(
            artifact_id=f"fo.scoring.{run_id}",
            artifact_type=ArtifactType.MODULE_OUTPUT,
            case_id=case_id, run_id=run_id, producer_module="FO",
            producer_agent="fo_scoring",
            file_location=(_rel(overview_path, root) if composite_present else None),
            audit_subpath=("composite" if composite_present else None),
            depends_on=[f"fo.measures.{run_id}", f"fo.triggers.{run_id}"],
            status=(ExecutionStatus.QUALIFIED if composite_present
                    else ExecutionStatus.ABSENT),
            status_reason=("rating provisional pending human approval"
                           if composite_present else "no composite recorded"),
        )
        if composite_present:
            scoring.rating_provisional = True
        entries.append(scoring)

    # Optional transcript files (present only when the run wrote them).
    entries.append(_entry_for_file(
        run_dir / "precheck_transcript.json", root,
        artifact_id=f"fo.precheck_transcript.{run_id}", artifact_type=ArtifactType.TRANSCRIPT,
        case_id=case_id, run_id=run_id, producer_module="FO",
        producer_agent="fo_precheck", depends_on=[f"fo.overview.{run_id}"],
    ))
    entries.append(_entry_for_file(
        run_dir / "trigger_transcript.json", root,
        artifact_id=f"fo.trigger_transcript.{run_id}", artifact_type=ArtifactType.TRANSCRIPT,
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

    ``m3_dirs`` optionally names rebuilt or unsupported historical M3 directories (which live
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

    from app.services.bo.m3_rebuild.adapters import registry_entries
    rebuild_root = output_root / "m3_rebuild" / case_id
    if rebuild_root.is_dir():
        for recorded in sorted(rebuild_root.glob("*/m3_rebuild_manifest.json")):
            manifest.artifacts.extend(registry_entries(recorded, root, case_id))

    for m3_dir in m3_dirs or []:
        m3_path = Path(m3_dir)
        if (m3_path / "m3_rebuild_manifest.json").is_file():
            entries = registry_entries(m3_path / "m3_rebuild_manifest.json", root, case_id)
            existing = {a.artifact_id for a in manifest.artifacts}
            manifest.artifacts.extend(e for e in entries if e.artifact_id not in existing)
        elif m3_path.is_dir():
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
