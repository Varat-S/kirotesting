"""Real-provider backend scaffold behind the LLMClient abstraction (task 6.1).

This is a SCAFFOLD. It implements the :class:`~app.services.llm.client.LLMBackend`
interface so a real provider can be dropped in without touching the rest of the
codebase (Req 19.3). It is NEVER invoked by tests and the test suite passes with
NO network.

Security (design.md Security & Data Hygiene / Req 25.2):

* Credentials and model-id come from env :class:`~app.core.config.Settings`
  (``llm_api_key`` is a ``SecretStr`` and is never logged or hardcoded).
* No provider SDK is a hard dependency. The SDK import is GUARDED so its absence
  cannot break offline installs; :meth:`generate` raises a clear error if the
  backend is used without the SDK/credentials present.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.services.llm.client import LLMBackend, LLMRawResult, LLMRequest


class ProviderNotConfiguredError(RuntimeError):
    """Raised when the real provider backend is used without SDK/credentials."""


class RealProviderBackend(LLMBackend):
    """Scaffold for a real LLM provider. Not used in tests.

    The constructor reads settings but does NOT import or require any provider
    SDK. :meth:`generate` performs the guarded import at call time so that
    simply importing this module (or constructing the backend) never fails on an
    offline install.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    @property
    def model_id(self) -> str:
        return self._settings.llm_model or "unconfigured"

    def generate(self, request: LLMRequest) -> LLMRawResult:  # pragma: no cover
        """Call the configured provider. Raises if unconfigured.

        This path is intentionally never exercised by the offline test suite.
        A concrete provider integration performs its guarded SDK import HERE,
        passing ``temperature=request.temperature`` and requesting structured
        JSON output against ``request.prompt.response_schema``.
        """
        if self._settings.llm_provider in (None, "", "none"):
            raise ProviderNotConfiguredError(
                "No LLM provider configured (set LLM_PROVIDER / LLM_API_KEY in the "
                "environment). The deterministic FakeLLMBackend is used in tests."
            )
        if self._settings.llm_api_key is None:
            raise ProviderNotConfiguredError(
                "LLM_API_KEY is not set; cannot call the real provider."
            )
        # A concrete integration would guard-import its SDK here, e.g.:
        #   try:
        #       import some_provider_sdk
        #   except ImportError as exc:
        #       raise ProviderNotConfiguredError(...) from exc
        # and then issue the structured-JSON request using the secret key via
        # ``self._settings.llm_api_key.get_secret_value()`` (never logged).
        raise ProviderNotConfiguredError(
            "RealProviderBackend is a scaffold; no concrete provider SDK is wired "
            "in this PoC. Inject a backend implementing LLMBackend to go live."
        )
