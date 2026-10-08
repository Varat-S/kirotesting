"""Promote validated agent output into typed ParameterResults (M10-M11).

A narrow / extraction agent emits ``{"parameters": [...]}``. After the output
passes deterministic validation (M8), this module converts each owned,
evidence-cited observation into a typed ``ParameterResult`` (method ``llm`` or
``hybrid``) and persists it to ``parameter_results``, carrying the owning
agent/run ids, analysis-run id and evidence ids.

Hard rules preserved:
* an agent may only promote parameters it OWNS in the registry;
* a financial-statement NUMBER is never promoted here (those are reconciled
  upstream); only interpretations/classifications and hybrid extracted
  contractual terms (validated before any deterministic engine uses them);
* promoted results start ``acceptance_state = accepted`` and may later be
  superseded by a rerun (append-only lineage, M13).
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.orm import ParameterResultRow
from app.schemas.agentic import (
    AcceptanceState,
    AgentRun,
    Method,
    ParameterResult,
    ParameterStatus,
    Topic,
)
from app.services.agents.registry import AgentDefinition


def promote_narrow_output(
    agent: AgentDefinition,
    parsed: dict,
    *,
    analysis_run_id: str,
    agent_run_id: str,
    model_id: str | None = None,
    prompt_id: str | None = None,
    input_hash: str | None = None,
) -> list[ParameterResult]:
    """Convert a validated narrow-agent output into owned ParameterResults."""
    owned = set(agent.owned_parameters)
    results: list[ParameterResult] = []
    for item in parsed.get("parameters", []):
        if not isinstance(item, dict):
            continue
        pid = item.get("parameter_id")
        if pid not in owned:
            # Ownership is enforced in validation; defensively skip here too.
            continue
        method = Method.HYBRID if item.get("extracted_term") else Method(
            item.get("method", "llm")
        )
        status = _status(item)
        results.append(
            ParameterResult(
                parameter_result_id=f"pr_{uuid.uuid4().hex[:16]}",
                analysis_run_id=analysis_run_id,
                parameter_id=pid,
                topic=agent.topic,
                value=_value(item),
                value_type=item.get("value_type", "category"),
                method=method,
                status=status,
                evidence_quality=item.get("evidence_quality"),
                materiality=item.get("materiality"),
                evidence_ids=list(item.get("evidence_ids", [])),
                agent_id=agent.agent_id,
                agent_run_id=agent_run_id,
                prompt_id=prompt_id,
                model_id=model_id,
                input_hash=input_hash,
                missing_information=list(item.get("missing_information", [])),
                notes=item.get("notes"),
                acceptance_state=AcceptanceState.ACCEPTED,
            )
        )
    return results


def _status(item: dict) -> ParameterStatus:
    raw = item.get("status")
    if raw:
        try:
            return ParameterStatus(raw)
        except ValueError:
            pass
    return ParameterStatus.OK


def _value(item: dict):
    term = item.get("extracted_term")
    if term and term.get("numeric_value") is not None:
        return term["numeric_value"]
    return item.get("value")


def promote_mitigant_output(
    agent: AgentDefinition,
    parsed: dict,
    *,
    analysis_run_id: str,
    agent_run_id: str,
) -> list[ParameterResult]:
    """Convert a validated mitigant-agent output into bounded proposal parameters.

    A risk-to-mitigant agent proposes BOUNDED mitigants (M15); each proposal is
    recorded as an owned ParameterResult (method ``llm``) that later feeds
    candidate structures (M16). The agent proposes; it computes nothing.
    """
    owned = set(agent.owned_parameters)
    results: list[ParameterResult] = []
    for item in parsed.get("proposals", []):
        if not isinstance(item, dict):
            continue
        pid = item.get("parameter_id")
        if pid not in owned:
            continue
        results.append(
            ParameterResult(
                parameter_result_id=f"pr_{uuid.uuid4().hex[:16]}",
                analysis_run_id=analysis_run_id,
                parameter_id=pid,
                topic=agent.topic,
                value=item.get("mitigant"),
                value_type="text",
                method=Method.LLM,
                status=ParameterStatus.OK,
                evidence_ids=list(item.get("evidence_ids", [])),
                agent_id=agent.agent_id,
                agent_run_id=agent_run_id,
                notes=item.get("addresses_risk"),
                acceptance_state=AcceptanceState.ACCEPTED,
            )
        )
    return results


class ParameterPromoter:
    """Persists promoted ParameterResults, append-only."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def persist(self, result: ParameterResult, *, case_id: str, snapshot_version: int
                ) -> ParameterResultRow:
        row = ParameterResultRow(
            parameter_result_id=result.parameter_result_id,
            analysis_run_id=result.analysis_run_id,
            case_id=case_id,
            snapshot_version=snapshot_version,
            parameter_id=result.parameter_id,
            topic=result.topic.value,
            method=result.method.value,
            value={"v": result.value},
            value_type=result.value_type,
            status=result.status.value,
            risk_signal=result.risk_signal,
            evidence_quality=(result.evidence_quality.value
                              if result.evidence_quality else None),
            confidence=result.confidence,
            materiality=result.materiality.value if result.materiality else None,
            source_fact_ids=list(result.source_fact_ids),
            source_parameter_ids=list(result.source_parameter_ids),
            evidence_ids=list(result.evidence_ids),
            formula_id=result.formula_id,
            formula_version=result.formula_version,
            agent_id=result.agent_id,
            agent_run_id=result.agent_run_id,
            prompt_id=result.prompt_id,
            model_id=result.model_id,
            input_hash=result.input_hash,
            contradictions=list(result.contradictions),
            missing_information=list(result.missing_information),
            notes=result.notes,
            supersedes_id=result.supersedes_id,
            parent_id=result.parent_id,
            rerun_of=result.rerun_of,
            acceptance_state=result.acceptance_state.value,
        )
        self._session.add(row)
        self._session.flush()
        return row
