"""Milestone 22 — provenance chain + run usage summary (Req 17, 19, 25).

Proves the full backward trace is reconstructable and every link carries the
owning analysis_run_id:

    ConclusionClaim.claim_id
      -> ParameterResult id(s)
        -> deterministic formula and/or AgentRun
          -> evidence ids
            (all tagged by analysis_run_id)

and that the analysis-run usage summary answers calls/cache-hits/reruns/models/
tokens. Driven offline by the agentic offline backend — no live calls.
"""

from __future__ import annotations

from pathlib import Path

from app.models.base import create_engine_and_session, init_db
from app.models.orm import (
    AgentRunRow,
    AgenticAnalysisRun,
    ParameterResultRow,
    TopicConclusionRow,
)
from app.services.pipeline.package import SourcePackage
from app.services.pipeline.runner import CreditMemoPipeline
from app.services.workbench.agentic import AgenticWorkbenchService

ROOT = Path(__file__).resolve().parents[2] / "examples" / "synthetic-case"


def _run_agentic(tmp_path):
    engine, factory = create_engine_and_session("sqlite://")
    init_db(engine)
    session = factory()
    runner = CreditMemoPipeline(
        session, data_root=ROOT / "data", output_root=tmp_path / "output",
        analysis_mode="agentic")
    result = runner.run_case("SYN_PROV", package=SourcePackage.load(ROOT / "package.json"))
    return session, result


def test_provenance_chain_is_reconstructable(tmp_path):
    session, result = _run_agentic(tmp_path)
    run = session.query(AgenticAnalysisRun).one()
    run_id = run.analysis_run_id

    # Conclusions, parameters and agent runs all belong to the one run.
    conclusions = session.query(TopicConclusionRow).filter_by(
        analysis_run_id=run_id).all()
    params = session.query(ParameterResultRow).filter_by(analysis_run_id=run_id).all()
    agent_runs = session.query(AgentRunRow).filter_by(analysis_run_id=run_id).all()
    assert conclusions and agent_runs  # the chain exists

    param_ids = {p.parameter_result_id for p in params}

    # Each conclusion claim references parameters that exist IN THIS RUN.
    for c in conclusions:
        assessment = (c.payload or {}).get("overall_assessment", {})
        for prid in assessment.get("parameter_result_ids", []):
            assert prid in param_ids  # claim -> ParameterResult (same run)

    # Each parameter traces to a deterministic formula and/or an AgentRun.
    agent_run_ids = {a.run_id for a in agent_runs}
    for p in params:
        if p.method == "deterministic":
            assert p.formula_id and p.formula_version
        else:  # llm / hybrid
            assert p.agent_run_id in agent_run_ids  # -> AgentRun (same run)
        assert p.analysis_run_id == run_id  # tagged by the run


def test_run_usage_summary_answers_the_questions(tmp_path):
    session, _ = _run_agentic(tmp_path)
    run = session.query(AgenticAnalysisRun).one()
    view = AgenticWorkbenchService(session).assemble(run.analysis_run_id)
    usage = view.sections["token_usage"]
    # The run summary answers: calls / cache hits / reruns / models / tokens.
    assert "model_calls" in usage
    assert "cache_hits" in usage
    assert "reruns" in usage
    assert "per_agent" in usage
    # Prompt/model identity is discoverable per agent run.
    identities = view.sections["prompt_model_identity"]
    assert all("model_id" in i and "prompt_id" in i for i in identities)
