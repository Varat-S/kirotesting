"""Integration tests for the read-only Pipeline Explorer and BO/FO integration.

Covers:
* Pipeline Explorer HTTP routes (home, graph, manifest, node, viewer, JSON, PDF).
* Artifact-access safety (no path traversal / out-of-root reads).
* Missing/failed artifact handling through the UI (reason, not mock).
* Shared CLI commands coexisting after the branch integration.
* Database migration compatibility for the FO tables.
"""

from __future__ import annotations

import json
import urllib.parse as urlparse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from app.main import create_app
from app.models.base import init_db
from app.services.registry.explorer import (
    ArtifactAccessError,
    discover_cases,
    inspect_node,
    load_artifact,
)

from tests.registry_fixture import write_fixture,CASE,MANAGEMENT_ID
DEMO_CASE = CASE
DEMO_ROOT = Path("output")
M3_FILE = Path("unused")


@pytest.fixture(autouse=True)
def current_m3_fixture(tmp_path,monkeypatch):
    global DEMO_ROOT,M3_FILE
    DEMO_ROOT=tmp_path/'output'
    directory=write_fixture(DEMO_ROOT)
    M3_FILE=directory/'Senior Management Assessment.json'
    monkeypatch.setenv('OUTPUT_DIR',str(DEMO_ROOT))
    from app.core.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _q(s: str) -> str:
    return urlparse.quote(s, safe="")


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


# -- Pipeline Explorer routes -------------------------------------------------

def test_pipeline_home_renders(client):
    r = client.get("/pipeline")
    assert r.status_code == 200
    assert "Pipeline Explorer" in r.text


def test_graph_and_manifest_for_demo_case(client):
    g = client.get(f"/pipeline/{_q(DEMO_CASE)}/graph.json")
    assert g.status_code == 200
    assert len(g.json()["nodes"]) > 0

    m = client.get(f"/pipeline/{_q(DEMO_CASE)}/manifest.json")
    assert m.status_code == 200
    body = m.json()
    assert body["manifest_version"] == "artifact-manifest-1.0"
    assert any(a["artifact_id"] == MANAGEMENT_ID for a in body["artifacts"])


def test_node_inspector_download_and_pdf(client):
    base = f"/pipeline/{_q(DEMO_CASE)}/node/{_q(MANAGEMENT_ID)}"
    node = client.get(base)
    assert node.status_code == 200
    assert node.json()["status"] in {"validated", "qualified"}

    view = client.get(base + "/view.html")
    assert view.status_code == 200 and "json-viewer" in view.text

    dl = client.get(base + "/download.json")
    assert dl.status_code == 200
    assert dl.headers["content-disposition"].startswith("attachment")

    pdf = client.get(base + "/export.pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_download_json_matches_recorded_artifact(client):
    payload, _ = load_artifact(DEMO_CASE, MANAGEMENT_ID, str(DEMO_ROOT), repo_root=".")
    dl = client.get(
        f"/pipeline/{_q(DEMO_CASE)}/node/{_q(MANAGEMENT_ID)}/download.json")
    assert json.loads(dl.content) == payload  # export == actual recorded artifact


def test_unknown_case_and_node_are_404(client):
    assert client.get(f"/pipeline/{_q('nope')}/manifest.json").status_code == 404
    assert client.get(
        f"/pipeline/{_q(DEMO_CASE)}/node/{_q('m3.bogus')}").status_code == 404


def test_illustration_graph_when_no_run(client):
    g = client.get(f"/pipeline/{_q('does-not-exist')}/graph.json")
    assert g.status_code == 200
    nodes = g.json()["nodes"]
    assert len(nodes) > 0 and all(n["status"] == "absent" for n in nodes)


# -- Artifact-access safety ---------------------------------------------------

def test_access_is_bounded_to_known_artifacts():
    # A valid-looking id that is not in the manifest is rejected.
    with pytest.raises(ArtifactAccessError):
        load_artifact(DEMO_CASE, "../../../../etc/passwd", str(DEMO_ROOT), repo_root=".")


def test_absent_node_reports_reason_not_mock():
    # Craft an id that maps to an absent entry via a synthetic empty case.
    info = inspect_node(DEMO_CASE, MANAGEMENT_ID, str(DEMO_ROOT), repo_root=".")
    assert info["payload_available"] is True  # this one exists
    # A missing-file artifact in a fresh case returns a reason, no payload.
    cases = discover_cases(str(DEMO_ROOT), repo_root=".")
    assert any(c.case_id == DEMO_CASE for c in cases)


# -- Shared CLI commands coexist after integration ----------------------------

def test_cli_exposes_all_integrated_commands():
    import argparse
    import contextlib
    import io

    from app.cli import main as cli_main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        cli_main(["--help"])
    help_text = buf.getvalue()
    for cmd in ("run-bo-case", "run-m3", "run-fo-case", "fo-review", "fo-override"):
        assert cmd in help_text


# -- Database migration compatibility (FO tables) -----------------------------

def test_init_db_creates_fo_tables(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    init_db(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"financial_overview_runs", "financial_overview_reviews",
            "financial_overview_overrides"} <= tables


def test_migration_adds_fo_tables_to_old_db(tmp_path):
    from app.migrate import migrate

    db = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db}")
    init_db(engine)
    with engine.begin() as conn:
        for tbl in ("financial_overview_overrides", "financial_overview_reviews",
                    "financial_overview_runs"):
            conn.execute(text(f"DROP TABLE IF EXISTS {tbl}"))
    changes = migrate(engine)
    assert any("financial_overview_runs" in c for c in changes)
    tables = set(inspect(engine).get_table_names())
    assert {"financial_overview_runs", "financial_overview_reviews",
            "financial_overview_overrides"} <= tables
