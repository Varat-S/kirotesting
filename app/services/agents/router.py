"""Evidence Router — build narrow, immutable, hashed evidence packets (Milestone 3).

The router is a DETERMINISTIC component (it NEVER calls an LLM). Given the
validated ``CanonicalEvidenceSnapshot``, the deterministic parameters/metrics
derived from it, and a per-agent routing spec, it produces exactly one immutable
:class:`~app.schemas.agentic.EvidencePacket` per agent.

Why routing matters (Req 4 / 26):

* each agent receives ONLY the evidence within its remit — the business-model
  agent never sees raw covenant terms; the covenant agent never sees management
  biographies; the EBITDA-adjustments agent never sees marketing text; the
  collateral agent never sees the full revenue history;
* the reduced context cuts token cost, correlated hallucination and
  prompt-injection surface;
* every packet is content-hashed (``packet_hash``) and persisted so a historical
  agent run can be reconstructed, and the hash feeds the cache identity (M6);
* evidence is DATA, never instructions — the router only selects/forwards
  structured fields; it never derives control flow from packet content.

The ``RoutingSpec`` for each agent is data (declared by the agent registry in
M4). The router here consumes those specs; it does not hard-code the roster.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.hashing import content_hash
from app.schemas.agentic import EvidencePacket

# A selector decides whether one evidence item belongs in an agent's packet.
Selector = Callable[[dict[str, Any]], bool]

ROUTER_VERSION = "evidence-router-1.0.0"


@dataclass(frozen=True)
class RoutingSpec:
    """Declarative selection rules for one agent's evidence packet (Req 4.6).

    Each predicate is applied to the corresponding evidence collection from the
    canonical snapshot. A ``None`` predicate means "include none of this
    category" (the default), which is how the router guarantees EXCLUSION — an
    agent gets a category only if its spec explicitly opts in.
    """

    agent_id: str
    fact_selector: Selector | None = None
    parameter_selector: Selector | None = None
    narrative_selector: Selector | None = None
    entity_relationship_selector: Selector | None = None
    facility_term_selector: Selector | None = None
    covenant_term_selector: Selector | None = None
    # Collateral / security / guarantee terms route through the facility-term
    # channel too, but a dedicated selector lets an agent opt into ONLY those
    # (e.g. collateral_security_guarantee) without pulling pricing/tenor terms.
    collateral_term_selector: Selector | None = None
    # Conflicts/limitations relevant to the agent are filtered by the fields
    # the agent is allowed to see; by default they follow the fact selector.
    include_conflicts: bool = True
    include_limitations: bool = True
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RouterInputs:
    """The deterministic material the router selects from.

    ``facts`` and ``narrative_evidence`` come straight from the canonical
    snapshot; ``parameters`` are the deterministic ParameterResults/metrics;
    ``facility_terms`` / ``covenant_terms`` are extracted-term records;
    ``entity_relationships`` describe parent/subsidiary/guarantor links;
    ``open_conflicts`` / ``data_limitations`` are the snapshot's data-quality
    issues projected to a list.
    """

    case_id: str
    snapshot_version: int
    analysis_run_id: str
    facts: list[dict[str, Any]] = field(default_factory=list)
    parameters: list[dict[str, Any]] = field(default_factory=list)
    narrative_evidence: list[dict[str, Any]] = field(default_factory=list)
    entity_relationships: list[dict[str, Any]] = field(default_factory=list)
    facility_terms: list[dict[str, Any]] = field(default_factory=list)
    covenant_terms: list[dict[str, Any]] = field(default_factory=list)
    open_conflicts: list[dict[str, Any]] = field(default_factory=list)
    data_limitations: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_snapshot(
        cls,
        snapshot: Any,
        *,
        analysis_run_id: str,
        parameters: list[dict[str, Any]] | None = None,
        facility_terms: list[dict[str, Any]] | None = None,
        covenant_terms: list[dict[str, Any]] | None = None,
        entity_relationships: list[dict[str, Any]] | None = None,
    ) -> "RouterInputs":
        """Build router inputs from a ``CanonicalEvidenceSnapshot`` model/dict."""
        data = snapshot.model_dump(mode="json") if hasattr(snapshot, "model_dump") else dict(snapshot)
        conflicts, limitations = _conflicts_and_limitations(data.get("data_quality", {}))
        return cls(
            case_id=data["case_id"],
            snapshot_version=data.get("snapshot_version", 1),
            analysis_run_id=analysis_run_id,
            facts=list(data.get("facts", [])),
            parameters=list(parameters or []),
            narrative_evidence=list(data.get("narrative_evidence", [])),
            entity_relationships=list(entity_relationships or data.get("entities", [])),
            facility_terms=list(facility_terms or []),
            covenant_terms=list(covenant_terms or []),
            open_conflicts=conflicts,
            data_limitations=limitations,
        )


def _conflicts_and_limitations(
    data_quality: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    conflicts: list[dict[str, Any]] = []
    limitations: list[dict[str, Any]] = []
    for field_name, state in sorted(data_quality.items()):
        dq = state if isinstance(state, dict) else {}
        s = dq.get("state")
        entry = {"field": field_name, "state": s}
        if s == "conflicting":
            conflicts.append({**entry, "values": dq.get("values", [])})
        elif s in {"missing", "not_disclosed", "stale"}:
            limitations.append(entry)
    return conflicts, limitations


class EvidenceRouter:
    """Build immutable, hashed, narrowly-scoped evidence packets (Req 4)."""

    def __init__(self, router_version: str = ROUTER_VERSION) -> None:
        self.router_version = router_version

    def build_packet(self, spec: RoutingSpec, inputs: RouterInputs) -> EvidencePacket:
        """Build one agent's packet by applying ``spec`` to ``inputs``."""
        facts = _select(inputs.facts, spec.fact_selector)
        parameters = _select(inputs.parameters, spec.parameter_selector)
        narrative = _select(inputs.narrative_evidence, spec.narrative_selector)
        entity_rels = _select(
            inputs.entity_relationships, spec.entity_relationship_selector
        )
        facility_terms = _merge(
            _select(inputs.facility_terms, spec.facility_term_selector),
            _select(inputs.facility_terms, spec.collateral_term_selector),
        )
        covenant_terms = _select(inputs.covenant_terms, spec.covenant_term_selector)

        # Only surface conflicts/limitations about fields the agent can see.
        visible_fields = {f.get("name") for f in facts} | {
            p.get("parameter_id") for p in parameters
        }
        conflicts = (
            [c for c in inputs.open_conflicts if _relevant(c, visible_fields)]
            if spec.include_conflicts
            else []
        )
        limitations = (
            [limitation for limitation in inputs.data_limitations
             if _relevant(limitation, visible_fields)]
            if spec.include_limitations
            else []
        )

        evidence_ids = _collect_evidence_ids(facts, narrative, facility_terms, covenant_terms)

        packet = EvidencePacket(
            case_id=inputs.case_id,
            snapshot_version=inputs.snapshot_version,
            router_version=self.router_version,
            analysis_run_id=inputs.analysis_run_id,
            agent_id=spec.agent_id,
            facts=facts,
            parameters=parameters,
            narrative_evidence=narrative,
            entity_relationships=entity_rels,
            facility_terms=facility_terms,
            covenant_terms=covenant_terms,
            open_conflicts=conflicts,
            data_limitations=limitations,
            evidence_ids=evidence_ids,
        )
        return packet.model_copy(update={"packet_hash": self.hash_packet(packet)})

    @staticmethod
    def hash_packet(packet: EvidencePacket) -> str:
        """Deterministic content hash over the packet (excluding packet_hash)."""
        payload = packet.model_dump(mode="json")
        payload.pop("packet_hash", None)
        return content_hash(payload)


def _select(items: list[dict[str, Any]], selector: Selector | None) -> list[dict[str, Any]]:
    if selector is None:
        return []
    return [item for item in items if selector(item)]


def _merge(*collections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union of selected items, de-duplicated by identity, order-preserving."""
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for collection in collections:
        for item in collection:
            marker = id(item)
            if marker not in seen:
                seen.add(marker)
                out.append(item)
    return out


def _relevant(entry: dict[str, Any], visible_fields: set) -> bool:
    field_name = entry.get("field")
    return field_name is None or field_name in visible_fields


def _collect_evidence_ids(*collections: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for collection in collections:
        for item in collection:
            for key in ("evidence_id", "fact_id", "id"):
                value = item.get(key)
                if isinstance(value, str) and value not in seen:
                    seen.add(value)
                    ids.append(value)
                    break
    return sorted(ids)
