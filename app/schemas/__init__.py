"""Schemas layer: Pydantic models and JSON Schema for the canonical
snapshots and all object schemas.

Re-exports the Milestone 1 evidence schemas, enums, snapshot skeletons, and the
JSON Schema validate-on-write entrypoint.
"""

from __future__ import annotations

from app.schemas.agentic import (
    AgenticAnalysisRun,
    AgentExecutionResult,
    AgentRun,
    AgentTask,
    CandidateFeasibility,
    CandidateStructure,
    ChallengeFinding,
    ConclusionClaim,
    EvidencePacket,
    ParameterResult,
    RiskScore,
    StructuringConclusion,
    TopicConclusion,
)
from app.schemas.enums import (
    NON_VALUE_STATUSES,
    EntityType,
    ExtractionMethod,
    FactStatus,
)
from app.schemas.evidence import CanonicalFact, EntityRecord, SourceRef
from app.schemas.json_schema import (
    SchemaValidationError,
    get_json_schema,
    validate_snapshot,
)
from app.schemas.snapshots import (
    SCHEMA_VERSION,
    CanonicalEvidenceSnapshot,
    EvidenceSnapshotRef,
    FinalCaseSnapshot,
)

__all__ = [
    "NON_VALUE_STATUSES",
    "EntityType",
    "ExtractionMethod",
    "FactStatus",
    "CanonicalFact",
    "EntityRecord",
    "SourceRef",
    "SchemaValidationError",
    "get_json_schema",
    "validate_snapshot",
    "SCHEMA_VERSION",
    "CanonicalEvidenceSnapshot",
    "EvidenceSnapshotRef",
    "FinalCaseSnapshot",
    # agentic contracts (Milestone 1.1)
    "AgenticAnalysisRun",
    "ParameterResult",
    "AgentTask",
    "AgentRun",
    "AgentExecutionResult",
    "ConclusionClaim",
    "TopicConclusion",
    "StructuringConclusion",
    "ChallengeFinding",
    "CandidateStructure",
    "CandidateFeasibility",
    "RiskScore",
    "EvidencePacket",
]
