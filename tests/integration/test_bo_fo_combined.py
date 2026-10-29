"""Shared filing intake and the concurrent BO + FO run, over the committed NVIDIA filing.

The filing is copied into a temporary ``edgar_filings/`` root and every run is offline: the BO on
its offline backend, the FO on scripted Pre-check Agent responses. No network, no model.
"""

from __future__ import annotations

import io
import json
import shutil
import sqlite3
import threading
import time
import zipfile
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.cli import main
from app.main import create_app
from app.models.base import init_db
from app.services import bo_fo, rms
from app.services.acquisition import shared_filing
from app.services.acquisition.models import SecFiling
from app.services.acquisition.sec_edgar import SecEdgarClient
from app.services.extraction.sec.bundle import SecFilingBundle
from app.services.fo import loader, precheck
from app.services.fo import models as fo_models
from app.services.rms import RmsRequest
from app.services.rms import runner as rms_runner
from tests.integration.test_fo_runner_api import FOLDER, NVDA, agent, responses

ACCESSION = "0001045810-26-000021"
FILED = date(2026, 2, 25)
REPORT = date(2026, 1, 25)
PRIMARY = "nvda-20260125.htm"


@pytest.fixture()
def root(tmp_path, monkeypatch):
    root = tmp_path / "edgar_filings"
    root.mkdir()
    monkeypatch.setattr(loader, "FILINGS_ROOT", root)
    return root


@pytest.fixture()
def loose(tmp_path):
    """NVIDIA's filing files without the bundle manifest: what a user has after saving them from EDGAR."""
    folder = tmp_path / "nvda_files"
    folder.mkdir()
    for p in NVDA.iterdir():
        if p.is_file() and p.suffix in shared_filing.SEC_SUFFIXES:
            shutil.copy(p, folder / p.name)
    return folder


def _database(tmp_path) -> Path:
    db = tmp_path / "data" / "main.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    init_db(engine)
    engine.dispose()
    return db


def _count(db, table):
    with sqlite3.connect(db) as con:
        return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


class FakeSec:
    """The parts of SecEdgarClient the intake uses, answering from the NVIDIA fixture."""

    annual_reports = staticmethod(SecEdgarClient.annual_reports)

    def __init__(self, accepted=None):
        self.filing = SecFiling("1045810", "10-K", ACCESSION, FILED, REPORT, PRIMARY, accepted, True)
        self.older = SecFiling("1045810", "10-K", "0001045810-25-000023", date(2025, 2, 26), date(2025, 1, 26),
                               "nvda-20250126.htm", None, True)
        self.legacy = SecFiling("1045810", "10-K", "0001045810-18-000010", date(2018, 2, 28), date(2018, 1, 28),
                                "nvda-2018x10k.htm", None, False)
        self.amended = SecFiling("1045810", "10-K/A", "0001045810-26-000030", date(2026, 3, 10), REPORT,
                                 "nvda-20260125a.htm", None, True)

    def lookup_ticker(self, ticker):
        assert ticker == "NVDA"
        return {"cik": "1045810", "ticker": "NVDA", "name": "NVIDIA CORP"}

    def list_filings(self, cik):
        assert str(cik) == "1045810"
        return [self.amended, self.filing, self.older, self.legacy]

    def proxy_for(self, filings, filing):
        return None

    def company_profile(self, cik):
        return {"cik": "1045810", "name": "NVIDIA CORP", "sic": 3674, "sic_description": "Semiconductors"}

    def fetch_bundle(self, company, filing, proxy=None):
        bundle = SecFilingBundle.load(NVDA / "bundle.json")
        return bundle.model_copy(update={"files": [f for f in bundle.files if f.role != "proxy"]})


# --- intake --------------------------------------------------------------------------------------------


