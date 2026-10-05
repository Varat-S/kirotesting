"""Actual multipart files reach review, with no model execution or approval."""

import io
import zipfile
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps import get_session
from app.core.config import Settings
from app.main import create_app
from app.models.orm import (
    AuditEvent,
    Case,
    EvidencePreview,
    HumanReview,
    ModelRun,
    Snapshot,
)
from app.services.extraction.xlsx_csv import CsvParser
from app.services.pipeline.runner import CreditMemoPipeline

ROOT = Path(__file__).resolve().parents[2]
CSV = b"Line,2024\nOperating Revenue,1000\nOperating Income,200\nCash,\n"
FORM = dict(
    case_id="UPLOAD_TEST",
    cutoff="2025-01-15T00:00",
    timezone_offset="+00:00",
    as_of_date="2024-12-31",
    legal_name="Test Borrower",
    available_at="2025-01-10T00:00",
    scale="millions",
    fiscal_year_end_month="12",
)


@pytest.fixture
def client(db_session, tmp_path, monkeypatch):
    import app.api.inspection as inspection

    monkeypatch.setattr(
        inspection,
        "get_settings",
        lambda: Settings(
            debug=False,
            data_dir=str(tmp_path / "data"),
            output_dir=str(tmp_path / "output"),
        ),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Upload preview must stop before any LLM stage.")

    monkeypatch.setattr(CreditMemoPipeline, "_ai", forbidden)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db_session
    with TestClient(app) as client:
        yield client


def post(client, data=FORM, name="financials.csv", content=CSV):
    return client.post(
        "/inspect", data=data, files={"files": (name, content)}, follow_redirects=False
    )


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def payload(client, response):
    assert response.status_code == 303, response.text
    url = response.headers["location"]
    html = client.get(url)
    assert html.status_code == 200, html.text
    assert "LLM calls: <strong>0</strong>" in html.text
    value = client.get(url + "/payload.json").json()
    assert client.get(url + "/llm-input.json").json() == {
        "canonical_evidence": value["evidence"]
    }
    return value


def test_csv_upload_review_scaling_provenance_readonly_and_duplicate(
    client, db_session
):
    value = payload(client, post(client))
    assert value["stage"] == "before_llm"
    assert value["metrics"]["operating_margin"]["result"] == 0.2
    assert value["metrics"]["operating_margin"]["review_required"]
    evidence = value["evidence"]
    assert evidence["documents"][0]["sha256"] == sha256(CSV).hexdigest()
    revenue = next(f for f in evidence["facts"] if f["name"] == "revenue")
    assert revenue["normalized_value"] == 1000
    assert revenue["fiscal_year"] == 2024 and revenue["period_end"] == "2024-12-31"
    assert revenue["source_refs"][0]["cell"] == "B2"
    cash = next(f for f in evidence["facts"] if f["source_label"] == "Cash")
    assert cash["normalized_value"] is None and cash["status"] == "missing"
    assert count(db_session, ModelRun) == count(db_session, HumanReview) == 0
    snapshots = list(db_session.scalars(select(Snapshot)))
    assert len(snapshots) == 1 and snapshots[0].snapshot_type == "canonical_evidence"
    before = count(db_session, AuditEvent)
    assert "Parsed evidence" in client.get("/").text
    assert client.get("/cases/UPLOAD_TEST/workbench.html").status_code == 200
    assert post(client).status_code == 409
    assert count(db_session, AuditEvent) == before
    assert count(db_session, EvidencePreview) == 1


def test_cutoff_rejects_before_parsing(client, monkeypatch):
    monkeypatch.setattr(
        CsvParser, "parse", lambda *a, **k: pytest.fail("Future file parsed")
    )
    value = payload(client, post(client, {**FORM, "available_at": "2025-01-20T00:00"}))
    assert value["evidence"]["documents"] == value["evidence"]["facts"] == []
    assert len(value["rejected_sources"]) == 1
    assert value["metrics"]["operating_margin"]["result"] is None


def test_periods_are_not_guessed_without_declared_year_end(client):
    value = payload(client, post(client, {**FORM, "fiscal_year_end_month": ""}))
    assert all(f["fiscal_year"] is None for f in value["evidence"]["facts"])
    assert value["metrics"]["operating_margin"]["result"] is None


def test_saved_output_exports_hash_exact_bytes_and_do_not_mutate_case(
    client, db_session
):
    from copy import deepcopy

    response = post(client)
    value = payload(client, response)
    base = response.headers["location"]
    preview_row = db_session.get(EvidencePreview, ("UPLOAD_TEST", 1))
    before = deepcopy(preview_row.payload)
    audit_count = count(db_session, AuditEvent)
    manifest = client.get(base + "/artifacts.json").json()
    assert manifest["algorithm"] == "SHA-256"
    assert len(manifest["artifacts"]) == 11
    assert "Recorded during processing" in manifest["origin"]
    downloaded = {}
    for entry in manifest["artifacts"]:
        output = client.get(entry["url"])
        assert output.status_code == 200
        assert sha256(output.content).hexdigest() == entry["sha256"]
        assert len(output.content) == entry["size_bytes"]
        assert output.headers["x-content-sha256"] == entry["sha256"]
        assert output.headers["etag"] == '"' + entry["sha256"] + '"'
        downloaded[entry["name"]] = output.json()
    assert downloaded["llm_input"] == {"canonical_evidence": value["evidence"]}
    assert downloaded["canonical_evidence"] == value["evidence"]
    assert downloaded["full_preview"] == value
    assert downloaded["extraction_mapping"]["facts"] == value["evidence"]["facts"]
    assert client.get(base + "/artifacts/not-a-stage.json").status_code == 404
    assert count(db_session, AuditEvent) == audit_count
    assert count(db_session, ModelRun) == count(db_session, HumanReview) == 0
    db_session.refresh(preview_row)
    assert preview_row.payload == before


def test_metrics_display_only_available_results_and_explain_missing_inputs(client):
    response = post(client)
    assert response.status_code == 303
    html = client.get(response.headers["location"]).text
    table = html.split('<table id="available-metrics">', 1)[1].split("</table>", 1)[0]
    assert "20.00%" in table and "operating margin" in table
    assert "net debt" not in table and "unavailable" not in table
    assert '<details id="unavailable-metrics">' in html
    assert "Not extracted as a canonical input" in html
    assert "total_debt" in html and "unrestricted_cash" in html


def test_real_sec_zip_upload_retains_diagnostics_and_excludes_late_proxy(
    client, db_session
):
    example = client.get("/inspect/example/nvda.zip")
    assert example.status_code == 200
    value = payload(
        client,
        post(
            client,
            dict(
                case_id="NVDA_UPLOAD",
                cutoff="2026-03-01T00:00",
                timezone_offset="+00:00",
                source_type="sec_bundle",
                legal_name="NVIDIA Corporation",
            ),
            "nvda.zip",
            example.content,
        ),
    )
    evidence = value["evidence"]
    assert len(evidence["documents"]) == 13
    assert len(value["rejected_sources"]) == 1
    assert len(evidence["facts"]) == 1305
    assert len(evidence["sec_filings"][0]["contexts"]) == 281
    assert len(evidence["narrative_evidence"]) == 156
    assert any(f["dimensions"] for f in evidence["facts"])
    assert value["metrics"]["operating_margin"]["result"] == pytest.approx(
        130387 / 215938
    )
    assert count(db_session, ModelRun) == 0


@pytest.mark.parametrize("filename", ["sample_table.pdf", "sample_financials.xlsx"])
def test_pdf_and_excel_real_files(client, filename):
    data = (ROOT / "tests/golden/fixtures" / filename).read_bytes()
    value = payload(
        client, post(client, {**FORM, "as_of_date": "2023-12-31"}, filename, data)
    )
    evidence = value["evidence"]
    revenue = next(
        f
        for f in evidence["facts"]
        if f["source_label"] == "Revenue" and f["fiscal_year"] == 2023
    )
    assert revenue["normalized_value"] == 1500
    ref = revenue["source_refs"][0]
    assert ref["document_id"] == evidence["documents"][0]["document_id"] and ref["cell"]
    if filename.endswith(".pdf"):
        assert ref["page"] == 1
        assert (
            evidence["narrative_evidence"]
            and evidence["narrative_evidence"][0]["page"] == 1
        )


def test_pdf_without_tables_keeps_readable_text_but_no_numeric_results(client):
    data = (ROOT / "tests/golden/fixtures/sample_narrative.pdf").read_bytes()
    value = payload(client, post(client, name="narrative.pdf", content=data))
    assert not value["evidence"]["facts"]
    assert value["evidence"]["narrative_evidence"]
    assert value["metrics"]["operating_margin"]["result"] is None


def test_uploaded_labels_and_names_are_html_escaped(client):
    response = post(
        client,
        {**FORM, "legal_name": '<script>alert("issuer")</script>'},
        content=b"Line,2024\n<script>alert(1)</script>,10\n",
    )
    assert response.status_code == 303
    html = client.get(response.headers["location"]).text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def zip_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "entries",
    [
        [("../evil.xml", b"evil")],
        [("/evil.xml", b"evil")],
        [("dir\\evil.xml", b"evil")],
        [("bundle.json", b"{bad")],
        [("bundle.json", b"[]")],
        [("bundle.json", b'{"files":[1]}')],
        [("bundle.json", b'{"files":[{"path":"missing.htm"}]}')],
        [
            ("bundle.json", b'{"files":[{"path":"primary.htm","admitted":true}]}'),
            ("primary.htm", b"x"),
        ],
        [
            ("bundle.json", b'{"files":[{"path":"primary.htm","sha256":"bad"}]}'),
            ("primary.htm", b"x"),
        ],
    ],
)
def test_invalid_bundles_are_reviewable_errors_without_case_writes(
    client, db_session, entries
):
    response = post(
        client, {**FORM, "source_type": "sec_bundle"}, "invalid.zip", zip_bytes(entries)
    )
    assert response.status_code == 422
    assert 'role="alert"' in response.text
    assert count(db_session, Case) == count(db_session, EvidencePreview) == 0


