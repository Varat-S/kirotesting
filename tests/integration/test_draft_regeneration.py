from sqlalchemy import select

from app.models.orm import ModelRun, Snapshot
from tests.integration.test_review_workflow import client as client
from tests.integration.test_review_workflow import prepare
from tests.integration.test_upload_preview import count


def test_explicit_regeneration_creates_new_draft_without_changing_evidence(
    client, db_session
):
    value = prepare(client)
    first = client.post(
        "/inspect/UPLOAD_TEST/1/continue",
        data={"mode": "offline", "confirmed": "yes"},
        follow_redirects=False,
    )
    assert first.status_code == 303
    assert (
        client.post(
            "/inspect/UPLOAD_TEST/1/continue",
            data={"mode": "offline", "confirmed": "yes"},
        ).status_code
        == 422
    )
    second = client.post(
        "/inspect/UPLOAD_TEST/1/continue",
        data={"mode": "offline", "confirmed": "yes", "rerun": "yes"},
        follow_redirects=False,
    )
    assert second.status_code == 303, second.text
    assert second.headers["location"].endswith("/drafts/2")
    assert count(db_session, ModelRun) == 6
    assert client.get("/inspect/UPLOAD_TEST/1/payload.json").json() == value
    draft = db_session.scalars(
        select(Snapshot).where(
            Snapshot.snapshot_type == "final_case", Snapshot.snapshot_version == 2
        )
    ).one()
    assert draft.supersedes is not None
    decision = {"reviewer": "Tester", "reason": "Reviewed", "confirmed": "yes"}
    assert (
        client.post(first.headers["location"] + "/approve", data=decision).status_code
        == 409
    )
    assert (
        client.post(first.headers["location"] + "/finalize", data=decision).status_code
        == 409
    )