def test_import_loose_files_with_declared_dates(root, loose):
    filing = shared_filing.import_filing([loose], accession=ACCESSION, filing_date=FILED)
    assert filing.name == "NVDA 10-K FY2026"
    assert (filing.cik, filing.form, filing.report_date, filing.availability) == ("1045810", "10-K", REPORT, "declared")
    bundle = shared_filing.load_bundle(filing.name)  # hashes re-checked from disk
    roles = {f.filename: f.role for f in bundle.files}
    assert roles[PRIMARY] == "primary_inline_xbrl" and roles["nvda-20260125.xsd"] == "schema"
    assert roles["nvda-20260125_pre.xml"] == "presentation_linkbase"
    # Without an EDGAR acceptance time, availability is the conservative end of the filing day (US Eastern).
    assert {f.available_at.isoformat() for f in bundle.files} == {"2026-02-26T05:00:00+00:00"}
    company = json.loads((filing.folder / "company.json").read_text(encoding="utf-8"))
    assert company["cik"] == 1045810 and company["ticker"] == "NVDA"
    # The FO reads the same folder.
    data, _ = loader.load(filing.name)
    assert data["meta"]["source"] == PRIMARY
    assert [r["name"] for r in shared_filing.list_shared() if r["runnable"]] == [filing.name]


