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
from tests.registry_fixture import CASE as M3_CASE, MANAGEMENT_ID, write_fixture
from app.services.registry.explorer import (
    ArtifactAccessError,
    discover_cases,
    inspect_node,
    load_artifact,
)

DEMO_CASE = M3_CASE
M3_ARTIFACT = MANAGEMENT_ID


def _q(s: str) -> str:
    return urlparse.quote(s, safe="")


@pytest.fixture()
def output_root(tmp_path, monkeypatch):
    """A recorded Module 3 run in a temporary output root, served by the Explorer."""
    write_fixture(tmp_path)
    monkeypatch.setattr("app.api.pipeline_explorer._output_dir", lambda: str(tmp_path))
    return tmp_path


@pytest.fixture()
def client(output_root) -> TestClient:
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
    assert any(a["artifact_id"] == M3_ARTIFACT for a in body["artifacts"])


def test_node_inspector_download_and_pdf(client):
    base = f"/pipeline/{_q(DEMO_CASE)}/node/{_q(M3_ARTIFACT)}"
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


def test_download_json_matches_recorded_artifact(client, output_root):
    payload, _ = load_artifact(DEMO_CASE, M3_ARTIFACT, str(output_root), repo_root=str(output_root))
    dl = client.get(
        f"/pipeline/{_q(DEMO_CASE)}/node/{_q(M3_ARTIFACT)}/download.json")
    assert json.loads(dl.content) == payload  # export == actual recorded artifact


def test_unknown_case_and_node_are_404(client):
    assert client.get(f"/pipeline/{_q('nope')}/manifest.json").status_code == 404
    assert client.get(
        f"/pipeline/{_q(DEMO_CASE)}/node/{_q('m3.rebuild.bogus')}").status_code == 404


def test_illustration_graph_when_no_run(client):
    g = client.get(f"/pipeline/{_q('does-not-exist')}/graph.json")
    assert g.status_code == 200
    nodes = g.json()["nodes"]
    assert len(nodes) > 0 and all(n["status"] == "absent" for n in nodes)


# -- Artifact-access safety ---------------------------------------------------

def test_access_is_bounded_to_known_artifacts(output_root):
    # A valid-looking id that is not in the manifest is rejected.
    with pytest.raises(ArtifactAccessError):
        load_artifact(DEMO_CASE, "../../../../etc/passwd", str(output_root), repo_root=str(output_root))


def test_recorded_node_reports_its_payload_and_cases_are_discovered(output_root):
    info = inspect_node(DEMO_CASE, M3_ARTIFACT, str(output_root), repo_root=str(output_root))
    assert info["payload_available"] is True
    cases = discover_cases(output_root, repo_root=output_root)
    assert [c.case_id for c in cases] == [DEMO_CASE] and cases[0].kind == "live"


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
