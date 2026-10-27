"""FEAT-001 — error-observability regression for ``run_single_pass`` (Task A).

A rejected single-pass specialist response used to be reported only as
``model output rejected: rejected`` — the bare validation OUTCOME, which hides
WHY the response was rejected. The fix surfaces the sanitized upstream
``validation_detail`` instead, so the four distinct failure causes stay
distinguishable in the arm failure string:

* a PROVIDER rejection (e.g. the Vertex HTTP 400 ``INVALID_ARGUMENT`` case),
  carried verbatim from :class:`ProviderCallError.public_message` via
  :meth:`LLMClient._sanitize_provider_error`;
* a local JSON-PARSE failure (``Response was not valid JSON ...``);
* a local SCHEMA-VALIDATION failure (``Response failed schema validation ...``);
* other provider exceptions, without their potentially sensitive messages;

the last two set by :meth:`LLMClient._validate_raw` against the UNCHANGED
canonical ``BO_M3_JSON_SCHEMA``.

Everything here is OFFLINE: each "backend" is a deterministic stand-in that
raises or returns scripted bytes. No provider and no network are touched. The
tests also assert that NO secret / raw request payload leaks into the failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.orm import Case
from app.services.bo.comparison import prepare_specialist, run_single_pass
from app.services.bo.roster_v3 import V3_SPEC_BY_CODE
from app.services.llm.client import LLMBackend, LLMRawResult, LLMRequest
from app.services.llm.providers import ProviderCallError
from tests import bo_helpers as h
from tests.integration.test_bo_m3_live_readiness import _live_valid_response

# A sentinel that must NEVER appear in any sanitized failure string. It stands
# in for anything private — a credential, an API key, or the request body the
# model was sent.
SECRET = "sk-live-TOPSECRET-should-never-surface"


@pytest.fixture()
def m3_prep(db_session):
    """A prepared Module 3 specialist over the synthetic NFH fixture (offline)."""
    db_session.add(Case(case_id=h.CASE, status="open"))
    db_session.flush()
    return prepare_specialist(
        db_session, case_id=h.CASE, evidence=h.evidence_snapshot(),
        metrics=h.metrics(), spec=V3_SPEC_BY_CODE["M3"])


class _RaisingBackend(LLMBackend):
    """A backend whose ``generate`` raises a SANITIZED provider error.

    It holds a secret internally (never embedded in ``public_message``) so the
    test can prove the secret does not leak into the arm failure.
    """

    model_id = "fake-vertex-under-test"

    def __init__(self, public_message: str) -> None:
        self._public_message = public_message

    def generate(self, request: LLMRequest) -> LLMRawResult:
        # The private material (``SECRET``) stays local; only the pre-sanitized
        # public message is raised, mirroring providers_vertex sanitation.
        assert SECRET not in self._public_message
        raise ProviderCallError(self._public_message)


class _RawBackend(LLMBackend):
    """A backend that returns a fixed RAW string (schema-invalid or non-JSON)."""

    model_id = "fake-vertex-under-test"

    def __init__(self, raw: str) -> None:
        self._raw = raw

    def generate(self, request: LLMRequest) -> LLMRawResult:
        return LLMRawResult(raw_response=self._raw, model_id=self.model_id)


def test_provider_rejection_detail_survives_into_failure(db_session, m3_prep):
    # Mirrors the live Vertex HTTP 400 INVALID_ARGUMENT case: the provider
    # rejected the request and the sanitized public_message is surfaced verbatim.
    message = "Vertex request rejected (HTTP 400 INVALID_ARGUMENT)."
    arm = run_single_pass(db_session, m3_prep,
                          backend=_RaisingBackend(message))

    assert arm.validation is None
    assert arm.parsed is None
    assert arm.failure == f"model output rejected: {message}"
    # The bare outcome word is NOT what gets reported anymore.
    assert arm.failure != "model output rejected: rejected"
    assert SECRET not in arm.failure


def test_non_json_response_detail_survives_into_failure(db_session, m3_prep):
    # A response that is not JSON at all -> local JSON-parse rejection.
    arm = run_single_pass(db_session, m3_prep,
                          backend=_RawBackend("this is not json at all"))

    assert arm.parsed is None
    assert arm.failure is not None
    assert arm.failure.startswith("model output rejected: ")
    assert "Response was not valid JSON" in arm.failure
    assert SECRET not in arm.failure


def test_schema_violation_detail_survives_into_failure(db_session, m3_prep):
    # Well-formed JSON that violates the canonical BO_M3_JSON_SCHEMA (the live
    # response schema for the m3_corporate_management prompt) -> local schema
    # rejection, distinct from both the provider and JSON-parse cases.
    arm = run_single_pass(db_session, m3_prep,
                          backend=_RawBackend('{"not": "a valid m3 response"}'))

    assert arm.parsed is None
    assert arm.failure is not None
    assert arm.failure.startswith("model output rejected: ")
    assert "Response failed schema validation" in arm.failure
    assert SECRET not in arm.failure


def test_the_three_rejection_causes_are_distinguishable(db_session, m3_prep):
    # The whole point of the fix: the three sanitized details are DIFFERENT, so
    # an analyst can tell a provider rejection from a local parse/schema failure.
    provider = run_single_pass(
        db_session, m3_prep,
        backend=_RaisingBackend("Vertex request rejected (HTTP 400 INVALID_ARGUMENT).")).failure
    non_json = run_single_pass(
        db_session, m3_prep, backend=_RawBackend("not json")).failure
    bad_schema = run_single_pass(
        db_session, m3_prep, backend=_RawBackend('{"not": "valid"}')).failure

    assert provider != non_json != bad_schema != provider
    assert all(SECRET not in f for f in (provider, non_json, bad_schema))


class _HttpFailure(Exception):
    def __init__(self, code=400, status="INVALID_ARGUMENT"):
        self.code = code
        self.status = status
        super().__init__(f"Bearer {SECRET}; request/response body: {SECRET}")


def _vertex_backend(*, error=None, raw=None):
    from app.core.config import Settings
    from app.services.llm.providers_vertex import VertexProviderBackend

    calls = []

    def generate_content(**kwargs):
        calls.append(kwargs)
        if error is not None:
            raise error
        return SimpleNamespace(text=raw, usage_metadata=None)

    settings = Settings(
        _env_file=None, llm_provider="vertex", vertex_api_key=None,
        google_cloud_project="offline-project", google_cloud_location="global",
        vertex_model_narrow="gemini-mocked", llm_max_input_bytes=1_500_000)
    backend = VertexProviderBackend(settings, m3_draft_contract=False, client_factory=lambda: SimpleNamespace(
        models=SimpleNamespace(generate_content=generate_content)))
    return backend, calls


@pytest.mark.parametrize("code,status,label", [
    (400, "INVALID_ARGUMENT", "HTTP 400 INVALID_ARGUMENT"),
    (429, "RESOURCE_EXHAUSTED", "HTTP 429 RESOURCE_EXHAUSTED"),
    (503, "UNAVAILABLE", "HTTP 503 UNAVAILABLE"),
    ("400", SECRET, "HTTP 400"),
])
def test_actual_vertex_error_path_retains_only_allowlisted_metadata(
        db_session, m3_prep, code, status, label):
    backend, calls = _vertex_backend(error=_HttpFailure(code, status))
    arm = run_single_pass(db_session, m3_prep, backend=backend)
    assert label in arm.failure
    assert SECRET not in arm.failure
    assert SECRET not in arm.calls[0]["error"]
    assert len(calls) == 1
    assert calls[0]["config"]["http_options"]["retry_options"]["attempts"] == 1


@pytest.mark.parametrize("error", [ValueError(SECRET), RuntimeError(SECRET)])
def test_vertex_non_http_exceptions_do_not_leak(db_session, m3_prep, error):
    backend, calls = _vertex_backend(error=error)
    arm = run_single_pass(db_session, m3_prep, backend=backend)
    assert "Vertex call failed" in arm.failure
    assert "HTTP" not in arm.failure
    assert SECRET not in arm.failure
    assert SECRET not in arm.calls[0]["error"]
    assert len(calls) == 1


@pytest.mark.parametrize("error", [ValueError(SECRET), RuntimeError(SECRET)])
def test_other_backend_exception_paths_are_sanitized(db_session, m3_prep, error):
    class BrokenBackend(LLMBackend):
        model_id = "offline-broken"

        def generate(self, request):
            raise error

    arm = run_single_pass(db_session, m3_prep, backend=BrokenBackend())
    assert type(error).__name__ in arm.failure
    assert SECRET not in arm.failure
    assert SECRET not in arm.calls[0]["error"]


@pytest.mark.parametrize("violation", ["enum", "type", "extra_property", "missing_key"])
def test_schema_diagnostics_never_echo_bad_response_values(db_session, m3_prep, violation):
    response = _live_valid_response()
    observation = response["m3_assessment"]["management_assessments"][0]
    if violation == "enum":
        observation["dimension"] = SECRET
    elif violation == "type":
        observation["legal_name"] = {SECRET: SECRET}
    elif violation == "extra_property":
        observation[SECRET] = SECRET
    else:
        del observation["role"]
        observation["legal_name"] = SECRET
    arm = run_single_pass(db_session, m3_prep, backend=_RawBackend(json.dumps(response)))
    # An invalid array item is quarantined rather than failing the response; the
    # repair report names paths and rules only, and the item never passes on.
    repairs = arm.metadata["draft_repairs"]
    assert {"action": "quarantined_item", "path": "/m3_assessment/management_assessments/[]"}.items()         <= repairs[0].items()
    assert SECRET not in json.dumps(arm.metadata, default=str)
    assert SECRET not in json.dumps(arm.parsed)
    assert SECRET not in (arm.failure or "")


def test_unrepairable_schema_violation_fails_without_echoing_values(db_session, m3_prep):
    # Outside any array item nothing can be isolated: the whole answer fails.
    response = _live_valid_response()
    response["m3_assessment"]["borrower_identification"]["status"] = SECRET
    arm = run_single_pass(db_session, m3_prep, backend=_RawBackend(json.dumps(response)))
    assert "Response failed schema validation" in arm.failure
    assert "schema_path=" in arm.failure
    assert SECRET not in arm.failure
    assert SECRET not in json.dumps(arm.metadata, default=str)


def test_missing_assessment_is_rejected_without_backfill(db_session, m3_prep):
    arm = run_single_pass(db_session, m3_prep, backend=_RawBackend(
        '{"parameters": [], "hypotheses": []}'))
    assert arm.parsed is None
    assert arm.validation is None
    assert "omitted m3_assessment" in arm.failure


@pytest.mark.parametrize("mode,expected", [
    ("http", "HTTP 400 INVALID_ARGUMENT"),
    ("json", "Response was not valid JSON"),
    ("schema", "Response failed schema validation"),
    ("exception", "Vertex call failed"),
    ("missing_assessment", "omitted m3_assessment"),
])
def test_standalone_preserves_safe_failure_in_manifest_and_database(db_session, mode, expected):
    from app.models.orm import AgenticAnalysisRun
    from app.services.bo.m3_standalone import run_m3

    error = (_HttpFailure() if mode == "http" else
             ValueError(SECRET) if mode == "exception" else None)
    response = _live_valid_response()
    # Outside any array item, so item-level repair cannot isolate it.
    response["m3_assessment"]["borrower_identification"]["status"] = SECRET
    raw = (SECRET if mode == "json" else
           '{"parameters": [], "hypotheses": []}' if mode == "missing_assessment" else
           json.dumps(response))
    backend, calls = _vertex_backend(error=error, raw=raw)
    doc = json.loads(Path("data/test_inputs/hca_simulated_corporate_structure.json").read_text())
    result = run_m3(db_session, case_id=h.CASE, evidence=h.evidence_snapshot(),
                    metrics=h.metrics(), live_backend=backend,
                    corporate_structure_document=doc)
    assert expected in result.specialist_failure
    assert SECRET not in result.specialist_failure
    assert result.run_manifest["execution"]["specialist_failure"] == result.specialist_failure
    assert SECRET not in json.dumps(result.run_manifest)
    row = db_session.get(AgenticAnalysisRun, result.analysis_run_id)
    assert row.failure_state == result.specialist_failure
    assert row.status == "partially_failed"
    assert result.governed_assessment is None
    assert result.specialist_validated is False
    assert result.m3_corporate_structure is not None
    assert len(calls) == 1


def test_mocked_vertex_success_preserves_exact_hca_fixture_and_validation(db_session):
    from app.services.bo.m3_standalone import run_m3

    response = _live_valid_response()
    backend, calls = _vertex_backend(raw=json.dumps(response))
    doc = json.loads(Path("data/test_inputs/hca_simulated_corporate_structure.json").read_text())
    result = run_m3(db_session, case_id=h.CASE, evidence=h.evidence_snapshot(),
                    metrics=h.metrics(), live_backend=backend,
                    corporate_structure_document=doc)
    assert len(calls) == 1
    facts = json.loads(calls[0]["contents"])["corporate_structure"]
    assert len(facts["nodes"]) == 14
    assert len(facts["links"]) == 13
    assert len(facts["guarantor_node_ids"]) == 5
    assert facts["source"]["is_simulated"] is True
    assert facts["source"]["simulation_disclaimer"] == doc["simulation_disclaimer"]
    assert result.run_manifest["execution"]["structure_in_specialist_context"] is True
    assert result.specialist_validated is True
    assert result.governed_assessment is not None
    # Canonical validity alone does not establish uncited management assertions.
    observation = result.governed_assessment["management_assessments"][0]
    assert observation["disposition"] == "unverified"
    assert observation["supporting_evidence_ids"] == []
    assert result.m3_corporate_structure["schema_version"] == "2.0"
    company = result.m3_corporate_structure["companies"]["HCA"]
    assert len(company["nodes"]) == 14
    assert len(company["ownership_links"]) == 13
    assert sum(node["is_guarantor"] for node in company["nodes"]) == 5