def test_import_without_dates_needs_them_or_edgar(root, loose):
    with pytest.raises(shared_filing.SharedFilingError, match="accession and filing date are needed"):
        shared_filing.import_filing([loose])
    filing = shared_filing.import_filing([loose], client=FakeSec())  # looked up on (fake) EDGAR
    bundle = shared_filing.load_bundle(filing.name)
    assert bundle.accession == ACCESSION and bundle.filing_date == FILED
    assert {f.source_url for f in bundle.files if f.filename == PRIMARY} == {
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000021/nvda-20260125.htm"}
    company = json.loads((filing.folder / "company.json").read_text(encoding="utf-8"))
    assert company["sic"] == "3674"


def test_import_rejects_a_date_edgar_contradicts(root, loose):
    with pytest.raises(shared_filing.SharedFilingError, match="not 2026-03-01"):
        shared_filing.import_filing([loose], filing_date=date(2026, 3, 1), client=FakeSec())


def test_import_an_uploaded_zip(root, loose):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for p in loose.iterdir():
            archive.write(p, f"NVDA 10-K/{p.name}")  # a folder inside the archive is flattened
    filing = shared_filing.import_filing({"nvda.zip": buffer.getvalue()}, accession=ACCESSION,
                                         filing_date=FILED, name="NVDA upload")
    assert filing.name == "NVDA upload" and filing.files == len(list(loose.iterdir()))


def test_import_prepares_a_folder_already_in_edgar_filings_in_place(root, loose):
    target = root / "NVDA 10-K FY2026"
    shutil.copytree(loose, target)
    before = sorted(p.name for p in target.iterdir())
    filing = shared_filing.import_filing([target], accession=ACCESSION, filing_date=FILED)
    assert filing.folder == target
    assert sorted(p.name for p in target.iterdir()) == sorted([*before, "bundle.json", "company.json"])
    with pytest.raises(shared_filing.SharedFilingError, match="already has a bundle.json"):
        shared_filing.import_filing([target], accession=ACCESSION, filing_date=FILED)


PROXY = "nvda-20260512.htm"
PROXY_FILING = SecFiling("1045810", "DEF 14A", "0001045810-26-000036", date(2026, 5, 12), None, PROXY)


def _with_proxy(loose, root, meta=True):
    target = root / "NVDA 10-K FY2026"
    shutil.copytree(loose, target)
    (target / "proxy").mkdir()
    shutil.copy(NVDA / "proxy" / PROXY, target / "proxy" / PROXY)
    if meta:
        (target / "proxy" / "filing.json").write_text(json.dumps({
            "form": "DEF 14A", "accession": PROXY_FILING.accession, "filing_date": "2026-05-12",
            "primary_document": PROXY}), encoding="utf-8")
    return target


def test_import_registers_the_proxy_folder_as_its_own_accession(root, loose):
    target = _with_proxy(loose, root)
    filing = shared_filing.import_filing([target], accession=ACCESSION, filing_date=FILED)
    proxy = next(f for f in shared_filing.load_bundle(filing.name).files if f.role == "proxy")
    assert (proxy.filename, proxy.accession, proxy.form) == (PROXY, PROXY_FILING.accession, "DEF 14A")
    assert proxy.availability_granularity == "declared"
    assert proxy.available_at.isoformat() == "2026-05-13T04:00:00+00:00"  # end of the US Eastern filing day
    manifest = json.loads((target / "bundle.json").read_text(encoding="utf-8"))
    assert {f["path"] for f in manifest["files"] if f["role"] == "proxy"} == {f"proxy/{PROXY}"}


def test_import_identifies_the_proxy_on_edgar_or_refuses_to_guess(root, loose):
    class WithProxy(FakeSec):
        def list_filings(self, cik):
            return [*super().list_filings(cik), PROXY_FILING]

        def proxy_for(self, filings, filing):
            return PROXY_FILING

    target = _with_proxy(loose, root, meta=False)
    with pytest.raises(shared_filing.SharedFilingError, match="filing.json"):
        shared_filing.import_filing([target], accession=ACCESSION, filing_date=FILED)
    filing = shared_filing.import_filing([target], client=WithProxy())
    proxy = next(f for f in shared_filing.load_bundle(filing.name).files if f.role == "proxy")
    assert proxy.accession == PROXY_FILING.accession
    assert proxy.source_url == f"https://www.sec.gov/Archives/edgar/data/1045810/000104581026000036/{PROXY}"


def test_import_refuses_what_is_not_a_10k(root, tmp_path):
    with pytest.raises(shared_filing.SharedFilingError, match="No .htm/.xsd/.xml"):
        shared_filing.import_filing({"notes.txt": b"hello"})
    with pytest.raises(shared_filing.SharedFilingError, match="No Inline XBRL"):
        shared_filing.import_filing({"page.htm": b"<html></html>", "s.xsd": b"<schema/>"},
                                    accession=ACCESSION, filing_date=FILED)
    with pytest.raises(shared_filing.SharedFilingError, match="acceptable SEC file name"):
        shared_filing.import_filing({"bad name.htm": b"x"})
    assert not [p for p in root.iterdir()]  # nothing half-written, no scratch folders left


def test_fetch_writes_a_flat_folder_both_sections_read(root):
    filing = shared_filing.fetch_filing(FakeSec(), "NVDA", fy=2026)
    assert filing.name == "NVDA 10-K FY2026"
    assert (filing.folder / PRIMARY).is_file() and not (filing.folder / ACCESSION).exists()
    assert shared_filing.load_bundle(filing.name).primary_document == PRIMARY
    with pytest.raises(shared_filing.SharedFilingError, match="already exists"):
        shared_filing.fetch_filing(FakeSec(), "NVDA", fy=2026)


def test_a_ticker_lists_its_10ks_to_choose_from(root):
    company, reports = shared_filing.annual_reports(FakeSec(), "NVDA")
    assert company["ticker"] == "NVDA"
    assert [(r["fiscal_year"], r["form"]) for r in reports] == [(2026, "10-K"), (2025, "10-K"), (2018, "10-K")]
    assert reports[0]["folder"] == "NVDA 10-K FY2026" and reports[0]["problem"] is None
    assert reports[2]["problem"] == "no Inline XBRL; not supported"
    _, with_amendments = shared_filing.annual_reports(FakeSec(), "NVDA", include_amendments=True)
    assert "10-K/A" in [r["form"] for r in with_amendments]
    shared_filing.fetch_filing(FakeSec(), "NVDA", accession=ACCESSION)
    assert shared_filing.annual_reports(FakeSec(), "NVDA")[1][0]["downloaded"] is True


def test_a_folder_without_a_manifest_is_fo_only(root, loose):
    shutil.copytree(loose, root / "NVDA plain")
    assert shared_filing.list_shared()[0]["runnable"] is False
    with pytest.raises(shared_filing.SharedFilingError, match="has no bundle.json"):
        bo_fo.plan("NVDA plain")


# --- the combined run ----------------------------------------------------------------------------------


@pytest.fixture()
def shared(root):
    shutil.copytree(NVDA, root / FOLDER)  # the fixture already has the shared layout
    return FOLDER


def test_both_sections_run_concurrently_into_one_database(shared, tmp_path):
    db = _database(tmp_path)
    seen = []
    out = bo_fo.run_bo_fo(database=db, folder=shared, bo_backend=None, fo_backend=agent(), fo_mode="scripted",
                          output_root=tmp_path / "out", on_progress=lambda s, m: seen.append((s, m)))
    assert out["case_id"] == "NVDA_FY2026"
    assert out["bo"]["status"] == "completed" and out["fo"]["status"] == "completed", out
    assert out["bo"]["summary"]["case_id"] == out["fo"]["summary"]["case_id"] == "NVDA_FY2026"
    assert out["cutoff"] == "2026-05-13T04:00:00+00:00"  # the latest file (the proxy): every file admitted
    # Module 3 is part of the BO run, not a separate analysis run.
    assert _count(db, "financial_overview_runs") == 1 and _count(db, "agentic_analysis_runs") == 1
    assert _count(db, "model_runs") >= 5  # the FO's agent calls came across from its staging database
    assert not list((tmp_path / "data" / "staging").iterdir())
    assert ("fo", "completed") in seen and ("bo", "completed") in seen
    # No requested amount: RMS still sizes the debt capacity, with no verdict.
    assert out["rms"]["status"] == "completed", out["rms"]
    summary = out["rms"]["summary"]
    assert summary["verdict"] == "capacity_only" and summary["requested_amount_m"] is None
    assert summary["approved_amount_m"] is None and summary["utilisation"] is None
    assert ("rms", "sizing the debt capacity (no requested amount)") in seen


def test_rms_runs_after_both_sections_complete(shared, tmp_path):
    db = _database(tmp_path)
    seen = []
    out = bo_fo.run_bo_fo(database=db, folder=shared, bo_backend=None, fo_backend=agent(), fo_mode="scripted",
                          output_root=tmp_path / "out", rms_request=RmsRequest(500, tenor_years=3),
                          on_progress=lambda s, m: seen.append((s, m)))
    assert out["rms"]["status"] == "completed", out["rms"]
    # RMS starts only once both sections have reported.
    first_rms = next(i for i, (s, _) in enumerate(seen) if s == "rms")
    assert {("bo", "completed"), ("fo", "completed")} <= set(seen[:first_rms])
    summary = out["rms"]["summary"]
    record = json.loads(Path(summary["output"]).read_text(encoding="utf-8"))
    assert Path(summary["output"]).parent.parent == tmp_path / "out" / "rms" / "NVDA_FY2026"
    assert record["sources"]["bo_analysis_run_id"] == out["bo"]["summary"]["analysis_run_id"]
    assert record["sources"]["fo_run_id"] == out["fo"]["summary"]["run_id"]
    assert record["request"]["requested_amount_m"] == 500 and record["requires_human_review"] is True
    # The offline BO asserts nothing, so Module 4 has no validated output and RMS refers the case.
    assert summary["verdict"] == "refer" and any(g.startswith("RMS-G05") for g in summary["gates"])


def test_rms_is_skipped_when_a_section_fails(shared, tmp_path, monkeypatch):
    from app.services.bo.runner import BusinessOverviewRunner

    def broken(self, *a, **k):
        raise RuntimeError("BO broke")

    monkeypatch.setattr(BusinessOverviewRunner, "run_case", broken)
    out = bo_fo.run_bo_fo(database=_database(tmp_path), folder=shared, bo_backend=None, fo_backend=agent(),
                          fo_mode="scripted", output_root=tmp_path / "out", rms_request=RmsRequest(500))
    assert out["fo"]["status"] == "completed"
    assert out["rms"] == {"status": "skipped", "reason": "Needs both sections completed; the BO did not complete."}
    assert not (tmp_path / "out" / "rms").exists()


def test_one_section_failing_leaves_the_other(shared, tmp_path, monkeypatch):
    from app.services.bo.runner import BusinessOverviewRunner

    def broken(self, *a, **k):
        raise RuntimeError("BO broke")

    monkeypatch.setattr(BusinessOverviewRunner, "run_case", broken)
    db = _database(tmp_path)
    out = bo_fo.run_bo_fo(database=db, folder=shared, bo_backend=None, fo_backend=agent(), fo_mode="scripted",
                          output_root=tmp_path / "out")
    assert out["bo"]["status"] == "failed" and "BO broke" in out["bo"]["error"]
    assert out["fo"]["status"] == "completed" and _count(db, "financial_overview_runs") == 1


def test_the_bo_runs_by_itself(shared, tmp_path):
    db = _database(tmp_path)
    out = bo_fo.run_bo_fo(database=db, folder=shared, bo_backend=None, fo_backend=None, fo_mode="live",
                          output_root=tmp_path / "out", rms_request=RmsRequest(500), bo_only=True)
    assert out["bo"]["status"] == "completed", out
    assert out["fo"] == {"status": "skipped", "reason": "Business Overview only run."}
    assert out["rms"] == {"status": "skipped", "reason": "Needs both sections; this run was Business Overview only."}
    assert _count(db, "financial_overview_runs") == 0 and _count(db, "agentic_analysis_runs") == 1


def test_bo_validators_off_marks_an_ungoverned_draft_and_rms_notes_it(shared, tmp_path):
    db = _database(tmp_path)
    out = bo_fo.run_bo_fo(database=db, folder=shared, bo_backend=None, fo_backend=agent(), fo_mode="scripted",
                          output_root=tmp_path / "out", rms_request=RmsRequest(500, tenor_years=3),
                          bo_validators=False)
    assert out["bo"]["status"] == "completed", out
    summary = out["bo"]["summary"]
    assert summary["validators_enforced"] is False
    assert summary["conclusion_status"] == "human_review_required"
    handoff = json.loads(Path(summary["handoff_path"]).read_text(encoding="utf-8"))
    assert handoff["validators_enforced"] is False
    folder = Path(summary["handoff_path"]).parent
    memo = json.loads((folder / "business_overview.json").read_text(encoding="utf-8"))
    assert memo["run"]["validators_enforced"] is False
    assert "Validators off: an ungoverned draft" in (folder / "business_overview.html").read_text(encoding="utf-8")
    assert out["rms"]["status"] == "completed", out["rms"]
    record = json.loads(Path(out["rms"]["summary"]["output"]).read_text(encoding="utf-8"))
    assert record["sources"]["bo_validators_enforced"] is False
    assert any("validators off" in note for note in record["limitations"])


def test_bo_validators_are_on_by_default(shared, tmp_path):
    out = bo_fo.run_bo_fo(database=_database(tmp_path), folder=shared, bo_backend=None, fo_backend=None,
                          fo_mode="live", output_root=tmp_path / "out", bo_only=True)
    assert out["bo"]["summary"]["validators_enforced"] is True
    handoff = json.loads(Path(out["bo"]["summary"]["handoff_path"]).read_text(encoding="utf-8"))
    assert handoff["validators_enforced"] is True


def test_a_stopped_run_records_nothing(shared, tmp_path):
    db = _database(tmp_path)
    stop = threading.Event()
    seen = []

    def progress(section, message):
        seen.append((section, message))
        if (section, message) == ("bo", "orchestrator"):
            stop.set()  # the analyst presses Stop while the BO synthesis is under way

    out = bo_fo.run_bo_fo(database=db, folder=shared, bo_backend=None, fo_backend=agent(), fo_mode="scripted",
                          output_root=tmp_path / "out", rms_request=RmsRequest(500), on_progress=progress, stop=stop)
    assert out["bo"]["status"] == "stopped"
    assert out["bo"]["reason"].startswith("Stopped by the analyst before ")
    assert out["bo"]["reason"].endswith("; nothing was recorded.")
    assert out["fo"]["status"] in ("completed", "stopped")  # the FO may have finished first
    assert out["rms"] == {"status": "skipped", "reason": "The run was stopped."}
    assert ("bo", "routing the evidence") in seen and ("bo", "stopped") in seen
    assert _count(db, "agentic_analysis_runs") == 0  # the BO's transaction was rolled back
    assert not (tmp_path / "out" / "rms").exists()


def test_the_fo_copy_waits_for_the_other_writer(shared, tmp_path):
    """A held write lock (the BO mid-run) delays the FO's copy; it lands once the lock is released."""
    db = _database(tmp_path)
    staging = tmp_path / "staging.db"
    engine = create_engine(f"sqlite:///{staging.as_posix()}")
    init_db(engine)
    with engine.begin() as con:
        con.execute(text("INSERT INTO financial_overview_runs (run_id, case_id, filing_folder, folder_signature, "
                         "engine_version, reference_hash, status, human_review_status, payload, content_hash, "
                         "started_at) VALUES ('fo-x', 'C', 'f', 's', 'e', 'r', 'completed', 'pending', '{}', 'h', "
                         "'2026-01-01 00:00:00')"))
    engine.dispose()
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        bo_fo.merge(staging, db, lock_wait=0.2)
    result = {}
    worker = threading.Thread(target=lambda: result.update(bo_fo.merge(staging, db, lock_wait=30)))
    worker.start()
    time.sleep(0.5)
    assert worker.is_alive()  # waiting, not failing
    holder.execute("COMMIT")
    holder.close()
    worker.join(30)
    assert result == {"financial_overview_runs": 1} and _count(db, "financial_overview_runs") == 1


def test_cli_run_bo_fo(shared, tmp_path, capsys, monkeypatch):
    import app.cli as cli

    common = ["--database", str(tmp_path / "data" / "main.db"), "--output", str(tmp_path / "out")]
    with pytest.raises(ValueError, match="The BO needs"):
        main([*common, "run-bo-fo", "--filing", shared])
    script = tmp_path / "agent.json"
    script.write_text(json.dumps(responses()), encoding="utf-8")
    monkeypatch.setattr(cli, "_live_backend", lambda: None)  # the BO's offline backend stands in for --live
    monkeypatch.setattr(bo_fo, "m3_live_backend", lambda model=None: None)
    main([*common, "run-bo-fo", "NVDA_BOTH", "--filing", shared, "--live", "--fo-responses", str(script),
          "--amount", "750", "--tenor", "4"])
    out = json.loads(capsys.readouterr().out)
    assert out["case_id"] == "NVDA_BOTH"
    assert (out["bo"]["status"], out["fo"]["status"], out["rms"]["status"]) == ("completed",) * 3
    assert out["rms"]["summary"]["requested_amount_m"] == 750


def test_cli_import_filing(root, loose, tmp_path, capsys):
    main(["--database", str(tmp_path / "data" / "main.db"), "import-filing", str(loose), "--offline",
          "--accession", ACCESSION, "--filing-date", FILED.isoformat()])
    out = json.loads(capsys.readouterr().out)
    assert out["folder"] == "NVDA 10-K FY2026" and out["availability"] == "declared"
    assert out["next"] == 'python -m app.cli run-bo-fo --filing "NVDA 10-K FY2026" --live'


# --- the browser ---------------------------------------------------------------------------------------


@pytest.fixture()
def client(root, tmp_path, monkeypatch):
    from app.core.config import get_settings

    db = _database(tmp_path)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db.as_posix()}")
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "web_out"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    get_settings.cache_clear()
    from app.api import deps

    deps._session_factory.cache_clear()
    monkeypatch.setattr(precheck, "live_backend", lambda model=None: agent())
    monkeypatch.setattr(precheck, "agent_available", lambda: (True, None))
    monkeypatch.setattr(fo_models, "available_models", lambda force=False: (["test-model"], None))
    monkeypatch.setattr(bo_fo, "bo_live_backend", lambda model=None: None)
    monkeypatch.setattr(bo_fo, "m3_live_backend", lambda model=None: None)
    with TestClient(create_app()) as test_client:
        yield test_client
    deps._session_factory.cache_clear()


