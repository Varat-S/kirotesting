"""Atomic-object, roster, date and inference necessary-support controls."""
from copy import deepcopy
import json
from pathlib import Path

import jsonschema
import pytest

from app.core.hashing import content_hash
from app.prompts.bo_prompts import BO_M3_JSON_SCHEMA
from app.services.bo.m3_management import govern_management_stability, stability_view
from app.services.bo.m3_roster import disclosed_rosters, roster_coverage
from app.services.bo.m3_validation import validate_m3_assessment
from app.services.llm.providers_vertex_m3 import executive_fact_example, vertex_m3_output_instructions, vertex_m3_response_schema


FIXTURE = Path("examples/bo_m3/management_stability/hca_fy2025_fixture.json")


def hca_example():
    fixture = json.loads(FIXTURE.read_text())
    if not Path(fixture["filing_path"]).exists():
        pytest.skip("Local HCA FY2025 source absent; never fetched or replaced by a model call.")
    response = fixture["response"]
    texts = {}
    for p in response["m3_assessment"]["management_stability"]["executives"]:
        for f in p["facts"]:
            source = f["source"]
            texts.setdefault(source["evidence_id"], set()).add(source["quote"])
    # These are only existing source excerpts; no new officer facts are authored.
    texts = {eid: "\n".join(sorted(quotes)) for eid, quotes in texts.items()}
    stability = response["m3_assessment"]["management_stability"]
    anchor = stability["as_of_source"]
    from html.parser import HTMLParser
    class Text(HTMLParser):
        def __init__(self): super().__init__(); self.parts = []
        def handle_data(self, value):
            if value.strip(): self.parts.append(value.strip())
    parser = Text()
    parser.feed(Path(fixture["filing_path"]).read_text())
    filing_text = "\n".join(parser.parts)
    start = filing_text.index(anchor["quote"])
    roster = filing_text[start:start + 2000]  # actual table, not generated from expected profiles
    texts[anchor["evidence_id"]] = roster + "\n" + texts.get(anchor["evidence_id"], "")
    for h in response["hypotheses"]:
        for q in h.get("evidence_quotes", []):
            texts[q["evidence_id"]] = texts.get(q["evidence_id"], "") + "\n" + q["quote"]
    risk = ("While we have adopted succession plans to prepare for such an event, our succession plans may not result in a successful transition. "
            "Further, institutional knowledge may be lost in any potential managerial transition.")
    assert risk in " ".join(filing_text.split())
    for observation in response["m3_assessment"]["management_assessments"]:
        for eid in observation["contradicting_evidence_ids"]:
            texts.setdefault(eid, risk)
    return response, texts


def test_provider_schema_is_exactly_the_live_accepted_v14_schema_and_example_is_typed():
    assert content_hash(vertex_m3_response_schema(BO_M3_JSON_SCHEMA, variant="compact")) == "6238ae584c5931311dc231c568ef1d676d2f6b0d4cc4f1523d8a6e0970398320"
    schema = BO_M3_JSON_SCHEMA["properties"]["m3_assessment"]["properties"]["management_stability"]["properties"]["executives"]["items"]
    jsonschema.validate(executive_fact_example(), schema)
    instructions = vertex_m3_output_instructions(BO_M3_JSON_SCHEMA, variant="compact")
    assert "NEVER strings or fact IDs" in instructions
    assert "Only management_assessments[].fact_ids contains strings" in instructions
    assert instructions.count("Its exact local JSON contract is:") == 1


@pytest.mark.parametrize("field", ["fact_id", "kind", "value", "source"])
def test_missing_required_atomic_fact_fields_fail_canonical_schema(field):
    response, _ = hca_example()
    del response["m3_assessment"]["management_stability"]["executives"][0]["facts"][0][field]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(response, BO_M3_JSON_SCHEMA)


def test_string_fact_reference_fails_and_no_parser_repair_is_performed():
    response, _ = hca_example()
    response["m3_assessment"]["management_stability"]["executives"][0]["facts"] = ["hazen_tenure"]
    before = deepcopy(response)
    with pytest.raises(jsonschema.ValidationError): jsonschema.validate(response, BO_M3_JSON_SCHEMA)
    assert response == before


