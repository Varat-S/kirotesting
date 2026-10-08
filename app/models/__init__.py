"""Models layer: SQLAlchemy ORM entities and persistence.

Re-exports the declarative base, engine/session helpers, and the ORM models so
callers can ``from app.models import Base, Case, Fact, ...``.
"""

from __future__ import annotations

from app.models.base import (
    Base,
    UTCDateTime,
    create_engine_and_session,
    init_db,
    utcnow,
)
from app.models.orm import (
    AgenticAnalysisRun,
    AgentRunRow,
    AuditEvent,
    Benchmark,
    CandidateFeasibilityRow,
    CandidateStructureRow,
    Case,
    ChallengeFindingRow,
    ConfigVersion,
    Document,
    Entity,
    Escalation,
    EvidencePacketRow,
    Fact,
    FactSourceRef,
    HumanReview,
    Metric,
    MetricDefinition,
    ParameterResultRow,
    RiskScoreRow,
    Rule,
    RuleVersion,
    Snapshot,
    TopicConclusionRow,
)

__all__ = [
    "Base",
    "UTCDateTime",
    "create_engine_and_session",
    "init_db",
    "utcnow",
    "AuditEvent",
    "Benchmark",
    "Case",
    "ConfigVersion",
    "Document",
    "Entity",
    "Escalation",
    "Fact",
    "FactSourceRef",
    "HumanReview",
    "Metric",
    "MetricDefinition",
    "Rule",
    "RuleVersion",
    "Snapshot",
    # agentic rows (Milestone 1.2)
    "AgenticAnalysisRun",
    "ParameterResultRow",
    "AgentRunRow",
    "EvidencePacketRow",
    "TopicConclusionRow",
    "ChallengeFindingRow",
    "CandidateStructureRow",
    "CandidateFeasibilityRow",
    "RiskScoreRow",
]
