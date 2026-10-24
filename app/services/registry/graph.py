"""Derive the left-to-right pipeline dataflow graph for the Pipeline Explorer.

The graph is **layout metadata only** — it carries no financial conclusions. Its
nodes and directed edges come from *declared* dependencies (the agent registry's
``AgentDefinition.dependencies`` and the fixed BO synthesis/challenger/governance
stages), never from guessed file naming. When an :class:`ArtifactManifest` is
supplied, each node is annotated with the recorded execution status of its
artifact so the UI can colour it; without a manifest every node is ``absent`` and
the graph still renders usefully (illustration mode).

Layer order (left -> right), matching Business Overview V6:

  0 raw_input        Raw documents & structured data (single node)
  1 evidence         Evidence admission
  2 snapshot         CanonicalEvidenceSnapshot
  3 modules          Module/agent branches: M1 (6 agents incl. Agent 6), M2, M3,
                     M4 (two specialists), and FO (branches here, alongside BO)
  4 validators       Per-module validators + module outputs
  5 synthesis_v1     Cross-module synthesis (orchestrator)
  6 challenger       Challenger
  7 synthesis_v2     Revised synthesis
  8 governance       Final deterministic governance
  9 outputs          Governed outputs (BO handoff, FO overview -> SO handoff)

Illustration nodes (fixed scaffolding) are clearly flagged ``illustration=True``
and never claim a successful execution.
"""

from __future__ import annotations

from typing import Any, Optional

from app.schemas.artifact_manifest import ArtifactManifest, ExecutionStatus

PIPELINE_GRAPH_VERSION = "pipeline-graph-1.0"


# -- fixed module / agent scaffolding ----------------------------------------
# Agent ids match app/services/bo/roster_v3.py (V3_SPECS). FO agents are logical
# labels (the FO service is deterministic, not registry-driven).

_M1_AGENTS = [
    ("m1_revenue_quality", "BO1 Revenue Quality"),
    ("m1_customer_stickiness", "BO2 Customer Stickiness"),
    ("m1_pricing_power", "BO3 Pricing Power"),
    ("m1_operating_model", "BO4 Operating Model"),
    ("m1_competitive_durability", "BO5 Competitive Durability"),
    ("m1_resilience_integration", "Agent 6 Synthesis (resilience)"),
]
_M1_PRIMARY_IDS = [a for a, _ in _M1_AGENTS[:5]]


def _node(
    node_id: str,
    label: str,
    layer: int,
    *,
    kind: str,
    group: Optional[str] = None,
    illustration: bool = False,
    artifact_id: Optional[str] = None,
    producer_agent: Optional[str] = None,
    producer_validator: Optional[str] = None,
    prefer_artifact_type: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "label": label,
        "layer": layer,
        "kind": kind,                 # input|evidence|snapshot|agent|validator|synthesis|challenger|governance|output|group
        "group": group,
        "illustration": illustration,
        "artifact_id": artifact_id,
        "producer_agent": producer_agent,
        "producer_validator": producer_validator,
        # Hint so manifest matching resolves to the right recorded artifact when
        # several share a producer (e.g. synthesis vs memo both come from the
        # orchestrator): prefer an entry of this ``artifact_type``.
        "prefer_artifact_type": prefer_artifact_type,
        "status": ExecutionStatus.ABSENT.value,
        "status_reason": None,
    }


