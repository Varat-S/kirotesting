"""Task 13 — controlled LIVE LLM integration for Module 1 Stages 1-3.

All provider interactions are MOCKED via an injected httpx transport; NO real
network call is ever made in these tests. They verify: the real backend
implements LLMBackend; offline mode never touches a provider; live mode requires
explicit authorisation; credentials never leak to logs/reports; valid structured
responses succeed and invalid ones fail; auth/rate-limit/timeout/budget errors
terminate cleanly; hypotheses come from the live response (not a fixture);
generated queries drive deterministic retrieval; out-of-scope citations are
rejected; the model cannot self-certify verification; all six dimensions stay
visible; there is no expensive retry loop; and offline + regression remain
intact.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.models.base import create_engine_and_session, init_db
from app.schemas.llm import CreditDimension
from app.services.analysis.module1_demo import build_demo_scenario
from app.services.analysis.module1_live import (
    BudgetedBackend,
    CallBudgetExceededError,
    LiveEvaluationConfig,
    LiveEvaluationError,
    build_source_context,
    run_live_evaluation,
)
from app.services.analysis.module1_live_report import build_live_report_model
from app.services.llm.client import LLMBackend, LLMClient, LLMRequest
from app.services.llm.providers import ProviderCallError, RealProviderBackend
from app.prompts.registry import PromptRegistry

SECRET = "fake-test-secret-NEVER-LEAK"


def _settings(**kw):
    return Settings(
        _env_file=None, debug=False, llm_provider="openai",
        llm_model="mock-model", llm_api_key=SecretStr(SECRET), **kw,
    )


def _session():
    engine, factory = create_engine_and_session("sqlite://")
    init_db(engine)
    return engine, factory


def _supplied_ids(session, tmp_path, n=2):
    """Discover genuine supplied passage ids + the primary doc id from the real
    corpus so a mock can cite real passages (not a hard-coded fixture answer)."""
    scenario = build_demo_scenario(
        "nvda-2026", session, data_root=tmp_path / "disc_d",
        output_root=tmp_path / "disc_o")
    ctx = build_source_context(scenario.corpus, LiveEvaluationConfig())
    return ctx.supplied_passage_ids[:n], scenario.primary_document_id


def _mock_transport(*, support_id, primary_id, hyp_dimension="operating_model_cost_resilience",
                    verification_status="unverified", support_ids=None,
                    include_contradictory=True, usage=True):
    """A deterministic provider transport: one hypothesis + a 3-perspective plan.

    ``support_ids`` overrides the cited supporting evidence ids (used to inject
    an out-of-scope citation). ``verification_status`` lets a test attempt a
    self-certified status (which governance must reject)."""
    cited = support_ids if support_ids is not None else [support_id]

    def handle(request):
        body = json.loads(request.content)
        fmt = body["text"]["format"]
        props = fmt["schema"]["properties"]
        if "hypotheses" in props:
            payload = {"hypotheses": [{
                "hypothesis_id": "m1", "dimension": hyp_dimension,
                "business_characteristic": "Third-party manufacturing dependence.",
                "statement": "Dependence on third-party foundries could constrain supply.",
                "credit_direction": "negative",
                "economic_reasoning": "Capacity limits can cap output and margins.",
                "potential_credit_impact": "Potential output / margin volatility.",
                "offsetting_characteristics": [],
                "supporting_evidence_ids": cited,
                "contradictory_evidence_ids": [],
                "additional_evidence_required": [], "missing_information": [],
                "calculation_refs": [], "classification": "inference",
                "verification_status": verification_status,
                "interpretation_confidence": 0.5,
            }]}
        else:
            enq = [
                ("e-sup", "supporting", "supporting_evidence",
                 "supply manufacturing dependence"),
                ("e-off", "offsetting", "offsetting_capability",
                 "supply agreements capacity reservations"),
            ]
            if include_contradictory:
                enq.insert(1, ("e-con", "contradictory", "contradictory_evidence",
                               "multiple suppliers capacity mitigation"))
            payload = {"enquiries": [
                {"enquiry_id": eid, "hypothesis_id": "m1",
                 "dimension": hyp_dimension, "requirement_kind": kind,
                 "search_intent": intent, "investigative_question": "q?",
                 "search_query": q, "rationale": "r",
                 "evidence_requirement": "req", "priority": 2,
                 "permitted_document_ids": [primary_id]}
                for (eid, kind, intent, q) in enq
            ], "planner_mode": "llm", "incomplete_coverage": []}
        resp = {"id": "resp-1", "status": "completed", "model": "mock-model",
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": json.dumps(payload)}]}]}
        if usage:
            resp["usage"] = {"input_tokens": 1000, "output_tokens": 200,
                             "total_tokens": 1200}
        return httpx.Response(200, json=resp)

    return httpx.MockTransport(handle)


def _run_live(session, tmp_path, transport, *, config=None):
    backend = RealProviderBackend(_settings(), transport=transport)
    return run_live_evaluation(
        "nvda-2026", session, backend=backend, provider="openai",
        data_root=tmp_path / "d", output_root=tmp_path / "o",
        config=config or LiveEvaluationConfig())


# ===========================================================================
# 1. Real backend implements LLMBackend.
# ===========================================================================


def test_real_backend_is_an_llm_backend():
    backend = RealProviderBackend(_settings())
    assert isinstance(backend, LLMBackend)
    assert hasattr(backend, "generate") and callable(backend.generate)


# 2. Offline mode never calls the external provider.
def test_offline_demo_uses_fake_backend_only(tmp_path):
    from app.services.analysis.module1_demo import run_demo_orchestration
    engine, factory = _session()
    try:
        with factory() as s:
            scenario = build_demo_scenario(
                "nvda-2026", s, data_root=tmp_path / "d", output_root=tmp_path / "o")
            from app.services.llm.client import FakeLLMBackend
            assert isinstance(scenario.backend, FakeLLMBackend)
            run = run_demo_orchestration(scenario, s)
            assert run.disposition.value == "blocked_pending_evidence"
    finally:
        engine.dispose()


# 3. Live mode requires explicit user authorisation (CLI gate).
def test_live_mode_requires_cost_acknowledgement(capsys):
    from app.demo import main as demo_main
    code = demo_main(["--mode", "live", "--output",
                      str(Path(tempfile.mkdtemp()) / "r")])
    assert code == 2
    assert "acknowledge-costs" in capsys.readouterr().out


def test_live_mode_without_provider_does_not_fall_back_offline(capsys, monkeypatch):
    from app.demo import main as demo_main
    for var in ["LLM_PROVIDER", "LLM_MODEL", "LLM_API_KEY"]:
        monkeypatch.delenv(var, raising=False)
    import app.core.config as cfg
    cfg.get_settings.cache_clear()
    code = demo_main(["--mode", "live", "--acknowledge-costs",
                      "--output", str(Path(tempfile.mkdtemp()) / "r")])
    cfg.get_settings.cache_clear()
    assert code == 2
    out = capsys.readouterr().out
    assert "not configured" in out
    assert "blocked_pending_evidence" not in out  # never ran offline instead


# 4. API credentials are never written into logs or reports.
def test_credentials_never_appear_in_report_or_runs(tmp_path):
    engine, factory = _session()
    try:
        with factory() as s:
            ids, primary = _supplied_ids(s, tmp_path)
        with factory() as s:
            result = _run_live(
                s, tmp_path,
                _mock_transport(support_id=ids[0], primary_id=primary))
            model = build_live_report_model(result)
            blob = json.dumps(model)
            assert SECRET not in blob
            assert "Authorization" not in blob and "Bearer" not in blob
            # Persisted model runs never contain the secret either.
            from app.models.orm import ModelRun
            for row in s.query(ModelRun).all():
                assert SECRET not in str(vars(row))
            # Section B states credentials are never exposed.
            assert "never exposed" in model["B_model_configuration"]["credentials"]
    finally:
        engine.dispose()


# 5. Valid structured provider response succeeds.
def test_valid_live_response_succeeds_through_stages_1_to_3(tmp_path):
    engine, factory = _session()
    try:
        with factory() as s:
            ids, primary = _supplied_ids(s, tmp_path)
        with factory() as s:
            result = _run_live(
                s, tmp_path,
                _mock_transport(support_id=ids[0], primary_id=primary))
            assert len(result.hypotheses.hypotheses) == 1
            assert len(result.enquiry_plan.enquiries) == 3
            assert len(result.candidate_evidence.executed) == 3
            assert result.model_calls == 2
            assert result.usage_totals["reported"] is True
            assert result.usage_totals["total_tokens"] == 2400
    finally:
        engine.dispose()


# 6. Invalid JSON / schema responses fail appropriately.
def test_invalid_schema_response_is_rejected(tmp_path):
    def handle(request):
        return httpx.Response(200, json={
            "id": "r", "status": "completed", "model": "mock-model",
            "output": [{"type": "message", "content": [
                {"type": "output_text", "text": '{"not_a_hypothesis_set": true}'}]}]})
    engine, factory = _session()
    try:
        with factory() as s:
            with pytest.raises(LiveEvaluationError):
                _run_live(s, tmp_path, httpx.MockTransport(handle))
    finally:
        engine.dispose()


# 7. Provider authentication and rate-limit errors are handled.
@pytest.mark.parametrize("status", [401, 429])
def test_provider_http_errors_handled(tmp_path, status):
    def handle(request):
        return httpx.Response(status, text=SECRET)  # body would contain a secret
    engine, factory = _session()
    try:
        with factory() as s:
            with pytest.raises(LiveEvaluationError):
                _run_live(s, tmp_path, httpx.MockTransport(handle))
            from app.models.orm import ModelRun
            for row in s.query(ModelRun).all():
                assert SECRET not in str(vars(row))
    finally:
        engine.dispose()


# 8. Timeout and budget exhaustion terminate cleanly.
def test_timeout_terminates_cleanly(tmp_path):
    def handle(request):
        raise httpx.ReadTimeout("boom", request=request)
    engine, factory = _session()
    try:
        with factory() as s:
            with pytest.raises(LiveEvaluationError):
                _run_live(s, tmp_path, httpx.MockTransport(handle))
    finally:
        engine.dispose()


def test_budget_exhaustion_fails_visibly(tmp_path):
    engine, factory = _session()
    try:
        with factory() as s:
            ids, primary = _supplied_ids(s, tmp_path)
        with factory() as s:
            # Budget of 1 permits generation but not planning -> fails before the
            # 2nd call, with no retry loop.
            with pytest.raises((CallBudgetExceededError, LiveEvaluationError)):
                _run_live(s, tmp_path,
                          _mock_transport(support_id=ids[0], primary_id=primary),
                          config=LiveEvaluationConfig(max_model_calls=1))
    finally:
        engine.dispose()


# 9. Hypotheses come from live-response content, not a hard-coded fixture.
def test_hypotheses_reflect_live_response_content(tmp_path):
    engine, factory = _session()
    try:
        with factory() as s:
            ids, primary = _supplied_ids(s, tmp_path)
        with factory() as s:
            # Script a DISTINCT dimension/direction and assert it propagates.
            result = _run_live(
                s, tmp_path,
                _mock_transport(support_id=ids[0], primary_id=primary,
                                hyp_dimension="competitive_durability"))
            h = result.hypotheses.hypotheses[0]
            assert h.dimension.value == "competitive_durability"
            assert h.supporting_evidence_ids == [ids[0]]  # the cited real passage
    finally:
        engine.dispose()


# 10. Generated queries feed the actual retrieval service.
def test_generated_queries_drive_real_retrieval(tmp_path):
    engine, factory = _session()
    try:
        with factory() as s:
            ids, primary = _supplied_ids(s, tmp_path)
        with factory() as s:
            result = _run_live(
                s, tmp_path,
                _mock_transport(support_id=ids[0], primary_id=primary))
            queries = {ex.enquiry.search_query
                       for ex in result.candidate_evidence.executed}
            assert "supply manufacturing dependence" in queries
            # Retrieval returned real corpus passages for at least one enquiry.
            corpus_ids = {p.passage_id for p in result.corpus.passages}
            hit = [ex for ex in result.candidate_evidence.executed
                   if ex.result.ranked_passages]
            assert hit
            for ex in hit:
                for rp in ex.result.ranked_passages:
                    assert rp.passage.passage_id in corpus_ids
    finally:
        engine.dispose()


# 11. Out-of-scope citations are rejected.
def test_out_of_scope_citation_is_rejected(tmp_path):
    engine, factory = _session()
    try:
        with factory() as s:
            ids, primary = _supplied_ids(s, tmp_path)
        with factory() as s:
            # Cite a passage id that was NOT supplied to the model.
            with pytest.raises(LiveEvaluationError):
                _run_live(
                    s, tmp_path,
                    _mock_transport(support_id=ids[0], primary_id=primary,
                                    support_ids=["not-a-supplied-passage"]))
    finally:
        engine.dispose()


# 12. Model output cannot self-certify verification.
def test_model_cannot_self_certify_supported(tmp_path):
    engine, factory = _session()
    try:
        with factory() as s:
            ids, primary = _supplied_ids(s, tmp_path)
        with factory() as s:
            # The model claims 'supported'; generation governance must reject it.
            with pytest.raises(LiveEvaluationError):
                _run_live(
                    s, tmp_path,
                    _mock_transport(support_id=ids[0], primary_id=primary,
                                    verification_status="supported"))
    finally:
        engine.dispose()


# 13. All six dimensions remain visible for coverage review.
def test_all_six_dimensions_visible_in_report(tmp_path):
    engine, factory = _session()
    try:
        with factory() as s:
            ids, primary = _supplied_ids(s, tmp_path)
        with factory() as s:
            result = _run_live(
                s, tmp_path,
                _mock_transport(support_id=ids[0], primary_id=primary))
            model = build_live_report_model(result)
            c = model["C_autonomous_hypotheses"]
            covered = set(c["dimensions_covered"])
            unassessed = set(c["dimensions_unassessed"])
            assert covered | unassessed == {d.value for d in CreditDimension}
            assert len(covered | unassessed) == 6
            # A silent dimension is UNASSESSED, never NOT_APPLICABLE.
            assert "operating_model_cost_resilience" in covered
            assert len(unassessed) == 5
    finally:
        engine.dispose()


# 14. No automatic expensive retry loops.
def test_no_automatic_retry_on_provider_error(tmp_path):
    calls = {"n": 0}

    def handle(request):
        calls["n"] += 1
        return httpx.Response(500, text="server error")
    engine, factory = _session()
    try:
        with factory() as s:
            with pytest.raises(LiveEvaluationError):
                _run_live(s, tmp_path, httpx.MockTransport(handle))
        # Exactly one provider attempt was made (no retry loop).
        assert calls["n"] == 1
    finally:
        engine.dispose()


def test_budgeted_backend_hard_caps_calls():
    class _Counter(LLMBackend):
        model_id = "counter"

        def __init__(self):
            self.n = 0

        def generate(self, request):
            self.n += 1
            from app.services.llm.client import LLMRawResult
            return LLMRawResult(raw_response="{}", model_id="counter")

    inner = _Counter()
    b = BudgetedBackend(inner, max_calls=1)

    class _P:
        prompt_id = "p"
        template = "t"
        response_schema = {"type": "object"}
    req = LLMRequest("analyze", _P())
    b.generate(req)
    with pytest.raises(CallBudgetExceededError):
        b.generate(req)
    assert inner.n == 1  # the second call never reached the inner backend


# 15. Existing offline demonstration remains functional.
def test_offline_demo_still_runs(tmp_path, capsys):
    from app.demo import main as demo_main
    out = tmp_path / "offline"
    code = demo_main(["--fixture", "nvda-2026", "--output", str(out)])
    assert code == 0
    assert (out / "module1_review.json").exists()
    assert "OFFLINE EVALUATION" in capsys.readouterr().out
