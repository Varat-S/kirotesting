"""The model picked for a combined run runs both sections. No live calls."""

import pytest

from app.core.config import Settings
from app.services import bo_fo


def _vertex(monkeypatch):
    settings = Settings(_env_file=None, llm_provider="vertex", vertex_api_key="fake-test-key",
                        vertex_model_narrow="gemini-3.5-flash-lite",
                        vertex_model_orchestrator="gemini-3.5-flash-lite",
                        vertex_model_challenge="gemini-3.5-flash-lite")
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.fo.models.configured_model", lambda: settings.vertex_model_narrow)
    return settings


def test_picked_model_runs_every_bo_tier(monkeypatch):
    _vertex(monkeypatch)
    backend = bo_fo.bo_live_backend("gemini-3.7-flash")
    assert {backend.model_for_tier(t) for t in ("narrow", "orchestrator", "challenge")} == {"gemini-3.7-flash"}


def test_no_pick_keeps_the_configured_tiers(monkeypatch):
    _vertex(monkeypatch)
    assert bo_fo.bo_live_backend().model_for_tier("orchestrator") == "gemini-3.5-flash-lite"


def test_a_model_outside_the_offered_list_is_refused(monkeypatch):
    _vertex(monkeypatch)
    with pytest.raises(ValueError, match="not one of the text-analysis models"):
        bo_fo.bo_live_backend("gemini-3.1-flash-image")


def test_web_run_passes_the_picked_model_to_both_sections(monkeypatch):
    from app.api import bo_fo as api
    from app.services.fo import precheck

    seen = {}
    monkeypatch.setattr(bo_fo, "sqlite_path", lambda url: "db")
    monkeypatch.setattr(bo_fo, "plan", lambda folder, case_id, cutoff: {"case_id": "C", "cutoff": None})
    monkeypatch.setattr(bo_fo, "bo_live_backend", lambda model=None: seen.setdefault("bo", model))
    monkeypatch.setattr(precheck, "live_backend", lambda model=None: seen.setdefault("fo", model))
    monkeypatch.setattr(bo_fo.jobs, "start", lambda **kwargs: "job1")
    api.start_run(folder="f", case_id="", model="gemini-3.6-flash",
                  cutoff="", requested_amount_m="", tenor_years="", m3_version="v1")
    assert seen == {"bo": "gemini-3.6-flash", "fo": "gemini-3.6-flash"}
