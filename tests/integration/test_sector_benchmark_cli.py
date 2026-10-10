"""CLI and SEC-identity entry points for sector benchmarking (offline)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app import cli
from app.services.acquisition.sec_edgar import SecEdgarClient
from tests.healthcare_helpers import PACKAGE, WORKBOOK, WORKBOOK_SHA256
from tests.unit.test_sec_edgar import FakeTransport, SpyLimiter, NOW, response


from tests.healthcare_helpers import requires_workbook  # noqa: E402

pytestmark = requires_workbook

def test_benchmark_import_writes_dataset_and_report_without_touching_the_source(
    tmp_path, capsys
):
    before = WORKBOOK.read_bytes()
    cli.main(["benchmark-import", str(WORKBOOK), "--out", str(tmp_path / "ref")])
    summary = json.loads(capsys.readouterr().out)
    assert summary["source_sha256"] == WORKBOOK_SHA256
    assert len(summary["industries"]) == 6
    assert summary["issue_counts"]["error"] == 0
    dataset = json.loads((tmp_path / "ref" / "normalized_dataset.json").read_text("utf-8"))
    report = json.loads((tmp_path / "ref" / "validation_report.json").read_text("utf-8"))
    assert dataset["dataset_hash"] == summary["dataset_hash"] == report["dataset_hash"]
    assert WORKBOOK.read_bytes() == before
    # A pure import creates no database.
    assert not list(tmp_path.glob("*.db"))


@pytest.mark.parametrize("mode", ["legacy", "agentic"])
def test_run_case_reports_the_benchmark_summary(tmp_path, capsys, mode):
    cli.main([
        "--database", str(tmp_path / "pilot.db"), "--output", str(tmp_path / "out"),
        "--analysis-mode", mode, "run-case", "SYK_CLI", "--package", str(PACKAGE),
    ])
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "draft"
    assert summary["mandatory_escalations"] == 0
    assert summary["industry_benchmarking"] == {
        "status": "completed",
        "classification": "classified",
        "industry_label": "Healthcare Products",
        "comparison_states": {"comparable": 3, "comparable_with_caveats": 10,
                              "not_comparable": 4, "unavailable": 6},
    }
    html = next((tmp_path / "out").rglob("*.html")).read_text(encoding="utf-8")
    assert "Healthcare Industry Benchmarking" in html


def _client(payload):
    return SecEdgarClient(
        "Offline Tests tests@example.com", transport=FakeTransport([response(payload)]),
        limiter=SpyLimiter(), now=lambda: NOW)


def test_company_profile_reads_identity_and_sic_from_sec_submissions():
    client = _client({"name": "STRYKER CORP", "sic": "3841",
                      "sicDescription": "Surgical & Medical Instruments & Apparatus",
                      "filings": {"recent": {}}})
    profile = client.company_profile("310764")
    assert profile == {
        "cik": "310764", "name": "STRYKER CORP", "sic": 3841,
        "sic_description": "Surgical & Medical Instruments & Apparatus",
        "source_url": "https://data.sec.gov/submissions/CIK0000310764.json",
    }
    assert _client({"name": "X", "sic": ""}).company_profile("1")["sic"] is None
    with pytest.raises(ValueError, match="Invalid CIK"):
        _client({}).company_profile("not-a-cik")


def test_live_sec_run_marks_identity_as_sec_verified(tmp_path):
    client = _client({"name": "STRYKER CORP", "sic": "3841", "sicDescription": "x"})
    args = SimpleNamespace(sector="medical_devices", reference_workbook=WORKBOOK,
                           reported_sic=None, sic_source=None, sic_retrieved_at=None)
    request = cli._sector_request(
        args, client, {"cik": "310764", "ticker": "SYK", "name": "STRYKER CORP"}, None)
    assert request.identity.identity_verification == "sec_verified"
    assert request.identity.reported_sic == 3841 and request.identity.cik == "310764"
    assert "data.sec.gov/submissions/CIK0000310764.json" in request.identity.sic_source

    bundle = SimpleNamespace(cik="310764", ticker="SYK")
    offline = SimpleNamespace(sector="medical_devices", reference_workbook=WORKBOOK,
                              reported_sic=3841, sic_source="operator",
                              sic_retrieved_at=None)
    declared = cli._sector_request(offline, None, None, bundle)
    assert declared.identity.identity_verification == "declared_unverified"
    assert cli._sector_request(SimpleNamespace(sector=None), None, None, bundle) is None
    with pytest.raises(ValueError, match="--reference-workbook"):
        cli._sector_request(SimpleNamespace(sector="medical_devices",
                                            reference_workbook=None), None, None, bundle)
