"""Single LLMClient provider abstraction + model-run registry (task 6.1).

All provider-specific calls live behind ONE interface with exactly three
methods -- ``extract``, ``analyze`` and ``challenge`` (Req 19.3). Each call:

* resolves the approved prompt version from the :class:`PromptRegistry`;
* sends the request to a pluggable ``LLMBackend`` requesting structured JSON,
  passing ``temperature=0`` where supported (Req 19.4);
* VALIDATES the raw response against the prompt's FIXED response JSON Schema
  BEFORE use (Req 19.4, 12.5, 13.2); invalid output is rejected;
* logs the FULL run to the ``model_runs`` registry -- prompt ID/version/hash,
  model ID + config, case version, evidence IDs, raw + parsed response, and the
  validation outcome -- and emits an ``llm_run`` audit event (Req 19.2);
* NEVER claims byte-exact reproducibility: the raw run is always stored (Req 19.4).

Backends:

* :class:`FakeLLMBackend` -- a DETERMINISTIC, in-memory backend used by all
  tests. It returns scripted canned structured responses keyed by
  ``(method, key)`` so tests are reproducible offline with NO network.
* A real provider backend may be scaffolded behind the same interface, reading
  credentials/model-id from env Settings (SecretStr, never logged/hardcoded). It
  is guarded so its absence never breaks offline installs and it is NEVER
  invoked by tests.
"""

from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import jsonschema
from sqlalchemy.orm import Session

from app.models.orm import ModelRun
from app.prompts.registry import PromptRegistry, RegisteredPrompt
from app.services.audit.log import ActorType, AuditLog, EventType


class LLMValidationError(ValueError):
    """Raised when a model response fails schema validation before use."""


# ---------------------------------------------------------------------------
# Request / backend contracts
# ---------------------------------------------------------------------------


@dataclass
class LLMRequest:
    """A single structured-JSON request to a backend.

    ``inputs`` is the evidence/context payload supplied to the model (facts,
    metrics, trends, benchmarks, snippets, limitations, conflicts). ``evidence_ids``
    are logged on the model run. ``key`` lets the deterministic fake backend pick
    a scripted response; it defaults to the method name.
    """

    method: str
    prompt: RegisteredPrompt
    inputs: dict[str, Any] = field(default_factory=dict)
    evidence_ids: list[str] = field(default_factory=list)
    case_id: str | None = None
    case_version: int | None = None
    temperature: float = 0.0
    key: str | None = None


@dataclass
class LLMRawResult:
    """What a backend returns: a RAW string response + the model identity."""

    raw_response: str
    model_id: str
    model_config: dict[str, Any] = field(default_factory=dict)


class LLMBackend(ABC):
    """A provider backend. Returns a RAW structured-JSON string response."""

    @abstractmethod
    def generate(self, request: LLMRequest) -> LLMRawResult:
        """Return the raw model response for ``request``."""


class FakeLLMBackend(LLMBackend):
    """Deterministic, offline, scripted backend for tests (no network).

    Responses are registered per ``(method, key)``. A response may be a dict (it
    is JSON-serialized to a raw string) or an already-serialized string (useful
    to simulate schema-invalid output). The same request always yields the same
    raw bytes, so runs are reproducible.
    """

    MODEL_ID = "fake-deterministic-v1"

    def __init__(self, model_id: str | None = None) -> None:
        self._model_id = model_id or self.MODEL_ID
        self._scripts: dict[tuple[str, str], str] = {}

    def register(self, method: str, response: Any, *, key: str = "default") -> None:
        """Register a canned response for ``(method, key)``.

        A ``dict``/``list`` is serialized deterministically (sorted keys); a
        ``str`` is used verbatim so invalid-JSON / schema-violating payloads can
        be simulated.
        """
        if isinstance(response, str):
            raw = response
        else:
            raw = json.dumps(response, sort_keys=True)
        self._scripts[(method, key)] = raw

    def generate(self, request: LLMRequest) -> LLMRawResult:
        key = request.key or "default"
        script = self._scripts.get((request.method, key))
        if script is None:
            raise KeyError(
                f"FakeLLMBackend has no scripted response for "
                f"method={request.method!r} key={key!r}."
            )
        return LLMRawResult(
            raw_response=script,
            model_id=self._model_id,
            model_config={"temperature": request.temperature, "backend": "fake"},
        )


# ---------------------------------------------------------------------------
# Model-run registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelRunResult:
    """Outcome of a validated (or rejected) LLM run."""

    run_id: str
    method: str
    validation_outcome: str  # "valid" | "rejected"
    parsed: dict[str, Any] | None
    raw_response: str
    prompt_id: str
    model_id: str

    @property
    def is_valid(self) -> bool:
        return self.validation_outcome == "valid"


