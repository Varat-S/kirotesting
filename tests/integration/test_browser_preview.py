"""The browser shows real saved drafts without approving or changing them."""

from copy import deepcopy

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.deps import get_session
from app.main import create_app
from app.models.base import create_engine_and_session
from app.models.orm import AuditEvent, HumanReview, Snapshot
from app.preview import seed_demo


def test_browser_inspects_three_real_drafts_without_writing(tmp_path):
    database, output = tmp_path / "demo.db", tmp_path / "output"
    seed_demo(database, output)
    seed_demo(database, output)  # Restarting must not duplicate cases/snapshots.
    engine, factory = create_engine_and_session(f"sqlite:///{database.as_posix()}")
    try:
        with factory() as session:
            snapshots = list(session.scalars(select(Snapshot)))
            before = [
                (s.id, s.finalized, s.content_hash, deepcopy(s.payload))
                for s in snapshots
            ]
            audit_count = session.scalar(select(func.count()).select_from(AuditEvent))
            assert len(snapshots) == 6
            app = create_app()
            app.dependency_overrides[get_session] = lambda: session
            with TestClient(app) as client:
                home = client.get("/")
                assert home.status_code == 200
                assert "SYNTHETIC_CONFLICT" in home.text
                for case in [
                    "SYNTHETIC_2024",
                    "SYNTHETIC_MISSING",
                    "SYNTHETIC_CONFLICT",
                ]:
                    response = client.get(f"/cases/{case}/workbench")
                    assert response.status_code == 200
                    view = response.json()
                    assert view["header"]["lineage"]["presented_version"] == 1
                    assert not view["header"]["lineage"]["finalized"]
                    assert view["header"]["lineage"]["all_final_versions"] == []
                    metrics = view["sections"]["metrics"]["metrics"]
                    if case == "SYNTHETIC_2024":
                        assert metrics["operating_margin"]["result"] == 0.2
                        assert metrics["net_debt_to_ebitda"]["result"] == 3
                    else:
                        assert metrics["operating_margin"]["result"] is None
                        assert any(
                            e["mandatory"]
                            for e in view["sections"]["exceptions"]["escalations"]
                        )
                    if case == "SYNTHETIC_MISSING":
                        assert view["counts"]["critical_missing_sources"] == 1
                    html = client.get(f"/cases/{case}/workbench.html")
                    assert html.status_code == 200
                    assert "unapproved" in html.text
                    assert "operating margin" in html.text
                    memo = client.get(f"/cases/{case}/memo.json")
                    assert memo.status_code == 200
                    assert memo.json()["final_status"] == "draft"
                    assert client.get(f"/cases/{case}/memo.html").status_code == 200
                assert client.get("/cases/nope/memo.html").status_code == 404
                assert (
                    client.get(
                        "/cases/SYNTHETIC_2024/memo.json?snapshot_version=99"
                    ).status_code
                    == 404
                )
            for row in snapshots:
                session.refresh(row)
            assert [
                (s.id, s.finalized, s.content_hash, s.payload) for s in snapshots
            ] == before
            assert (
                session.scalar(select(func.count()).select_from(AuditEvent))
                == audit_count
            )
            assert session.scalar(select(func.count()).select_from(HumanReview)) == 0
    finally:
        engine.dispose()
