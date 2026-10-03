"""Unit tests for the frozen case workbench view-model (Req 24.1-24.4; task 10.1).

These assert directly on the :class:`WorkbenchService` view-model without HTTP:

* nine navigable sections + as-of/cutoff header (Req 24.1);
* each of the four counts derived from its authoritative source (Req 24.2);
* the workbench is read-only -- assembling a view does not mutate the finalized
  snapshot (its ``content_hash`` is unchanged) (Req 24.3);
* version lineage: a later review creates a new version referencing its
  predecessor while the old version stays viewable and frozen (Req 24.3, 24.4).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.config_registry import ConfigRegistry
from app.models.orm import Case, ReconciliationRecord
from app.schemas.enums import EntityType
from app.schemas.evidence import EntityRecord
from app.services.audit.log import AuditLog
from app.services.escalation.engine import EscalationEngine
from app.services.escalation.rules import EscalationCategory, Severity
from app.services.ingestion.completeness import CompletenessEvaluator
from app.services.reconciliation.snapshot import SnapshotAssembler
from app.services.review.finalization import FinalSnapshotAssembler
from app.services.review.workflow import HumanReviewWorkflow, ReviewAction
from app.services.workbench import (
    CaseNotFoundError,
    SnapshotVersionNotFoundError,
    WorkbenchService,
)

AS_OF = date(2024, 12, 31)
CUTOFF = datetime(2025, 1, 15, tzinfo=timezone.utc)


def _seed_evidence(db_session: Session, registry: ConfigRegistry, case_id: str) -> None:
    assembler = SnapshotAssembler(db_session, registry)
    entity = EntityRecord(
        entity_id="DAL", legal_name="Delta", entity_type=EntityType.BORROWER
    )
    evidence = assembler.assemble(
        case_id=case_id,
        facts=[],
        reconciliations=[],
        entities=[entity],
        documents=[{"document_id": "D1", "filename": "10-K.htm"}],
        as_of_date=AS_OF,
        evidence_cutoff_timestamp=CUTOFF,
    )
    assembler.persist(evidence)


def _finalize_case(
    db_session: Session,
    case_id: str = "DAL_2024",
    *,
    with_open_exception: bool = True,
) -> tuple[FinalSnapshotAssembler, object]:
    """Seed + finalize a FinalCaseSnapshot v1 and return (assembler, row)."""
    db_session.add(Case(case_id=case_id, as_of_date=AS_OF, evidence_cutoff_timestamp=CUTOFF))
    db_session.flush()
    registry = ConfigRegistry(db_session)
    registry.register("policy", {"net_debt_to_ebitda": 3.5})
    registry.register("source_profiles", {"filing": "critical", "fuel": "optional"})
    _seed_evidence(db_session, registry, case_id)

    audit = AuditLog(db_session)
    if with_open_exception:
        EscalationEngine(db_session, audit=audit, case_id=case_id).raise_escalation(
            rule_id="R-EVIDENCE-01",
            category=EscalationCategory.EVIDENCE,
            severity=Severity.REVIEW,
            reason="Debt maturity table missing.",
        )

    wf = HumanReviewWorkflow(db_session, audit=audit, case_id=case_id)
    wf.sign_off_recommendation(reviewer="credit.officer")

    final = FinalSnapshotAssembler(db_session, registry, audit=audit)
    snapshot = final.assemble(
        case_id=case_id,
        evidence_snapshot_version=1,
        metrics={"net_debt_to_ebitda": 3.1},
        benchmarks={"peer_median_leverage": 2.75},
        business_analysis={"claims": [{"statement": "Legacy carrier."}]},
        financial_analysis={"summary": "Adequate liquidity."},
        risks=[{"title": "Leverage", "severity": "high"}],
        mitigants=[{"statement": "Strong liquidity."}],
        recommendation={"status": "draft", "rating": "BB"},
    )
    row = final.finalize(
        snapshot, signed_off_by="credit.officer", has_sign_off=wf.has_sign_off()
    )
    return final, row


# -- header + sections (Req 24.1) --------------------------------------------


def test_header_shows_as_of_and_cutoff(db_session: Session) -> None:
    _finalize_case(db_session)
    view = WorkbenchService(db_session).assemble("DAL_2024")

    assert view.header.case_id == "DAL_2024"
    assert view.header.as_of_date == AS_OF.isoformat()
    assert view.header.evidence_cutoff_timestamp is not None
    assert "2025-01-15" in view.header.evidence_cutoff_timestamp


def test_nine_navigable_sections_present(db_session: Session) -> None:
    _finalize_case(db_session)
    view = WorkbenchService(db_session).assemble("DAL_2024")

    expected = {
        "sources",
        "canonical_data",
        "metrics",
        "business_analysis",
        "financial_analysis",
        "risks",
        "exceptions",
        "human_review",
        "memo",
    }
    assert set(view.sections) == expected
    # Section order preserves the nine named sections.
    assert [s["key"] for s in view.section_order] == [
        "sources",
        "canonical_data",
        "metrics",
        "business_analysis",
        "financial_analysis",
        "risks",
        "exceptions",
        "human_review",
        "memo",
    ]


def test_sections_sourced_from_memo_json(db_session: Session) -> None:
    _finalize_case(db_session)
    view = WorkbenchService(db_session).assemble("DAL_2024")

    # Finalized -> section data comes from the M8 memo JSON (single source).
    assert view.sections["metrics"]["metrics"] == {"net_debt_to_ebitda": 3.1}
    assert view.sections["metrics"]["benchmarks"] == {"peer_median_leverage": 2.75}
    assert view.sections["risks"]["risks"][0]["title"] == "Leverage"
    assert view.sections["memo"]["final_status"] == "final"
    # Sources section reflects the ingested document from the evidence snapshot.
    docs = view.sections["sources"]["documents"]
    assert any(d.get("document_id") == "D1" for d in docs)


# -- counts (Req 24.2) -------------------------------------------------------


def test_count_open_exceptions(db_session: Session) -> None:
    _finalize_case(db_session, with_open_exception=True)
    view = WorkbenchService(db_session).assemble("DAL_2024")
    assert view.counts.open_exceptions == 1


def test_count_open_exceptions_zero_when_none(db_session: Session) -> None:
    _finalize_case(db_session, with_open_exception=False)
    view = WorkbenchService(db_session).assemble("DAL_2024")
    assert view.counts.open_exceptions == 0


def test_count_critical_missing_sources(db_session: Session) -> None:
    _finalize_case(db_session)
    completeness = CompletenessEvaluator(ConfigRegistry(db_session))
    service = WorkbenchService(db_session, completeness=completeness)

    # The profile marks "filing" critical and "fuel" optional. Present only
    # "fuel" -> the critical "filing" source is missing.
    view = service.assemble("DAL_2024", present_source_types=["fuel"])
    assert view.counts.critical_missing_sources == 1

    # Present "filing" -> no critical missing source.
    view2 = service.assemble("DAL_2024", present_source_types=["filing", "fuel"])
    assert view2.counts.critical_missing_sources == 0


def test_count_ai_deterministic_conflicts(db_session: Session) -> None:
    _finalize_case(db_session)
    # Seed two conflicting reconciliation records and one verified one.
    db_session.add_all(
        [
            ReconciliationRecord(
                case_id="DAL_2024",
                field="revenue",
                comparison_method="relative",
                resolved_state="conflicting",
            ),
            ReconciliationRecord(
                case_id="DAL_2024",
                field="total_debt",
                comparison_method="relative",
                resolved_state="conflicting",
            ),
            ReconciliationRecord(
                case_id="DAL_2024",
                field="cash",
                comparison_method="relative",
                resolved_state="verified",
            ),
        ]
    )
    db_session.flush()

    view = WorkbenchService(db_session).assemble("DAL_2024")
    assert view.counts.ai_deterministic_conflicts == 2


def test_count_awaiting_human_decisions(db_session: Session) -> None:
    # Fresh case with a gated-but-unsigned review + an open mandatory escalation.
    db_session.add(Case(case_id="C1", as_of_date=AS_OF, evidence_cutoff_timestamp=CUTOFF))
    db_session.flush()
    audit = AuditLog(db_session)

    wf = HumanReviewWorkflow(db_session, audit=audit, case_id="C1")
    # Gated action recorded WITHOUT sign-off -> a decision awaiting a human.
    wf.record_action(
        ReviewAction.RESOLVE_CONFLICT,
        reviewer="analyst",
        signed_off=False,
    )
    # Open mandatory escalation -> also awaiting a human decision.
    EscalationEngine(db_session, audit=audit, case_id="C1").raise_escalation(
        rule_id="R-POLICY-01",
        category=EscalationCategory.FINANCIAL_RULES,
        severity=Severity.MANDATORY,
        reason="Hard policy breach.",
    )

    view = WorkbenchService(db_session).assemble("C1")
    assert view.counts.awaiting_human_decisions == 2


# -- read-only / frozen (Req 24.3) -------------------------------------------


def test_assembling_view_does_not_mutate_snapshot(db_session: Session) -> None:
    _final, row = _finalize_case(db_session)
    hash_before = row.content_hash
    version_before = row.snapshot_version

    service = WorkbenchService(db_session)
    service.assemble("DAL_2024")
    service.assemble("DAL_2024")  # render twice

    db_session.refresh(row)
    assert row.content_hash == hash_before
    assert row.snapshot_version == version_before
    assert row.finalized is True


def test_unknown_case_raises(db_session: Session) -> None:
    with pytest.raises(CaseNotFoundError):
        WorkbenchService(db_session).assemble("missing")


def test_unknown_version_raises(db_session: Session) -> None:
    _finalize_case(db_session)
    with pytest.raises(SnapshotVersionNotFoundError):
        WorkbenchService(db_session).assemble("DAL_2024", snapshot_version=99)


# -- version lineage: later review creates a new version (Req 24.4) ----------


def test_later_review_creates_new_version_old_stays_frozen(db_session: Session) -> None:
    final, v1 = _finalize_case(db_session, with_open_exception=False)
    v1_hash = v1.content_hash

    # A later review supersedes v1 with v2 (Req 16.3 / 24.4). v1 is untouched.
    v2 = final.supersede(
        v1,
        signed_off_by="credit.officer",
        has_sign_off=True,
        recommendation={"status": "draft", "rating": "BBB-"},
    )

    db_session.refresh(v1)
    assert v1.content_hash == v1_hash  # old version frozen / unchanged
    assert v2.snapshot_version == 2
    assert (v2.payload or {}).get("supersedes_snapshot") == 1

    service = WorkbenchService(db_session)

    # Default view presents the latest (v2) and surfaces predecessor lineage.
    latest = service.assemble("DAL_2024")
    assert latest.header.lineage.presented_version == 2
    assert latest.header.lineage.finalized is True
    assert latest.header.lineage.predecessor_version == 1
    assert latest.header.lineage.successor_versions == []
    assert latest.header.lineage.all_final_versions == [1, 2]

    # The old version remains viewable and frozen, surfacing its successor.
    old = service.assemble("DAL_2024", snapshot_version=1)
    assert old.header.lineage.presented_version == 1
    assert old.header.lineage.finalized is True
    assert old.header.lineage.predecessor_version is None
    assert old.header.lineage.successor_versions == [2]
    # Presenting the old version did not rewrite it.
    db_session.refresh(v1)
    assert v1.content_hash == v1_hash


# -- pre-final case ----------------------------------------------------------


def test_pre_final_case_renders_pending_sections(db_session: Session) -> None:
    db_session.add(Case(case_id="PF", as_of_date=AS_OF, evidence_cutoff_timestamp=CUTOFF))
    db_session.flush()
    registry = ConfigRegistry(db_session)
    registry.register("source_profiles", {"filing": "critical"})
    _seed_evidence(db_session, registry, "PF")

    view = WorkbenchService(db_session).assemble("PF")
    assert view.header.lineage.presented_version is None
    assert view.header.lineage.finalized is False
    assert view.header.as_of_date == AS_OF.isoformat()
    # Analysis/memo sections are explicitly pending, not fabricated.
    assert view.sections["memo"]["status"] == "pending"
    assert view.sections["business_analysis"]["status"] == "pending"
    # Pre-final still shows the evidence that exists.
    assert view.sections["canonical_data"]["entities"]
