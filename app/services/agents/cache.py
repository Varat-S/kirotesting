"""Result cache / idempotency for agent runs (Milestone 6 / Req 18, 28).

A valid agent output may be REUSED when every relevant identity input is
unchanged, keyed by a content hash over:

    analysis-run context, case_id, evidence_snapshot_version, agent_id,
    agent_definition_hash, prompt_hash, response_schema_hash, model_id,
    model_config, router_version, packet_hash

The ``agent_definition_hash`` (owned params, selectors, deps, task type, schema)
is part of the key, so a SEMANTIC agent change invalidates reuse even when the
prompt text is unchanged (Remediation 7). Any change to the evidence snapshot,
prompt, model, router version, response schema or routed packet also changes the
key. ``force_regenerate`` bypasses reuse entirely.

The cache is backed by the ``agent_runs`` table: a prior VALID row carrying the
same ``cache_key`` is a hit. No separate store is needed for the PoC. This module
is deterministic and never calls an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.hashing import content_hash
from app.models.orm import AgentRunRow


@dataclass(frozen=True)
class CacheIdentity:
    """The identity inputs that compose a cache key."""

    case_id: str
    evidence_snapshot_version: int
    agent_id: str
    agent_definition_hash: str
    prompt_hash: str
    response_schema_hash: str | None
    model_id: str
    model_config: dict[str, Any]
    router_version: str
    packet_hash: str

    def key(self) -> str:
        return content_hash(
            {
                "case_id": self.case_id,
                "evidence_snapshot_version": self.evidence_snapshot_version,
                "agent_id": self.agent_id,
                "agent_definition_hash": self.agent_definition_hash,
                "prompt_hash": self.prompt_hash,
                "response_schema_hash": self.response_schema_hash,
                "model_id": self.model_id,
                "model_config": self.model_config,
                "router_version": self.router_version,
                "packet_hash": self.packet_hash,
            }
        )


@dataclass(frozen=True)
class CacheHit:
    """A reusable prior run: its parsed output and originating run id."""

    cache_key: str
    run_id: str
    parsed: dict[str, Any] | None


def compute_cache_key(identity: CacheIdentity) -> str:
    """Public helper to compute a cache key from its identity inputs."""
    return identity.key()


class AgentResultCache:
    """Idempotent reuse of VALID agent runs, scoped to an analysis run."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def lookup(
        self, identity: CacheIdentity, *, force_regenerate: bool = False
    ) -> CacheHit | None:
        """Return a reusable prior VALID run for ``identity`` or ``None``.

        ``force_regenerate`` always returns ``None`` (bypass). A hit requires a
        prior row with the same ``cache_key`` AND ``validation_status == 'valid'``
        (Req 18.5: a cached result must have previously passed validation).
        """
        if force_regenerate:
            return None
        key = identity.key()
        row = self._session.execute(
            select(AgentRunRow)
            .where(AgentRunRow.cache_key == key)
            .where(AgentRunRow.validation_status == "valid")
            .order_by(AgentRunRow.created_at.asc())
        ).scalars().first()
        if row is None:
            return None
        return CacheHit(cache_key=key, run_id=row.run_id, parsed=row.parsed_response)
