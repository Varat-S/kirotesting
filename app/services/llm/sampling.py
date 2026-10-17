"""Sampling policy, independent of SDKs; no extra model calls for capability discovery."""
import math
import re

from app.core.config import Settings


class SamplingConfigurationError(ValueError, RuntimeError):
    """Invalid sampling input, also handled by the shared provider-error path."""


def sampling_temperature(settings: Settings, requested: float | None, model: str) -> tuple[float | None, float | None]:
    requested = requested if requested is not None else settings.llm_temperature
    if requested is not None and (not math.isfinite(requested) or not 0 <= requested <= settings.llm_temperature_max):
        raise SamplingConfigurationError(f"Temperature must be finite and in [0, {settings.llm_temperature_max}]")
    gemini = settings.llm_provider == "vertex" and "gemini-" in model.lower()
    supported = settings.llm_temperature_supported
    if gemini and supported is None:
        supported = True
    # Retain the legacy compatible endpoint's sampling default; an explicit
    # capability=False disables temperature for servers that do not support it.
    if settings.llm_provider == "openai_compatible" and supported is None:
        supported = True
    if supported is False or (supported is None and requested is not None):
        if requested is not None:
            raise SamplingConfigurationError("Temperature override requires confirmed model support (LLM_TEMPERATURE_SUPPORTED)")
        return requested, None
    if requested is not None:
        return requested, requested
    if gemini and re.search(r"gemini-3(?:[.\-]|$)", model.lower()):
        return None, 1.0
    if settings.llm_provider in {"vertex", "openai_compatible"}:
        return None, 0.0
    # Existing OpenAI Responses calls omit temperature; preserve that behavior.
    return None, None
