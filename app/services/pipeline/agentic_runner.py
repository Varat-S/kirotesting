"""Agentic downstream analysis orchestrator (Milestone 21.1).

The thin ``CreditMemoPipeline._ai`` façade delegates here under
``analysis_mode="agentic"``. This orchestrator runs the bounded multi-agent
chain AFTER the human-reviewed ``CanonicalEvidenceSnapshot`` boundary and returns
the SAME ``(output, runs, grounding, qualitative)`` tuple shape the legacy path
returns, so the pipeline's FinalCaseSnapshot assembly is unchanged.

It opens exactly one ``AgenticAnalysisRun`` (Remediation 1), computes
deterministic parameters, runs the narrow Business/Financial agents through the
DAG executor, promotes validated ParameterResults, computes deterministic
Business/Financial/Obligor scores, runs the topic orchestrators, and synthesizes
a draft analysis from the accepted conclusions. The output carries the agentic
provenance (analysis_run_id, scores, conclusions) for the final snapshot.

This is intentionally a bounded, offline-testable composition of the primitives
built in M1-M18; it never performs official arithmetic in an LLM and never lets
an LLM assign a score.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.bootstrap import bootstrap_config
from app.core.config_registry import ConfigRegistry
from app.models.orm import AgenticAnalysisRun as RunRow
from app.prompts.registry import PromptRegistry
from app.schemas.agentic import (
    AgentTask,
    AgenticAnalysisRun,
    ScoreKind,
    Topic,
)
from app.services.agents.executor import DagExecutor, PreparedTask
from app.services.agents.registry import default_registry
from app.core.hashing import content_hash
from app.prompts.agent_schemas import ORCHESTRATOR_JSON_SCHEMA
from app.services.agents.router import ROUTER_VERSION, EvidenceRouter, RouterInputs
from app.services.agents.runtime import AgentRuntime
from app.services.agents.validation import DeterministicValidator
from app.services.llm.client import LLMClient
from app.services.orchestration.benchmark_context import (
    build_benchmark_context,
    build_benchmark_parameter_results,
    context_evidence_ids,
    known_parameter_values,
    validate_benchmark_narrative,
)
from app.services.orchestration.business import build_business_orchestrator_input
from app.services.orchestration.conclusions import (
    ConclusionProvenanceError,
    ConclusionStore,
    build_topic_conclusion,
    validate_conclusion_provenance,
)
from app.services.orchestration.financial import build_financial_orchestrator_input
from app.services.orchestration.sector_observations import (
    dimensions_for_agent,
    sector_context_for_agent,
    validate_sector_observations,
)
from app.services.parameters.metric_adapter import metric_to_parameter
from app.services.orchestration.promotion import (
    ParameterPromoter,
    PromotionRejected,
    ValidatedAgentOutput,
    promote_narrow_output,
)
from app.services.llm.client import FakeLLMBackend
from app.services.scoring.engine import ScoringConfig, ScoringEngine


def agentic_offline_backend() -> FakeLLMBackend:
    """A fake backend with safe-empty responses for every agent task type.

    Lets the agentic stage execute offline without inventing content: narrow /
    extraction agents return no parameters, orchestrators return a minimal
    assessment, challengers return no challenges, mitigants return no proposals.
    """
    backend = FakeLLMBackend()
    for tt in ("business_model", "competition_pricing",
               "customer_supplier_contract", "management_governance",
               "ma_capex_execution", "regulatory_material_events",
               "ebitda_adjustments", "cashflow_working_capital",
               "debt_liquidity_terms", "covenant_extraction",
               "accounting_audit_quality", "forecast_stress_drivers",
               "facility_terms", "collateral_security_guarantee",
               "legal_undertakings_conditions"):
        backend.register(tt, {"parameters": []})
    backend.register("mitigant", {"proposals": []})
    backend.register("orchestrate", {"overall_assessment": {
        "claim_id": "a", "category": "assessment",
        "text": "No overriding signal from supplied evidence."}})
    backend.register("challenge", {"challenges": []})
    return backend


# The topic each narrow/early agent belongs to (orchestrators/challengers skipped
# in this bounded composition; the full challenge loop is M13 and runs on demand).
_NARROW_TASK_TYPES = {"orchestrate", "challenge", "mitigant"}


class AgenticAnalysisOrchestrator:
    """Run the agentic downstream analysis and return the legacy tuple shape."""

    def __init__(self, session: Session, *, backend=None, prompt_versions=None) -> None:
        self._session = session
        self._backend = backend
        self._prompt_versions = prompt_versions

    def run(
        self, case_id, evidence, metrics, trends, benchmarks, completeness,
        configs, escalation, audit, flag, sector_context=None,
    ):
        """Run the agentic analysis.

        ``sector_context`` is the deterministic sector-benchmark payload (or
        None). When it is a COMPLETED benchmark, its numbers become accepted
        deterministic ParameterResults of this run, the existing Financial
        Orchestrator receives a bounded ``benchmark_context`` and the existing
        narrow agents receive their sector risk dimensions. Official scores are
        computed from exactly the same inputs as without it.
        """
        registry = default_registry()
        prompts = PromptRegistry(self._session)
        prompts.register_catalogue()
        client = LLMClient(
            self._backend or agentic_offline_backend(),
            prompts=prompts, session=self._session,
            prompt_versions=self._prompt_versions,
        )
        runtime = AgentRuntime(client)
        snapshot_version = evidence.snapshot_version

        # Scoring config (reuse bootstrapped config; bootstrap if needed).
        cfg_registry = ConfigRegistry(self._session)
        if cfg_registry.latest("scoring") is None:
            bootstrap_config(cfg_registry)
        scoring_cfg = ScoringConfig.from_registry(cfg_registry)
        scorer = ScoringEngine(scoring_cfg)

        # Open exactly one analysis run (Remediation 1).
        analysis_run_id = f"ar_{uuid.uuid4().hex[:16]}"
        run_model = AgenticAnalysisRun(
            analysis_run_id=analysis_run_id, case_id=case_id,
            evidence_snapshot_version=snapshot_version,
            started_at=datetime.now(timezone.utc), router_version=ROUTER_VERSION,
            agent_registry_version=registry.version,
            agent_registry_hash=registry.registry_hash(),
            scoring_config_version=scoring_cfg.version,
            scoring_config_hash=scoring_cfg.content_hash,
        )
        self._session.add(RunRow(
            analysis_run_id=analysis_run_id, case_id=case_id,
            evidence_snapshot_version=snapshot_version, status="created",
            router_version=ROUTER_VERSION, agent_registry_version=registry.version,
            agent_registry_hash=registry.registry_hash(),
            scoring_config_version=scoring_cfg.version,
            scoring_config_hash=scoring_cfg.content_hash,
        ))
        self._session.flush()

        # --- run the narrow Business/Financial/Structuring agents ---
        promoter = ParameterPromoter(self._session)
        evidence_ids = [d["document_id"] for d in evidence.documents]
        promoted: list = []

        # The deterministic metrics already computed for the case enter the run
        # as accepted deterministic ParameterResults via the existing adapter,
        # so the run owns its numeric inputs and they can be quoted by id.
        # They are NOT passed to the scoring engine: the scoring config's
        # ``interest_coverage`` / ``liquidity`` dimensions are not defined the
        # same way as the like-named metrics (the metric ``liquidity`` is a
        # currency amount, the scoring band a ratio), so wiring them in would
        # change official scores and needs its own specification.
        deterministic_params = []
        for _, metric in sorted(metrics.items()):
            pr = metric_to_parameter(metric, analysis_run_id=analysis_run_id)
            promoter.persist(pr, case_id=case_id, snapshot_version=snapshot_version)
            deterministic_params.append(pr)

        # --- sector benchmark context (only for a completed sector benchmark) ---
        sector = (
            sector_context
            if sector_context and sector_context.get("status") == "completed"
            else None
        )
        benchmark_prs: list = []
        benchmark_ctx = None
        narrative_ids: set[str] = set()
        router_inputs = None
        # Mutable results of the sector-aware steps, read after the DAG run.
        sector_state = {"financial_score": None, "conclusion": None, "outcome": None,
                        "observations": [], "rejected_observations": []}
        if sector is not None:
            benchmark_prs = build_benchmark_parameter_results(
                sector, analysis_run_id=analysis_run_id)
            for pr in benchmark_prs:
                promoter.persist(pr, case_id=case_id, snapshot_version=snapshot_version)
            benchmark_ctx = build_benchmark_context(sector)
            router_inputs = RouterInputs.from_snapshot(
                evidence, analysis_run_id=analysis_run_id)
            narrative_ids = {
                p["evidence_id"] for p in router_inputs.narrative_evidence
                if p.get("evidence_id")
            }

        def build_financial_orchestrator(agent):
            """The existing Financial Orchestrator, given the bounded context."""
            score = scorer.score_financial(promoted, analysis_run_id=analysis_run_id)
            sector_state["financial_score"] = score
            orchestrator_input = build_financial_orchestrator_input(
                analysis_run_id=analysis_run_id,
                parameters=[*promoted, *benchmark_prs],
                financial_score=score,
                benchmark_context=benchmark_ctx,
            )
            inputs = orchestrator_input.as_prompt_inputs()
            return PreparedTask(
                task=AgentTask(
                    agent_id=agent.agent_id, analysis_run_id=analysis_run_id,
                    topic=agent.topic, task_type=agent.task_type,
                    prompt_name=agent.agent_id, response_schema_ref=agent.agent_id,
                    model_tier=agent.model_tier, case_id=case_id,
                    snapshot_version=snapshot_version,
                    packet_ref=content_hash(inputs)),
                inputs=inputs,
                agent_definition_hash=agent.definition_hash(),
                evidence_ids=context_evidence_ids(benchmark_ctx),
            )

        def build(agent, context):
            if agent.agent_id == "financial_orchestrator" and benchmark_ctx is not None:
                return build_financial_orchestrator(agent)
            if agent.task_type in _NARROW_TASK_TYPES:
                return None  # bounded composition: narrow agents only here
            inputs = {"canonical_evidence": {"facts": evidence.facts}}
            if sector is not None:
                routed = (
                    EvidenceRouter().build_packet(agent.routing, router_inputs)
                    .narrative_evidence if agent.routing is not None else []
                )
                sector_inputs = sector_context_for_agent(sector, agent.agent_id, routed)
                if sector_inputs is not None:
                    inputs["sector_context"] = sector_inputs
            return PreparedTask(
                task=AgentTask(
                    agent_id=agent.agent_id, analysis_run_id=analysis_run_id,
                    topic=agent.topic, task_type=agent.task_type,
                    prompt_name=agent.agent_id, response_schema_ref=agent.agent_id,
                    model_tier=agent.model_tier, case_id=case_id,
                    snapshot_version=snapshot_version,
                    packet_ref=(content_hash(inputs["sector_context"])
                                if "sector_context" in inputs else "packet")),
                inputs=inputs,
                agent_definition_hash=agent.definition_hash(),
                evidence_ids=evidence_ids,
            )

        def on_orchestrator_result(agent, parsed, context):
            """Gate the orchestrator's benchmark interpretation deterministically.

            Accepted only if: the schema holds; every cited evidence id is
            admitted; every quoted number equals an accepted ParameterResult;
            no number is stated without such a quote; no score/band/rating is
            assigned; and every material claim has accepted provenance. A
            rejected answer promotes nothing and the deterministic default
            conclusion is used instead.
            """
            accepted_results = [*promoted, *benchmark_prs]
            validator = DeterministicValidator(
                admitted_evidence_ids=(set(evidence_ids) | narrative_ids
                                       | set(context_evidence_ids(benchmark_ctx))),
                known_parameters=known_parameter_values(accepted_results),
            )
            outcome = validator.validate(parsed, schema=ORCHESTRATOR_JSON_SCHEMA)
            issues = [f"{i.check.value}: {i.detail}" for i in outcome.issues]
            issues.extend(validate_benchmark_narrative(parsed))
            conclusion = None
            if not issues:
                conclusion = build_topic_conclusion(
                    Topic.FINANCIAL, parsed, analysis_run_id=analysis_run_id,
                    orchestrator_run_id=context.run_ids[agent.agent_id],
                    score_reference=sector_state["financial_score"].score_id)
                try:
                    validate_conclusion_provenance(
                        conclusion,
                        accepted_parameter_ids={
                            r.parameter_result_id for r in accepted_results})
                except ConclusionProvenanceError as exc:
                    issues.append(f"provenance: {exc}")
                    conclusion = None
            sector_state["conclusion"] = conclusion
            sector_state["quoted"] = _quoted_by_claim(parsed) if conclusion else {}
            sector_state["outcome"] = {
                "status": "accepted" if conclusion is not None else "rejected",
                "agent_run_id": context.run_ids.get(agent.agent_id),
                "issues": issues,
            }

        def on_sector_observations(agent, parsed, context):
            allowed = {d["dimension_id"] for d in dimensions_for_agent(sector, agent.agent_id)}
            accepted, issues = validate_sector_observations(
                parsed, allowed_dimensions=allowed,
                admitted_evidence_ids=set(evidence_ids) | narrative_ids)
            run_id = context.run_ids.get(agent.agent_id)
            for observation in accepted:
                sector_state["observations"].append(
                    {**observation, "agent_id": agent.agent_id, "agent_run_id": run_id})
            if issues:
                sector_state["rejected_observations"].append(
                    {"agent_id": agent.agent_id, "agent_run_id": run_id, "issues": issues})

        def on_result(agent, parsed, context):
            if agent.agent_id == "financial_orchestrator":
                on_orchestrator_result(agent, parsed, context)
                return
            validator = DeterministicValidator.for_agent(
                _packet(agent, analysis_run_id, case_id, snapshot_version,
                        evidence_ids),
                agent,
            )
            outcome = validator.validate(parsed)
            if not outcome.valid:
                return  # invalid output never promotes (gate enforced below too)
            try:
                validated = ValidatedAgentOutput.gate(
                    agent, parsed, outcome, analysis_run_id=analysis_run_id,
                    agent_run_id=context.run_ids[agent.agent_id])
            except PromotionRejected:
                return
            for pr in promote_narrow_output(validated):
                promoter.persist(pr, case_id=case_id,
                                 snapshot_version=snapshot_version)
                promoted.append(pr)
            if sector is not None:
                on_sector_observations(agent, parsed, context)

        executor = DagExecutor(self._session, registry, runtime)
        report = executor.run(run_model, build_task=build, on_result=on_result)

        # --- deterministic scores from promoted + deterministic parameters ---
        # Scores use ONLY ``promoted`` (validated agent parameters), exactly as
        # without a sector benchmark. The ``sector_benchmark.*`` parameter
        # results are never passed to the scoring engine.
        business = scorer.score_business(promoted, analysis_run_id=analysis_run_id)
        financial = sector_state["financial_score"] or scorer.score_financial(
            promoted, analysis_run_id=analysis_run_id)
        obligor = scorer.score_obligor(business, financial,
                                       analysis_run_id=analysis_run_id)
        for score in (business, financial, obligor):
            self._session.add(_score_row(score, case_id, snapshot_version))
        self._session.flush()

        # --- topic conclusions (orchestrators synthesize; compute nothing) ---
        store = ConclusionStore(self._session)
        conclusions = {}
        for topic, score in ((Topic.BUSINESS, business), (Topic.FINANCIAL, financial)):
            if topic is Topic.FINANCIAL and sector_state["conclusion"] is not None:
                # The validated orchestrator interpretation of the benchmark context.
                conclusion = sector_state["conclusion"]
                store.persist(conclusion, case_id=case_id,
                              snapshot_version=snapshot_version)
                conclusions[topic] = conclusion
                continue
            conclusion = build_topic_conclusion(
                topic,
                {"overall_assessment": {
                    "claim_id": f"cl_{topic.value}", "category": "assessment",
                    "text": f"{topic.value.title()} assessment from validated "
                            "parameters.",
                    "parameter_result_ids": [p.parameter_result_id for p in promoted
                                             if p.topic is topic][:5],
                    "evidence_ids": evidence_ids[:1]}},
                analysis_run_id=analysis_run_id, orchestrator_run_id="orch",
                score_reference=score.score_id)
            store.persist(conclusion, case_id=case_id, snapshot_version=snapshot_version)
            conclusions[topic] = conclusion

        # Mark the run completed.
        run_row = self._session.get(RunRow, analysis_run_id)
        run_row.status = "partially_failed" if report.failed else "completed"
        self._session.flush()

        output = self._draft_output(
            analysis_run_id, business, financial, obligor, conclusions, promoted,
            scoring_cfg,
        )
        output["agentic"]["deterministic_parameter_result_ids"] = [
            p.parameter_result_id for p in deterministic_params]
        if sector is not None:
            self._attach_sector_results(
                output, sector, analysis_run_id, benchmark_prs, benchmark_ctx,
                sector_state)
        runs = self._model_runs(case_id, snapshot_version)
        return output, runs, [], []

    @staticmethod
    def _attach_sector_results(output, sector, analysis_run_id, benchmark_prs,
                               benchmark_ctx, sector_state):
        """Record the benchmark artifacts on the draft and the sector payload."""
        conclusion = sector_state["conclusion"]
        quoted = sector_state.get("quoted") or {}
        narrative = []
        if conclusion is not None:
            claims = [conclusion.overall_assessment, *conclusion.strengths,
                      *conclusion.weaknesses, *conclusion.key_drivers,
                      *conclusion.material_risks]
            narrative = [
                {"claim_id": c.claim_id, "category": c.category.value, "text": c.text,
                 "parameter_result_ids": list(c.parameter_result_ids),
                 "evidence_ids": list(c.evidence_ids),
                 "quoted_values": quoted.get(c.claim_id, []),
                 "materiality": c.materiality.value, "uncertainty": c.uncertainty}
                for c in claims
            ]
            output["repayment_analysis"] = [
                {"claim_id": c["claim_id"], "text": c["text"], "kind": "interpretation",
                 "evidence_ids": c["evidence_ids"], "materiality": c["materiality"],
                 "uncertainty": c["uncertainty"],
                 "parameter_result_ids": c["parameter_result_ids"]}
                for c in narrative
            ]
        ids = [r.parameter_result_id for r in benchmark_prs]
        outcome = sector_state["outcome"] or {
            "status": "not_run", "agent_run_id": None,
            "issues": ["Financial Orchestrator did not run (an upstream agent "
                       "failed or was skipped)."]}
        interpretation = {
            "analysis_run_id": analysis_run_id,
            "context_version": benchmark_ctx["context_version"],
            "context_hash": benchmark_ctx["context_hash"],
            "benchmark_parameter_result_ids": ids,
            "orchestrator_outcome": outcome,
            "narrative_claims": narrative,
            "open_questions": list(conclusion.open_questions) if conclusion else [],
            "unresolved_contradictions": (
                list(conclusion.unresolved_contradictions) if conclusion else []),
            "qualitative_observations": sector_state["observations"],
            "rejected_qualitative_observations": sector_state["rejected_observations"],
        }
        sector["agentic_interpretation"] = interpretation
        output["agentic"]["accepted_parameter_result_ids"].extend(ids)
        output["agentic"]["industry_benchmark"] = {
            "context_version": benchmark_ctx["context_version"],
            "context_hash": benchmark_ctx["context_hash"],
            "benchmark_parameter_result_ids": ids,
            "orchestrator_outcome": outcome,
            "affects_official_scores": False,
        }

    # -- helpers --------------------------------------------------------------

    def _draft_output(self, analysis_run_id, business, financial, obligor,
                      conclusions, promoted, scoring_cfg):
        """Build an AnalysisResponse-shaped draft carrying agentic provenance."""
        def _claims(topic):
            c = conclusions.get(topic)
            if c is None:
                return []
            return [{"claim_id": c.overall_assessment.claim_id,
                     "text": c.overall_assessment.text, "kind": "interpretation",
                     "evidence_ids": c.overall_assessment.evidence_ids,
                     "materiality": "medium", "uncertainty": None}]
        return {
            "business_overview": _claims(Topic.BUSINESS),
            "repayment_analysis": _claims(Topic.FINANCIAL),
            "key_risks": [],
            "mitigants": [],
            "data_limitations": [],
            "questions_for_human": [],
            "challenges": [],
            # Agentic provenance recorded on the FinalCaseSnapshot.
            "agentic": {
                "analysis_mode": "agentic",
                "accepted_analysis_run_id": analysis_run_id,
                "scores": {
                    "business": _score_dict(business),
                    "financial": _score_dict(financial),
                    "obligor": _score_dict(obligor),
                },
                "accepted_parameter_result_ids": [p.parameter_result_id
                                                  for p in promoted],
                "scoring_config_version": scoring_cfg.version,
                "scoring_config_hash": scoring_cfg.content_hash,
            },
        }

    def _model_runs(self, case_id, version):
        from app.models.orm import ModelRun

        rows = self._session.query(ModelRun).filter_by(
            case_id=case_id, case_version=version).all()
        return [
            {"run_id": r.run_id, "method": r.method, "prompt_id": r.prompt_id,
             "prompt_hash": r.prompt_hash, "prompt_version": r.prompt_version,
             "model_id": r.model_id, "validation_outcome": r.validation_outcome}
            for r in rows
        ]


def _quoted_by_claim(parsed) -> dict:
    """``{claim_id: quoted_values}`` from a validated orchestrator answer."""
    out = {}
    for key in ("overall_assessment", "strengths", "weaknesses", "key_drivers",
                "material_risks"):
        value = parsed.get(key)
        claims = [value] if isinstance(value, dict) else (value or [])
        for claim in claims:
            if isinstance(claim, dict) and claim.get("claim_id") and claim.get("quoted_values"):
                out[claim["claim_id"]] = claim["quoted_values"]
    return out


def _packet(agent, analysis_run_id, case_id, snapshot_version, evidence_ids):
    from app.schemas.agentic import EvidencePacket

    return EvidencePacket(
        case_id=case_id, snapshot_version=snapshot_version, router_version=ROUTER_VERSION,
        analysis_run_id=analysis_run_id, agent_id=agent.agent_id,
        evidence_ids=list(evidence_ids), packet_hash="packet")


def _score_dict(score) -> dict:
    return {"score_id": score.score_id, "kind": score.kind.value,
            "status": score.status.value, "band": score.band}


def _score_row(score, case_id, snapshot_version):
    from app.models.orm import RiskScoreRow

    return RiskScoreRow(
        score_id=score.score_id, analysis_run_id=score.analysis_run_id,
        case_id=case_id, snapshot_version=snapshot_version, kind=score.kind.value,
        status=score.status.value, band=score.band,
        scoring_config_version=score.scoring_config_version,
        scoring_config_hash=score.scoring_config_hash,
        contributing_parameter_ids=list(score.contributing_parameter_ids),
        missing_required_parameter_ids=list(score.missing_required_parameter_ids),
        critical_missing_parameter_ids=list(score.critical_missing_parameter_ids),
        coverage_weight=score.coverage_weight,
        applied_overlays=list(score.applied_overlays), detail=score.detail,
        acceptance_state="accepted")
