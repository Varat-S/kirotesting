"""Milestone 2: normal preparation -> canonical snapshot -> routed M3 packet."""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models.orm import EvidencePacketRow, Metric, Snapshot
from app.services.bo.offline import ScriptedBoBackend
from app.services.bo.runner import BusinessOverviewRunner, M3_PACKET_AGENT_ID
from app.services.pipeline.package import Module3Input, SourceInput, SourcePackage
from tests.integration.test_bo_v3_complete import _script
from .responses import with_credit_response
from .review_fixtures import FixtureReviewerBackend


ROOT = Path(__file__).resolve().parents[2] / "examples" / "synthetic-case"


def _pdf_bytes():
    from reportlab.pdfgen.canvas import Canvas

    stream = BytesIO()
    canvas = Canvas(stream)
    for lines in (
        (
            "Form 10-K",
            "Delta Synthetic Fixture",
            "For the fiscal year ended December 31, 2024",
        ),
        (
            "Executive Officers",
            "As of January 10, 2025, our executive officers were as follows:",
            "Alex Sample........ 50 Chief Executive Officer",
            "Alex Sample was appointed Chief Executive Officer effective May 1, 2024.",
            "Alex Sample previously served as President of the Company from January 2020 through April 2024.",
            "Item 2.",
        ),
    ):
        y = 760
        for line in lines:
            canvas.drawString(72, y, line)
            y -= 22
        canvas.showPage()
    canvas.save()
    return stream.getvalue()


def _corporate_payload(company="Delta Synthetic Fixture"):
    return {
        "schema_version": "1.0",
        "document_type": "corporate_structure_onboarding",
        "company_name": company,
        "data_status": "simulated_educational_example",
        "corporate_tree": {
            "id": "DAL",
            "name": "Delta Synthetic Fixture",
            "role": "Borrower",
            "is_guarantor": False,
            "children": [],
        },
    }


def _package(*, management_available_at=None, corporate_company=None):
    package = SourcePackage.load(ROOT / "package.json").model_copy(deep=True)
    admitted_at = datetime(2025, 1, 10, tzinfo=timezone.utc)
    package.sources.extend([
        SourceInput(
            filename="corporate-structure.json",
            data=json.dumps(_corporate_payload(
                corporate_company or "Delta Synthetic Fixture"
            )).encode(),
            parser="structured_json", tags=["m3", "corporate_structure"],
            available_at=admitted_at, entity_id="DAL", scope="consolidated",
            accounting_basis="not_applicable",
        ),
        SourceInput(
            filename="management-10k.pdf", data=_pdf_bytes(), parser="pdf_text",
            tags=["m3", "management", "form_10k"],
            available_at=management_available_at or admitted_at,
            entity_id="DAL", scope="consolidated", accounting_basis="GAAP",
        ),
    ])
    module3 = Module3Input(
        corporate_source="corporate-structure.json",
        management_source="management-10k.pdf",
        company="Delta Synthetic Fixture", filing_period="2024-12-31",
    )
    return SourcePackage(
        as_of_date=package.as_of_date,
        evidence_cutoff_timestamp=package.evidence_cutoff_timestamp,
        borrower_entity_id=package.borrower_entity_id,
        entities=package.entities,
        sources=package.sources,
        sec_bundles=package.sec_bundles,
        sector_benchmark=package.sector_benchmark,
        module3=module3,
    )


