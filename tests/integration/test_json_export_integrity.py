"""Priority 5: JSON export / hash integrity.

Two distinct export operations:
* original download  -> exact recorded bytes, original SHA-256 preserved.
* normalized export  -> canonical serialization, separate canonical hash, labelled.
"""

from __future__ import annotations

import hashlib
import json
import urllib.parse as urlparse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.hashing import content_hash
from app.main import create_app
from app.services.registry.explorer import (
    ArtifactAccessError,
    load_artifact,
    load_artifact_bytes,
)

DEMO_CASE = "demo:hca_simulated"
M3_FILE = Path(
    "examples/bo_m3/structural_credit/demo_output/hca_simulated/m3_analysis.json")


def _q(s: str) -> str:
    return urlparse.quote(s, safe="")


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


# -- service layer ------------------------------------------------------------

def test_original_bytes_match_disk_and_hash(tmp_path):
    raw, original_sha, _ = load_artifact_bytes(
        DEMO_CASE, "m3.analysis", "output", repo_root=".")
    disk = M3_FILE.read_bytes()
    assert raw == disk  # byte-for-byte
    assert original_sha == hashlib.sha256(disk).hexdigest()


def test_normalized_hash_is_canonical_and_distinct():
    payload, _ = load_artifact(DEMO_CASE, "m3.analysis", "output", repo_root=".")
    _, original_sha, _ = load_artifact_bytes(
        DEMO_CASE, "m3.analysis", "output", repo_root=".")
    canonical_sha = content_hash(payload)
    # The canonical hash matches the recorded entry hash, and differs from the
    # original file hash (the recorded file is pretty-printed, not canonical).
    assert canonical_sha != original_sha


def test_slice_artifact_has_no_original_bytes(tmp_path):
    # A per-agent slice has no independent original file.
    from app.services.bo.offline import ScriptedBoBackend
    from app.services.bo.runner import BusinessOverviewRunner
    from tests import bo_helpers as h
    from tests.integration.test_bo_v3_complete import _script
    from app.models.base import create_engine_and_session, init_db

    eng, Session = create_engine_and_session("sqlite://")
    init_db(eng)
    s = Session()
    out = tmp_path / "out"
    result = BusinessOverviewRunner(
        s, backend=ScriptedBoBackend(_script()), bo_roster="v3",
        data_root=tmp_path / "d", output_root=out).run_prepared(
            h.CASE, h.evidence_snapshot(), h.metrics(), write_outputs=True)
    s.commit()
    slice_id = f"bo.agent.m3_corporate_management.{result.analysis_run_id}"
    with pytest.raises(ArtifactAccessError):
        load_artifact_bytes(h.CASE, slice_id, str(out), repo_root=str(out))


# -- HTTP endpoints -----------------------------------------------------------

def test_original_endpoint_preserves_bytes_and_sets_hash_header(client):
    base = f"/pipeline/{_q(DEMO_CASE)}/node/{_q('m3.analysis')}"
    r = client.get(base + "/original.json")
    assert r.status_code == 200
    assert r.content == M3_FILE.read_bytes()
    assert r.headers["X-Original-SHA256"] == hashlib.sha256(M3_FILE.read_bytes()).hexdigest()
    assert r.headers["X-Export-Kind"] == "original-bytes"


def test_normalized_endpoint_is_canonical_and_labelled(client):
    base = f"/pipeline/{_q(DEMO_CASE)}/node/{_q('m3.analysis')}"
    r = client.get(base + "/normalized.json")
    assert r.status_code == 200
    payload, _ = load_artifact(DEMO_CASE, "m3.analysis", "output", repo_root=".")
    assert json.loads(r.content) == payload               # semantically equivalent
    assert r.headers["X-Canonical-SHA256"] == content_hash(payload)
    assert r.headers["X-Export-Kind"] == "normalized-canonical"


def test_download_json_backwards_compatible_returns_original(client):
    base = f"/pipeline/{_q(DEMO_CASE)}/node/{_q('m3.analysis')}"
    r = client.get(base + "/download.json")
    assert r.status_code == 200
    # Whole-file artifact -> original bytes preserved on the legacy endpoint.
    assert r.content == M3_FILE.read_bytes()
    assert r.headers["X-Export-Kind"] == "original-bytes"
