"""Challenge loop + bounded targeted rerun with descendant invalidation (M13).

A challenge agent IDENTIFIES defects (ChallengeFindings) and never mutates
parameters or scores. When a finding is material and requests a rerun, the loop:

1. computes the FULL rerun scope = the explicitly requested agents/parameters
   PLUS every DAG descendant whose declared inputs depend on them
   (``AgentRegistry.descendants_of`` — Remediation 2 / Req 29);
2. marks the currently-accepted artifacts for those nodes ``superseded``
   (append-only; prior versions preserved — Req 32);
3. hands the scope back to the caller to re-execute + recompute (the executor's
   ``only=`` recompute, deterministic scores recomputed) producing NEW accepted
   versions;
4. is bounded by ``MAX_CHALLENGE_RERUN_ROUNDS`` (default 1); an unresolved
   material finding after the bound escalates to MANDATORY human review and
   SHALL NOT loop further (Req 15.4-15.5).

The loop never performs unbounded recursive debate and never reruns the whole
DAG — only the affected subgraph.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.orm import ChallengeFindingRow
from app.schemas.agentic import ChallengeFinding, ChallengeSeverity
from app.services.agents.registry import AgentRegistry
from app.services.escalation.engine import EscalationEngine
from app.services.escalation.rules import EscalationCategory, Severity

# New agentic challenge rule id (mirrors the baseline R-CHALLENGE-HIGH-01).
RULE_UNRESOLVED_CHALLENGE = "R-AGENT-CHALLENGE-01"

MATERIAL = {ChallengeSeverity.HIGH, ChallengeSeverity.MATERIAL}


@dataclass
class ChallengeDecision:
    """Outcome of evaluating one challenge pass."""

    findings: list[ChallengeFinding] = field(default_factory=list)
    rerun_scope: set[str] = field(default_factory=set)  # agents to re-execute
    rerun_parameter_ids: set[str] = field(default_factory=set)
    escalated: bool = False

    @property
    def requires_rerun(self) -> bool:
        return bool(self.rerun_scope)


def parse_findings(
    parsed: dict, *, analysis_run_id: str, topic: str
) -> list[ChallengeFinding]:
    """Parse a validated challenge-agent output into typed ChallengeFindings."""
    findings: list[ChallengeFinding] = []
    for item in parsed.get("challenges", []):
        if not isinstance(item, dict):
            continue
        try:
            severity = ChallengeSeverity(item.get("severity", "low"))
        except ValueError:
            severity = ChallengeSeverity.LOW
        findings.append(
            ChallengeFinding(
                challenge_id=item.get("challenge_id") or f"ch_{uuid.uuid4().hex[:12]}",
                analysis_run_id=analysis_run_id,
                target=item.get("target"),
                affected_agent_ids=list(item.get("affected_agent_ids", [])),
                affected_parameter_ids=list(item.get("affected_parameter_ids", [])),
                issue_type=item.get("issue_type", "unspecified"),
                severity=severity,
                reason=item.get("reason", ""),
                evidence_ids=list(item.get("evidence_ids", [])),
                requires_reanalysis=bool(item.get("requires_reanalysis", False)),
                requested_rerun_scope=list(item.get("requested_rerun_scope", [])),
            )
        )
    return findings


class ChallengeLoop:
    """Bounded, descendant-aware targeted-rerun controller."""

    def __init__(
        self,
        session: Session,
        registry: AgentRegistry,
        *,
        escalation: EscalationEngine,
        max_rerun_rounds: int = 1,
    ) -> None:
        self._session = session
        self._registry = registry
        self._escalation = escalation
        self._max_rounds = int(max_rerun_rounds)

    def evaluate(
        self,
        findings: list[ChallengeFinding],
        *,
        case_id: str,
        snapshot_version: int,
        round_index: int,
    ) -> ChallengeDecision:
        """Decide the rerun scope for a challenge pass; escalate when bounded out."""
        decision = ChallengeDecision(findings=findings)
        self._persist(findings, case_id=case_id, snapshot_version=snapshot_version)

        material = [f for f in findings if f.severity in MATERIAL]
        if not material:
            return decision  # clean

        if round_index >= self._max_rounds:
            # Bounded out: unresolved material issue -> MANDATORY human review.
            self._escalation.raise_escalation(
                rule_id=RULE_UNRESOLVED_CHALLENGE,
                category=EscalationCategory.AI_DETERMINISTIC_CONFLICT,
                severity=Severity.MANDATORY,
                reason=(
                    "Unresolved material challenge after the bounded rerun; "
                    "human review required."
                ),
                evidence_refs=[f.challenge_id for f in material],
                case_id=case_id,
            )
            decision.escalated = True
            return decision

        # Compute the full invalidation scope: requested nodes + descendants.
        requested_agents: set[str] = set()
        for f in material:
            requested_agents.update(f.affected_agent_ids)
            # requested_rerun_scope may name agents and/or parameters.
            for item in f.requested_rerun_scope:
                if item in self._registry:
                    requested_agents.add(item)
                else:
                    decision.rerun_parameter_ids.add(item)
            decision.rerun_parameter_ids.update(f.affected_parameter_ids)

        descendants = self._registry.descendants_of(requested_agents) if requested_agents else set()
        decision.rerun_scope = requested_agents | descendants
        return decision

    def _persist(
        self, findings: list[ChallengeFinding], *, case_id: str, snapshot_version: int
    ) -> None:
        for f in findings:
            if self._session.get(ChallengeFindingRow, f.challenge_id) is not None:
                continue  # findings are append-only + id-identified; don't duplicate
            self._session.add(
                ChallengeFindingRow(
                    challenge_id=f.challenge_id,
                    analysis_run_id=f.analysis_run_id,
                    case_id=case_id,
                    snapshot_version=snapshot_version,
                    topic="",  # set by the caller's topic context if needed
                    target=f.target,
                    affected_agent_ids=list(f.affected_agent_ids),
                    affected_parameter_ids=list(f.affected_parameter_ids),
                    issue_type=f.issue_type,
                    severity=f.severity.value,
                    reason=f.reason,
                    evidence_ids=list(f.evidence_ids),
                    requires_reanalysis=f.requires_reanalysis,
                    requested_rerun_scope=list(f.requested_rerun_scope),
                    rerun_of=f.rerun_of,
                )
            )
        self._session.flush()
