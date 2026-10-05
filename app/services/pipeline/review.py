"""Review saved evidence and continue from that exact version; never re-ingest."""

import json
from copy import deepcopy

from sqlalchemy import func, select

from app.core.config_registry import ConfigRegistry
from app.core.hashing import canonical_json, content_hash
from app.models.orm import EvidencePreview, Snapshot
from app.schemas.evidence import CanonicalFact
from app.schemas.snapshots import CanonicalEvidenceSnapshot
from app.services.audit.log import ActorType, AuditLog, EventType
from app.services.escalation.engine import EscalationEngine
from app.services.escalation.rules import EscalationCategory, RuleRegistry
from app.services.ingestion.completeness import CompletenessAssessment
from app.services.pipeline.artifacts import (
    ArtifactIntegrityError,
    StageRecorder,
    verified_artifacts,
)
from app.services.pipeline.inspection_views import stage_outputs
from app.services.pipeline.package import SourcePackage
from app.services.reconciliation.snapshot import SnapshotAssembler
from app.services.reporting.memo import MemoReportGenerator
from app.services.review.finalization import FinalSnapshotAssembler
from app.services.review.workflow import HumanReviewWorkflow


def checked_preview(session, case_id, version):
    row = session.get(EvidencePreview, (case_id, version))
    if not row:
        raise ValueError("Evidence preview not found.")
    stages = {r.name: r for r in verified_artifacts(session, case_id, version)}
    if any(
        expected["name"] not in stages
        or stages[expected["name"]].chain_hash != expected["chain_hash"]
        for expected in row.payload.get("recorded_stages", [])
    ):
        raise ArtifactIntegrityError(
            "Saved processing artifacts are missing or changed."
        )
    if "full_preview" not in stages or stages["full_preview"].sha256 != content_hash(
        row.payload
    ):
        raise ArtifactIntegrityError(
            "This preview has no matching recorded processing output; prepare a fresh case."
        )
    return deepcopy(row.payload)


def pinned_context(session, preview):
    evidence = CanonicalEvidenceSnapshot.model_validate(preview["evidence"])
    registry = ConfigRegistry(session)
    configs = {
        kind: registry.get(kind, version).content
        for kind, version in evidence.config_versions.items()
    }
    package = SourcePackage(
        as_of_date=evidence.as_of_date,
        evidence_cutoff_timestamp=evidence.evidence_cutoff_timestamp,
        borrower_entity_id=next(
            e["entity_id"] for e in evidence.entities if e["borrower_flag"]
        ),
        entities=evidence.entities,
        sources=[],
    )
    return evidence, registry, configs, package


