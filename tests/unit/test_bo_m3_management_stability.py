"""Observable leadership facts; positive, negative and neutral admission offline."""

from copy import deepcopy

import pytest

from app.services.bo.m3_management import stability_view
from app.services.bo.m3_validation import validate_m3_assessment
from app.services.bo.m3_grounding import strengthen_m3_assessment, gate_m3_parameters


HEADER = "As of February 1, 2026, our executive officers were as follows."
ROLES = {"CEO": "Chief Executive Officer", "CFO": "Chief Financial Officer", "COO": "Chief Operating Officer"}


def profile(pid="ceo", name="Dana Lee", role="CEO", appointed="2025-01-01", internal=None,
            external=False, industry=False, planned=False, abrupt=False):
    full_role = ROLES[role]
    month = {"2019-01": "January 2019", "2024-05-01": "May 1, 2024", "2025-01-01": "January 1, 2025"}[appointed]
    facts, sentences = [], []

    def fact(kind, value, quote, when=None, end=None):
        sentences.append(quote)
        facts.append({"fact_id": f"{pid}-{kind}", "kind": kind, "value": value, "date": when,
                      "end_date": end, "source": {"evidence_id": pid, "quote": quote}})

    fact("appointment", full_role, f"{name} was appointed {full_role} effective {month}.", appointed)
    if internal is True:
        prior_end = str(int(appointed[:4]) - 1)
        fact("prior_internal_role", "Vice President", f"{name} previously served as Vice President of the Company from 2004 to {prior_end}.", "2004", prior_end)
        fact("company_join", "the Company", f"{name} joined the Company in 2004.", "2004")
        fact("internal_promotion", "promoted internally", f"{name} was promoted internally at the Company.")
    if external:
        fact("external_appointment", "recruited externally", f"{name} was recruited externally as {full_role}.")
    if internal is False:
        fact("no_prior_company_experience", "no prior experience", f"{name} had no prior experience at the Company.")
    if industry:
        fact("external_role", "Meridian Group", f"{name} previously worked in the industry at Meridian Group as an executive.")
    if planned:
        fact("planned_succession", "planned succession", f"{name} was appointed under a planned succession at the Company.")
    if abrupt:
        fact("departure", "abrupt departure", f"{name} replaced the outgoing executive following an abrupt departure on January 1, 2025.", "2025-01-01")
    return {"executive_id": pid, "name": name, "current_role": full_role, "facts": facts}, {pid: " ".join(sentences)}


def observation(profiles, direction="positive", hypothesis=None, **overrides):
    finding = {
        "observation_id": "continuity-1", "management_id": "reviewed-team", "legal_name": "Disclosed executive group",
        "role": "Senior executives", "dimension": "transition_risk" if direction == "negative" else "leadership_continuity",
        "evidence_basis": "disclosed_leadership_history", "attribution": "company_reported",
        "hypothesis": hypothesis or (
            "The recent external appointments may increase company-specific learning and coordination uncertainty."
            if direction == "negative" else "Disclosed internal experience may support leadership continuity and company familiarity."),
        "credit_mechanism": "Leadership continuity or transition uncertainty may affect operating coordination and cash-flow predictability.",
        "executive_ids": [p["executive_id"] for p in profiles],
        "fact_ids": [f["fact_id"] for p in profiles for f in p["facts"]],
        "evidence_summary": "Disclosed appointment and role history, with exact quoted evidence.",
        "supporting_evidence_ids": [p["executive_id"] for p in profiles], "contradicting_evidence_ids": [],
        "supporting_evidence_investigated": True, "contradictory_evidence_investigated": True,
        "disposition": "qualified", "admissibility": "qualified_narrative", "credit_direction": direction,
        "materiality": "medium", "confidence": "medium", "missing_information": [],
        "uncertainties": ["Realised disruption and decision-making quality are not established."],
        "mitigating_considerations": ["Disclosed external expertise and succession arrangements may mitigate learning risk."],
    }
    finding.update(overrides)
    return finding


def govern(profiles, texts, findings, **stability_overrides):
    stability = {"schema_version": "bo-m3-management-stability-1.0", "as_of_date": "2026-02-01",
        "as_of_source": {"evidence_id": "header", "quote": HEADER}, "recent_change_window_years": 3,
        "review_scope": "ABC Corporation annual filing, disclosed CEO/CFO/COO biographies and transition risks.",
        "executives": profiles, "limitations": ["Only disclosed role histories are assessed."]}
    stability.update(stability_overrides)
    proposal = {"schema_version": "bo-m3-corporate-assessment-1.2",
        "borrower_identification": {"status": "unavailable", "limitation": "No transaction evidence."},
        "management_assessments": findings, "management_stability": stability,
        "information_gaps": ["Governance and corporate structure remain separate reviews."]}
    texts = {"header": HEADER, **texts}
    before = deepcopy(proposal)
    result = validate_m3_assessment(proposal, allowed_evidence_ids=set(texts), evidence_texts=texts,
                                    management_company_aliases=("ABC Corporation",))
    result, _ = strengthen_m3_assessment(result, evidence_texts=texts, structure_facts=None)
    assert proposal == before
    data = result.model_dump(mode="json")
    return data, stability_view(data["management_stability"], data["management_assessments"])


