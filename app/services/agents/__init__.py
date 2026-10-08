"""Agentic analysis services (Milestone 2+).

This package holds the bounded multi-agent runtime that runs AFTER the
human-reviewed ``CanonicalEvidenceSnapshot`` boundary: the agent runtime
(execute one task), the evidence router, the agent registry, the async DAG
executor, result caching and the deterministic validation layer.

Every component here is driven by the single :class:`~app.services.llm.client.LLMClient`
choke point and is tested offline with the ``FakeLLMBackend`` — no live
provider calls occur in automated tests.
"""

from __future__ import annotations

from app.services.agents.router import (
    ROUTER_VERSION,
    EvidenceRouter,
    RouterInputs,
    RoutingSpec,
)
from app.services.agents.runtime import AgentRuntime

__all__ = [
    "AgentRuntime",
    "EvidenceRouter",
    "RouterInputs",
    "RoutingSpec",
    "ROUTER_VERSION",
]