class OfflinePacketExtractor:
    """Saved-response stand-in whose source IDs come from the routed packet."""

    extraction_mode = "offline_fixture"

    def extract(self, sources, diagnostics):
        assert sources.source_path.startswith("canonical-document:")
        assert sources.section_ids
        source_id = sources.section_ids[0]
        assert source_id in sources.pages
        current = (
            "Alex Sample was appointed Chief Executive Officer effective May 1, 2024."
        )
        prior = (
            "Alex Sample previously served as President of the Company from "
            "January 2020 through April 2024."
        )
        diagnostics.update(answer_returned=True, parsing="not_attempted")
        return json.dumps(with_credit_response({
            "roster_date": "2025-01-10",
            "roster_source_id": source_id,
            "roster_quote": (
                "As of January 10, 2025, our executive officers were as follows:"
            ),
            "executives": [{
                "name": "Alex Sample", "position": "Chief Executive Officer",
                "appointment_date": "2024-05-01", "source_id": source_id,
                "quote": current,
                "prior_internal_roles": [{
                    "position": "President of the Company",
                    "start_date": "2020-01", "end_date": "2024-04",
                    "source_id": source_id, "quote": prior,
                }],
                "external_recruitment_quote": "", "external_source_id": "",
            }],
        }, recent_names=['Alex Sample']))


def _run(db_session, tmp_path, package):
    return BusinessOverviewRunner(
        db_session, backend=ScriptedBoBackend(_script()), bo_roster="v3", m3_version="v2",
        data_root=tmp_path / "data", output_root=tmp_path / "out",
        m3_extractor=OfflinePacketExtractor(),
        m3_reviewer_backend=FixtureReviewerBackend(),
    ).run_case("M3_SHARED_INPUT", package=package, write_outputs=False)


def test_normal_case_preparation_routes_and_persists_only_admitted_m3_inputs(
        db_session, tmp_path, monkeypatch):
    from app.services.bo.m3_rebuild import workflow

    monkeypatch.setattr(
        workflow, "locate_sources",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("integrated M3 reopened a private PDF path")
        ),
    )
    result = _run(db_session, tmp_path, _package())
    snapshot_row = db_session.scalars(select(Snapshot).where(
        Snapshot.case_id == "M3_SHARED_INPUT",
        Snapshot.snapshot_type == "canonical_evidence",
    )).one()
    snapshot = snapshot_row.payload
    structured = snapshot["structured_evidence"]
    assert len(structured) == 1
    corporate = structured[0]
    assert corporate["validation_status"] == "contract_validated"
    assert corporate["assumption_status"] == "simulated"
    assert corporate["reporting_company"] == "Delta Synthetic Fixture"

    packet_row = db_session.scalars(select(EvidencePacketRow).where(
        EvidencePacketRow.analysis_run_id == result.analysis_run_id,
        EvidencePacketRow.agent_id == M3_PACKET_AGENT_ID,
    )).one()
    packet = packet_row.payload
    assert packet["structured_evidence"] == structured
    assert datetime.fromisoformat(
        packet["context"]["evidence_cutoff_timestamp"]
    ) == datetime(2025, 1, 15, tzinfo=timezone.utc)
    assert packet["context"]["company"] == "Delta Synthetic Fixture"
    assert packet["context"]["filing_period"] == "2024-12-31"
    management_document_id = packet["context"]["management_document_id"]
    documents = {item["document_id"]: item for item in snapshot["documents"]}
    assert packet["context"]["corporate_document_id"] == corporate["document_id"]
    assert packet["context"]["corporate_document_sha256"] == corporate[
        "document_sha256"
    ] == documents[corporate["document_id"]]["sha256"]
    assert packet["context"]["management_document_sha256"] == documents[
        management_document_id
    ]["sha256"]
    expected_passages = [
        passage for passage in snapshot["narrative_evidence"]
        if passage["document_id"] == management_document_id
    ]
    expected_passages.sort(key=lambda item: (item.get("page") or 0, item.get("location") or 0))
    assert packet["narrative_evidence"] == expected_passages
    assert {p["evidence_id"] for p in expected_passages} <= set(packet["evidence_ids"])
    assert {p["document_id"] for p in packet["narrative_evidence"]} == {
        management_document_id
    }

    block = result.handoff["module_conclusions"]["M3"]
    structure = block["m3_rebuild"]["structure"]["artifact"]
    management = block["m3_rebuild"]["management"]["artifact"]
    assert structure["provenance"]["input_document_id"] == corporate["document_id"]
    assert structure["provenance"]["source_sha256"] == corporate["document_sha256"]
    assert structure["provenance"]["structured_evidence_id"] == corporate["evidence_id"]
    assert management["source"]["path"] == (
        "canonical-document:" + management_document_id
    )
    assert set(management["source"]["section_ids"]) <= set(packet["evidence_ids"])
    management_sources = json.loads(
        (Path(block["source_manifest"]).parent / "management_sources.json")
        .read_text(encoding="utf-8")
    )
    assert set(management_sources["pages"]) <= set(packet["evidence_ids"])
    assert management_sources["document_id"] == management_document_id
    assert management_sources["evidence_packet_hash"] == packet["packet_hash"]
    assert {block["m3_rebuild"][name]["status"]
            for name in ("structure", "management")} == {"complete"}

    # The ordinary v3 roster and deterministic financial preparation still ran.
    agent_ids = {item["agent_id"] for item in result.audit["agents"]}
    assert {"m1_revenue_quality", "m2_industry", "m4_strategy_capital"} <= agent_ids
    assert "m3_corporate_management" not in agent_ids
    metrics = db_session.scalars(select(Metric).where(
        Metric.case_id == "M3_SHARED_INPUT"
    )).all()
    assert any(metric.metric_name == "operating_margin" for metric in metrics)