def test_browser_upload_then_run_both(client, loose):
    page = client.get("/run")
    assert page.status_code == 200 and "No folder in" in page.text
    files = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in sorted(loose.iterdir())]
    uploaded = client.post("/run/import", files=files,
                           data={"accession": ACCESSION, "filing_date": FILED.isoformat()},
                           follow_redirects=False)
    assert uploaded.status_code == 303 and uploaded.headers["location"] == "/run?folder=NVDA%2010-K%20FY2026"
    assert 'name="requested_amount_m"' in client.get("/run").text
    started = client.post("/run/start", data={"folder": "NVDA 10-K FY2026", "requested_amount_m": "500"},
                          follow_redirects=False)
    assert started.status_code == 303
    job_id = started.headers["location"].split("job=")[1]
    for _ in range(600):
        job = client.get(f"/run/jobs/{job_id}").json()
        if job["status"] != "running":
            break
        time.sleep(0.5)
    assert job["status"] == "finished", job
    assert job["sections"]["bo"]["status"] == job["sections"]["fo"]["status"] == "completed"
    assert job["sections"]["rms"]["status"] == "completed" and job["result"]["rms"]["summary"]["verdict"] == "refer"
    page = client.get(f"/run?job={job_id}").text
    assert "Risk Mitigation &amp; Structuring" in page and "US$500.0M requested" in page
    # NVIDIA isn't benchmarked as a healthcare company, so there is no FO composite and no capacity to show.
    assert job["result"]["rms"]["summary"]["capacity"] is None and 'class="rms-capacity"' not in page
    assert f"/cases/NVDA_FY2026/fo.html?run_id={job['result']['fo']['summary']['run_id']}" in page
    assert client.get(f"/cases/NVDA_FY2026/fo.html?run_id={job['result']['fo']['summary']['run_id']}").status_code == 200


