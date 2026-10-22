"""Which Vertex models the FO Pre-check Agent can run on, for the workbench's model picker.

Vertex can't list models with an API key (the list call needs OAuth), so the candidates are named here and each
is checked with a free ``count_tokens`` call: it generates nothing and isn't billed, and it fails (404) when the
model doesn't exist or isn't open to the key's project and region. Only text-analysis models are offered: the
pre-checks need structured JSON back, which audio (TTS, transcribe), live, translate and image models don't give.
The result is cached, so the page doesn't re-check on every load.
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

# The candidate models (as given by the user, 2026-10-22). Edit this list to offer others.
CANDIDATES = [
    "gemini-3.8-flash", "gemini-3.8-live", "gemini-3.8-live-extended-thinking", "gemini-3.8-flash-tts",
    "gemini-3.8-flash-lite-tts", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite",
    "gemini-3.5-transcribe", "gemini-3.5-transcribe-live", "gemini-3.5-live-translate-preview",
    "gemini-3.1-pro-preview", "gemini-3.1-flash-lite", "gemini-3.1-flash-live-preview", "gemini-3.1-flash-image",
    "gemini-3.1-flash-lite-image", "gemini-3-flash-preview", "gemini-3-pro-image", "gemini-2.5-pro",
    "gemini-2.5-flash-image", "gemini-omni-1.1-flash", "gemini-nano-banana-2.1",
]
NOT_TEXT = re.compile(r"tts|transcribe|live|translate|image|nano-banana|omni", re.I)
CACHE_SECONDS = 6 * 3600

_cache = {"at": 0.0, "models": None, "note": None}
_lock = threading.Lock()


def text_candidates() -> list[str]:
    """The candidates that can do text analysis (structured JSON), in the listed order."""
    return [m for m in CANDIDATES if not NOT_TEXT.search(m)]


def configured_model() -> str | None:
    """The model ``.env`` sets for the narrow tier (what a run uses when no model is picked)."""
    from app.core.config import get_settings

    return get_settings().vertex_model_narrow


def _probe(client, model: str) -> bool:
    try:
        client.models.count_tokens(model=model, contents="Hello")
        return True
    except Exception:  # 404: not available to this key / region
        return False


def available_models(force: bool = False) -> tuple[list[str], str | None]:
    """(the text-analysis models the configured Vertex key can use, a note when they couldn't be checked). The
    configured model is always offered."""
    with _lock:
        if not force and _cache["models"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
            return list(_cache["models"]), _cache["note"]
    current = configured_model()
    models, note = [], None
    try:
        from app.core.config import get_settings
        from app.services.llm.providers_vertex import VertexProviderBackend

        settings = get_settings()
        if settings.llm_provider != "vertex":
            raise RuntimeError(f"the provider is {settings.llm_provider!r}, not Vertex")
        client = VertexProviderBackend(settings)._client()
        with ThreadPoolExecutor(max_workers=8) as pool:
            found = dict(zip(text_candidates(), pool.map(lambda m: _probe(client, m), text_candidates())))
        models = [m for m, ok in found.items() if ok]
    except Exception as exc:  # no provider, no SDK, no network: offer only the configured model
        note = f"Models couldn't be checked ({exc}); only the configured model is offered."
    if current and current not in models:
        models.insert(0, current)
    with _lock:
        _cache.update(at=time.time(), models=models, note=note)
    return list(models), note


def check_model(model: str) -> str:
    """The model, if it may be used for a run: a text-analysis candidate, or the configured model."""
    model = (model or "").strip()
    if model == configured_model() or model in text_candidates():
        return model
    raise ValueError(f"{model!r} is not one of the text-analysis models offered for the Pre-check Agent.")