class LLMClient:
    """The single provider abstraction (Req 19.3).

    Wraps a backend, the prompt registry and the model-run registry. The three
    public methods map 1:1 to the LLMClient contract.
    """

    def __init__(
        self,
        backend: LLMBackend,
        *,
        prompts: PromptRegistry,
        session: Session | None = None,
        audit: AuditLog | None = None,
        prompt_versions: dict[str, int] | None = None,
    ) -> None:
        self._backend = backend
        self._prompts = prompts
        self._session = session
        self._audit = audit
        self._prompt_versions = prompt_versions

    # -- public interface -----------------------------------------------------

    def extract(
        self,
        inputs: dict[str, Any],
        *,
        evidence_ids: list[str] | None = None,
        case_id: str | None = None,
        case_version: int | None = None,
        prompt_name: str = "qualitative_extraction",
        key: str | None = None,
    ) -> ModelRunResult:
        """Run the qualitative-extraction prompt."""
        return self._run(
            "extract",
            prompt_name,
            inputs,
            evidence_ids=evidence_ids,
            case_id=case_id,
            case_version=case_version,
            key=key,
        )

    def analyze(
        self,
        inputs: dict[str, Any],
        *,
        evidence_ids: list[str] | None = None,
        case_id: str | None = None,
        case_version: int | None = None,
        prompt_name: str = "business_analysis",
        key: str | None = None,
    ) -> ModelRunResult:
        """Run the generative credit-analysis prompt."""
        return self._run(
            "analyze",
            prompt_name,
            inputs,
            evidence_ids=evidence_ids,
            case_id=case_id,
            case_version=case_version,
            key=key,
        )

    def challenge(
        self,
        inputs: dict[str, Any],
        *,
        evidence_ids: list[str] | None = None,
        case_id: str | None = None,
        case_version: int | None = None,
        prompt_name: str = "challenge",
        key: str | None = None,
    ) -> ModelRunResult:
        """Run the challenge / critic prompt."""
        return self._run(
            "challenge",
            prompt_name,
            inputs,
            evidence_ids=evidence_ids,
            case_id=case_id,
            case_version=case_version,
            key=key,
        )

    # -- core run path --------------------------------------------------------

    def _run(
        self,
        method: str,
        prompt_name: str,
        inputs: dict[str, Any],
        *,
        evidence_ids: list[str] | None,
        case_id: str | None,
        case_version: int | None,
        key: str | None,
    ) -> ModelRunResult:
        prompt = (
            self._prompts.get(prompt_name, self._prompt_versions[prompt_name])
            if self._prompt_versions is not None
            else self._prompts.latest(prompt_name)
        )
        if prompt is None:
            raise ValueError(
                f"No registered prompt named {prompt_name!r}; register the prompt "
                "catalogue before running the LLMClient (no silent default)."
            )

        request = LLMRequest(
            method=method,
            prompt=prompt,
            inputs=inputs,
            evidence_ids=list(evidence_ids or []),
            case_id=case_id,
            case_version=case_version,
            temperature=0.0,
            key=key,
        )
        raw = self._backend.generate(request)

        parsed: dict[str, Any] | None = None
        outcome = "valid"
        detail: str | None = None
        try:
            parsed_obj = json.loads(raw.raw_response)
            jsonschema.validate(instance=parsed_obj, schema=prompt.response_schema)
            parsed = parsed_obj
        except json.JSONDecodeError as exc:
            outcome = "rejected"
            detail = f"Response was not valid JSON: {exc}"
        except jsonschema.ValidationError as exc:
            outcome = "rejected"
            detail = f"Response failed schema validation: {exc.message}"

        run = self._log_run(request, raw, parsed, outcome, detail)
        return ModelRunResult(
            run_id=run.run_id,
            method=method,
            validation_outcome=outcome,
            parsed=parsed,
            raw_response=raw.raw_response,
            prompt_id=prompt.prompt_id,
            model_id=raw.model_id,
        )

    def _log_run(
        self,
        request: LLMRequest,
        raw: LLMRawResult,
        parsed: dict[str, Any] | None,
        outcome: str,
        detail: str | None,
    ) -> ModelRun:
        """Record the full run (Req 19.2) and emit ``llm_run`` (Req 18.2)."""
        prompt = request.prompt
        run = ModelRun(
            run_id=str(uuid.uuid4()),
            case_id=request.case_id,
            case_version=request.case_version,
            method=request.method,
            prompt_id=prompt.prompt_id,
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            prompt_hash=prompt.content_hash,
            model_id=raw.model_id,
            model_config_json=dict(raw.model_config),
            evidence_ids=list(request.evidence_ids),
            raw_response=raw.raw_response,
            parsed_response=parsed,
            validation_outcome=outcome,
            validation_detail=detail,
            temperature=request.temperature,
        )
        if self._session is not None:
            self._session.add(run)
            self._session.flush()
        if self._audit is not None:
            self._audit.record(
                EventType.LLM_RUN,
                case_id=request.case_id,
                actor_type=ActorType.MODEL,
                actor_id=raw.model_id,
                after={
                    "run_id": run.run_id,
                    "method": request.method,
                    "prompt_id": prompt.prompt_id,
                    "prompt_version": prompt.version,
                    "prompt_hash": prompt.content_hash,
                    "model_id": raw.model_id,
                    "evidence_ids": list(request.evidence_ids),
                    "validation_outcome": outcome,
                },
                reason=detail or f"LLM {request.method} run.",
                linked_objects=[f"model_run:{run.run_id}", *request.evidence_ids],
            )
        return run
