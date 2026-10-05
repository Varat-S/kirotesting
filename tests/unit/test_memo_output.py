"""Unit tests for Milestone 8 output generation (tasks 8.1-8.3).

Covers Req 17.1-17.7:

* 8.1 -- the canonical FinalCaseSnapshot memo JSON is produced FIRST, contains
  source refs / facts / metrics / analysis / escalations / human reviews / final
  status / version metadata, validates against the JSON Schema, and emits
  ``memo_generated`` + ``case_finalized`` (Req 17.1).
* 8.2 -- the memo is rendered from that JSON ONLY via deterministic templates:
  configured sections present, no LLM, no DB re-read, as-of / cutoff /
  unresolved exceptions shown (Req 17.2, 17.3, 17.6).
* 8.3 -- JSON<->PDF(HTML) parity: numerical parity (Req 17.4), claim->evidence
  mapping (Req 17.5), stable re-render (Req 17.7), output linked to the exact
  snapshot (Req 16.4 / 17.7).

The HTML artifact is the parity-checkable surface (binary PDFs can embed
non-deterministic timestamps), so parity and re-render equivalence are asserted
on the HTML. PDF production itself is tested only when an offline backend is
installed and skipped gracefully otherwise.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.config_registry import ConfigRegistry
from app.models.orm import AuditEvent, Case
from app.schemas.enums import EntityType
from app.schemas.evidence import EntityRecord
from app.services.audit.log import AuditLog
from app.services.escalation.engine import EscalationEngine
from app.services.escalation.rules import EscalationCategory, Severity
from app.services.reconciliation.snapshot import SnapshotAssembler
from app.services.reporting.memo import (
    MEMO_SECTIONS,
    MemoReportGenerator,
    OutputIntegrityError,
    PdfBackendUnavailableError,
    detect_pdf_backend,
)
from app.services.review.workflow import HumanReviewWorkflow
from app.services.review.finalization import FinalSnapshotAssembler

AS_OF = date(2024, 12, 31)
CUTOFF = datetime(2025, 1, 15, tzinfo=timezone.utc)


def _seed(session: Session) -> ConfigRegistry:
    """Create a case + a persisted CanonicalEvidenceSnapshot v1 with dates."""
    session.add(Case(case_id="DAL_2024"))
    session.flush()

    registry = ConfigRegistry(session)
    registry.register("tolerances", {"revenue": 0.005})
    registry.register("policy", {"net_debt_to_ebitda": 3.5})
    registry.register("source_profiles", {"filing": "critical"})

    assembler = SnapshotAssembler(session, registry)
    entity = EntityRecord(
        entity_id="DAL", legal_name="Delta", entity_type=EntityType.BORROWER
    )
    snapshot = assembler.assemble(
        case_id="DAL_2024",
        facts=[],
        reconciliations=[],
        entities=[entity],
        as_of_date=AS_OF,
        evidence_cutoff_timestamp=CUTOFF,
    )
    assembler.persist(snapshot)
    return registry


def _finalize(
    session: Session,
    registry: ConfigRegistry,
    audit: AuditLog,
    *,
    metrics: dict | None = None,
    benchmarks: dict | None = None,
    business_analysis: dict | None = None,
    financial_analysis: dict | None = None,
    risks: list | None = None,
    mitigants: list | None = None,
    recommendation: dict | None = None,
):
    final = FinalSnapshotAssembler(session, registry, audit=audit)
    snapshot = final.assemble(
        case_id="DAL_2024",
        evidence_snapshot_version=1,
        metrics=metrics,
        benchmarks=benchmarks,
        business_analysis=business_analysis,
        financial_analysis=financial_analysis,
        risks=risks,
        mitigants=mitigants,
        recommendation=recommendation,
        metric_definition_versions={"net_debt_to_ebitda": 1},
        rule_versions={"R-POLICY-LEV-01": 1},
        prompt_model_versions={"business_analysis": "v1.0"},
    )
    HumanReviewWorkflow(session, case_id="DAL_2024").sign_off_recommendation(
        reviewer="officer", snapshot=snapshot
    )
    return final.finalize(snapshot, signed_off_by="officer")


# -- 8.1: JSON first ----------------------------------------------------------


def test_json_generated_first_with_required_content(db_session: Session) -> None:
    """Req 17.1: JSON contains source refs, metrics, analysis, versions, status."""
    registry = _seed(db_session)
    audit = AuditLog(db_session)
    _finalize(
        db_session,
        registry,
        audit,
        metrics={"net_debt_to_ebitda": 3.1, "revenue": 50000.0},
        business_analysis={
            "summary": {
                "statement": "Delta is a legacy carrier.",
                "evidence_ids": ["F1", "F2"],
            }
        },
    )

    gen = MemoReportGenerator(db_session, audit=audit)
    memo = gen.generate_json(case_id="DAL_2024", snapshot_version=1)

    # Linked to the exact finalized snapshot (version + content_hash).
    assert memo["source_snapshot"]["snapshot_version"] == 1
    assert memo["source_snapshot"]["content_hash"]
    # As-of / cutoff resolved from the referenced evidence snapshot and embedded.
    assert memo["as_of_date"] == AS_OF.isoformat()
    assert memo["evidence_cutoff_timestamp"].startswith("2025-01-15")
    # Full FinalCaseSnapshot content present.
    snap = memo["final_case_snapshot"]
    assert snap["snapshot_type"] == "final_case"
    assert snap["metrics"]["net_debt_to_ebitda"] == 3.1
    assert snap["config_versions"]["policy"] == 1
    assert snap["metric_definition_versions"] == {"net_debt_to_ebitda": 1}
    assert memo["final_status"] == "final"


def test_json_validates_against_schema(db_session: Session) -> None:
    """Req 17.1: the embedded FinalCaseSnapshot validates against its schema."""
    from app.schemas.json_schema import validate_snapshot

    registry = _seed(db_session)
    audit = AuditLog(db_session)
    _finalize(db_session, registry, audit, metrics={"leverage": 3.0})
    gen = MemoReportGenerator(db_session, audit=audit)
    memo = gen.generate_json(case_id="DAL_2024", snapshot_version=1)

    # Does not raise.
    validate_snapshot(memo["final_case_snapshot"])


def test_json_emits_memo_generated_and_case_finalized(db_session: Session) -> None:
    """Req 17.1 / Req 18.2: both audit events are emitted exactly once."""
    registry = _seed(db_session)
    audit = AuditLog(db_session)
    _finalize(db_session, registry, audit)
    gen = MemoReportGenerator(db_session, audit=audit)
    gen.generate_json(case_id="DAL_2024", snapshot_version=1, actor_id="svc")

    types = [
        e.event_type
        for e in db_session.query(AuditEvent)
        .filter(AuditEvent.case_id == "DAL_2024")
        .all()
    ]
    assert types.count("memo_generated") == 1
    assert types.count("case_finalized") == 1


def test_json_requires_finalized_snapshot(db_session: Session) -> None:
    """Outputs can only be generated from an existing finalized snapshot."""
    _seed(db_session)
    gen = MemoReportGenerator(db_session)
    with pytest.raises(ValueError):
        gen.generate_json(case_id="DAL_2024", snapshot_version=1)


def test_json_rejects_tampered_snapshot(db_session: Session) -> None:
    """A finalized snapshot whose payload drifts from its frozen hash is refused.

    A finalized snapshot is immutable (M7), so this corruption cannot happen via
    the normal API; we insert a finalized row whose payload does NOT match its
    recorded ``content_hash`` to prove the output path re-verifies integrity and
    refuses to render stale content.
    """
    from app.models.base import utcnow
    from app.models.orm import Snapshot

    _seed(db_session)
    # Insert a finalized final_case row with a deliberately wrong hash.
    corrupt = Snapshot(
        case_id="DAL_2024",
        snapshot_type="final_case",
        snapshot_version=1,
        schema_version="1.0",
        finalized=True,
        config_versions={},
        payload={
            "schema_version": "1.0",
            "snapshot_type": "final_case",
            "snapshot_version": 1,
            "evidence_snapshot_ref": {"case_id": "DAL_2024", "snapshot_version": 1},
            "recommendation": {"status": "final"},
            "finalized": True,
        },
        content_hash="deadbeef",
        finalized_at=utcnow(),
    )
    db_session.add(corrupt)
    db_session.flush()

    gen = MemoReportGenerator(db_session)
    with pytest.raises(OutputIntegrityError):
        gen.generate_json(case_id="DAL_2024", snapshot_version=1)


# -- 8.2: deterministic rendering from JSON -----------------------------------


def test_html_includes_all_configured_sections(db_session: Session) -> None:
    """Req 17.3: every configured memo section is rendered."""
    registry = _seed(db_session)
    audit = AuditLog(db_session)
    _finalize(db_session, registry, audit, metrics={"leverage": 3.0})
    gen = MemoReportGenerator(db_session, audit=audit)
    memo = gen.generate_json(case_id="DAL_2024", snapshot_version=1)
    html = gen.render_html(memo)

    for key, _title in MEMO_SECTIONS:
        assert f'data-section="{key}"' in html


def test_html_shows_as_of_cutoff_and_unresolved_exceptions(db_session: Session) -> None:
    """Req 17.6: as-of, cutoff, and unresolved exceptions appear in the memo."""
    registry = _seed(db_session)
    audit = AuditLog(db_session)
    # Raise a non-mandatory escalation so it surfaces as an unresolved exception
    # without blocking finalization.
    engine = EscalationEngine(db_session, audit=audit, case_id="DAL_2024")
    engine.raise_escalation(
        rule_id="R-EVIDENCE-01",
        category=EscalationCategory.EVIDENCE,
        severity=Severity.REVIEW,
        reason="Interim statements missing.",
    )
    _finalize(db_session, registry, audit)
    gen = MemoReportGenerator(db_session, audit=audit)
    memo = gen.generate_json(case_id="DAL_2024", snapshot_version=1)
    html = gen.render_html(memo)

    assert AS_OF.isoformat() in html
    assert "2025-01-15" in html
    assert "R-EVIDENCE-01" in html
    assert "Interim statements missing." in html


def test_html_contains_no_content_absent_from_json(db_session: Session) -> None:
    """Req 17.5: metric names rendered are exactly those present in the JSON."""
    registry = _seed(db_session)
    audit = AuditLog(db_session)
    _finalize(db_session, registry, audit, metrics={"leverage": 3.0, "coverage": 4.2})
    gen = MemoReportGenerator(db_session, audit=audit)
    memo = gen.generate_json(case_id="DAL_2024", snapshot_version=1)
    html = gen.render_html(memo)

    rendered_metrics = set(re.findall(r'data-metric="([^"]+)"', html))
    rendered_metrics.discard("unresolved_exceptions_count")
    assert rendered_metrics == set(memo["final_case_snapshot"]["metrics"].keys())


# -- 8.3: parity, claim->evidence, re-render, linkage -------------------------


def test_numerical_parity_json_to_html(db_session: Session) -> None:
    """Req 17.4: every metric/benchmark number in the HTML equals the JSON."""
    registry = _seed(db_session)
    audit = AuditLog(db_session)
    _finalize(
        db_session,
        registry,
        audit,
        metrics={"net_debt_to_ebitda": 3.1, "revenue": 50000.0},
        benchmarks={"peer_median_leverage": 2.75},
    )
    gen = MemoReportGenerator(db_session, audit=audit)
    memo = gen.generate_json(case_id="DAL_2024", snapshot_version=1)
    html = gen.render_html(memo)

    for name, value in memo["final_case_snapshot"]["metrics"].items():
        m = re.search(rf'data-metric="{re.escape(name)}">([^<]+)<', html)
        assert m is not None, f"metric {name} not rendered"
        assert float(m.group(1)) == float(value)

    for name, value in memo["final_case_snapshot"]["benchmarks"].items():
        m = re.search(rf'data-benchmark="{re.escape(name)}">([^<]+)<', html)
        assert m is not None, f"benchmark {name} not rendered"
        assert float(m.group(1)) == float(value)


def test_claims_map_back_to_evidence(db_session: Session) -> None:
    """Req 17.5: every major claim carries evidence refs present in the JSON."""
    registry = _seed(db_session)
    audit = AuditLog(db_session)
    _finalize(
        db_session,
        registry,
        audit,
        business_analysis={
            "claims": [
                {"statement": "Liquidity is adequate.", "evidence_ids": ["F10"]},
            ]
        },
        risks=[
            {
                "title": "Fuel price exposure",
                "statement": "Jet fuel is volatile.",
                "evidence_ids": ["F20", "F21"],
                "severity": "high",
            },
        ],
    )
    gen = MemoReportGenerator(db_session, audit=audit)
    memo = gen.generate_json(case_id="DAL_2024", snapshot_version=1)
    html = gen.render_html(memo)

    # Each rendered claim exposes a non-empty data-evidence attribute.
    claim_evidence = re.findall(r'data-claim="[^"]+"\s+data-evidence="([^"]*)"', html)
    major = [e for e in claim_evidence if e != ""]
    assert major, "expected at least one major claim with evidence refs"
    for refs in major:
        assert all(ref for ref in refs.split(","))
    # The specific evidence IDs from the JSON appear in the mapping.
    joined = ",".join(major)
    assert "F10" in joined
    assert "F20" in joined and "F21" in joined


def test_re_render_is_stable(db_session: Session) -> None:
    """Req 17.7: re-rendering the same snapshot yields identical HTML + JSON."""
    registry = _seed(db_session)
    audit = AuditLog(db_session)
    _finalize(db_session, registry, audit, metrics={"leverage": 3.0})
    gen = MemoReportGenerator(db_session, audit=audit)

    memo1 = gen.generate_json(case_id="DAL_2024", snapshot_version=1)
    memo2 = gen.generate_json(case_id="DAL_2024", snapshot_version=1)
    assert gen.render_html(memo1) == gen.render_html(memo2)

    from app.core.hashing import content_hash

    assert content_hash(memo1) == content_hash(memo2)


def test_output_linked_to_exact_snapshot(db_session: Session) -> None:
    """Req 16.4 / 17.7: output records the exact snapshot version + hash."""
    registry = _seed(db_session)
    audit = AuditLog(db_session)
    row = _finalize(db_session, registry, audit)
    gen = MemoReportGenerator(db_session, audit=audit)
    memo = gen.generate_json(case_id="DAL_2024", snapshot_version=1)

    assert memo["source_snapshot"]["snapshot_version"] == row.snapshot_version
    assert memo["source_snapshot"]["content_hash"] == row.content_hash
    html = gen.render_html(memo)
    assert row.content_hash in html


def test_writes_outputs_to_configurable_root(db_session: Session, tmp_path) -> None:
    """Output root is configurable; artifacts land under tmp_path, not the repo."""
    registry = _seed(db_session)
    audit = AuditLog(db_session)
    _finalize(db_session, registry, audit, metrics={"leverage": 3.0})
    gen = MemoReportGenerator(db_session, audit=audit, output_root=tmp_path)
    out = gen.generate(case_id="DAL_2024", snapshot_version=1, write=True)

    assert out.json_path is not None and out.json_path.exists()
    assert out.html_path is not None and out.html_path.exists()
    assert str(out.json_path).startswith(str(tmp_path))
    # The written JSON is the canonical serialization (stable key order).
    from app.core.hashing import canonical_json

    assert out.json_path.read_text(encoding="utf-8") == canonical_json(out.memo_json)


# -- PDF production (skips gracefully without an offline backend) -------------


def test_pdf_production_or_skip(db_session: Session) -> None:
    """Req 17.2: PDF is produced from the SAME HTML when a backend exists."""
    registry = _seed(db_session)
    audit = AuditLog(db_session)
    _finalize(db_session, registry, audit, metrics={"leverage": 3.0})
    gen = MemoReportGenerator(db_session, audit=audit)
    memo = gen.generate_json(case_id="DAL_2024", snapshot_version=1)

    if detect_pdf_backend() is None:
        with pytest.raises(PdfBackendUnavailableError):
            gen.render_pdf(memo)
        pytest.skip("No offline HTML->PDF backend installed; HTML artifact verified.")
    else:  # pragma: no cover - only when a backend is installed
        pdf = gen.render_pdf(memo)
        assert pdf[:4] == b"%PDF"
