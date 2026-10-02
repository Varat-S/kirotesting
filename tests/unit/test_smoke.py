"""Scaffold smoke tests.

These verify that the package imports, the config loader works from the
environment without exposing secrets (Requirement 25.2), and the FastAPI
health endpoint responds. The FastAPI/httpx-dependent checks skip gracefully
if those optional dependencies are not installed in the environment.
"""

from __future__ import annotations

import pytest


def test_app_package_imports() -> None:
    import app

    assert app.__version__


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings

    monkeypatch.setenv("APP_NAME", "credit-memo-poc")
    monkeypatch.setenv("LLM_API_KEY", "super-secret-value")

    settings = Settings()

    assert settings.app_name == "credit-memo-poc"
    # Secret is loaded but masked: never present in repr/log output (Req 25.2).
    assert settings.llm_api_key is not None
    assert "super-secret-value" not in repr(settings)
    assert settings.llm_api_key.get_secret_value() == "super-secret-value"


def test_health_endpoint() -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")

    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"]