def reviewed_preview(
    pipeline,
    case_id,
    version,
    *,
    action,
    fact_id,
    reviewer,
    reason,
    canonical_name=None,
    field=None,
):
    session = pipeline.session
    preview = checked_preview(session, case_id, version)
    evidence, registry, configs, package = pinned_context(session, preview)
    latest = session.scalar(
        select(func.max(EvidencePreview.evidence_version)).where(
            EvidencePreview.case_id == case_id
        )
    )
    if latest != version:
        raise ValueError(
            "Review the latest evidence version; this page has been superseded."
        )
    if not reviewer.strip() or not reason.strip():
        raise ValueError("Reviewer and reason are required.")
    original = next((f for f in evidence.facts if f["fact_id"] == fact_id), None)
    if original is None or original["normalized_value"] is None:
        raise ValueError("Choose a numeric fact from this evidence version.")
    audit = AuditLog(session)
    workflow = HumanReviewWorkflow(session, audit=audit, case_id=case_id)
    before = evidence.model_dump(mode="json")
    prior_fact = deepcopy(original)
    new_fact = original
    if action == "map":
        if original["mapping_status"] not in {"unmapped", "ambiguous"}:
            raise ValueError(
                "Mapping review applies to unmapped or ambiguous observations."
            )
        allowed = {r["canonical_name"] for r in configs["financial_mappings"]["rules"]}
        if canonical_name not in allowed:
            raise ValueError(
                "Choose a canonical field from the pinned mapping configuration."
            )
        new_fact = {
            **original,
            "name": canonical_name,
            "mapping_status": "mapped",
            "mapping_candidates": [canonical_name],
            "created_by": "human_review",
            "status": "unverified",
        }
        new_fact["fact_id"] = content_hash(
            {k: v for k, v in new_fact.items() if k != "fact_id"}
        )
        pipeline._persist_fact(CanonicalFact.model_validate(new_fact), case_id)
        evidence.facts.append(new_fact)
        # Retain original observations and values; explicitly supersede their
        # mapping in this new snapshot only. Their ORM rows remain untouched.
        original["mapping_status"] = "superseded_by_review"
        preview["mapping_issues"] = [
            i for i in preview["mapping_issues"] if i.get("fact_id") != fact_id
        ]
        from app.services.reconciliation.service import Reconciler, ToleranceConfig
        from app.services.reconciliation.snapshot import _to_data_quality

        reconciler = Reconciler(
            ToleranceConfig(
                configs["tolerances"], evidence.config_versions["tolerances"]
            ),
            precedence=configs["parser_precedence"],
            selection_version=evidence.config_versions["parser_precedence"],
        )
        key = canonical_json(
            [
                new_fact["entity_id"],
                new_fact["fiscal_year"],
                new_fact["period_type"],
                str(new_fact["period_end"]),
                canonical_name,
                sorted(new_fact["dimensions"].items()),
                str(new_fact["period_start"]),
                new_fact["accounting_basis"],
            ]
        )
        candidates = [
            CanonicalFact.model_validate(f)
            for f in evidence.facts
            if f["mapping_status"] == "mapped"
            and canonical_json(
                [
                    f["entity_id"],
                    f["fiscal_year"],
                    f["period_type"],
                    str(f["period_end"]),
                    f["name"],
                    sorted(f["dimensions"].items()),
                    str(f["period_start"]),
                    f["accounting_basis"],
                ]
            )
            == key
        ]
        result = reconciler.reconcile_field(key, candidates)
        evidence.data_quality[key] = _to_data_quality(result)
        review_action = "approve_accounting_adjustment"
    elif action in {"verify", "select"}:
        matches = [
            (key, dq)
            for key, dq in evidence.data_quality.items()
            if (field is None or key == field)
            and (
                dq.selected_fact_id == fact_id
                or any(
                    content_hash(r)
                    in {content_hash(s) for s in original["source_refs"]}
                    for r in dq.source_refs
                )
            )
            and json.loads(key)[1:5]
            == [
                original["fiscal_year"],
                original["period_type"],
                str(original["period_end"]),
                original["name"],
            ]
            and json.loads(key)[0] == original["entity_id"]
        ]
        if len(matches) != 1:
            raise ValueError(
                "Choose the exact reconciliation field and one of its source facts."
            )
        key, dq = matches[0]
        if action == "verify" and dq.state == "conflicting":
            raise ValueError(
                "Resolve this conflict by selecting its source value explicitly."
            )
        if action == "select" and dq.state != "conflicting":
            raise ValueError("The selected field is not conflicting.")
        dq.state, dq.value, dq.selected_fact_id = (
            "verified",
            original["normalized_value"],
            fact_id,
        )
        dq.selection_reason = f"Human {action}: {reason}"
        dq.detail = "Original observations retained; explicit human selection is applied in this evidence version."
        evidence.provenance["human_verified_fact_ids"] = sorted(
            set(evidence.provenance.get("human_verified_fact_ids", [])) | {fact_id}
        )
        review_action = "resolve_conflict" if action == "select" else "verify_source"
    else:
        raise ValueError("Unsupported evidence review action.")
    review = workflow.record_action(
        review_action,
        reviewer=reviewer.strip(),
        reason=reason.strip(),
        target_type="fact",
        target_id=fact_id,
        prior_value=prior_fact,
        new_value={"action": action, "fact": new_fact},
        linked_evidence=[fact_id],
        signed_off=True,
    )
    evidence.snapshot_version += 1
    evidence.provenance["review_ids"] = evidence.provenance.get("review_ids", []) + [
        review.review_id
    ]
    evidence.provenance["supersedes_evidence_version"] = version
    evidence.financials = {
        key: {
            "value": dq.value,
            "state": dq.state,
            "selected_fact_id": dq.selected_fact_id,
        }
        for key, dq in evidence.data_quality.items()
    }
    SnapshotAssembler(session, registry).persist(evidence)
    metrics, all_metrics, _ = pipeline._metrics(
        case_id, evidence, configs, audit, package.borrower_entity_id
    )
    trends, benchmarks, outcomes = pipeline._context(
        case_id,
        package,
        metrics,
        all_metrics,
        configs,
        evidence.config_versions,
        RuleRegistry(session),
        audit,
    )
    engine = EscalationEngine(
        session, audit=audit, case_id=case_id, deterministic_ids=True
    )
    for outcome in outcomes:
        engine.raise_from_outcome(outcome)
    preview.update(
        evidence=evidence.model_dump(mode="json"),
        evidence_version=evidence.snapshot_version,
        metrics={k: v.as_payload() for k, v in metrics.items()},
        trends=trends,
        benchmarks=benchmarks,
        escalations=[
            pipeline._escalation_payload(e) for e in engine.escalations_for_case()
        ],
    )
    preview["next_llm_input"] = {"canonical_evidence": preview["evidence"]}
    recorder = StageRecorder(session, case_id, evidence.snapshot_version)
    recorder.record(
        "human_review",
        {
            "review_id": review.review_id,
            "prior_evidence_sha256": content_hash(before),
            "prior_evidence_version": version,
            "action": action,
            "fact_id": fact_id,
        },
    )
    for name, (_, output) in stage_outputs(preview).items():
        if name != "full_preview":
            recorder.record(name, output)
    preview["recorded_stages"] = recorder.references()
    recorder.record("full_preview", preview)
    row = EvidencePreview(
        case_id=case_id, evidence_version=evidence.snapshot_version, payload=preview
    )
    session.add(row)
    session.flush()
    return row