def _skeleton() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build the fixed node/edge scaffolding with declared edges."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    def edge(src: str, dst: str, reason: str = "declared_dependency") -> None:
        edges.append({"source": src, "target": dst, "reason": reason})

    # Layer 0-2: linear spine.
    nodes.append(_node("raw_input", "Raw documents & structured data", 0, kind="input"))
    nodes.append(_node("evidence_admission", "Evidence admission", 1, kind="evidence"))
    nodes.append(_node("canonical_snapshot", "CanonicalEvidenceSnapshot", 2, kind="snapshot"))
    edge("raw_input", "evidence_admission", "pipeline_stage")
    edge("evidence_admission", "canonical_snapshot", "pipeline_stage")

    # Layer 3: module/agent branches (groups + individual agent nodes).
    # Module 1 (six agents). BO1-5 depend on the snapshot; Agent 6 depends on BO1-5.
    for agent_id, label in _M1_AGENTS:
        nodes.append(_node(
            f"agent.{agent_id}", label, 3, kind="agent", group="M1",
            producer_agent=agent_id, prefer_artifact_type="agent_output",
        ))
    for pid in _M1_PRIMARY_IDS:
        edge("canonical_snapshot", f"agent.{pid}", "declared_dependency")
    for pid in _M1_PRIMARY_IDS:
        edge(f"agent.{pid}", "agent.m1_resilience_integration", "declared_dependency")

    # Module 2.
    nodes.append(_node("agent.m2_industry", "M2 Industry & External", 3,
                       kind="agent", group="M2", producer_agent="m2_industry",
                       prefer_artifact_type="agent_output"))
    edge("canonical_snapshot", "agent.m2_industry", "declared_dependency")

    # Module 3 (authoritative for corporate/entity relationships).
    nodes.append(_node("agent.m3_corporate_management", "M3 Corporate Structure & Management", 3,
                       kind="agent", group="M3", producer_agent="m3_corporate_management",
                       artifact_id="m3.analysis", prefer_artifact_type="agent_output"))
    edge("canonical_snapshot", "agent.m3_corporate_management", "declared_dependency")

    # Module 4: TWO internal specialists, one governed Module 4 output.
    nodes.append(_node("agent.m4_strategy_capital", "M4 Strategy & Capital Allocation", 3,
                       kind="agent", group="M4", producer_agent="m4_strategy_capital",
                       prefer_artifact_type="agent_output"))
    nodes.append(_node("agent.m4_debt_structure", "M4 Debt Structure & Refinancing", 3,
                       kind="agent", group="M4", producer_agent="module4_debt_structure_specialist"))
    edge("canonical_snapshot", "agent.m4_strategy_capital", "declared_dependency")
    edge("canonical_snapshot", "agent.m4_debt_structure", "declared_dependency")
    # M4 may *consume* verified M3 entity relationships when available (never as fact).
    edge("agent.m3_corporate_management", "agent.m4_debt_structure",
         "optional_verified_entity_relationships")

    # Financial Overview branches from the canonical evidence / financial facts
    # layer, alongside BO. Deterministic measures + pre-check + triggers.
    nodes.append(_node("fo.precheck", "FO Pre-check (going concern, covenants)", 3,
                       kind="agent", group="FO", producer_agent="fo_precheck"))
    nodes.append(_node("fo.measures", "FO Seven Measures (deterministic)", 3,
                       kind="agent", group="FO", producer_agent="fo_measures"))
    nodes.append(_node("fo.triggers", "FO Trigger Agents", 3,
                       kind="agent", group="FO", producer_agent="fo_triggers"))
    edge("canonical_snapshot", "fo.precheck", "declared_dependency")
    edge("canonical_snapshot", "fo.measures", "declared_dependency")
    edge("fo.measures", "fo.triggers", "declared_dependency")

    # Layer 4: validators + module outputs.
    validators = [
        ("validator.v7", "Validator 7 (M3 numeric binding)", "validator_7",
         "agent.m3_corporate_management", "m3.validator_7"),
        ("validator.v8_strategy", "Validator 8 (M4 strategy/capital)", "validator_8",
         "agent.m4_strategy_capital", None),
        ("validator.v8_debt", "Validator 8 (M4 debt-structure reconciliation)",
         "module4_validator8", "agent.m4_debt_structure", None),
    ]
    for vid, label, val_id, upstream, artifact_id in validators:
        nodes.append(_node(vid, label, 4, kind="validator",
                           producer_validator=val_id, artifact_id=artifact_id))
        edge(upstream, vid, "declared_dependency")

    # FO scoring -> FO governed output (its own validation branch).
    nodes.append(_node("fo.scoring", "FO Scoring (provisional rating)", 4,
                       kind="validator", producer_agent="fo_scoring"))
    edge("fo.triggers", "fo.scoring", "declared_dependency")
    edge("fo.precheck", "fo.scoring", "declared_dependency")

    # Layer 5: cross-module synthesis (orchestrator) = synthesis V1.
    nodes.append(_node("synthesis_v1", "Cross-module synthesis V1 (orchestrator)", 5,
                       kind="synthesis", producer_agent="business_orchestrator",
                       prefer_artifact_type="module_output"))
    for agent_id, _ in _M1_AGENTS:
        edge(f"agent.{agent_id}", "synthesis_v1", "declared_dependency")
    edge("agent.m2_industry", "synthesis_v1", "declared_dependency")
    edge("validator.v7", "synthesis_v1", "declared_dependency")
    edge("validator.v8_strategy", "synthesis_v1", "declared_dependency")
    edge("validator.v8_debt", "synthesis_v1", "declared_dependency")

    # Layer 6: challenger.
    nodes.append(_node("challenger", "Challenger", 6, kind="challenger",
                       producer_agent="business_challenge",
                       prefer_artifact_type="module_output"))
    edge("synthesis_v1", "challenger", "declared_dependency")

    # Layer 7: revised synthesis V2.
    nodes.append(_node("synthesis_v2", "Revised synthesis V2", 7, kind="synthesis",
                       producer_agent="business_orchestrator", illustration=True))
    edge("challenger", "synthesis_v2", "bounded_remediation")

    # Layer 8: final deterministic governance.
    nodes.append(_node("governance", "Final deterministic governance", 8, kind="governance"))
    edge("synthesis_v2", "governance", "pipeline_stage")

    # Layer 9: governed outputs + BO/FO -> SO handoff.
    nodes.append(_node("output.bo_handoff", "BO governed handoff", 9, kind="output",
                       artifact_id="bo.handoff"))
    nodes.append(_node("output.fo_overview", "FO governed overview", 9, kind="output",
                       artifact_id="fo.overview"))
    nodes.append(_node("output.so_handoff", "BO + FO -> SO handoff", 9, kind="output",
                       illustration=True))
    edge("governance", "output.bo_handoff", "pipeline_stage")
    edge("fo.scoring", "output.fo_overview", "pipeline_stage")
    edge("output.bo_handoff", "output.so_handoff", "handoff")
    edge("output.fo_overview", "output.so_handoff", "handoff")

    return nodes, edges