def test_late_or_private_sources_cannot_bypass_admission_and_outcomes_are_independent(
        db_session, tmp_path):
    late = datetime(2025, 1, 20, tzinfo=timezone.utc)
    result = _run(db_session, tmp_path, _package(management_available_at=late))
    block = result.handoff["module_conclusions"]["M3"]["m3_rebuild"]
    assert block["structure"]["status"] == "complete"
    assert block["management"]["status"] == "not_attempted"
    packet = db_session.scalars(select(EvidencePacketRow).where(
        EvidencePacketRow.analysis_run_id == result.analysis_run_id,
        EvidencePacketRow.agent_id == M3_PACKET_AGENT_ID,
    )).one().payload
    assert packet["context"]["management_document_id"] is None
    assert packet["narrative_evidence"] == []

    with pytest.raises(ValueError, match="private inputs are unsupported"):
        BusinessOverviewRunner(
            db_session, m3_filing="another-copy.pdf", m3_structure={},
        )
    with pytest.raises(ValueError, match="must name exactly one declared source"):
        package = _package()
        SourcePackage(
            as_of_date=package.as_of_date,
            evidence_cutoff_timestamp=package.evidence_cutoff_timestamp,
            borrower_entity_id=package.borrower_entity_id,
            entities=package.entities,
            sources=package.sources,
            sec_bundles=package.sec_bundles,
            sector_benchmark=package.sector_benchmark,
            module3={
                "corporate_source": "missing.json",
                "management_source": "management-10k.pdf",
            },
        )


def test_wrong_reporting_company_is_rejected_without_blocking_management(
        db_session, tmp_path):
    result = _run(
        db_session, tmp_path, _package(corporate_company="Unrelated Company")
    )
    block = result.handoff["module_conclusions"]["M3"]["m3_rebuild"]
    assert block["structure"]["status"] == "not_attempted"
    assert block["management"]["status"] == "complete"
    snapshot = db_session.scalars(select(Snapshot).where(
        Snapshot.case_id == "M3_SHARED_INPUT",
        Snapshot.snapshot_type == "canonical_evidence",
    )).one().payload
    assert snapshot["structured_evidence"] == []
    packet = db_session.scalars(select(EvidencePacketRow).where(
        EvidencePacketRow.analysis_run_id == result.analysis_run_id,
        EvidencePacketRow.agent_id == M3_PACKET_AGENT_ID,
    )).one().payload
    assert packet["structured_evidence"] == []