def test_browser_runs_the_bo_by_itself(client, shared, monkeypatch):
    monkeypatch.setattr(precheck, "live_backend", lambda model=None: pytest.fail("the FO backend was built"))
    assert 'value="bo"' in client.get("/run").text
    started = client.post("/run/start", data={"folder": FOLDER, "sections": "bo", "requested_amount_m": "500"},
                          follow_redirects=False)
    assert started.status_code == 303
    job_id = started.headers["location"].split("job=")[1]
    for _ in range(600):
        job = client.get(f"/run/jobs/{job_id}").json()
        if job["status"] != "running":
            break
        time.sleep(0.5)
    assert job["status"] == "finished" and job["bo_only"] is True, job
    assert (job["sections"]["bo"]["status"], job["sections"]["fo"]["status"], job["sections"]["rms"]["status"]) == (
        "completed", "skipped", "skipped")
    page = client.get(f"/run?job={job_id}").text
    assert f"bo.html?analysis_run_id={job['result']['bo']['summary']['analysis_run_id']}" in page
    assert 'id="section-fo"' not in page and 'id="section-rms"' not in page


def test_browser_bo_validators_checkbox(client, shared, monkeypatch):
    form = client.get("/run").text
    assert 'name="bo_validators" value="off"' in form
    assert 'id="bo_validators" name="bo_validators" value="on" style="width:auto" checked' in form
    seen = {}

    def records(**kwargs):
        seen.update(kwargs)
        return "job1"

    monkeypatch.setattr(bo_fo.jobs, "start", records)
    # A ticked box sends the hidden "off" then "on"; an unticked one sends only "off".
    client.post("/run/start", data={"folder": FOLDER, "sections": "bo", "bo_validators": ["off", "on"]},
                follow_redirects=False)
    assert seen["bo_validators"] is True
    client.post("/run/start", data={"folder": FOLDER, "sections": "bo", "bo_validators": "off"},
                follow_redirects=False)
    assert seen["bo_validators"] is False
    client.post("/run/start", data={"folder": FOLDER, "sections": "bo"}, follow_redirects=False)
    assert seen["bo_validators"] is True  # a caller that doesn't send the field keeps them on


