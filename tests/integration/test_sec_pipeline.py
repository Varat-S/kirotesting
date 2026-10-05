"""Real bytes, real ingestion/mapping/metrics and deterministic offline AI."""

from datetime import date, datetime, timezone
from pathlib import Path
import json

import pytest
from sqlalchemy import func, select

from app.core.hashing import content_hash
from app.models.base import create_engine_and_session, init_db
from app.models.orm import AuditEvent, CaseEntity, Document, Fact, FactSourceRef
from app.services.pipeline.runner import CreditMemoPipeline
from app.services.pipeline.sec import sec_source_package
from app.services.extraction.sec.bundle import SecFilingBundle
from tests.sec_helpers import bundle, filing_file

FIXTURE = Path(__file__).resolve().parents[2] / "examples/sec/nvda-2026/bundle.json"


def run(
    session,
    root,
    source,
    cutoff="2025-01-15T00:00:00Z",
    case="POC_2024",
    scope="consolidated",
):
    package = sec_source_package(
        source, cutoff=datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    )
    package.sec_bundles[0].scope = scope
    return CreditMemoPipeline(
        session, data_root=root / "data", output_root=root / "output"
    ).run_case(case, package=package)


def test_dimension_safety_single_filing_repeats_and_persisted_lineage(
    db_session, tmp_path
):
    result = run(db_session, tmp_path, bundle())
    revenue = [
        dq
        for key, dq in result.evidence_snapshot.data_quality.items()
        if json.loads(key)[4] == "revenue"
    ]
    assert len(revenue) == 3
    assert sorted(dq.value for dq in revenue) == [30, 70, 100]
    assert all(dq.state == "unverified" for dq in revenue)
    assert not any(
        dq.state == "conflicting"
        for dq in result.evidence_snapshot.data_quality.values()
    )
    assert result.metrics["operating_margin"].result == 0.2
    assert result.metrics["operating_margin"].review_required
    facts = list(db_session.scalars(select(Fact)))
    assert len(facts) == 5 and sum(bool(f.dimensions) for f in facts) == 2
    refs = list(db_session.scalars(select(FactSourceRef)))
    assert {r.inline_element_id for r in refs} == {"r", "r2", "gaming", "dc", "o"}
    assert all(r.xbrl_context_id and r.sec_accession for r in refs)


def test_explicit_segment_scope_never_becomes_consolidated_metric(db_session, tmp_path):
    result = run(db_session, tmp_path, bundle(), scope="segment")
    assert result.metrics["operating_margin"].result is None
    assert all(
        f["consolidation_scope"] == "segment" for f in result.evidence_snapshot.facts
    )


def test_reacquiring_same_accession_is_not_independent_corroboration(
    db_session, tmp_path
):
    source = bundle()
    second = bundle(
        [
            source.files[0].model_copy(
                update={"retrieved_at": datetime(2026, 10, 2, tzinfo=timezone.utc)}
            )
        ]
    )
    package = sec_source_package(
        source, cutoff=datetime(2025, 1, 15, tzinfo=timezone.utc)
    )
    package.sec_bundles.append(
        package.sec_bundles[0].model_copy(update={"bundle": second})
    )
    result = CreditMemoPipeline(
        db_session, data_root=tmp_path / "data", output_root=tmp_path / "output"
    ).run_case("POC_2024", package=package)
    assert len(result.evidence_snapshot.documents) == 2
    assert len(result.evidence_snapshot.facts) == 10
    assert all(
        dq.state == "unverified"
        for dq in result.evidence_snapshot.data_quality.values()
    )
    assert result.metrics["operating_margin"].result == 0.2
    assert result.metrics["operating_margin"].review_required


