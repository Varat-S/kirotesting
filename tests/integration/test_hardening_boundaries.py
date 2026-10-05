import pytest
from sqlalchemy import select

from app.cli import main
from app.core.config_registry import ConfigRegistry
from app.models.orm import AuditEvent, CaseEntity
from app.schemas.enums import FactStatus
from app.schemas.snapshots import FinalCaseSnapshot
from app.services.escalation.engine import EscalationEngine
from app.services.llm.client import FakeLLMBackend
from app.services.pipeline.package import SourcePackage
from app.services.pipeline.runner import CreditMemoPipeline
from app.services.review.workflow import HumanReviewWorkflow, SignOffRequiredError
from tests.integration.test_pipeline_acceptance import ROOT
from tests.unit.test_hardening import draft, fact
from app.services.audit.log import AuditLog
from app.services.reconciliation.service import (
    Reconciler,
    ToleranceConfig,
    DEFAULT_TOLERANCES,
)


def test_unresolved_mandatory_then_valid_approval(db_session):
    final, snapshot = draft(db_session)
    engine = EscalationEngine(db_session, case_id="C")
    escalation = engine.raise_escalation(
        rule_id="R-TEST",
        category="data_integrity",
        severity="mandatory",
        reason="Review required",
    )
    wf = HumanReviewWorkflow(db_session, case_id="C")
    wf.sign_off_recommendation(reviewer="officer", snapshot=snapshot)
    with pytest.raises(SignOffRequiredError):
        final.finalize(snapshot, signed_off_by="officer")
    engine.resolve(
        escalation.escalation_id,
        resolved_by="officer",
        resolution={"accepted": True},
        reason="Investigated",
    )
    row = final.finalize(snapshot, signed_off_by="officer")
    assert row.finalized


def test_nonvalue_reconciliation_retains_state_and_audit(db_session):
    rec = Reconciler(
        ToleranceConfig(DEFAULT_TOLERANCES, 1),
        session=db_session,
        audit=AuditLog(db_session),
    )
    result = rec.reconcile_field(
        "revenue",
        [
            fact("a", None, FactStatus.MISSING),
            fact("b", None, FactStatus.NOT_DISCLOSED),
        ],
    )
    row = rec.persist(result)
    assert row.resolved_state == "not_disclosed"
    event = db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "fact_reconciled")
    ).one()
    assert event.after["resolved_state"] == "not_disclosed"
    assert sorted(event.after["fact_ids"]) == ["a", "b"]


def test_pipeline_rejects_case_scope_mismatch(db_session, tmp_path):
    package = SourcePackage.load(ROOT / "package.json")
    entities = [
        e.model_copy(update={"expected_consolidation_scope": "consolidated"})
        for e in package.entities
    ]
    sources = [
        s.model_copy(update={"scope": "standalone"}) if s.entity_id == "DAL" else s
        for s in package.sources
    ]
    result = CreditMemoPipeline(
        db_session, data_root=tmp_path / "data", output_root=tmp_path / "output"
    ).run_case(
        "SCOPE",
        package=package.model_copy(update={"entities": entities, "sources": sources}),
    )
    assert result.metrics["operating_margin"].result is None
    assert any(e["rule_id"] == "R-DATA-METADATA-01" for e in result.escalations)
    assert (
        db_session.get(CaseEntity, ("SCOPE", "DAL")).expected_consolidation_scope
        == "consolidated"
    )


def test_pinned_config_replay_keeps_old_definition_links(db_session, tmp_path):
    package = SourcePackage.load(ROOT / "package.json")
    runner = CreditMemoPipeline(
        db_session, data_root=tmp_path / "data", output_root=tmp_path / "output"
    )
    original = runner.run_case("PINNED", package=package)
    versions = original.evidence_snapshot.config_versions
    registry = ConfigRegistry(db_session)
    cfg = registry.get("metric_defs", versions["metric_defs"]).content
    changed = {
        **cfg,
        "definitions": {
            name: {**definition, "review_note": "revised illustrative definition"}
            for name, definition in cfg["definitions"].items()
        },
    }
    registry.register("metric_defs", changed)
    from app.services.metrics.definitions import MetricDefinitionRegistry

    defs = MetricDefinitionRegistry(db_session)
    for name, definition in changed["definitions"].items():
        defs.register(name, definition)
    repeated = runner.run_case("PINNED", package=package, config_versions=versions)
    assert repeated.evidence_snapshot.config_versions == versions
    assert (
        repeated.draft_snapshot.payload["metric_definition_versions"]
        == original.draft_snapshot.payload["metric_definition_versions"]
    )
    assert original.evidence_snapshot.snapshot_version == 1
    assert repeated.evidence_snapshot.snapshot_version == 2