def test_browser_stops_a_run(client, shared, monkeypatch):
    def waits_for_stop(*, stop, on_progress, folder, case_id, **kwargs):
        on_progress("bo", "primary specialists")
        assert stop.wait(30)
        on_progress("bo", "stopped")
        return {"case_id": case_id, "folder": folder, "cutoff": "",
                "bo": {"status": "stopped", "reason": "Stopped by the analyst before resilience; nothing was recorded."},
                "fo": {"status": "skipped", "reason": "Business Overview only run."},
                "rms": {"status": "skipped", "reason": "The run was stopped."}}

    monkeypatch.setattr(bo_fo, "run_bo_fo", waits_for_stop)
    started = client.post("/run/start", data={"folder": FOLDER, "sections": "bo"}, follow_redirects=False)
    job_id = started.headers["location"].split("job=")[1]
    assert 'id="job-stop"' in client.get(f"/run?job={job_id}").text
    stopped = client.post(f"/run/jobs/{job_id}/stop", follow_redirects=False)
    assert stopped.status_code == 303 and stopped.headers["location"] == f"/run?job={job_id}"
    for _ in range(100):
        job = client.get(f"/run/jobs/{job_id}").json()
        if job["status"] != "running":
            break
        time.sleep(0.1)
    assert job["status"] == "stopped" and job["stop_requested"] is True
    assert job["sections"]["bo"]["status"] == "stopped"
    page = client.get(f"/run?job={job_id}").text
    assert "Stopped by the analyst before resilience" in page
    assert client.post("/run/jobs/nope/stop").status_code == 404