def test_standalone_sec_primary_with_metadata(client):
    from tests.sec_helpers import bundle

    primary = bundle().files[0]
    form = {
        **FORM,
        "source_type": "sec_inline",
        "cik": primary.cik,
        "accession": primary.accession,
        "filing_date": primary.filing_date.isoformat(),
    }
    value = payload(client, post(client, form, primary.filename, primary.content))
    assert value["evidence"]["sec_filings"]
    assert value["metrics"]["operating_margin"]["result"] == 0.2


def test_xbrl_entity_declaration_is_rejected_before_stdlib_parser(client):
    malicious = b'<!DOCTYPE xbrl [<!ENTITY a "bad">]><xbrl>&a;</xbrl>'
    value = payload(client, post(client, name="filing.xbrl", content=malicious))
    assert not value["evidence"]["facts"]
    assert any("entity declarations" in e["reason"] for e in value["escalations"])


def test_explicit_excel_sheet_and_scale(client):
    import openpyxl

    workbook = openpyxl.Workbook()
    workbook.active.append(["Do not use", "2024"])
    sheet = workbook.create_sheet("Financials")
    for row in [
        ["Line", "FY2024"],
        ["Operating Revenue", 1000000],
        ["Operating Income", 200000],
    ]:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    value = payload(
        client,
        post(
            client,
            {**FORM, "sheet_name": "Financials", "scale": "thousands"},
            "financials.xlsx",
            buffer.getvalue(),
        ),
    )
    revenue = next(f for f in value["evidence"]["facts"] if f["name"] == "revenue")
    assert revenue["normalized_value"] == 1000
    assert revenue["source_refs"][0]["table"] == "Financials"
    assert value["metrics"]["operating_margin"]["result"] == 0.2
