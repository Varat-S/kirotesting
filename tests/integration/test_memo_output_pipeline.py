"""Integration tests for the Milestone 8 output pipeline (tasks 8.1-8.3).

Exercises the full flow end-to-end over a persisted case: seed evidence ->
finalize a FinalCaseSnapshot (with human review + an unresolved exception) ->
generate the canonical JSON FIRST -> render the memo from that JSON ONLY, and
assert JSON<->HTML parity, claim->evidence mapping, as-of/cutoff/exception
display, stable re-render, and linkage to the exact finalized snapshot
(Req 17.1-17.7, 16.4).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.core.config_registry import ConfigRegistry
from app.models.orm import Case
from app.schemas.enums import EntityType
from app.schemas.evidence import EntityRecord
from app.services.audit.log import AuditLog
from app.services.escalation.engine import EscalationEngine
from app.services.escalation.rules import EscalationCategory, Severity
from app.services.reconciliation.snapshot import SnapshotAssembler
from app.services.reporting.memo import MemoReportGenerator
from app.services.review.finalization import FinalSnapshotAssembler
from app.services.review.workflow import HumanReviewWorkflow

AS_OF = date(2024, 12, 31)
CUTOFF = datetime(2025, 1, 15, tzinfo=timezone.utc)


def test_full_output_pipeline(db_session: Session, tmp_path) -> None:
    db_session.add(Case(case_id="DAL_2024"))
    db_session.flush()
    registry = ConfigRegistry(db_session)
    registry.register("policy", {"net_debt_to_ebitda": 3.5})
    registry.register("source_profiles", {"filing": "critical"})

    assembler = SnapshotAssembler(db_session, registry)
    entity = EntityRecord(
        entity_id="DAL", legal_name="Delta", entity_type=EntityType.BORROWER
    )
    evidence = assembler.assemble(
        case_id="DAL_2024",
        facts=[],
        reconciliations=[],
        entities=[entity],
        as_of_date=AS_OF,
        evidence_cutoff_timestamp=CUTOFF,
    )
    assembler.persist(evidence)

    audit = AuditLog(db_session)

    # A non-mandatory escalation remains open -> surfaces as unresolved exception.
    engine = EscalationEngine(db_session, audit=audit, case_id="DAL_2024")
    engine.raise_escalation(
        rule_id="R-EVIDENCE-01",
        category=EscalationCategory.EVIDENCE,
        severity=Severity.REVIEW,
        reason="Debt maturity table missing.",
    )

    # A human review + sign-off.
    wf = HumanReviewWorkflow(db_session, audit=audit, case_id="DAL_2024")
    wf.sign_off_recommendation(reviewer="credit.officer")

    final = FinalSnapshotAssembler(db_session, registry, audit=audit)
    snapshot = final.assemble(
        case_id="DAL_2024",
        evidence_snapshot_version=1,
        metrics={"net_debt_to_ebitda": 3.1, "ebitda": 6500.0},
        benchmarks={"peer_median_leverage": 2.75},
        business_analysis={
            "claims": [
                {"statement": "Delta is a legacy network carrier.",
                 "evidence_ids": ["F1"]}
            ]
        },
        risks=[
            {"title": "Leverage", "statement": "Leverage above peer median.",
             "evidence_ids": ["M1"], "severity": "high"}
        ],
        mitigants=[{"statement": "Strong liquidity position.", "evidence_ids": ["F7"]}],
        human_reviews=[
            {"reviewer": "credit.officer", "action": "sign_off_recommendation",
             "reason": "Final recommendation approved."}
        ],
        recommendation={"status": "draft", "rating": "BB"},
    )
    row = final.finalize(
        snapshot, signed_off_by="credit.officer", has_sign_off=wf.has_sign_off()
    )

    gen = MemoReportGenerator(db_session, audit=audit, output_root=tmp_path)
    out = gen.generate(case_id="DAL_2024", snapshot_version=1, write=True)

    memo = out.memo_json
    html = out.html

    # Linked to the exact finalized snapshot.
    assert memo["source_snapshot"]["snapshot_version"] == row.snapshot_version
    assert memo["source_snapshot"]["content_hash"] == row.content_hash

    # As-of / cutoff / unresolved exception shown (Req 17.6).
    assert AS_OF.isoformat() in html
    assert "2025-01-15" in html
    assert "R-EVIDENCE-01" in html
    assert "Debt maturity table missing." in html

    # Numerical parity (Req 17.4).
    assert 'data-metric="net_debt_to_ebitda">3.1<' in html
    assert 'data-benchmark="peer_median_leverage">2.75<' in html

    # Claim -> evidence mapping (Req 17.5).
    assert 'data-evidence="F1"' in html
    assert 'data-evidence="M1"' in html
    assert 'data-evidence="F7"' in html

    # Human review history rendered.
    assert "sign_off_recommendation" in html or "credit.officer" in html

    # Artifacts written under tmp_path, not the repo tree.
    assert out.json_path.exists() and str(out.json_path).startswith(str(tmp_path))
    assert out.html_path.exists()

    # Stable re-render.
    out2 = gen.generate(case_id="DAL_2024", snapshot_version=1, write=False)
    assert out2.html == html
    assert out2.canonical_json == out.canonical_json