def test_the_run_page_shows_how_the_debt_capacity_was_worked_out():
    from app.api import bo_fo as api
    from tests.unit.test_rms import FO as HCA_FO
    from tests.unit.test_rms import M4 as HCA_M4

    result = rms.assess(RmsRequest(2000), HCA_FO, m4_analysis=HCA_M4)
    summary = rms_runner.rms_summary({"run_id": "rms-x", "case_id": "HCA_FY2025", **result.to_dict()},
                                     Path("rms.json"))
    job = {"job_id": "j1", "status": "finished", "folder": "HCA 10-K FY2025", "case_id": "HCA_FY2025",
           "started_at": "", "error": None,
           "sections": {s: {"status": "completed", "step": "completed", "steps": []} for s in ("bo", "fo", "rms")},
           "result": {"bo": {"status": "failed", "error": "-"}, "fo": {"status": "failed", "error": "-"},
                      "rms": {"status": "completed", "summary": summary}}}
    page = api._env.get_template("bo_fo_run.html.j2").render(
        filings=[], job=job, jobs=[], values={}, fo_ok=False, bo_ok=False, models=[])
    assert "Debt capacity: US$10,371.4M" in page
    assert "Leverage, downside (EBITDAR -20%): 4.5x EBITDAR - lease-adjusted net debt <strong>(binding)</strong>" in page
    assert "EBITDAR US$16,119.0M" in page and "existing debt service US$2,248.0M" in page
    assert "BO haircut 0%: none" in page


