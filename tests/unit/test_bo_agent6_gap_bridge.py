"""Deterministic gap decisions; HCA names occur in fixtures only."""
import re

import pytest

from app.services.bo.resilience_retrieval import annotate_gap_searches, bridge_requests
from app.services.bo.retrieval import RetrievalBudget

CONTRACT = "Specific contract renewal schedule and percentage of managed care contracts expiring within the next 12-24 months."
SAVINGS = "Quantitative net cost savings attributable specifically to Galen College of Nursing clinical graduate hires."
ATTRIBUTION = "Direct quantitative attribution of dollar savings specifically achieved via Galen College graduate placement."


def record(gaps=None, kind="compound_risk", **kw):
    return {"hypothesis_id": "M1A6-H001", "materiality": "high", "admissibility": "qualified_narrative",
            "disposition": "qualified", "interaction_kind": kind,
            "claim": "Customer contract renewals and service volumes may compound operating costs.",
            "missing_information": gaps or [], **kw}


def decide(gaps=None, records=None, explicit=None, passages=None, budget=None):
    return bridge_requests({"missing_information": gaps, "retrieval_requests": explicit or []},
                           records or [record()], passages or [], budget or RetrievalBudget())


def test_sources_malformed_entries_and_global_association():
    r = record(["Reported customer contract renewal schedule.", None, "", 42])
    requests, _, gaps = decide(["Reported customer contract renewal schedule."], [r])
    assert len(gaps) == 5 and len(requests) == 1
    assert gaps[0]["hypothesis_id"] == "M1A6-H001"
    assert gaps[-1]["hypothesis_id"] is None and gaps[-1]["source"] == "response_level"
    assert gaps[-1]["outcome"] == "duplicate" and gaps[-1]["query_index"] == 0
    assert all(g["outcome"] == "invalid_gap" for g in gaps[1:4])
    assert all(g["unresolved"] for g in gaps)
    assert decide(None)[0] == []
    assert decide("not a list")[2][0]["outcome"] == "invalid_gap"


def test_global_material_gap_stays_global():
    requests, audits, gaps = decide(["Reported customer contract renewal schedule."])
    assert requests[0]["hypothesis_id"] is None
    assert requests[0]["purpose"] == "support"
    assert audits[0]["request_origin"] == "missing_information_bridge"
    assert gaps[0]["materiality_basis_ids"] == ["M1A6-H001"]


@pytest.mark.parametrize("text,outcome", [
    ("More information", "not_specific"),
    ("Financial business performance outcomes", "not_specific"),
    ("Competitors' confidential internal staffing records.", "unavailable"),
    ("Undisclosed customer contract renewal schedule.", "unavailable"),
    ("Prediction of actual future customer service volumes.", "unavailable"),
    ("Savings attribution not separately disclosed for the workforce initiative.", "unavailable"),
    ("Exact volcanic mineral deposits and isotope composition.", "no_material_relationship"),
])
def test_conservative_rejections(text, outcome):
    requests, _, gaps = decide([text])
    assert not requests and gaps[0]["outcome"] == outcome and gaps[0]["unresolved"]


def test_low_materiality_does_not_trigger_a_search():
    assert not decide(records=[record(["Reported customer contract renewal schedule."], materiality="low")])[0]


def test_safe_purpose_assignment_and_ambiguity():
    assert decide(records=[record(["Reported outcomes of workforce training initiative."], "mitigating_capability")])[0][0]["purpose"] == "mitigant_challenge"
    assert decide(records=[record(["Existence of workforce training capability."], "mitigating_capability")])[0][0]["purpose"] == "mitigant"
    assert decide(records=[record(["Counterevidence about customer contract renewal schedule."])])[0][0]["purpose"] == "contradict"
    assert decide(records=[record(["Evidence challenging customer contract renewal assumptions."])])[0][0]["purpose"] == "contradict"
    a = record(claim="Workforce training initiative outcomes may affect risk.")
    b = record(kind="mitigating_capability", hypothesis_id="M1A6-H002", claim=a["claim"])
    requests, _, gaps = decide(["Reported workforce training initiative outcomes."], [a, b])
    assert not requests and gaps[0]["outcome"] == "ambiguous_purpose"