def admitted(data):
    return [o for o in data["management_assessments"] if o["disposition"] in {"qualified", "supported"}]


def test_long_serving_ceo_internal_experience_can_be_positive_without_group_overclaim():
    p, texts = profile(appointed="2019-01", internal=True)
    data, view = govern([p], texts, [observation([p])])
    assert len(admitted(data)) == 1
    detail = view["key_executive_details"][0]
    assert detail["appointment_date"] == "2019-01"
    assert detail["current_role_tenure"]["appointment_date_precision"] == "month"
    assert detail["current_role_tenure"]["min_completed_months"] <= detail["current_role_tenure"]["max_completed_months"]
    assert view["consolidated_management_stability_conclusion"]["classification"] == "insufficient_information"


def test_recent_cfo_internal_promotion_not_automatically_negative():
    p, texts = profile("cfo", "Alex Chen", "CFO", "2024-05-01", internal=True)
    neg = observation([p], "negative", observation_id="unsupported-negative")
    data, view = govern([p], texts, [observation([p], "neutral"), neg])
    detail = view["key_executive_details"][0]
    assert detail["transition_type"] == "internal_promotion"
    assert detail["recent_appointment"] == "yes"
    assert detail["company_tenure"]["min_completed_months"] >= 20 * 12
    assert {o["credit_direction"] for o in admitted(data)} == {"neutral"}


@pytest.mark.parametrize("abrupt", [False, True])
def test_recent_external_ceo_and_cfo_without_prior_service_can_be_credit_negative(abrupt):
    a, ta = profile(internal=False, external=True, abrupt=abrupt)
    b, tb = profile("cfo", "Alex Chen", "CFO", internal=False, external=True, abrupt=abrupt)
    data, view = govern([a, b], {**ta, **tb}, [observation([a, b], "negative")])
    assert len(admitted(data)) == 1
    assert view["consolidated_management_stability_conclusion"]["credit_direction"] == "negative"
    assert view["overall_management_stability_assessment"]["change_history_status"] == "changes_established"
    assert all(p["prior_company_experience"] == "explicitly_absent" for p in view["key_executive_details"])


def test_external_ceo_industry_experience_is_mitigation_and_not_an_overall_negative():
    p, texts = profile(internal=False, external=True, industry=True)
    data, view = govern([p], texts, [observation([p], "negative")])
    assert len(admitted(data)) == 1
    assert view["key_executive_details"][0]["external_roles"] == ["Meridian Group"]
    assert view["credit_findings"][0]["mitigating_considerations"]
    assert view["consolidated_management_stability_conclusion"]["credit_direction"] != "negative"


def test_negative_finding_cannot_ignore_disclosed_external_experience_mitigation():
    p, texts = profile(internal=False, external=True, industry=True)
    data, _ = govern([p], texts, [observation([p], "negative", mitigating_considerations=[])])
    assert not admitted(data)


@pytest.mark.parametrize("direction", ["neutral", "positive"])
def test_planned_internal_coo_succession_can_be_neutral_or_positive(direction):
    p, texts = profile("coo", "Morgan Reed", "COO", internal=True, planned=True)
    data, _ = govern([p], texts, [observation([p], direction)])
    assert len(admitted(data)) == 1


def test_one_external_appointment_with_otherwise_stable_leadership_not_overall_negative():
    ceo, a = profile(internal=False, external=True)
    cfo, b = profile("cfo", "Alex Chen", "CFO", "2019-01", internal=True)
    coo, c = profile("coo", "Morgan Reed", "COO", "2019-01", internal=True)
    data, view = govern([ceo, cfo, coo], {**a, **b, **c}, [
        observation([ceo], "negative"), observation([cfo, coo], observation_id="internal-positive")])
    assert len(admitted(data)) == 2
    assert view["consolidated_management_stability_conclusion"]["credit_direction"] == "mixed"


def test_missing_internal_history_is_unknown_not_absent_even_for_external_hire():
    p, texts = profile(internal=None, external=True)
    data, view = govern([p], texts, [observation([p], "negative")])
    assert not admitted(data)
    assert view["key_executive_details"][0]["prior_company_experience"] == "unknown"


def test_missing_biographies_do_not_become_inexperienced_or_stable():
    p = {"executive_id": "ceo", "name": "Dana Lee", "current_role": ROLES["CEO"], "facts": []}
    data, view = govern([p], {}, [])
    assert not admitted(data)
    assert view["key_executive_details"][0]["prior_company_experience"] == "unknown"
    assert view["consolidated_management_stability_conclusion"]["classification"] == "insufficient_information"


def test_single_filing_cannot_create_a_turnover_frequency_or_no_change_claim():
    p, texts = profile(appointed="2019-01", internal=True)
    data, view = govern([p], texts, [observation([p], hypothesis="No recent leadership changes occurred; continuity may support execution.")])
    assert not admitted(data)
    overall = view["overall_management_stability_assessment"]
    assert overall["history_coverage"] == "partial"
    assert overall["change_history_status"] == "insufficient_information"
    assert "turnover_frequency" not in overall