def test_supported_multiple_executives_dates_and_internal_transitions_pass_actual_validator():
    response, texts = hca_example()
    result = validate_m3_assessment(response["m3_assessment"], allowed_evidence_ids=set(texts),
        evidence_texts=texts, management_company_aliases=("HCA", "HCA Healthcare"))
    data = result.model_dump(mode="json")
    profiles = {p["executive_id"]: p for p in data["management_stability"]["executives"]}
    assert len(profiles) == 7
    assert profiles["marks"]["transition_type"] == "internal_succession"
    assert profiles["marks"]["prior_company_experience"] == "established"
    assert profiles["foster"]["recent_appointment"] == "no"
    assert profiles["rossitto"]["recent_appointment"] == "yes"
    assert profiles["hazen"]["tenure"]["appointment_date_precision"] == "month"
    view = stability_view(data["management_stability"], data["management_assessments"])
    assert view["overall_management_stability_assessment"]["executive_roster_coverage"]["verified_count"] == 7
    assert len(view["credit_findings"]) == 3


@pytest.mark.parametrize("error", ["filing_date", "fabricated_day", "other_role_date", "missing_appointment_date", "missing_source_quote"])
def test_dates_and_required_appointment_support_cannot_be_fabricated(error):
    response, texts = hca_example()
    stability = response["m3_assessment"]["management_stability"]
    fact = stability["executives"][0]["facts"][0]
    if error == "filing_date":
        stability["as_of_date"] = "2026-02-10"
        texts["filing"] = "Filed on February 10, 2026."
        stability["as_of_source"] = {"evidence_id": "filing", "quote": texts["filing"]}
    if error == "fabricated_day": fact["date"] = "2019-01-01"
    if error == "other_role_date": fact["date"] = "2018-09"  # director date exists in same biography, not CEO appointment
    if error == "missing_appointment_date": fact["date"] = None
    if error == "missing_source_quote": fact["source"]["quote"] = ""
    result = validate_m3_assessment(response["m3_assessment"], allowed_evidence_ids=set(texts), evidence_texts=texts,
                                    management_company_aliases=("HCA", "HCA Healthcare"))
    profile = result.management_stability.executives[0]
    if error == "filing_date":
        assert not profile.tenure
        assert any("roster date" in r for r in result.management_stability.validation_reasons)
    else:
        assert profile.facts[0].verification_status == "unverified"


def test_omitted_roster_executives_are_reported_and_individual_supported_finding_is_retained():
    response, texts = hca_example()
    stability = response["m3_assessment"]["management_stability"]
    stability["review_scope"] = "Comprehensive review of all executive officers."
    stability["executives"] = stability["executives"][:1]
    response["m3_assessment"]["management_assessments"] = response["m3_assessment"]["management_assessments"][:1]
    result = validate_m3_assessment(response["m3_assessment"], allowed_evidence_ids=set(texts), evidence_texts=texts,
                                    management_company_aliases=("HCA", "HCA Healthcare"))
    data = result.model_dump(mode="json")
    assert len(data["management_stability"]["executives"]) == 1  # no fabricated profiles
    view = stability_view(data["management_stability"], data["management_assessments"])
    coverage = view["overall_management_stability_assessment"]["executive_roster_coverage"]
    assert coverage["expected_count"] == 7 and coverage["proposed_count"] == 1
    assert any("Marks" in name for name in coverage["omitted_names"])
    assert len(view["credit_findings"]) == 1
    assert view["consolidated_management_stability_conclusion"]["classification"] == "insufficient_information"
    assert any("comprehensive" in r for r in data["management_stability"]["validation_reasons"])


@pytest.mark.parametrize("claim", [
    "CEO tenure facilitates predictable strategic execution.",
    "Experience may support continuity, and ensures predictable strategic execution.",
    "Long service proves superior competence.",
    "Tenure improves cash flow.",
])
def test_leadership_history_cannot_launder_performance_or_competence_claims(claim):
    response, texts = hca_example()
    item = response["m3_assessment"]["management_assessments"][0]
    item["hypothesis"] = claim
    result = validate_m3_assessment(response["m3_assessment"], allowed_evidence_ids=set(texts), evidence_texts=texts,
                                    management_company_aliases=("HCA", "HCA Healthcare"))
    assert result.management_assessments[0].disposition.value == "unverified"