def test_cli_fetch_filing_without_a_year_lists_the_choices(root, tmp_path, capsys, monkeypatch):
    import app.cli as cli

    monkeypatch.setattr(cli, "_sec_client", lambda session: FakeSec())
    main(["--database", str(tmp_path / "data" / "main.db"), "fetch-filing", "NVDA"])
    out = json.loads(capsys.readouterr().out)
    assert [r["fiscal_year"] for r in out["choose_one"]] == [2026, 2025, 2018]
    assert out["choose_one"][0]["download"] == f"python -m app.cli fetch-filing NVDA --accession {ACCESSION}"
    assert not list(root.iterdir())  # listing downloads nothing


def test_browser_ticker_then_choose_a_year(client, root, monkeypatch):
    from app.api import bo_fo as api

    monkeypatch.setattr(api, "_client", lambda session: FakeSec())
    monkeypatch.setattr(api, "get_settings", lambda: type("S", (), {"sec_user_agent": "Test t@example.com"})())
    listed = client.get("/run/reports", params={"ticker": "NVDA"})
    assert listed.status_code == 200
    assert "NVIDIA CORP (NVDA): choose a 10-K to download" in listed.text
    assert f'value="{ACCESSION}"' in listed.text and "FY2025" in listed.text
    assert "no Inline XBRL; not supported" in listed.text
    fetched = client.post("/run/fetch", data={"ticker": "NVDA", "accession": ACCESSION}, follow_redirects=False)
    assert fetched.status_code == 303 and fetched.headers["location"] == "/run?folder=NVDA%2010-K%20FY2026"
    assert (root / "NVDA 10-K FY2026" / "bundle.json").is_file()
    again = client.get("/run/reports", params={"ticker": "NVDA"})
    assert "already in <code>edgar_filings/NVDA 10-K FY2026</code>" in again.text


def test_browser_refuses_a_bad_upload(client):
    bad = client.post("/run/import", files=[("files", ("x.htm", b"<html></html>", "text/html"))],
                      data={"accession": ACCESSION, "filing_date": FILED.isoformat()})
    assert bad.status_code == 400 and "Import failed" in bad.text