def test_explicit_priority_and_budget_are_shared():
    explicit = [{"purpose": "contradict", "terms": ["original explicit pattern"], "hypothesis_id": "M1A6-H001"}]
    requests, audits, gaps = decide(["Reported customer contract renewal schedule."], explicit=explicit,
                                   budget=RetrievalBudget(max_followup_requests=1))
    assert requests == explicit
    assert audits[0]["request_origin"] == "agent_explicit"
    assert gaps[0]["outcome"] == "request_budget_exhausted"
    explicit[0].update(purpose="support", terms=["customer contract", "renewal schedule"])
    assert decide(["Reported customer contract renewal schedule."], explicit=explicit)[2][0]["outcome"] == "duplicate"


def test_query_entities_original_vocabulary_and_regex_escaping():
    text = "Reported cost savings from Northstar Research Institute graduate hires."
    r = record([text], "mitigating_capability")
    requests, _, gaps = decide(records=[r], budget=RetrievalBudget(max_followup_terms=3))
    terms = requests[0]["terms"]
    assert len(terms) <= 3
    assert re.escape("Northstar Research Institute") in terms
    assert re.escape("cost savings") in terms
    assert not any("Reported" in t for t in terms)
    special = "Reported cost savings from Acme+ Lab (Pilot.*) placements."
    terms = decide(records=[record([special], "mitigating_capability")])[0][0]["terms"]
    assert any(r"\+" in t for t in terms)
    assert all(re.compile(t) for t in terms)
    assert gaps[0]["original_gap"] == text


def test_initial_evidence_prevents_redundant_query_without_claiming_resolution():
    gap = "Reported acquired business revenue."
    requests, _, gaps = decide(records=[record([gap])], passages=[{
        "evidence_id": "ev_existing", "text": "Acquired business revenue was $40 million."}])
    assert not requests and gaps[0]["outcome"] == "already_in_packet"
    assert gaps[0]["already_admitted_evidence_ids"] == ["ev_existing"]
    assert gaps[0]["unresolved"]


def test_causal_attribution_is_never_resolved_by_a_co_mention():
    gap = "Quantitative cost savings attributable to Acme graduate hires."
    requests, _, gaps = decide(records=[record([gap], "mitigating_capability")], passages=[{
        "evidence_id": "ev_mention", "text": "Acme graduate hires and cost savings were discussed."}])
    assert requests and gaps[0]["unresolved"]


def test_hca_run2_regression_fixture():
    risk = record(claim="Fixed reimbursement schedules and multi-year managed care contracts restrict immediate pricing flexibility, creating operating margin vulnerability when clinical labor costs increase.")
    mitigation = record([ATTRIBUTION], "mitigating_capability", hypothesis_id="M1A6-H002",
        claim="Workforce development programs provide operating mitigants against clinical staffing shortages.")
    requests, audits, gaps = decide([CONTRACT, SAVINGS], [risk, mitigation])
    assert len(requests) == 2
    assert requests[0]["purpose"] == "mitigant_challenge" and requests[0]["hypothesis_id"] == "M1A6-H002"
    assert requests[1]["purpose"] == "support" and requests[1]["hypothesis_id"] is None
    assert any("Galen" in term for term in requests[0]["terms"])
    assert any("renewal" in term for term in requests[1]["terms"])
    assert gaps[2]["outcome"] == "duplicate" and gaps[2]["hypothesis_id"] is None
    assert (requests, audits, gaps) == decide([CONTRACT, SAVINGS], [risk, mitigation])


def test_gap_outcomes_link_to_one_search_without_marking_a_hit_as_resolution():
    _, _, gaps = decide(["Reported customer contract renewal schedule."])
    attempts = [{"attempt_id": "a1", "results": [{"evidence_id": "ev_new"}]}]
    annotate_gap_searches(gaps, attempts, {"ev_new"}, reconsideration_occurred=True)
    assert gaps[0]["attempt_ids"] == ["a1"]
    assert gaps[0]["new_evidence_admitted"] and gaps[0]["reconsideration_occurred"]
    assert gaps[0]["unresolved"]
