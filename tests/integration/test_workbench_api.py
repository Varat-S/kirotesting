"""Integration tests for the read-only workbench API (Req 24.1-24.4; task 10.1).

Drives the FastAPI app via TestClient over an isolated in-memory database:

* GET returns the workbench with the nine sections, the as-of/cutoff header,
  and the four counts (Req 24.1, 24.2);
* viewing the workbench does NOT mutate the case (the finalized snapshot's
  content hash is unchanged after repeated GETs) (Req 24.3);
* a specific finalized version is viewable and frozen, with predecessor/
  successor lineage after a later review created a new version (Req 24.4);
* the optional HTML rendering returns the header, counts, and section anchors.

The app's ``get_session`` dependency is overridden to share the test's
in-memory session so the seeded case is visible to the routes.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.schemas.snapshots import FinalCaseSnapshot
from app.core.config_registry import ConfigRegistry
from app.main import create_app
from app.models.orm import Case
from app.schemas.enums import EntityType
from app.schemas.evidence import EntityRecord
from app.services.audit.log import AuditLog
from app.services.escalation.engine import EscalationEngine
from app.services.escalation.rules import EscalationCategory, Severity
from app.services.reconciliation.snapshot import SnapshotAssembler
from app.services.review.finalization import FinalSnapshotAssembler
from app.services.review.workflow import HumanReviewWorkflow

AS_OF = date(2024, 12, 31)
CUTOFF = datetime(2025, 1, 15, tzinfo=timezone.utc)


@pytest.fixture()
def client(db_session: Session):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    app = create_app()
    app.dependency_overrides[get_session] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _finalize_case(db_session: Session, case_id: str = "DAL_2024"):
    db_session.add(
        Case(case_id=case_id, as_of_date=AS_OF, evidence_cutoff_timestamp=CUTOFF)
    )
    db_session.flush()
    registry = ConfigRegistry(db_session)
    registry.register("policy", {"net_debt_to_ebitda": 3.5})
    registry.register("source_profiles", {"filing": "critical", "fuel": "optional"})

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

    audit = AuditLog(db_session)
    EscalationEngine(db_session, audit=audit, case_id=case_id).raise_escalation(
        rule_id="R-EVIDENCE-01",
        category=EscalationCategory.EVIDENCE,
        severity=Severity.REVIEW,
        reason="Debt maturity table missing.",
    )
    wf = HumanReviewWorkflow(db_session, audit=audit, case_id=case_id)

    final = FinalSnapshotAssembler(db_session, registry, audit=audit)
    snapshot = final.assemble(
        case_id=case_id,
        evidence_snapshot_version=1,
        metrics={"net_debt_to_ebitda": 3.1},
        benchmarks={"peer_median_leverage": 2.75},
        business_analysis={"claims": [{"statement": "Legacy carrier."}]},
        financial_analysis={"summary": "Adequate liquidity."},
        risks=[{"title": "Leverage", "severity": "high"}],
        recommendation={"status": "draft", "rating": "BB"},
    )
    wf.sign_off_recommendation(reviewer="credit.officer", snapshot=snapshot)
    row = final.finalize(snapshot, signed_off_by="credit.officer")
    db_session.commit()
    return final, row


def test_get_workbench_returns_sections_and_counts(client, db_session: Session) -> None:
    _finalize_case(db_session)

    resp = client.get("/cases/DAL_2024/workbench")
    assert resp.status_code == 200
    body = resp.json()

    # Header: as-of + cutoff present (Req 24.1).
    assert body["header"]["case_id"] == "DAL_2024"
    assert body["header"]["as_of_date"] == AS_OF.isoformat()
    assert "2025-01-15" in body["header"]["evidence_cutoff_timestamp"]

    # Nine navigable sections (Req 24.1).
    assert set(body["sections"]) == {
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
    assert len(body["section_order"]) == 9

    # Four counts (Req 24.2).
    counts = body["counts"]
    assert counts["open_exceptions"] == 1
    assert counts["ai_deterministic_conflicts"] == 0
    assert counts["awaiting_human_decisions"] == 0
    assert "critical_missing_sources" in counts


def test_critical_missing_count_via_query_param(client, db_session: Session) -> None:
    _finalize_case(db_session)
    resp = client.get(
        "/cases/DAL_2024/workbench",
        params={"present_source_types": ["fuel"]},
    )
    assert resp.status_code == 200
    assert resp.json()["counts"]["critical_missing_sources"] == 1


def test_viewing_does_not_mutate_case(client, db_session: Session) -> None:
    _final, row = _finalize_case(db_session)
    hash_before = row.content_hash

    for _ in range(3):
        assert client.get("/cases/DAL_2024/workbench").status_code == 200

    db_session.refresh(row)
    assert row.content_hash == hash_before
    assert row.finalized is True


def test_version_lineage_after_later_review(client, db_session: Session) -> None:
    final, v1 = _finalize_case(db_session)
    v1_hash = v1.content_hash

    # A later review creates a NEW version referencing its predecessor.
    v2 = final.supersede(
        v1,
        recommendation={"status": "draft", "rating": "BBB-"},
    )
    revised = FinalCaseSnapshot.model_validate(v2.payload)
    HumanReviewWorkflow(db_session, case_id="DAL_2024").sign_off_recommendation(
        reviewer="credit.officer", snapshot=revised
    )
    v2 = final.finalize(revised, signed_off_by="credit.officer")
    db_session.commit()

    # Latest view -> v2, predecessor v1.
    latest = client.get("/cases/DAL_2024/workbench").json()
    assert latest["header"]["lineage"]["presented_version"] == 2
    assert latest["header"]["lineage"]["predecessor_version"] == 1

    # Old version still viewable and frozen, surfacing its successor.
    old = client.get("/cases/DAL_2024/workbench/versions/1").json()
    assert old["header"]["lineage"]["presented_version"] == 1
    assert old["header"]["lineage"]["finalized"] is True
    assert old["header"]["lineage"]["successor_versions"] == [2]

    db_session.refresh(v1)
    assert v1.content_hash == v1_hash


def test_unknown_case_returns_404(client, db_session: Session) -> None:
    assert client.get("/cases/nope/workbench").status_code == 404


def test_unknown_version_returns_404(client, db_session: Session) -> None:
    _finalize_case(db_session)
    assert client.get("/cases/DAL_2024/workbench/versions/99").status_code == 404


def test_workbench_html_rendering(client, db_session: Session) -> None:
    _finalize_case(db_session)
    resp = client.get("/cases/DAL_2024/workbench.html")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    html = resp.text

    # Header + counts + section anchors present.
    assert "Case Workbench" in html
    assert AS_OF.isoformat() in html
    assert "2025-01-15" in html
    assert 'data-count="open_exceptions">1<' in html
    for key in (
        "sources",
        "canonical_data",
        "metrics",
        "business_analysis",
        "financial_analysis",
        "risks",
        "exceptions",
        "human_review",
        "memo",
    ):
        assert f'data-section="{key}"' in html


def test_final_memo_browser_read_preserves_approval_and_hash(client, db_session):
    from sqlalchemy import func, select
    from app.models.orm import AuditEvent

    _assembler, row = _finalize_case(db_session)
    before_hash = row.content_hash
    before_events = db_session.scalar(select(func.count()).select_from(AuditEvent))
    memo = client.get("/cases/DAL_2024/memo.json?snapshot_version=1")
    assert memo.status_code == 200
    assert memo.json()["final_status"] == "final"
    html = client.get("/cases/DAL_2024/memo.html?snapshot_version=1")
    assert html.status_code == 200
    assert "Credit Memo (FINAL" in html.text
    db_session.refresh(row)
    assert row.finalized and row.content_hash == before_hash
    assert (
        db_session.scalar(select(func.count()).select_from(AuditEvent)) == before_events
    )