def continue_preview(pipeline, case_id, version, *, rerun=False):
    session = pipeline.session
    preview = checked_preview(session, case_id, version)
    evidence, registry, configs, package = pinned_context(session, preview)
    latest = session.scalar(
        select(func.max(EvidencePreview.evidence_version)).where(
            EvidencePreview.case_id == case_id
        )
    )
    if version != latest:
        raise ValueError("Continue from the latest reviewed evidence version.")
    existing = session.scalars(
        select(Snapshot)
        .where(Snapshot.case_id == case_id, Snapshot.snapshot_type == "final_case")
        .order_by(Snapshot.snapshot_version.desc())
    ).first()
    if (
        existing
        and existing.payload["evidence_snapshot_ref"]["snapshot_version"] == version
        and not rerun
    ):
        raise ValueError(
            "A draft already exists for this evidence version; inspect it before creating another version."
        )
    audit = AuditLog(session)
    engine = EscalationEngine(
        session, audit=audit, case_id=case_id, deterministic_ids=True
    )
    conditions = configs["escalation_rules"]["conditions"]
    rule_versions = {}
    for name, cfg in conditions.items():
        rule_versions[name] = pipeline._register_rule(
            RuleRegistry(session),
            cfg["rule_id"],
            concept="data_integrity",
            category="data_integrity",
            definition=cfg,
            config_kind="escalation_rules",
            config_version=evidence.config_versions["escalation_rules"],
        ).version

    def flag(name, reason, refs=()):
        cfg = conditions[name]
        return engine.raise_escalation(
            rule_id=cfg["rule_id"],
            rule_version=rule_versions[name],
            category=EscalationCategory.EVIDENCE,
            severity=cfg["severity"],
            reason=reason,
            evidence_refs=list(refs),
        )

    metrics, _, definitions = pipeline._metrics(
        case_id, evidence, configs, audit, package.borrower_entity_id
    )
    completeness = CompletenessAssessment(**preview["coverage"])
    prior_runs = {r["run_id"] for r in pipeline._model_runs(case_id, version)}
    analysis, runs, grounding, qualitative = pipeline._ai(
        case_id,
        evidence,
        metrics,
        preview["trends"],
        preview["benchmarks"],
        completeness,
        configs,
        engine,
        audit,
        flag,
    )
    runs = [r for r in runs if r["run_id"] not in prior_runs]
    payloads = [pipeline._escalation_payload(e) for e in engine.escalations_for_case()]
    final = FinalSnapshotAssembler(session, registry, audit=audit)
    draft = final.assemble(
        case_id=case_id,
        evidence_snapshot_version=version,
        metrics={k: v.as_payload() for k, v in metrics.items()},
        benchmarks=preview["benchmarks"],
        business_analysis=analysis,
        financial_analysis={
            "trends": preview["trends"],
            "grounding": grounding,
            "qualitative_facts": qualitative,
            "data_limitations": preview["coverage"],
        },
        risks=analysis.get("key_risks", []),
        mitigants=analysis.get("mitigants", []),
        escalations=payloads,
        metric_definition_versions=definitions,
        rule_versions={conditions[n]["rule_id"]: v for n, v in rule_versions.items()},
        prompt_model_versions=pipeline._prompt_versions(runs),
        human_reviews=[
            vars(r)
            for r in HumanReviewWorkflow(session, case_id=case_id).reviews_for_case()
        ],
        recommendation={
            "status": "draft",
            "human_sign_off_required": True,
            "inability_to_conclude": any(v.result is None for v in metrics.values())
            or completeness.mandatory_escalation,
        },
        supersedes_snapshot=existing.snapshot_version if existing else None,
    )
    draft.config_versions = evidence.config_versions
    row = final.persist_draft(draft)
    recorder = StageRecorder(session, case_id, version)
    recorder.record(f"llm_runs_draft_{draft.snapshot_version}", runs)
    recorder.record(
        f"analysis_draft_{draft.snapshot_version}",
        {
            "analysis": analysis,
            "grounding": grounding,
            "qualitative_facts": qualitative,
        },
    )
    recorder.record(f"draft_{draft.snapshot_version}", row.payload)
    reporter = MemoReportGenerator(
        session, audit=audit, output_root=pipeline.output_root
    )
    memo = reporter.generate_draft_json(
        case_id=case_id, snapshot_version=draft.snapshot_version
    )
    pipeline._write_draft(case_id, draft.snapshot_version, memo, reporter)
    audit.record(
        EventType.DRAFT_MEMO_GENERATED,
        case_id=case_id,
        actor_type=ActorType.SYSTEM,
        after={
            "snapshot_version": draft.snapshot_version,
            "evidence_version": version,
            "memo_hash": content_hash(memo),
        },
    )
    return row