def test_each_companion_cutoff_checked_and_rejections_audited(db_session, tmp_path):
    primary = filing_file()
    old = filing_file(
        "old.xsd",
        "schema",
        b'<schema xmlns="http://www.w3.org/2001/XMLSchema"/>',
        filing_date=date(2025, 1, 9),
    )
    proxy = filing_file(
        "proxy.htm",
        "proxy",
        b"<html><p>Board ownership governance</p></html>",
        filing_date=date(2025, 5, 1),
        accession="0000000001-25-000003",
        form="DEF 14A",
    )
    amendment = filing_file(
        "amendment.htm",
        "other_exhibit",
        b"<html>Amended filing</html>",
        filing_date=date(2025, 2, 1),
        accession="0000000001-25-000002",
        form="10-K/A",
    )
    result = run(db_session, tmp_path, bundle([primary, old, proxy, amendment]))
    assert len(result.admitted_source_hashes) == 2
    assert {f["form"] for f in result.rejected_sources} == {"DEF 14A", "10-K/A"}
    assert db_session.scalar(select(func.count()).select_from(Document)) == 2
    events = list(
        db_session.scalars(
            select(AuditEvent).where(AuditEvent.event_type == "evidence_rejected")
        )
    )
    assert len(events) == 2 and all(e.after["available_at"] for e in events)
    assert not any(
        p["sec_accession"] == proxy.accession
        for p in result.evidence_snapshot.narrative_evidence
    )


@pytest.mark.parametrize(
    "cutoff,admitted",
    [
        ("2025-01-10T23:59:59Z", 0),
        ("2025-01-11T04:59:59Z", 0),
        ("2025-01-11T05:00:00Z", 1),
    ],
)
def test_same_day_date_only_filing_is_conservative(
    db_session, tmp_path, cutoff, admitted
):
    result = run(db_session, tmp_path, bundle(), cutoff=cutoff)
    assert len(result.admitted_source_hashes) == admitted
    assert len(result.rejected_sources) == 1 - admitted


def test_official_acceptance_allows_precise_same_day_cutoff(db_session, tmp_path):
    file = filing_file(accepted_at=datetime(2025, 1, 10, 18, tzinfo=timezone.utc))
    result = run(db_session, tmp_path, bundle([file]), cutoff="2025-01-10T18:00:00Z")
    assert len(result.admitted_source_hashes) == 1
    assert result.metrics["operating_margin"].result == 0.2


def test_nvda_real_fixture_to_memo_and_browser_is_reproducible(db_session, tmp_path):
    source = SecFilingBundle.load(FIXTURE)
    result = run(
        db_session,
        tmp_path / "first",
        source,
        cutoff="2026-03-01T00:00:00Z",
        case="NVDA_2026",
    )
    evidence = result.evidence_snapshot
    diagnostics = evidence.sec_filings[0]
    assert len(diagnostics["contexts"]) > 200
    assert len(evidence.facts) > 1000
    assert len(diagnostics["presentation"]) > 50
    assert any(f["dimensions"] for f in evidence.facts)
    assert any(f["type"] == "text_block" for f in diagnostics["observations"])
    revenues = [
        f
        for f in evidence.facts
        if f["taxonomy_concept"] == "us-gaap:Revenues"
        and f["fiscal_year"] == 2026
        and f["period_type"] == "FY"
        and not f["dimensions"]
    ]
    assert revenues and all(f["normalized_value"] == 215938 for f in revenues)
    assert all(
        f["normalized_unit"] == "USD_million"
        and f["xbrl_context_id"]
        and f["inline_element_id"]
        for f in revenues
    )
    assert all(
        f["source_refs"][0]["document_id"] == diagnostics["primary_document_id"]
        for f in revenues
    )
    assert result.metrics["operating_margin"].result == pytest.approx(130387 / 215938)
    assert result.metrics["revenue_growth"].result == pytest.approx(215938 / 130497 - 1)
    assert all(
        m.evidence_quality == "unverified"
        for m in result.metrics.values()
        if m.result is not None
    )
    assert (
        len(result.admitted_source_hashes) == 13 and len(result.rejected_sources) == 1
    )
    assert result.rejected_sources[0]["form"] == "DEF 14A"
    assert len(diagnostics["subsidiaries"]) == 3
    assert all(
        "Subsidiaries of Registrant" not in row["name"]
        for row in diagnostics["subsidiaries"]
    )
    assert db_session.scalar(select(func.count()).select_from(CaseEntity)) == 1
    assert any(
        f["taxonomy_concept"].startswith("nvda:") and f["mapping_status"] == "unmapped"
        for f in evidence.facts
    )
    assert diagnostics["deferred_linkbases"] == [
        "calculation_linkbase",
        "definition_linkbase",
    ]
    assert evidence.narrative_evidence and all(
        p["status"] == "unverified" for p in evidence.narrative_evidence
    )
    assert result.output_paths["html"].exists() and result.output_paths["json"].exists()
    assert result.final_snapshot is None and not result.draft_snapshot.finalized
    event_types = set(db_session.scalars(select(AuditEvent.event_type)))
    assert {
        "evidence_admitted",
        "evidence_rejected",
        "inline_xbrl_parsed",
        "narrative_evidence_extracted",
        "subsidiaries_extracted",
        "mapping_review_required",
    } <= event_types
    # The workbench exposes SEC diagnostics and escaped candidate text read-only.
    from fastapi.testclient import TestClient
    from app.api.deps import get_session
    from app.main import create_app

    before = db_session.scalar(select(func.count()).select_from(AuditEvent))
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db_session
    with TestClient(app) as browser:
        view = browser.get("/cases/NVDA_2026/workbench").json()
        assert (
            view["sections"]["canonical_data"]["sec_filings"][0]["accession"]
            == source.accession
        )
        html = browser.get("/cases/NVDA_2026/workbench.html")
        assert html.status_code == 200 and "SEC filing diagnostics" in html.text
        assert browser.get("/cases/NVDA_2026/memo.html").status_code == 200
    assert db_session.scalar(select(func.count()).select_from(AuditEvent)) == before
    engine, factory = create_engine_and_session("sqlite://")
    init_db(engine)
    try:
        with factory() as second_session:
            second = run(
                second_session,
                tmp_path / "second",
                source,
                cutoff="2026-03-01T00:00:00Z",
                case="NVDA_2026",
            )
            assert content_hash(second.evidence_snapshot) == content_hash(evidence)
            assert (
                second.memo_json["source_snapshot"]["content_hash"]
                == result.memo_json["source_snapshot"]["content_hash"]
            )
            assert (
                second.output_paths["json"].read_bytes()
                == result.output_paths["json"].read_bytes()
            )
    finally:
        engine.dispose()


