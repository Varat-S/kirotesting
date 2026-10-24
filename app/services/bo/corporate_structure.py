"""Generic single-company corporate-structure JSON adapter + Validator 7 checks.

This module turns an *explicitly case-associated* corporate-structure JSON into a
validated, render-ready :class:`CorporateStructureArtifact`. Two input contracts
are accepted:

* the **simplified v1.0 onboarding** document (``schema_version`` "1.0",
  ``document_type`` "corporate_structure_onboarding") — a nested corporate tree
  with no presentation. It is parsed into a canonical graph, validated and
  deterministically transformed to the rich v2.0 contract (see
  :mod:`app.services.bo.corporate_structure_onboarding`), then reconciled back to
  the input for round-trip fidelity; and
* the **rich v2.0 presentation-ready** document (``schema_version`` "2.0") — the
  legacy direct contract, handled unchanged for backward compatibility.

Either way the result is a validated v2.0 artifact (``refined_structure`` is the
primary ``m3_corporate_structure.json`` payload). The adapter is company-
agnostic: no company key, entity name, count, percentage, role, guarantor flag
or coordinate is hardcoded. The bundled development fixture is never auto-loaded.

Mandatory rule — *no JSON, no diagram*. A diagram is produced only when the
current case has an explicitly ingested, valid, case-associated corporate-
structure JSON. When that is not the case the artifact status is ``unavailable``
or ``rejected`` and the precise reason is recorded; no partial, inferred or
generic tree is produced and nothing is reconstructed from a 10-K, SEC Exhibit
21, external research or model knowledge.

The adapter extends Validator 7's remit to the structural (diagram) side. It
never mutates the source JSON (it is only read), and it preserves full
provenance for audit.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from app.core.hashing import content_hash
from app.schemas.agentic import Materiality
from app.schemas.bo import Confidence, Direction
from app.schemas.bo_m3_structure import (
    CORP_STRUCTURE_ADAPTER_VERSION,
    CORP_STRUCTURE_RENDERER_VERSION,
    CORP_STRUCTURE_TRANSFORM_VERSION,
    CORP_STRUCTURE_VALIDATOR_VERSION,
    NO_JSON_MESSAGE,
    SUPPORTED_ARTIFACT_TYPES,
    SUPPORTED_ONBOARDING_DOCUMENT_TYPES,
    SUPPORTED_ONBOARDING_SCHEMA_VERSIONS,
    SUPPORTED_SCHEMA_VERSIONS,
    CorporateStructureArtifact,
    CorporateStructureProvenance,
    StructuralCreditSignal,
    StructureLink,
    StructureNode,
)
from app.services.bo.corporate_structure_onboarding import (
    build_onboarding_graph,
    reconcile_roundtrip,
    transform_to_refined_v2,
)


# Role tokens that indicate an aggregate grouping label rather than a single
# named legal person. Matched case-insensitively as whole-word hints; display is
# still permitted, but the node is flagged so it never enters a governed
# legal-entity graph as an established legal person.
_GROUPING_ROLE_HINTS = ("other", "jv", "joint venture", "minority", "various")
_GROUPING_NAME_HINTS = ("other subsidiaries", "jv", "joint venture", "minority",
                        "various subsidiaries", "miscellaneous")


class CorporateStructureError(ValueError):
    """Raised only for programmer errors; validation problems are returned."""


@dataclass(frozen=True)
class CaseCorporateStructureSource:
    """A corporate-structure JSON *registered for a specific case*.

    Case association is carried by trusted metadata (``case_id`` recorded at
    ingestion / registration time), never by filename or company-name
    similarity. ``document_hash`` is computed by the caller at ingestion.
    """

    case_id: str
    payload: dict[str, Any]
    source_document_id: str | None = None
    document_hash: str | None = None
    # The case id the JSON was registered against at ingestion time. If this
    # does not equal ``case_id`` the source is not associated with the case.
    registered_case_id: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def _issue(issues: list[dict], rule_id: str, item_id: str | None, action: str,
           reason: str) -> None:
    issues.append({"rule_id": rule_id, "item_id": item_id, "action": action,
                   "reason": reason})


def _is_grouping(role: str, display_name: str) -> bool:
    role_l = (role or "").casefold()
    name_l = (display_name or "").casefold()
    return (any(h in role_l for h in _GROUPING_ROLE_HINTS)
            or any(h in name_l for h in _GROUPING_NAME_HINTS))


def _unavailable(message: str, issues: list[dict], status: str = "unavailable",
                 limitations: list[str] | None = None) -> CorporateStructureArtifact:
    return CorporateStructureArtifact(
        status=status, message=message, validation_issues=issues,
        limitations=limitations or [])


def build_corporate_structure_artifact(
    source: CaseCorporateStructureSource | None,
    *,
    expected_case_id: str,
) -> CorporateStructureArtifact:
    """Adapt + validate a case-associated corporate-structure JSON.

    Returns a :class:`CorporateStructureArtifact`. The caller passes ``None``
    (or a source for a different case) to express "no JSON for this case" — the
    result is the mandated ``unavailable`` artifact with the verbatim message.
    The source payload is deep-copied and never mutated.
    """

    issues: list[dict] = []

    # --- MANDATORY RULE: no case-associated JSON -> no diagram ---------------
    if source is None:
        _issue(issues, "m3.corp_structure_required", None, "recorded_gap",
               "No corporate-structure JSON was ingested or registered for this case.")
        return _unavailable(NO_JSON_MESSAGE, issues)

    # Case association must come from trusted metadata, not filename/name.
    associated_case = source.registered_case_id
    if associated_case is None or associated_case != expected_case_id:
        _issue(issues, "m3.corp_structure_case_association", source.source_document_id,
               "rejected",
               "Corporate-structure JSON is not associated with the current case "
               "via trusted case metadata; name or filename similarity is not "
               "sufficient.")
        return _unavailable(NO_JSON_MESSAGE, issues, status="rejected",
                            limitations=["Source is not case-associated; diagram withheld."])

    if source.case_id != expected_case_id:
        _issue(issues, "m3.corp_structure_case_association", source.source_document_id,
               "rejected",
               "Source case id does not match the current analysis case.")
        return _unavailable(NO_JSON_MESSAGE, issues, status="rejected",
                            limitations=["Source case id mismatch; diagram withheld."])

    original_payload = deepcopy(source.payload)
    payload = deepcopy(source.payload)
    if not isinstance(payload, dict):
        _issue(issues, "m3.corp_structure_malformed", source.source_document_id,
               "rejected", "Corporate-structure source is not a JSON object.")
        return _unavailable(NO_JSON_MESSAGE, issues, status="rejected",
                            limitations=["Malformed corporate-structure JSON; diagram withheld."])

    # --- Simplified v1.0 onboarding input -> deterministic v2.0 transform ----
    # When the supplied document is a simplified onboarding corporate tree, build
    # the canonical graph, validate it structurally, and deterministically
    # transform it to the rich v2.0 contract that the downstream validator and
    # renderer already consume. A legacy v2.0 document is handled directly below
    # (backward compatible).
    onboarding_meta: dict[str, Any] | None = None
    roundtrip: dict[str, Any] | None = None
    refined_payload: dict[str, Any] | None = None
    if payload.get("document_type") in SUPPORTED_ONBOARDING_DOCUMENT_TYPES \
            or payload.get("schema_version") in SUPPORTED_ONBOARDING_SCHEMA_VERSIONS:
        graph, onboarding_issues = build_onboarding_graph(payload)
        issues.extend(onboarding_issues)
        if graph is None:
            return CorporateStructureArtifact(
                status="rejected", message=NO_JSON_MESSAGE,
                validation_issues=issues,
                limitations=["Simplified onboarding corporate-structure JSON "
                             "failed validation; diagram withheld."])
        refined_payload = transform_to_refined_v2(graph)
        roundtrip = reconcile_roundtrip(graph, refined_payload)
        if not roundtrip["consistent"]:
            # The deterministic transform must round-trip exactly; a mismatch is
            # a programmer error, not a data problem. Fail closed rather than
            # emit an unreconciled diagram.
            _issue(issues, "m3.onboarding_roundtrip", None, "rejected",
                   "Transformed v2.0 output does not reconcile to the validated "
                   f"v1.0 input: {roundtrip['discrepancies']}")
            return CorporateStructureArtifact(
                status="rejected", message=NO_JSON_MESSAGE,
                validation_issues=issues,
                roundtrip_reconciliation=roundtrip,
                limitations=["Transform round-trip reconciliation failed; diagram "
                             "withheld."])
        onboarding_meta = {
            "onboarding_schema_version": str(payload.get("schema_version")),
            "onboarding_document_type": str(payload.get("document_type")),
            "onboarding_document_hash": (source.document_hash
                                         or content_hash(original_payload)),
            "transform_version": CORP_STRUCTURE_TRANSFORM_VERSION,
        }
        # Continue the existing v2.0 validation against the generated payload.
        payload = refined_payload

    schema_version = payload.get("schema_version")
    artifact_type = payload.get("artifact_type")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        _issue(issues, "m3.corp_structure_schema_version", source.source_document_id,
               "rejected",
               f"Unsupported schema_version {schema_version!r}; "
               f"supported: {list(SUPPORTED_SCHEMA_VERSIONS)}.")
        return _unavailable(NO_JSON_MESSAGE, issues, status="rejected",
                            limitations=["Unsupported schema_version; diagram withheld."])
    if artifact_type not in SUPPORTED_ARTIFACT_TYPES:
        _issue(issues, "m3.corp_structure_artifact_type", source.source_document_id,
               "rejected",
               f"Unsupported artifact_type {artifact_type!r}.")
        return _unavailable(NO_JSON_MESSAGE, issues, status="rejected",
                            limitations=["Unsupported artifact_type; diagram withheld."])

    companies = payload.get("companies")
    if not isinstance(companies, dict) or not companies:
        _issue(issues, "m3.corp_structure_company_count", source.source_document_id,
               "rejected", "The JSON contains no company entry.")
        return _unavailable(NO_JSON_MESSAGE, issues, status="rejected",
                            limitations=["No company entry; diagram withheld."])
    if len(companies) != 1:
        _issue(issues, "m3.corp_structure_single_company", source.source_document_id,
               "rejected",
               f"Exactly one company is required; found {len(companies)} entries "
               f"({sorted(companies)}).")
        return _unavailable(NO_JSON_MESSAGE, issues, status="rejected",
                            limitations=["Multiple company entries; diagram withheld."])

    # Extract the single company DYNAMICALLY — the key is never part of selection.
    company_key = next(iter(companies))
    company = companies[company_key]
    if not isinstance(company, dict):
        _issue(issues, "m3.corp_structure_malformed", company_key, "rejected",
               "Company entry is not a JSON object.")
        return _unavailable(NO_JSON_MESSAGE, issues, status="rejected",
                            limitations=["Malformed company entry; diagram withheld."])

    # --- Hierarchy validation ------------------------------------------------
    raw_nodes = company.get("nodes")
    raw_links = company.get("ownership_links")
    root_node_id = company.get("root_node_id")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        _issue(issues, "m3.corp_structure_nodes", company_key, "rejected",
               "Company has no nodes.")
        return _unavailable(NO_JSON_MESSAGE, issues, status="rejected",
                            limitations=["No nodes; diagram withheld."])
    if not isinstance(raw_links, list):
        raw_links = []

    node_index: dict[str, dict] = {}
    nodes: list[StructureNode] = []
    grouping_ids: list[str] = []
    guarantor_ids: list[str] = []
    fatal = False
    for raw in raw_nodes:
        nid = str(raw.get("id") or "")
        box = raw.get("box") or {}
        if not nid:
            _issue(issues, "m3.node_id_present", None, "rejected",
                   "A node is missing its id.")
            fatal = True
            continue
        if nid in node_index:
            _issue(issues, "m3.node_id_unique", nid, "rejected",
                   "Duplicate node id.")
            fatal = True
            continue
        for coord in ("center_x_px", "top_y_px", "width_px", "height_px"):
            if not isinstance(box.get(coord), (int, float)):
                _issue(issues, "m3.node_coordinates", nid, "rejected",
                       f"Node is missing numeric layout coordinate {coord!r}.")
                fatal = True
        role = str(raw.get("role") or "")
        display_name = str(raw.get("display_name") or "")
        is_guarantor = bool(raw.get("is_guarantor"))
        grouping = _is_grouping(role, display_name)
        node = StructureNode(
            node_id=nid, display_name=display_name, role=role,
            is_guarantor=is_guarantor, is_grouping_node=grouping,
            center_x_px=float(box.get("center_x_px", 0.0)),
            top_y_px=float(box.get("top_y_px", 0.0)),
            width_px=float(box.get("width_px", 0.0)),
            height_px=float(box.get("height_px", 0.0)))
        node_index[nid] = raw
        nodes.append(node)
        if grouping:
            grouping_ids.append(nid)
        if is_guarantor:
            guarantor_ids.append(nid)

    if not root_node_id or root_node_id not in node_index:
        _issue(issues, "m3.root_node", str(root_node_id), "rejected",
               "root_node_id is missing or does not reference a known node.")
        fatal = True

    links: list[StructureLink] = []
    seen_edges: dict[tuple[str, str], float | None] = {}
    child_parents: dict[str, set[str]] = {}
    for i, raw in enumerate(raw_links):
        parent = str(raw.get("parent_id") or "")
        child = str(raw.get("child_id") or "")
        link_id = f"link:{parent}->{child}"
        pct = raw.get("ownership_percent")
        if parent not in node_index or child not in node_index:
            _issue(issues, "m3.link_referential_integrity", link_id, "rejected",
                   "Ownership link references an unknown node.")
            fatal = True
            continue
        if parent == child:
            _issue(issues, "m3.link_self_reference", link_id, "rejected",
                   "Ownership link is a self-reference.")
            fatal = True
            continue
        if pct is not None and not (0 <= float(pct) <= 100):
            _issue(issues, "m3.ownership_percentage_bounds", link_id, "rejected",
                   f"Ownership percentage {pct} is outside 0-100.")
            fatal = True
            continue
        edge = (parent, child)
        if edge in seen_edges:
            if seen_edges[edge] != pct:
                _issue(issues, "m3.link_contradictory", link_id, "rejected",
                       "Contradictory ownership percentage for the same edge.")
                fatal = True
            continue
        seen_edges[edge] = float(pct) if pct is not None else None
        child_parents.setdefault(child, set()).add(parent)
        links.append(StructureLink(
            link_id=link_id, parent_id=parent, child_id=child,
            ownership_percent=float(pct) if pct is not None else None))

    # Cycle detection (reject invalid cycles / contradictory hierarchy).
    if not fatal and _has_cycle(node_index.keys(), links):
        _issue(issues, "m3.hierarchy_acyclic", None, "rejected",
               "Ownership hierarchy contains a cycle.")
        fatal = True

    # Every non-root node must be reachable from the root via links; no node may
    # have more than one declared parent (the diagram is a tree).
    if not fatal:
        for child, parents in child_parents.items():
            if len(parents) > 1:
                _issue(issues, "m3.single_parent", child, "rejected",
                       "A node has more than one parent in the ownership tree.")
                fatal = True
        reachable = _reachable_from(root_node_id, links)
        for node in nodes:
            if node.node_id != root_node_id and node.node_id not in reachable:
                _issue(issues, "m3.node_reachability", node.node_id, "rejected",
                       "Node is not connected to the root via ownership links.")
                fatal = True

    if fatal:
        return CorporateStructureArtifact(
            status="rejected", message=NO_JSON_MESSAGE, validation_issues=issues,
            limitations=["Corporate-structure JSON failed validation; diagram withheld."])

    # --- Provenance (never mutate the source) --------------------------------
    # The document hash is over the ORIGINAL ingested source so an auditor can
    # tie the diagram to exactly what was onboarded (the v1.0 doc when a
    # transform ran, or the v2.0 doc for a direct source).
    document_hash = source.document_hash or content_hash(original_payload)
    visual_spec = payload.get("visual_spec") or {}
    footer = (visual_spec.get("footer") or {}).get("text")
    provenance = CorporateStructureProvenance(
        source_document_id=source.source_document_id,
        document_hash=document_hash,
        json_schema_version=str(schema_version),
        artifact_type=str(artifact_type),
        selected_company_key=company_key,
        company_display_name=str(company.get("company_display_name") or company_key),
        simulation_status=company.get("simulation_status"),
        simulation_disclaimer=payload.get("simulation_disclaimer"),
        footer_text=footer,
        node_ids=[n.node_id for n in nodes],
        relationship_ids=[l.link_id for l in links],
        json_pointers={
            "companies": "/companies",
            "selected_company": f"/companies/{company_key}",
            "nodes": f"/companies/{company_key}/nodes",
            "ownership_links": f"/companies/{company_key}/ownership_links",
            "visual_spec": "/visual_spec",
        },
        adapter_version=CORP_STRUCTURE_ADAPTER_VERSION,
        validator_version=CORP_STRUCTURE_VALIDATOR_VERSION,
        renderer_version=CORP_STRUCTURE_RENDERER_VERSION,
        **(onboarding_meta or {}),
    )

    is_simulated = bool(payload.get("simulation_disclaimer")
                        or company.get("simulation_status"))
    limitations: list[str] = []
    if is_simulated:
        limitations.append(
            "Entities, ownership percentages, borrower designation and guarantor "
            "markings are simulated/illustrative, not verified facts; a simulated "
            "guarantor marking does not establish an enforceable guarantee.")
    if grouping_ids:
        limitations.append(
            "Aggregate grouping nodes are drawn for context only and are not "
            "treated as independently established legal persons.")

    signals = _derive_structural_signals(
        nodes=nodes, links=links, node_index={n.node_id: n for n in nodes},
        grouping_ids=set(grouping_ids), guarantor_ids=set(guarantor_ids),
        is_simulated=is_simulated)

    return CorporateStructureArtifact(
        status="available",
        message=None,
        provenance=provenance,
        nodes=nodes,
        links=links,
        root_node_id=root_node_id,
        guarantor_node_ids=guarantor_ids,
        grouping_node_ids=grouping_ids,
        plain_text_tree=company.get("plain_text_tree"),
        structural_signals=signals,
        validation_issues=issues,
        limitations=limitations,
        is_simulated=is_simulated,
        # Deliverable A: the refined, presentation-ready v2.0 JSON. For a
        # transformed onboarding document this is the generated output; for a
        # direct v2.0 source it is the validated payload itself.
        refined_structure=refined_payload if refined_payload is not None else payload,
        roundtrip_reconciliation=roundtrip,
    )


def _has_cycle(node_ids, links: list[StructureLink]) -> bool:
    adj: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for link in links:
        adj[link.parent_id].append(link.child_id)
    WHITE, GREY, BLACK = 0, 1, 2
    color = {nid: WHITE for nid in node_ids}

    def visit(nid: str) -> bool:
        color[nid] = GREY
        for nxt in adj[nid]:
            if color[nxt] == GREY:
                return True
            if color[nxt] == WHITE and visit(nxt):
                return True
        color[nid] = BLACK
        return False

    return any(color[nid] == WHITE and visit(nid) for nid in node_ids)


def _reachable_from(root: str, links: list[StructureLink]) -> set[str]:
    adj: dict[str, list[str]] = {}
    for link in links:
        adj.setdefault(link.parent_id, []).append(link.child_id)
    seen: set[str] = set()
    stack = [root]
    while stack:
        nid = stack.pop()
        for child in adj.get(nid, []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return seen


def _children_of(parent: str, links: list[StructureLink]) -> list[StructureLink]:
    return [l for l in links if l.parent_id == parent]


def _derive_structural_signals(
    *, nodes: list[StructureNode], links: list[StructureLink],
    node_index: dict[str, StructureNode], grouping_ids: set[str],
    guarantor_ids: set[str], is_simulated: bool,
) -> list[StructuralCreditSignal]:
    """Produce concise, grounded, qualified structural observations.

    These are descriptive observations only. Restrictions are honoured: ownership
    does not imply unrestricted cash access, an OpCo role does not prove cash
    generation, a guarantor marking does not prove enforceability/coverage, and
    holdco layers are not treated as risky per se. If the JSON supports only
    descriptive observations they are reported as neutral or insufficient
    evidence; no signal is manufactured.
    """

    signals: list[StructuralCreditSignal] = []
    base_qual = (["Derived from a simulated/illustrative structure; not a verified fact."]
                 if is_simulated else [])

    def qual(*extra: str) -> list[str]:
        return [*base_qual, *extra]

    # Borrower position in the hierarchy (role token contains "borrower").
    borrowers = [n for n in nodes if "borrower" in (n.role or "").casefold()]
    depth = _depths(links)
    if borrowers:
        b = borrowers[0]
        parents_above = depth.get(b.node_id, 0)
        children = _children_of(b.node_id, links)
        signals.append(StructuralCreditSignal(
            signal_id="struct.borrower_position",
            dimension="borrower_position",
            observation=(f"The designated borrower sits {parents_above} ownership "
                         f"level(s) below the ultimate parent with "
                         f"{len(children)} direct subsidiary link(s)."),
            credit_direction=Direction.NEUTRAL,
            credit_mechanism=("Borrower position relative to operating subsidiaries "
                              "affects structural proximity to operating cash and "
                              "assets; this is a descriptive observation only."),
            source_node_ids=[b.node_id],
            source_link_ids=[l.link_id for l in children],
            assessment_availability="sufficient",
            materiality=Materiality.MEDIUM,
            confidence=Confidence.MEDIUM,
            qualifications=qual(
                "Holding-company layers above the borrower are not treated as "
                "risky in themselves; actual structural subordination depends on "
                "debt, guarantees and restrictions not shown in the diagram."),
        ))
    else:
        signals.append(StructuralCreditSignal(
            signal_id="struct.borrower_position",
            dimension="borrower_position",
            observation=("No node in the structure is designated as the borrower."),
            credit_direction=Direction.NEUTRAL,
            credit_mechanism=("Borrower designation is required to assess structural "
                              "position; it is absent from the structure JSON."),
            assessment_availability="insufficient_evidence",
            materiality=Materiality.MEDIUM,
            confidence=Confidence.LOW,
            qualifications=qual("Borrower must be confirmed from case transaction evidence."),
        ))

    # Operating subsidiaries vs borrower (opco role tokens).
    opcos = [n for n in nodes if "opco" in (n.role or "").casefold()
             or "operating" in (n.role or "").casefold()]
    if opcos:
        signals.append(StructuralCreditSignal(
            signal_id="struct.operating_vs_borrower",
            dimension="operating_vs_borrower",
            observation=(f"The structure shows {len(opcos)} operating-company node(s) "
                         "beneath holding/borrower levels."),
            credit_direction=Direction.NEUTRAL,
            credit_mechanism=("Operating subsidiaries are where operations typically "
                              "sit; their position relative to the borrower is "
                              "descriptive only and does not prove cash generation."),
            source_node_ids=[n.node_id for n in opcos],
            assessment_availability="sufficient",
            materiality=Materiality.MEDIUM,
            confidence=Confidence.MEDIUM,
            qualifications=qual(
                "An OpCo role label does not prove material cash generation; no "
                "financial figures are inferred from the diagram."),
        ))

    # Minority / partial ownership and JV exposure.
    partial_links = [l for l in links
                     if l.ownership_percent is not None and l.ownership_percent < 100]
    jv_nodes = [n for n in nodes if n.node_id in grouping_ids
                and "jv" in (n.role or "").casefold()]
    if partial_links or jv_nodes:
        signals.append(StructuralCreditSignal(
            signal_id="struct.minority_or_jv_exposure",
            dimension="minority_or_jv_exposure",
            observation=(f"{len(partial_links)} ownership link(s) are below 100% and "
                         f"{len(jv_nodes)} joint-venture / minority grouping node(s) "
                         "are present."),
            credit_direction=Direction.NEUTRAL,
            credit_mechanism=("Less-than-wholly-owned entities can carry minority "
                              "interests and distribution leakage; the diagram shows "
                              "ownership only, not the governing agreements."),
            source_link_ids=[l.link_id for l in partial_links],
            source_node_ids=[n.node_id for n in jv_nodes],
            assessment_availability="sufficient",
            materiality=Materiality.MEDIUM,
            confidence=Confidence.MEDIUM,
            qualifications=qual(
                "Minority / JV cash-sharing terms are not shown; actual leakage "
                "cannot be inferred from ownership percentages alone."),
        ))

    # Structural complexity (count of levels and nodes).
    max_depth = max(depth.values(), default=0)
    signals.append(StructuralCreditSignal(
        signal_id="struct.complexity",
        dimension="structural_complexity",
        observation=(f"The structure has {len(nodes)} node(s) across "
                     f"{max_depth + 1} ownership level(s)."),
        credit_direction=Direction.NEUTRAL,
        credit_mechanism=("Structural depth and breadth describe complexity; "
                          "complexity alone is neither favourable nor adverse."),
        source_node_ids=[n.node_id for n in nodes],
        assessment_availability="sufficient",
        materiality=Materiality.LOW,
        confidence=Confidence.MEDIUM,
        qualifications=qual("Complexity is descriptive; it is not scored as risk."),
    ))

    # Illustrative guarantor coverage.
    if guarantor_ids:
        signals.append(StructuralCreditSignal(
            signal_id="struct.guarantor_coverage",
            dimension="illustrative_guarantor_coverage",
            observation=(f"{len(guarantor_ids)} node(s) carry an illustrative "
                         "guarantor marking."),
            credit_direction=Direction.NEUTRAL,
            credit_mechanism=("Guarantor markings indicate intended credit support in "
                              "the diagram; coverage of obligations is not shown."),
            source_node_ids=sorted(guarantor_ids),
            assessment_availability="sufficient",
            materiality=Materiality.MEDIUM,
            confidence=Confidence.MEDIUM,
            qualifications=qual(
                "A guarantor marking does not establish an enforceable guarantee, "
                "its ranking, or the share of debt it covers."),
        ))

    # Potential dependency on subsidiary distributions / cash-access limits — the
    # diagram supports only a descriptive, insufficient-evidence observation.
    signals.append(StructuralCreditSignal(
        signal_id="struct.distribution_dependency",
        dimension="subsidiary_distribution_dependency",
        observation=("The diagram shows ownership links but no distribution, "
                     "restriction or cash-balance data."),
        credit_direction=Direction.NEUTRAL,
        credit_mechanism=("Repayment at a holding/borrower level can depend on "
                          "upstream distributions; the diagram cannot confirm this."),
        assessment_availability="insufficient_evidence",
        materiality=Materiality.MEDIUM,
        confidence=Confidence.LOW,
        qualifications=qual(
            "Ownership does not imply unrestricted access to subsidiary cash; "
            "actual transfer restrictions are not shown in the structure JSON."),
    ))

    return signals


def _depths(links: list[StructureLink]) -> dict[str, int]:
    """Depth of each node from its top-most ancestor (root depth = 0)."""
    parent_of: dict[str, str] = {l.child_id: l.parent_id for l in links}
    depths: dict[str, int] = {}

    def depth(nid: str, seen: set[str]) -> int:
        if nid in depths:
            return depths[nid]
        parent = parent_of.get(nid)
        if parent is None or parent in seen:
            depths[nid] = 0
            return 0
        d = depth(parent, seen | {nid}) + 1
        depths[nid] = d
        return d

    all_nodes = set(parent_of) | {l.parent_id for l in links}
    for nid in all_nodes:
        depth(nid, set())
    return depths