def test_invalid_model_output_is_rejected_logged_and_blocks_finalization(
    db_session, tmp_path
):
    from app.models.orm import ModelRun

    backend = FakeLLMBackend()
    backend.register("extract", {"unexpected": True})
    runner = CreditMemoPipeline(
        db_session,
        data_root=tmp_path / "data",
        output_root=tmp_path / "output",
        backend=backend,
    )
    result = runner.run_case(
        "INVALID", package=SourcePackage.load(ROOT / "package.json")
    )
    assert result.draft_snapshot.finalized is False
    assert "unexpected" not in result.draft_snapshot.payload["business_analysis"]
    assert any(
        e["rule_id"] == "R-AI-GROUNDING-01" and e["mandatory"]
        for e in result.escalations
    )
    stored = db_session.scalars(
        select(ModelRun).where(ModelRun.case_id == "INVALID")
    ).one()
    assert stored.validation_outcome == "rejected"
    snapshot = FinalCaseSnapshot.model_validate(result.draft_snapshot.payload)
    HumanReviewWorkflow(db_session, case_id="INVALID").sign_off_recommendation(
        reviewer="officer", snapshot=snapshot
    )
    with pytest.raises(SignOffRequiredError):
        runner.finalize_case("INVALID", 1, signed_off_by="officer")


def test_cli_draft_approve_and_finalize_require_separate_actions(tmp_path, capsys):
    common = [
        "--database",
        str(tmp_path / "case.db"),
        "--output",
        str(tmp_path / "output"),
    ]
    main(
        common + ["run-case", "SYNTHETIC_2024", "--package", str(ROOT / "package.json")]
    )
    assert '"status": "draft"' in capsys.readouterr().out
    with pytest.raises(SignOffRequiredError):
        main(
            common
            + ["finalize", "SYNTHETIC_2024", "--version", "1", "--reviewer", "officer"]
        )
    main(
        common
        + [
            "approve",
            "SYNTHETIC_2024",
            "--version",
            "1",
            "--reviewer",
            "officer",
            "--reason",
            "Synthetic test approval",
        ]
    )
    main(
        common
        + ["finalize", "SYNTHETIC_2024", "--version", "1", "--reviewer", "officer"]
    )
    assert '"status": "final"' in capsys.readouterr().out


def test_scripted_ai_claims_are_grounded_using_canonical_metrics(db_session, tmp_path):
    from app.services.pipeline.runner import offline_backend

    backend = offline_backend()
    backend.register(
        "analyze",
        {
            "business_overview": [
                {
                    "claim_id": "margin",
                    "text": "Operating margin is 20%.",
                    "kind": "fact",
                    "evidence_ids": ["operating_margin"],
                }
            ]
        },
    )
    result = CreditMemoPipeline(
        db_session,
        data_root=tmp_path / "data",
        output_root=tmp_path / "output",
        backend=backend,
    ).run_case("GROUNDED", package=SourcePackage.load(ROOT / "package.json"))
    grounding = result.draft_snapshot.payload["financial_analysis"]["grounding"]
    assert grounding[0]["is_grounded"]
    assert result.evaluation_artifacts["unsupported_claims"] == 0
    assert not any(e["rule_id"] == "R-AI-GROUNDING-01" for e in result.escalations)


def test_scripted_ai_contradiction_is_visible_and_cannot_change_metrics(
    db_session, tmp_path
):
    from app.services.pipeline.runner import offline_backend

    backend = offline_backend()
    backend.register(
        "analyze",
        {
            "business_overview": [
                {
                    "claim_id": "margin",
                    "text": "Operating margin is 80%.",
                    "kind": "fact",
                    "evidence_ids": ["operating_margin"],
                }
            ]
        },
    )
    result = CreditMemoPipeline(
        db_session,
        data_root=tmp_path / "data",
        output_root=tmp_path / "output",
        backend=backend,
    ).run_case("CONTRADICTION", package=SourcePackage.load(ROOT / "package.json"))
    assert result.metrics["operating_margin"].result == 0.2
    grounding = result.draft_snapshot.payload["financial_analysis"]["grounding"]
    assert grounding[0]["entailment_state"] == "contradictory"
    assert result.evaluation_artifacts["unsupported_claims"] == 1
    assert any(
        e["rule_id"] == "R-AI-GROUNDING-01" and e["mandatory"]
        for e in result.escalations
    )