def _apply_manifest(
    nodes: list[dict[str, Any]], manifest: ArtifactManifest
) -> None:
    """Annotate nodes with recorded status from the manifest (best-effort match).

    Matches on ``artifact_id`` exactly, else by ``producer_agent`` /
    ``producer_validator``. Prefix-matches run-scoped ids (``bo.handoff.<run>``).
    """
    def find(node: dict[str, Any]):
        prefer = node.get("prefer_artifact_type")
        pa, pv = node.get("producer_agent"), node.get("producer_validator")

        # 1. When a preferred artifact type is set, match producer + that type
        #    first, so an agent node resolves to its per-agent record (not the
        #    whole memo) and synthesis/challenger resolve to their own blocks.
        if prefer:
            for entry in manifest.artifacts:
                if entry.artifact_type.value != prefer:
                    continue
                if (pv and entry.producer_validator == pv) or \
                        (pa and entry.producer_agent == pa):
                    return entry

        # 2. Exact / prefix artifact-id match.
        aid = node.get("artifact_id")
        if aid:
            for entry in manifest.artifacts:
                if entry.artifact_id == aid or entry.artifact_id.startswith(aid + "."):
                    return entry

        # 3. Fall back to any producer match.
        for entry in manifest.artifacts:
            if pv and entry.producer_validator == pv:
                return entry
            if pa and entry.producer_agent == pa:
                return entry
        return None

    for node in nodes:
        entry = find(node)
        if entry is not None:
            node["status"] = entry.status.value
            node["status_reason"] = entry.status_reason
            node["artifact_id"] = entry.artifact_id


def build_pipeline_graph(
    manifest: Optional[ArtifactManifest] = None,
) -> dict[str, Any]:
    """Return the left-to-right dataflow graph (nodes + edges + metadata).

    With no manifest, every node is ``absent`` (illustration mode) and the graph
    still renders. Layout metadata is kept separate from any financial content.
    """
    nodes, edges = _skeleton()
    if manifest is not None:
        _apply_manifest(nodes, manifest)

    layers = {
        0: "Raw input", 1: "Evidence admission", 2: "Canonical evidence snapshot",
        3: "Modules & agents", 4: "Validators & module outputs",
        5: "Cross-module synthesis", 6: "Challenger", 7: "Revised synthesis",
        8: "Final governance", 9: "Governed outputs",
    }
    return {
        "graph_version": PIPELINE_GRAPH_VERSION,
        "case_id": manifest.case_id if manifest else None,
        "run_id": manifest.run_id if manifest else None,
        "layers": layers,
        "nodes": nodes,
        "edges": edges,
        "legend": {
            ExecutionStatus.VALIDATED.value: "Produced & validated",
            ExecutionStatus.QUALIFIED.value: "Accepted with qualification",
            ExecutionStatus.INCOMPLETE.value: "Partial / incomplete",
            ExecutionStatus.FAILED.value: "Failed / rejected",
            ExecutionStatus.SKIPPED.value: "Skipped",
            ExecutionStatus.ABSENT.value: "No recorded output",
            ExecutionStatus.ERROR.value: "Unreadable artifact",
        },
    }
