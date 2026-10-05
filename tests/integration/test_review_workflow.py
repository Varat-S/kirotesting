"""Browser reviews version evidence; saved inputs continue without re-parsing."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.api.deps import get_session
from app.core.config import Settings
from app.main import create_app
from app.models.orm import (
    EvidencePreview,
    HumanReview,
    ModelRun,
    Snapshot,
    StageArtifact,
)
from app.services.pipeline.artifacts import ArtifactIntegrityError, verified_artifacts
from app.services.pipeline.runner import CreditMemoPipeline
from tests.integration.test_upload_preview import CSV, FORM, count, payload


@pytest.fixture
def client(db_session, tmp_path, monkeypatch):
    import app.api.inspection as inspection

    monkeypatch.setattr(
        inspection,
        "get_settings",
        lambda: Settings(
            debug=False,
            _env_file=None,
            data_dir=str(tmp_path / "data"),
            output_dir=str(tmp_path / "output"),
            llm_provider="none",
            llm_model=None,
            llm_api_key=None,
        ),
    )
    app = create_app()
    app.dependency_overrides[get_session] = lambda: db_session
    with TestClient(app) as c:
        yield c


def prepare(client, content=CSV):
    return payload(
        client,
        client.post(
            "/inspect",
            data=FORM,
            files={"files": ("input.csv", content)},
            follow_redirects=False,
        ),
    )


def test_review_versions_evidence_and_continues_without_reparse(
    client, db_session, monkeypatch
):
    first = prepare(client)
    original = deepcopy(first)
    fact = next(f for f in first["evidence"]["facts"] if f["name"] == "revenue")
    response = client.post(
        "/inspect/UPLOAD_TEST/1/review",
        data={
            "action": "verify",
            "fact_id": fact["fact_id"],
            "reviewer": "Test analyst",
            "reason": "Checked source CSV and period",
            "confirmed": "yes",
        },
        follow_redirects=False,
    )
    updated = payload(client, response)
    assert updated["evidence_version"] == 2
    assert client.get("/inspect/UPLOAD_TEST/1/payload.json").json() == original
    assert count(db_session, HumanReview) == 1
    assert any(
        q["state"] == "verified" for q in updated["evidence"]["data_quality"].values()
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Continuing a saved preview must not reparse sources.")

    monkeypatch.setattr(CreditMemoPipeline, "_run", forbidden)
    result = client.post(
        "/inspect/UPLOAD_TEST/2/continue",
        data={"mode": "offline", "confirmed": "yes"},
        follow_redirects=False,
    )
    assert result.status_code == 303, result.text
    assert "Credit memo review" in client.get(result.headers["location"]).text
    assert count(db_session, ModelRun) == 3
    outputs = client.get("/inspect/UPLOAD_TEST/2/artifacts.json").json()["artifacts"]
    runs = [
        client.get(a["url"]).json()
        for a in outputs
        if a["name"].startswith("model_run_")
    ]
    assert (
        next(r for r in runs if r["method"] == "extract")["inputs"]
        == updated["next_llm_input"]
    )
    assert (
        client.post(
            "/inspect/UPLOAD_TEST/1/continue",
            data={"mode": "offline", "confirmed": "yes"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/inspect/UPLOAD_TEST/2/continue",
            data={"mode": "offline", "confirmed": "yes"},
        ).status_code
        == 422
    )
    assert db_session.get(EvidencePreview, ("UPLOAD_TEST", 1)).payload == original


def test_mapping_review_retains_original_and_records_before_after(client, db_session):
    value = prepare(
        client, b"Line,2024\nOdd revenue label,1000\nOperating Income,200\n"
    )
    fact = next(
        f for f in value["evidence"]["facts"] if f["name"] == "Odd revenue label"
    )
    response = client.post(
        "/inspect/UPLOAD_TEST/1/review",
        data={
            "action": "map",
            "fact_id": fact["fact_id"],
            "canonical_name": "revenue",
            "reviewer": "Tester",
            "reason": "Source identifies total operating revenue",
            "confirmed": "yes",
        },
        follow_redirects=False,
    )
    updated = payload(client, response)
    assert updated["metrics"]["operating_margin"]["result"] == 0.2
    assert any(
        f["fact_id"] == fact["fact_id"]
        and f["mapping_status"] == "superseded_by_review"
        for f in updated["evidence"]["facts"]
    )
    assert (
        client.get("/inspect/UPLOAD_TEST/1/payload.json").json()["evidence"]["facts"]
        == value["evidence"]["facts"]
    )
    review = db_session.scalars(select(HumanReview)).one()
    assert review.prior_value["mapping_status"] == "unmapped" and review.signed_off
    assert review.new_value["fact"]["name"] == "revenue"


def test_conflict_selection_keeps_both_values_and_requires_latest_version(client):
    value = prepare(
        client,
        b"Line,2024\nOperating Revenue,1000\nRevenue,1100\nOperating Income,200\n",
    )
    field, quality = next(
        (k, q) for k, q in value["evidence"]["data_quality"].items() if '"revenue"' in k
    )
    assert quality["state"] == "conflicting"
    fact = next(
        f
        for f in value["evidence"]["facts"]
        if f["name"] == "revenue" and f["normalized_value"] == 1000
    )
    decision = {
        "action": "select",
        "fact_id": fact["fact_id"],
        "field": field,
        "reviewer": "Tester",
        "reason": "Selected source's declared total revenue after review",
        "confirmed": "yes",
    }
    response = client.post(
        "/inspect/UPLOAD_TEST/1/review", data=decision, follow_redirects=False
    )
    updated = payload(client, response)
    dq = updated["evidence"]["data_quality"][field]
    assert dq["state"] == "verified" and dq["value"] == 1000
    assert set(dq["values"]) == {1000, 1100}
    assert updated["metrics"]["operating_margin"]["result"] == 0.2
    assert (
        client.post("/inspect/UPLOAD_TEST/1/review", data=decision).status_code == 422
    )


def test_admitted_source_download_is_bound_to_the_original_hash(client, db_session):
    from app.models.orm import Document
    from pathlib import Path

    value = prepare(client)
    doc = value["evidence"]["documents"][0]
    url = "/inspect/UPLOAD_TEST/1/sources/" + doc["document_id"]
    assert client.get(url).content == CSV
    assert client.get("/inspect/UPLOAD_TEST/1/sources/unknown").status_code == 404
    row = db_session.get(Document, doc["document_id"])
    Path(row.storage_path).write_bytes(b"tampered")
    assert client.get(url).status_code == 409


def test_approval_finalization_gates_and_resolution_are_explicit(client, db_session):
    prepare(client)
    result = client.post(
        "/inspect/UPLOAD_TEST/1/continue",
        data={"mode": "offline", "confirmed": "yes"},
        follow_redirects=False,
    )
    url = result.headers["location"]
    decision = {
        "reviewer": "Tester",
        "reason": "Reviewed limitations and draft",
        "confirmed": "yes",
    }
    assert client.post(url + "/finalize", data=decision).status_code == 409
    assert (
        client.post(url + "/approve", data=decision, follow_redirects=False).status_code
        == 303
    )
    assert client.post(url + "/finalize", data=decision).status_code == 409
    from app.services.escalation.engine import EscalationEngine

    for esc in EscalationEngine(db_session, case_id="UPLOAD_TEST").open_escalations():
        response = client.post(
            "/inspect/UPLOAD_TEST/1/resolve",
            data={**decision, "escalation_id": esc.escalation_id},
            follow_redirects=False,
        )
        assert response.status_code == 303, response.text
    assert (
        client.post(
            url + "/finalize", data={**decision, "reviewer": "Different signer"}
        ).status_code
        == 409
    )
    response = client.post(url + "/finalize", data=decision, follow_redirects=False)
    assert response.status_code == 303, response.text
    final = db_session.scalars(
        select(Snapshot).where(Snapshot.snapshot_type == "final_case")
    ).one()
    assert final.finalized and final.content_hash
    assert (
        client.post(
            "/inspect/UPLOAD_TEST/1/continue",
            data={"mode": "offline", "confirmed": "yes"},
        ).status_code
        == 409
    )


def test_recorded_artifact_tampering_is_detected(client, db_session):
    prepare(client)
    stage = db_session.get(StageArtifact, ("UPLOAD_TEST", 1, "extraction"))
    stage.payload_json = "{}"
    with pytest.raises(ArtifactIntegrityError):
        db_session.flush()
    db_session.expire(stage)
    db_session.execute(
        text("UPDATE stage_artifacts SET payload_json='{}' WHERE name='extraction'")
    )
    db_session.expire_all()
    assert client.get("/inspect/UPLOAD_TEST/1/artifacts.json").status_code == 409
    with pytest.raises(ArtifactIntegrityError):
        verified_artifacts(db_session, "UPLOAD_TEST", 1)


def test_cross_origin_and_unconfirmed_review_rejected(client):
    value = prepare(client)
    fact = value["evidence"]["facts"][0]
    decision = {
        "action": "verify",
        "fact_id": fact["fact_id"],
        "reviewer": "Tester",
        "reason": "Checked",
        "confirmed": "yes",
    }
    assert (
        client.post(
            "/inspect/UPLOAD_TEST/1/review",
            data=decision,
            headers={"Origin": "https://unrelated.example"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/inspect/UPLOAD_TEST/1/review", data={**decision, "confirmed": ""}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/inspect/UPLOAD_TEST/1/continue", data={"mode": "live", "confirmed": "yes"}
        ).status_code
        == 422
    )
