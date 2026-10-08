"""Milestone 3 — Evidence Router (Req 4, 26).

Proves: packets are narrowly scoped (business-model packet has no covenant
terms; covenant packet has no management bios); packets are hashed and the hash
is stable yet content-sensitive; irrelevant evidence is excluded; conflicts are
only surfaced for fields the agent can see.
"""

from __future__ import annotations

from app.schemas.agentic import EvidencePacket
from app.services.agents.router import (
    EvidenceRouter,
    RouterInputs,
    RoutingSpec,
)


def _inputs() -> RouterInputs:
    return RouterInputs(
        case_id="DAL_2024",
        snapshot_version=1,
        analysis_run_id="AR1",
        facts=[
            {"fact_id": "f_rev", "name": "revenue", "value": 63364},
            {"fact_id": "f_debt", "name": "total_debt", "value": 20000},
            {"fact_id": "f_ceo", "name": "ceo_bio", "topic": "management",
             "statement": "CEO since 2016"},
        ],
        parameters=[
            {"parameter_id": "net_leverage", "topic": "financial", "value": 3.07},
        ],
        narrative_evidence=[
            {"evidence_id": "n_mgmt", "candidate_topics": ["management"],
             "text": "The CEO has led since 2016."},
            {"evidence_id": "n_mkt", "candidate_topics": ["competitive_position"],
             "text": "Market share grew in 2024."},
        ],
        facility_terms=[{"id": "ft_amt", "term": "facility_amount", "value": 1000}],
        covenant_terms=[
            {"id": "cv_lev", "covenant": "max_net_leverage", "threshold": 3.5,
             "text": "Maximum net leverage of 3.50x."}
        ],
        open_conflicts=[{"field": "revenue", "state": "conflicting",
                         "values": [63364, 63000]}],
        data_limitations=[{"field": "customer_concentration", "state": "missing"}],
    )


def _business_model_spec() -> RoutingSpec:
    return RoutingSpec(
        agent_id="business_model",
        fact_selector=lambda f: f.get("topic") == "management"
        or f.get("name") in {"revenue"},
        narrative_selector=lambda n: "management" in n.get("candidate_topics", []),
        # NB: no covenant_term_selector / facility_term_selector => excluded.
    )


def _covenant_spec() -> RoutingSpec:
    return RoutingSpec(
        agent_id="covenant_extraction",
        covenant_term_selector=lambda c: True,
        parameter_selector=lambda p: p.get("parameter_id") == "net_leverage",
        # No fact/narrative selectors => no management bios.
    )


def test_business_model_packet_excludes_covenant_terms():
    router = EvidenceRouter()
    packet = router.build_packet(_business_model_spec(), _inputs())
    assert packet.covenant_terms == []
    assert packet.facility_terms == []
    # It DID receive its management narrative + management fact.
    assert any(n["evidence_id"] == "n_mgmt" for n in packet.narrative_evidence)
    assert any(f["fact_id"] == "f_ceo" for f in packet.facts)
    # It did NOT receive unrelated competitive narrative (selector excluded it).
    assert all(n["evidence_id"] != "n_mkt" for n in packet.narrative_evidence)


def test_covenant_packet_excludes_management_bios():
    router = EvidenceRouter()
    packet = router.build_packet(_covenant_spec(), _inputs())
    assert packet.narrative_evidence == []  # no management bios
    assert all(f.get("topic") != "management" for f in packet.facts)
    assert any(c["covenant"] == "max_net_leverage" for c in packet.covenant_terms)


def test_packet_is_hashed_and_hash_is_stable():
    router = EvidenceRouter()
    p1 = router.build_packet(_business_model_spec(), _inputs())
    p2 = router.build_packet(_business_model_spec(), _inputs())
    assert p1.packet_hash is not None
    assert p1.packet_hash == p2.packet_hash  # deterministic


def test_packet_hash_changes_with_content():
    router = EvidenceRouter()
    base = router.build_packet(_business_model_spec(), _inputs())
    # A spec that also pulls the competitive narrative changes the packet.
    wider = RoutingSpec(
        agent_id="business_model",
        fact_selector=lambda f: f.get("topic") == "management"
        or f.get("name") in {"revenue"},
        narrative_selector=lambda n: True,
    )
    changed = router.build_packet(wider, _inputs())
    assert changed.packet_hash != base.packet_hash


def test_router_only_surfaces_relevant_conflicts():
    router = EvidenceRouter()
    # Business-model agent sees 'revenue' fact, so the revenue conflict surfaces,
    # but the missing customer_concentration limitation (an unseen field) does not.
    packet = router.build_packet(_business_model_spec(), _inputs())
    assert any(c["field"] == "revenue" for c in packet.open_conflicts)
    assert all(
        limit["field"] != "customer_concentration" for limit in packet.data_limitations
    )


def test_default_spec_excludes_every_category():
    router = EvidenceRouter()
    empty = router.build_packet(RoutingSpec(agent_id="noop"), _inputs())
    assert empty.facts == []
    assert empty.parameters == []
    assert empty.narrative_evidence == []
    assert empty.covenant_terms == []
    assert empty.facility_terms == []
    assert empty.evidence_ids == []


def test_from_snapshot_projects_conflicts_and_limitations():
    from app.schemas.snapshots import CanonicalEvidenceSnapshot

    snap = CanonicalEvidenceSnapshot(
        case_id="DAL_2024",
        facts=[{"fact_id": "f1", "name": "revenue"}],
        narrative_evidence=[{"evidence_id": "n1", "text": "x",
                             "candidate_topics": []}],
        data_quality={
            "revenue": {"field": "revenue", "state": "conflicting",
                        "values": [1, 2]},
            "cash": {"field": "cash", "state": "missing"},
        },
    )
    inp = RouterInputs.from_snapshot(snap, analysis_run_id="AR1")
    assert any(c["field"] == "revenue" for c in inp.open_conflicts)
    assert any(limit["field"] == "cash" for limit in inp.data_limitations)
    assert inp.analysis_run_id == "AR1"


def test_evidence_ids_collected_and_sorted():
    router = EvidenceRouter()
    spec = RoutingSpec(
        agent_id="all",
        fact_selector=lambda f: True,
        narrative_selector=lambda n: True,
        covenant_term_selector=lambda c: True,
    )
    packet = router.build_packet(spec, _inputs())
    assert packet.evidence_ids == sorted(packet.evidence_ids)
    assert "f_rev" in packet.evidence_ids
    assert "n_mgmt" in packet.evidence_ids


def test_packet_round_trips_as_model():
    router = EvidenceRouter()
    packet = router.build_packet(_covenant_spec(), _inputs())
    restored = EvidencePacket.model_validate(packet.model_dump(mode="json"))
    assert restored.packet_hash == packet.packet_hash
    assert EvidenceRouter.hash_packet(restored) == packet.packet_hash