def test_later_cutoff_proxy_cites_its_own_document(db_session, tmp_path):
    primary = filing_file()
    proxy = filing_file(
        "proxy.htm",
        "proxy",
        b"<html><h2>Board governance</h2><p>Beneficial ownership of executive officers and directors.</p></html>",
        filing_date=date(2025, 5, 1),
        accession="0000000001-25-000003",
        form="DEF 14A",
    )
    result = run(
        db_session, tmp_path, bundle([primary, proxy]), cutoff="2025-06-01T00:00:00Z"
    )
    documents = {d["role"]: d for d in result.evidence_snapshot.documents}
    proxy_passages = [
        p
        for p in result.evidence_snapshot.narrative_evidence
        if p["sec_accession"] == proxy.accession
    ]
    assert proxy_passages and all(
        p["document_id"] == documents["proxy"]["document_id"] for p in proxy_passages
    )
    assert (
        documents["proxy"]["document_id"]
        != documents["primary_inline_xbrl"]["document_id"]
    )
    assert "proxy_linked" in set(db_session.scalars(select(AuditEvent.event_type)))


def test_narrative_citation_is_present_but_not_automatically_entailed(
    db_session, tmp_path
):
    from app.schemas.llm import AnalysisResponse, AnalyticalClaim
    from app.services.pipeline.runner import offline_backend
    from app.services.extraction.sec.statements import parse_bundle

    source = bundle()
    # Stable source metadata gives the same document ID as normal admission.
    did = content_hash({"case": "POC_2024", "source": source.files[0].metadata()})
    _, _, passages = parse_bundle(
        source.model_copy(
            update={
                "files": [
                    source.files[0].model_copy(
                        update={"admitted": True, "document_id": did}
                    )
                ]
            }
        ),
        entity_id="POC",
    )
    claim = AnalyticalClaim(
        claim_id="supplier-claim",
        text="The company depends on third-party manufacturers.",
        kind="fact",
        evidence_ids=[passages[0]["evidence_id"]],
    )
    backend = offline_backend()
    backend.register(
        "analyze", AnalysisResponse(business_overview=[claim]).model_dump(mode="json")
    )
    package = sec_source_package(
        source, cutoff=datetime(2025, 1, 15, tzinfo=timezone.utc)
    )
    result = CreditMemoPipeline(
        db_session,
        backend=backend,
        data_root=tmp_path / "data",
        output_root=tmp_path / "output",
    ).run_case("POC_2024", package=package)
    grounding = result.draft_snapshot.payload["financial_analysis"]["grounding"][0]
    assert grounding["citation_present"] is True
    assert (
        grounding["entailment_state"] == "not_verifiable"
        and not grounding["is_grounded"]
    )
