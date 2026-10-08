"""LLMClient + model-run registry tests (task 6.1; Req 19.2-19.4)."""

from __future__ import annotations

from app.models.orm import ModelRun
from app.prompts.registry import PromptRegistry
from app.services.audit.log import AuditLog
from app.services.llm.client import FakeLLMBackend, LLMClient


def _client(db_session, backend=None):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    backend = backend or FakeLLMBackend()
    audit = AuditLog(db_session)
    return (
        LLMClient(backend, prompts=registry, session=db_session, audit=audit),
        backend,
        audit,
    )


def _valid_extraction_payload():
    return {
        "facts": [
            {
                "fact_id": "f1",
                "name": "CEO tenure",
                "topic": "management",
                "statement": "CEO since 2016.",
                "status": "unverified",
                "confidence": 0.8,
                "source_refs": [{"document_id": "d1", "page": 4}],
            }
        ]
    }


def test_valid_run_is_logged_with_full_provenance(db_session):
    client, backend, _ = _client(db_session)
    backend.register("extract", _valid_extraction_payload())

    result = client.extract(
        {"snippets": []},
        evidence_ids=["d1"],
        case_id="C1",
        case_version=3,
    )

    assert result.is_valid
    assert result.parsed is not None

    row = db_session.get(ModelRun, result.run_id)
    assert row is not None
    # Req 19.2: full run logging.
    assert row.prompt_id == "qualitative_extraction_v1.0"
    assert row.prompt_version == 1
    assert row.prompt_hash
    assert row.model_id == FakeLLMBackend.MODEL_ID
    assert row.case_version == 3
    assert row.evidence_ids == ["d1"]
    assert row.raw_response  # raw run stored (no byte-exact claim, Req 19.4)
    assert row.parsed_response is not None
    assert row.validation_outcome == "valid"
    assert row.temperature == 0.0  # temperature 0 where supported (Req 19.4)


def test_llm_run_audit_event_emitted(db_session):
    client, backend, audit = _client(db_session)
    backend.register("extract", _valid_extraction_payload())
    client.extract({}, case_id="C1")

    events = [e.event_type for e in audit.events_for_case("C1")]
    assert "llm_run" in events


def test_schema_invalid_output_is_rejected_and_logged(db_session):
    """Invalid output is rejected BEFORE use and the rejection is logged."""
    client, backend, _ = _client(db_session)
    # Missing required fields / wrong shape -> schema validation fails.
    backend.register("analyze", {"business_overview": [{"claim_id": "x"}]})

    result = client.analyze({}, case_id="C1")

    assert not result.is_valid
    assert result.validation_outcome == "rejected"
    assert result.parsed is None

    row = db_session.get(ModelRun, result.run_id)
    assert row.validation_outcome == "rejected"
    assert row.parsed_response is None
    assert row.raw_response  # raw still stored for audit
    assert row.validation_detail


def test_non_json_output_is_rejected(db_session):
    client, backend, _ = _client(db_session)
    backend.register("challenge", "this is not json")
    result = client.challenge({}, case_id="C1")
    assert not result.is_valid
    assert result.validation_outcome == "rejected"


def test_same_request_is_deterministic(db_session):
    client, backend, _ = _client(db_session)
    backend.register("extract", _valid_extraction_payload())
    r1 = client.extract({}, case_id="C1")
    r2 = client.extract({}, case_id="C1")
    assert r1.raw_response == r2.raw_response


# --- Milestone 2.1: DB-free provider path --------------------------------


def test_generate_only_performs_no_db_writes(db_session):
    client, backend, _ = _client(db_session)
    backend.register("extract", _valid_extraction_payload())
    request = client.build_request("extract", "qualitative_extraction", {}, case_id="C1")

    before = db_session.query(ModelRun).count()
    result = client.generate_only(request)
    # No model_run row written by generate_only, even uncommitted/pending.
    db_session.flush()
    after = db_session.query(ModelRun).count()

    assert before == after == 0
    assert result.is_valid
    assert result.parsed is not None
    assert result.request.prompt.name == "qualitative_extraction"


def test_log_run_persists_a_generate_only_result(db_session):
    client, backend, _ = _client(db_session)
    backend.register("analyze", {"business_overview": [], "repayment_analysis": [],
                                 "key_risks": [], "mitigants": [],
                                 "data_limitations": [], "questions_for_human": []})
    request = client.build_request("analyze", "business_analysis", {}, case_id="C1")
    result = client.generate_only(request)
    assert db_session.query(ModelRun).count() == 0

    run = client.log_run(result)
    db_session.flush()
    assert db_session.query(ModelRun).count() == 1
    assert run.validation_outcome == "valid"
    assert run.method == "analyze"


def test_generate_only_rejects_invalid_without_db_writes(db_session):
    client, backend, _ = _client(db_session)
    backend.register("challenge", "not valid json")
    request = client.build_request("challenge", "challenge", {}, case_id="C1")
    result = client.generate_only(request)
    db_session.flush()
    assert db_session.query(ModelRun).count() == 0
    assert not result.is_valid
    assert result.validation_detail


def test_generate_only_async_matches_sync(db_session):
    import asyncio

    client, backend, _ = _client(db_session)
    backend.register("extract", _valid_extraction_payload())
    request = client.build_request("extract", "qualitative_extraction", {}, case_id="C1")

    async def run():
        return await client.generate_only_async(request)

    result = asyncio.run(run())
    db_session.flush()
    assert db_session.query(ModelRun).count() == 0  # async path is also DB-free
    assert result.is_valid
    assert result.parsed == client.generate_only(request).parsed


def test_backend_generate_async_offloads_sync(db_session):
    import asyncio

    _, backend, _ = _client(db_session)
    backend.register("extract", _valid_extraction_payload())
    registry = PromptRegistry(db_session)
    prompt = registry.latest("qualitative_extraction")
    from app.services.llm.client import LLMRequest

    request = LLMRequest(method="extract", prompt=prompt)

    async def run():
        return await backend.generate_async(request)

    raw = asyncio.run(run())
    assert raw.model_id == FakeLLMBackend.MODEL_ID