def test_unrecognized_roster_format_is_unavailable_not_an_invented_complete_roster():
    assert not disclosed_rosters({"text": "There are several executives; biographies are missing."})
    assert roster_coverage({}, []) ["status"] == "unavailable"


@pytest.mark.parametrize("claim,admitted", [
    ("The auditor reported effective internal control over financial reporting as of December 31, 2025.", True),
    ("No material governance concerns exist because internal control over financial reporting is effective.", False),
    ("The internal control opinion establishes strong governance.", False),
    ("No material events were identified in the internal control opinion.", False),
])
def test_scoped_icfr_finding_retained_but_broad_absence_is_not_admitted(claim, admitted):
    from app.services.bo.m3_grounding import strengthen_m3_assessment
    source = "The auditor reported effective internal control over financial reporting as of December 31, 2025."
    proposal = {"borrower_identification": {"status": "unavailable", "limitation": "No transaction evidence."},
        "governance_screening": [{"finding_id": "control", "credit_hypothesis": claim,
            "causal_mechanism": "Scoped reporting assurance may support reliability of financial information used by lenders.",
            "supporting_evidence_ids": ["audit"], "contradicting_evidence_ids": [],
            "supporting_evidence_investigated": True, "contradictory_evidence_investigated": True,
            "disposition": "qualified", "admissibility": "qualified_narrative"}],
        "information_gaps": ["Broader governance review is unavailable."]}
    result = validate_m3_assessment(proposal, allowed_evidence_ids={"audit"}, evidence_texts={"audit": source})
    checked, _ = strengthen_m3_assessment(result, evidence_texts={"audit": source}, structure_facts=None)
    assert (checked.governance_screening[0].disposition.value in {"supported", "qualified"}) == admitted


def test_contingent_only_finding_cannot_ignore_existing_matters_in_same_source():
    from app.services.bo.m3_grounding import strengthen_m3_assessment
    source = "Our facilities have received government inquiries. Their resolution could have a material adverse effect on liquidity."
    proposal = {"borrower_identification": {"status": "unavailable", "limitation": "No transaction evidence."},
        "governance_screening": [{"finding_id": "litigation", "credit_hypothesis": "Litigation exposure is merely contingent.",
            "causal_mechanism": "Government investigations may affect liquidity.", "supporting_evidence_ids": ["legal"],
            "contradicting_evidence_ids": ["legal"], "supporting_evidence_investigated": True,
            "contradictory_evidence_investigated": True, "disposition": "qualified", "admissibility": "qualified_narrative"}],
        "information_gaps": ["Litigation severity unavailable."]}
    result = validate_m3_assessment(proposal, allowed_evidence_ids={"legal"}, evidence_texts={"legal": source})
    checked, reviews = strengthen_m3_assessment(result, evidence_texts={"legal": source}, structure_facts=None)
    assert checked.governance_screening[0].disposition.value == "unverified"
    assert reviews[0]["matched_caveats"]
    assert checked.governance_screening[0].contradictory_evidence_investigated is True


def test_calendar_mask_does_not_hide_a_financial_amount_with_the_same_number():
    from app.services.bo.m3_validation import _deterministic_claim_checks
    checks = _deterministic_claim_checks("Revenue was $31 million for FY2025 as of December 31, 2025.",
        ["source"], {"source": "Revenue was $42 million for FY2025 as of December 31, 2025."})
    assert any(c.name == "numeric_value_present_in_cited_evidence" and not c.passed and "31" in c.detail for c in checks)
    assert any(c.name == "calendar_date_in_cited_evidence" and c.passed for c in checks)


def test_changed_calendar_day_is_unsupported_not_a_verified_date():
    from app.services.bo.m3_validation import _deterministic_claim_checks
    checks = _deterministic_claim_checks("ICFR was effective as of December 30, 2025.",
        ["source"], {"source": "ICFR was effective as of December 31, 2025."})
    assert any(c.name == "calendar_date_in_cited_evidence" and not c.passed for c in checks)
