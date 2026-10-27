"""Case-bound hypothetical facts, sealed separately from real filing evidence.

Only validated onboarding structure can create this passage. It establishes
scenario assumptions, never real legal identity, debt, enforceability or cash.
"""
from dataclasses import replace

from app.core.hashing import content_hash
from app.services.agents.router import EvidenceRouter


CASE_EVIDENCE_VERSION = "m3-hypothetical-case-1.0"


def scenario_passage(facts, case_id):
    source = (facts or {}).get("source") or {}
    if not case_id or not source.get("is_simulated") or not source.get("document_hash"):
        return None
    lines = ["Authoritative assumptions within this hypothetical lending case only; "
             "not verified actual company legal facts.",
             f"Case: {case_id}. Onboarding source: {source.get('source_document_id')}; "
             f"hash: {source['document_hash']}."]
    for n in facts.get("nodes", []):
        lines.append(f"Node {n['node_id']}: {n['display_name']}; role {n['role']}; "
                     f"assumed guarantor {'yes' if n['is_guarantor'] else 'no'}; "
                     f"aggregate grouping {'yes' if n['is_grouping_node'] else 'no'}.")
    for link in facts.get("links", []):
        pct = link.get("ownership_percent")
        lines.append(f"Ownership {link['link_id']}: {link['parent_id']} -> {link['child_id']}; "
                     f"assumed ownership {pct:g}%." if pct is not None else
                     f"Ownership {link['link_id']}: {link['parent_id']} -> {link['child_id']}; percentage unspecified.")
    borrower = (facts.get("borrower") or {}).get("node_id")
    lines.append(f"Hypothetical borrower node: {borrower or 'unspecified'}.")
    lines.append("Missing documentation: actual subsidiary debt and creditor ranking, legally enforceable "
                 "guarantee terms, cash-flow amounts, dividend rights and distribution restrictions. "
                 "Ownership and scenario guarantor assumptions do not establish these matters.")
    text = "\n".join(lines)
    eid = content_hash({"policy": CASE_EVIDENCE_VERSION, "case_id": case_id, "text": text})
    return {"evidence_id": eid, "document_id": source.get("source_document_id"),
            "section": "Hypothetical lending-case assumptions", "heading": "Scenario facts; not SEC evidence",
            "location": None, "text": text, "retrieved_for": ["support", "limitations"],
            "evidence_category": "hypothetical_case_assumptions"}


def attach_case_evidence(prep):
    """Seal one deterministic case passage in M3's packet, not the SEC corpus."""
    from copy import deepcopy
    facts = deepcopy(prep.structural_facts)
    passage = scenario_passage(facts, prep.case_id)
    if passage is None:
        return prep
    eid = passage["evidence_id"]
    facts["source"].update(authority="hypothetical_case_assumptions", case_id=prep.case_id,
                            scenario_evidence_id=eid, real_world_verification="not_established")
    passages = [*prep.passages, passage]
    packet = prep.packet.model_copy(update={"narrative_evidence": passages,
        "evidence_ids": sorted({*prep.packet.evidence_ids, eid}), "packet_hash": None})
    packet = packet.model_copy(update={"packet_hash": EvidenceRouter.hash_packet(packet)})
    return replace(prep, structural_facts=facts, passages=passages, packet=packet,
        passage_text={**prep.passage_text, eid: passage["text"]},
        corpus=[*prep.corpus, passage],
        m3_selection_summary={**prep.m3_selection_summary,
            "additional_case_assumption_passages": 1, "case_assumption_chars": len(passage["text"]),
            "case_evidence_policy": CASE_EVIDENCE_VERSION})


def verified_scenario_source(facts, allowed, texts):
    """Reject invented IDs/text/authority; recompute from trusted case context."""
    source = (facts or {}).get("source") or {}
    if source.get("authority") != "hypothetical_case_assumptions":
        return None
    passage = scenario_passage(facts, source.get("case_id"))
    if (not passage or source.get("scenario_evidence_id") != passage["evidence_id"]
            or passage["evidence_id"] not in allowed
            or texts.get(passage["evidence_id"]) != passage["text"]):
        return None
    return passage["evidence_id"]
