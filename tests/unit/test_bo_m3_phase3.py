"""Portable adversarial support checks; all passages here are fictional."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.services.bo.m3_grounding import gate_m3_parameters, received_inquiry


def _gate(pid, value, text, *, claim=None, additional=None, status="ok"):
    parameter = {"parameter_id": pid, "value": value, "status": status,
                 "evidence_ids": ["source"], "hypothesis_ids": ["H1"]}
    record = SimpleNamespace(hypothesis_id="H1", usable=True, claim=claim or text,
                             supporting_evidence_ids=["source"], evidence_quotes=[])
    original = {"parameters": [parameter]}
    before = deepcopy(original)
    result, changes = gate_m3_parameters(original, [record], evidence_texts={
        "source": text, **(additional or {})})
    assert original == before
    return result["parameters"][0], changes


@pytest.mark.parametrize("value", ["low", "moderate", "high"])
def test_generic_departure_disclosure_cannot_calibrate_dependency(value):
    parameter, changes = _gate("key_person_dependency_assessment", value,
        "Our managers would be difficult to replace. We have succession plans, "
        "but they may not deliver a successful transition.")
    assert parameter["status"] == "unavailable"
    assert parameter["value"] is None
    assert "calibration" in changes[0]["reason"]


def test_explicit_dependency_assessment_retained_without_invented_threshold():
    parameter, changes = _gate("key_person_dependency_assessment", "moderate",
        "The independent review assessed key-person dependency as moderate.",
        claim="Key-person dependency is moderate according to the independent review.")
    assert parameter["status"] == "ok"
    assert not changes


def test_parameter_grade_must_agree_with_linked_hypothesis():
    parameter, changes = _gate("key_person_dependency_assessment", "moderate",
        "The independent review assessed key-person dependency as moderate.",
        claim="Key-person dependency is high.")
    assert parameter["status"] == "unavailable"
    assert "conflicts" in changes[0]["reason"]


@pytest.mark.parametrize("text", [
    "The review assessed key-person dependency as high despite moderate staff turnover.",
    "The review has not rated key-person dependency as moderate.",
    "The review could have assessed key-person dependency as moderate.",
])
def test_grade_words_nearby_do_not_establish_the_proposed_grade(text):
    parameter, _ = _gate("key_person_dependency_assessment", "moderate", text)
    assert parameter["status"] == "unavailable"


@pytest.mark.parametrize("pid,value", [
    ("governance_concern_assessment", "none"),
    ("material_event_assessment", "none_identified"),
])
def test_narrow_healthcare_investigation_statement_cannot_prove_broad_absence(pid, value):
    parameter, changes = _gate(pid, value,
        "Except as disclosed elsewhere, we are not aware of material investigations under health care laws.")
    assert parameter["status"] == "unavailable"
    assert "broad_dated_review" in changes[0]["reason"]


def test_bounded_dated_broad_governance_review_can_support_absence():
    text = ("The FY2025 governance review assessed board oversight, related-party transactions, "
            "conflicts of interest, financial reporting controls and regulatory enforcement. "
            "No material governance concerns were identified.")
    parameter, changes = _gate("governance_concern_assessment", "none", text)
    assert parameter["status"] == "ok"
    assert not changes


def test_bounded_dated_event_review_can_support_none_identified():
    text = ("The FY2025 material-event review covered corporate actions, acquisitions, leadership, "
            "litigation, regulatory matters and financial reporting controls. "
            "No material events were identified.")
    parameter, changes = _gate("material_event_assessment", "none_identified", text)
    assert parameter["status"] == "ok"
    assert not changes


def test_dated_absence_review_does_not_override_admitted_counterexample():
    text = ("The FY2025 material-event review covered corporate actions, leadership, litigation "
            "and controls. No material events were identified.")
    parameter, changes = _gate("material_event_assessment", "none_identified", text,
                               additional={"adverse": "We completed a material acquisition."})
    assert parameter["status"] == "unavailable"
    assert changes[0]["counterevidence_ids"] == ["adverse"]


@pytest.mark.parametrize("text,expected", [
    ("Certain of our individual facilities and/or affiliates have received, and other facilities "
     "may receive, government inquiries.", True),
    ("We have received government inquiries.", True),
    ("We have not received government inquiries.", False),
    ("We may receive government inquiries.", False),
    ("If we have received government inquiries, losses could occur.", False),
    ("We may have received government inquiries.", False),
    ("We have received government inquiries, but all such inquiries were closed.", False),
])
def test_affirmative_inquiry_detection_keeps_negation_and_modality(text, expected):
    assert received_inquiry(text) is expected


def test_received_inquiries_prevent_contingent_only_even_if_citation_id_is_correct():
    parameter, changes = _gate("litigation_risk_assessment", "contingent_exposure_only",
        "Future investigations could result in material liabilities.",
        additional={"inquiry": "Our facilities have received government inquiries."})
    assert parameter["status"] == "unavailable"
    assert changes[0]["counterevidence_ids"] == ["inquiry"]


def test_explicit_no_current_matter_screen_allows_contingent_exposure():
    parameter, changes = _gate("litigation_risk_assessment", "contingent_exposure_only",
        "The Company has no pending investigations, inquiries or litigation. Future inquiries could cause contingent liability.")
    assert parameter["status"] == "ok"
    assert not changes


@pytest.mark.parametrize("text", [
    "The Company has no pending investigations.",
    "If there are no pending investigations or litigation, exposure will be contingent.",
    "There are no pending investigations, but litigation exists.",
])
def test_narrow_or_hypothetical_matter_screen_cannot_support_exclusive_contingency(text):
    parameter, _ = _gate("litigation_risk_assessment", "contingent_exposure_only", text)
    assert parameter["status"] == "unavailable"


def test_broad_review_with_explicit_excluded_domain_cannot_support_absence():
    text = ("The FY2025 governance review assessed board oversight, related-party transactions, "
            "conflicts of interest and regulatory enforcement, excluding financial reporting controls. "
            "No material governance concerns were identified.")
    parameter, _ = _gate("governance_concern_assessment", "none", text)
    assert parameter["status"] == "unavailable"


def test_supported_existing_litigation_category_not_blocked():
    parameter, changes = _gate("litigation_risk_assessment", "existing_proceedings_not_quantified",
        "We are defendants in an ongoing proceeding; the exposure has not been quantified.")
    assert parameter["status"] == "ok"
    assert not changes


def test_legitimate_unavailable_value_is_not_a_failed_or_invented_classification():
    parameter, changes = _gate("governance_concern_assessment", None,
                               "No adequate governance assessment is supplied.", status="unavailable")
    assert parameter["status"] == "unavailable"
    assert parameter["value"] is None
    assert not changes


def test_resolving_but_unrelated_parameter_citation_is_held_back():
    raw = {"parameters": [{"parameter_id": "litigation_risk_assessment", "value": "existing_proceedings_not_quantified",
                           "status": "ok", "evidence_ids": ["biography"], "hypothesis_ids": ["H1"]}]}
    record = SimpleNamespace(hypothesis_id="H1", usable=True, claim="A proceeding exists.",
                             supporting_evidence_ids=["legal"], evidence_quotes=[])
    gated, changes = gate_m3_parameters(raw, [record], evidence_texts={
        "biography": "The manager has long tenure.", "legal": "A proceeding exists."})
    assert gated["parameters"][0]["status"] == "unavailable"
    assert "not_linked" in changes[0]["reason"]