def test_continuity_cannot_be_used_to_assert_superior_decision_making():
    p, texts = profile(appointed="2019-01", internal=True)
    data, _ = govern([p], texts, [observation([p], hypothesis="Long tenure may establish superior decision-making ability.")])
    assert not admitted(data)


def test_transition_risk_does_not_establish_realised_disruption():
    p, texts = profile(internal=False, external=True)
    data, _ = govern([p], texts, [observation([p], "negative", hypothesis="The appointments have disrupted financial management.")])
    assert not admitted(data)


def test_explicit_complete_period_review_can_support_a_scoped_no_change_finding():
    p, texts = profile(appointed="2019-01", internal=True)
    review = ("A comprehensive leadership review of CEO, CFO and COO roles from 2023-02-01 to 2026-02-01 "
              "found no leadership changes.")
    finding = observation([p], hypothesis="No recent leadership changes were identified in the reviewed period; internal history may support continuity.",
                          supporting_evidence_ids=["ceo", "history"])
    data, view = govern([p], {**texts, "history": review}, [finding], history_start="2023-02-01", history_end="2026-02-01",
                       history_source={"evidence_id": "history", "quote": review})
    assert len(admitted(data)) == 1
    assert view["overall_management_stability_assessment"]["change_history_status"] == "no_changes_established"


def test_recent_appointment_counterexample_blocks_a_no_change_review():
    p, texts = profile(internal=True)
    review = ("A comprehensive leadership review of CEO, CFO and COO roles from 2023-02-01 to 2026-02-01 "
              "found no leadership changes.")
    data, _ = govern([p], {**texts, "history": review}, [observation([p], hypothesis="No recent leadership changes occurred; continuity may help.",
                       supporting_evidence_ids=["ceo", "history"])], history_start="2023-02-01", history_end="2026-02-01",
                       history_source={"evidence_id": "history", "quote": review})
    assert not admitted(data)


@pytest.mark.parametrize("negated", [True, False])
def test_hypothetical_or_negated_external_recruitment_is_not_an_established_fact(negated):
    p, texts = profile(internal=False, external=True)
    fact = next(f for f in p["facts"] if f["kind"] == "external_appointment")
    sentence = ("Dana Lee was not recruited externally as Chief Executive Officer." if negated else
                "If Dana Lee were recruited externally, transition risk could increase.")
    fact["source"]["quote"] = sentence
    texts["ceo"] += " " + sentence
    data, _ = govern([p], texts, [observation([p], "negative")])
    assert not admitted(data)


def test_year_only_company_join_does_not_become_an_invented_day():
    p, texts = profile(internal=True)
    next(f for f in p["facts"] if f["kind"] == "company_join")["date"] = "2004-01-01"
    data, _ = govern([p], texts, [])
    fact = next(f for f in data["management_stability"]["executives"][0]["facts"] if f["kind"] == "company_join")
    assert fact["verification_status"] == "unverified"
    assert "precision" in fact["validation_reasons"][0]


def test_former_employee_can_be_recruited_externally_without_losing_company_familiarity():
    p, texts = profile(internal=True, external=True)
    data, view = govern([p], texts, [])
    detail = view["key_executive_details"][0]
    assert detail["prior_company_experience"] == "established"
    assert detail["transition_type"] == "external_appointment"


def test_correct_evidence_id_cannot_verify_a_fact_about_another_executive():
    p, texts = profile(internal=True)
    p["facts"][0]["source"]["quote"] = "Someone Else was appointed Chief Executive Officer effective January 1, 2025."
    texts["ceo"] += " " + p["facts"][0]["source"]["quote"]
    data, _ = govern([p], texts, [observation([p])])
    assert not admitted(data)


def test_conflicting_internal_and_absent_experience_remains_disputed():
    p, texts = profile(internal=False, external=True)
    fact = {"fact_id": "previous-role", "kind": "prior_internal_role", "value": "Vice President", "date": None,
            "end_date": None, "source": {"evidence_id": "ceo", "quote": "Dana Lee previously served as Vice President of the Company."}}
    p["facts"].append(fact)
    texts["ceo"] += " " + fact["source"]["quote"]
    data, _ = govern([p], texts, [observation([p], "negative")])
    assert not admitted(data)
    assert data["management_stability"]["executives"][0]["prior_company_experience"] == "disputed"


def test_legacy_rating_contract_is_retired_only_in_new_stability_mode():
    raw = {"m3_assessment": {"management_stability": {}}, "parameters": [
        {"parameter_id": pid, "value": "adequate", "status": "ok"}
        for pid in ("management_quality_assessment", "key_person_dependency_assessment")]}
    output, changes = gate_m3_parameters(raw, [], evidence_texts={})
    assert all(p["value"] is None and p["status"] == "unavailable" for p in output["parameters"])
    assert all(c["reason"] == "m3.legacy_management_rating_retired" for c in changes)
