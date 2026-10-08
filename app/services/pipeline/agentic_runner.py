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
from app.services.agents.router import ROUTER_VERSION
from app.services.agents.runtime import AgentRuntime
from app.services.agents.validation import DeterministicValidator
from app.services.llm.client import LLMClient
from app.services.orchestration.business import build_business_orchestrator_input
from app.services.orchestration.conclusions import ConclusionStore, build_topic_conclusion
from app.services.orchestration.financial import build_financial_orchestrator_input
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
        configs, escalation, audit, flag,
    ):
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

        def build(agent, context):
            if agent.task_type in _NARROW_TASK_TYPES:
                return None  # bounded composition: narrow agents only here
            return PreparedTask(
                task=AgentTask(
                    agent_id=agent.agent_id, analysis_run_id=analysis_run_id,
                    topic=agent.topic, task_type=agent.task_type,
                    prompt_name=agent.agent_id, response_schema_ref=agent.agent_id,
                    model_tier=agent.model_tier, case_id=case_id,
                    snapshot_version=snapshot_version, packet_ref="packet"),
                inputs={"canonical_evidence": {"facts": evidence.facts}},
                agent_definition_hash=agent.definition_hash(),
                evidence_ids=evidence_ids,
            )

        def on_result(agent, parsed, context):
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

        executor = DagExecutor(self._session, registry, runtime)
        report = executor.run(run_model, build_task=build, on_result=on_result)

        # --- deterministic scores from promoted + deterministic parameters ---
        business = scorer.score_business(promoted, analysis_run_id=analysis_run_id)
        financial = scorer.score_financial(promoted, analysis_run_id=analysis_run_id)
        obligor = scorer.score_obligor(business, financial,
                                       analysis_run_id=analysis_run_id)
        for score in (business, financial, obligor):
            self._session.add(_score_row(score, case_id, snapshot_version))
        self._session.flush()

        # --- topic conclusions (orchestrators synthesize; compute nothing) ---
        store = ConclusionStore(self._session)
        conclusions = {}
        for topic, score in ((Topic.BUSINESS, business), (Topic.FINANCIAL, financial)):
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
        runs = self._model_runs(case_id, snapshot_version)
        return output, runs, [], []

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
