"""Offline integration checks; genuine BO remains local, never a tracked test fixture."""
import json
import os
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from app.schemas.credit_memo import FinancialOverview, PartialCreditAssessment
from app.services.reporting.memo import PdfBackendUnavailableError, detect_pdf_backend
from app.services.structural_overview.adapter import adapt_business_overview, assessment_content_hash
from app.services.structural_overview.credit_memo import (
    build_partial_assessment, calculate_financials, render_partial_html, validate_assessment, write_partial_artifacts,
)
from tests.integration.test_structural_overview_thin_slice import REAL_HCA_HANDOFF, _handoff

FIXTURE = Path("examples/credit_memo/synthetic_fo_v1.json")


def fo_for(bo):
    fo = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fo.update(case_id=bo["case_id"], borrower_name=bo["borrower_name"])
    return fo


def test_bo_governance_survives_and_missing_structure_renders():
    bo = _handoff()
    assessment = build_partial_assessment(bo, fo_for(bo))
    original = adapt_business_overview(bo)
    assert assessment.business_assessment == original.business_assessment
    assert assessment.governance == original.governance
    assert assessment.evidence_references == original.evidence_references
    assert assessment.status == "partial"
    assert assessment.final_credit_score is assessment.facility_approval is None
    assert any(r.status == "unavailable" for r in assessment.structural_relationships)
    html = render_partial_html(assessment)
    assert "INCOMPLETE" in html and "SIMULATED FINANCIAL INFORMATION" in html
    assert not assessment.cross_domain_findings  # no invented BO relationship


def test_financial_math_and_provenance():
    packet = FinancialOverview.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))
    calculated = {(c.period, c.metric_id): c for c in calculate_financials(packet)}
    assert calculated["FY2025", "revenue_growth"].value == pytest.approx(.05)
    assert calculated["FY2024", "operating_margin"].value == pytest.approx(.15)
    assert calculated["FY2025", "operating_margin"].value == pytest.approx(.14)
    assert calculated["FY2025", "net_debt_to_ebitda"].value == pytest.approx(39800 / 11970)
    assert calculated["FY2025", "interest_coverage"].value == pytest.approx(11970 / 2200)
    assert calculated["FY2025", "free_cash_flow"].value == 3200
    assert calculated["FY2025", "liquidity"].value == 7000
    assert all(c.provenance == "synthetic_fixture" and c.input_refs and c.formula_id for c in calculated.values())
    assert all(f.provenance == "synthetic_fixture" for p in packet.periods for f in p.facts.values())
    assert all(f.provenance == "synthetic_fixture" for f in packet.findings)


def test_bad_denominator_is_explicit_not_zero():
    fo = fo_for(_handoff())
    fo["periods"][1]["facts"]["interest_expense"]["value"] = 0
    result = next(c for c in calculate_financials(FinancialOverview.model_validate(fo)) if c.period == "FY2025" and c.metric_id == "interest_coverage")
    assert result.value is None and result.state == "requires_review"


def test_future_fo_boundary_uses_same_builder_without_admitting_evidence():
    # Contract mechanics only: this deliberately invented test packet is not real FO.
    bo = _handoff()
    fo = fo_for(bo)
    fo.update(provenance="validated_fo", source_run_id="test_future_fo", notice="Test FO packet; upstream admission still required.")
    for period in fo["periods"]:
        for fact in period["facts"].values():
            fact.update(provenance="validated_fo", evidence_ids=["test-only-evidence-ref"])
    for finding in fo["findings"]:
        finding["provenance"] = "validated_fo"
    a = build_partial_assessment(bo, fo)
    assert a.status == "partial" and a.final_credit_score is None
    assert a.financial_calculations[0].provenance == "validated_fo"
    assert "source admission" in a.disclosure
    assert "source admission" in render_partial_html(a)


def test_simulation_disclosure_cannot_be_removed_by_rehashing():
    bo = _handoff()
    a = build_partial_assessment(bo, fo_for(bo)).model_dump(mode="json")
    a["disclosure"] = "Completed credit memo"
    a["content_hash"] = assessment_content_hash(a)
    with pytest.raises(ValueError, match="disclosure"):
        render_partial_html(a)


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(schema_version="fo-adapter-9.0"),
    lambda p: p.update(case_id="OTHER"),
    lambda p: p.update(borrower_name="Other borrower"),
    lambda p: p["periods"][1].update(fiscal_year=2027),
    lambda p: p["periods"][1]["facts"]["revenue"].update(value=1),
    lambda p: p["periods"][1]["facts"]["ebitda"].update(value=float("nan")),
    lambda p: p["periods"][1]["facts"]["revenue"].update(provenance="validated_fo"),
    lambda p: p["periods"][1]["facts"]["revenue"].update(evidence_ids=["SEC-FAKE"]),
    lambda p: p["findings"][0].update(metric_refs=["missing-metric"]),
])
def test_incompatible_inputs_fail(mutation):
    bo = _handoff()
    fo = fo_for(bo)
    mutation(fo)
    with pytest.raises(ValueError):
        build_partial_assessment(bo, fo)


def test_frozen_assessment_integrity_and_deterministic_rendering():
    bo = _handoff()
    a = build_partial_assessment(bo, fo_for(bo))
    assert a == build_partial_assessment(bo, fo_for(bo))
    assert render_partial_html(a) == render_partial_html(json.loads(a.model_dump_json()))
    tampered = a.model_dump(mode="json")
    tampered["financial_calculations"][0]["value"] = 999.0
    with pytest.raises(ValueError, match="hash"):
        render_partial_html(tampered)
    tampered["content_hash"] = assessment_content_hash(tampered)
    with pytest.raises(ValueError, match="calculations"):
        validate_assessment(tampered)
    tampered = a.model_dump(mode="json")
    tampered["final_credit_score"] = 3
    with pytest.raises(ValueError):
        PartialCreditAssessment.model_validate(tampered)


def test_pdf_is_required_not_silently_replaced_by_html(tmp_path, monkeypatch):
    import app.services.structural_overview.credit_memo as module
    monkeypatch.setattr(module, "detect_pdf_backend", lambda: None)
    bo = _handoff()
    with pytest.raises(PdfBackendUnavailableError, match="PDF required"):
        write_partial_artifacts(build_partial_assessment(bo, fo_for(bo)), tmp_path)


def test_real_bo_synthetic_fo_pdf_and_lineage(tmp_path):
    path = Path(os.environ.get("HCA_BO_HANDOFF", str(REAL_HCA_HANDOFF)))
    if not path.exists():
        pytest.skip("Genuine local HCA handoff absent; set HCA_BO_HANDOFF")
    bo = json.loads(path.read_text(encoding="utf-8"))
    a = build_partial_assessment(bo, fo_for(bo))
    buckets = [a.business_assessment.business_model_and_revenue_drivers,
               a.business_assessment.industry_and_competitive_risks,
               a.business_assessment.management_and_operational_findings,
               a.business_assessment.strategy_and_capital_allocation_findings]
    findings = [f for bucket in buckets for f in bucket]
    assert len(findings) == 28 and sum(f.accepted_for_handoff for f in findings) == 8
    assert a.governance.human_review_status == "pending"
    assert a.governance.provisional_business_risk_band == 3
    assert a.governance.unresolved_challenges == bo["unresolved_challenges"]
    risk = next(f for f in findings if f.provenance.source_hypothesis_id == "M1A6-H001")
    assert risk.disposition == "qualified" and risk.admissibility == "qualified_narrative"
    cross = a.cross_domain_findings[0]
    assert cross.simulation_dependent and cross.certainty == "unverified"
    assert risk.provenance.source_hypothesis_id in cross.bo_hypothesis_ids
    assert set(cross.evidence_ids) == set(risk.supporting_evidence_ids)
    if detect_pdf_backend() is None:
        pytest.fail("Real integration acceptance requires a working PDF backend")
    paths = write_partial_artifacts(a, tmp_path)
    frozen = json.loads(paths[0].read_text(encoding="utf-8"))
    assert validate_assessment(frozen) == a
    assert paths[1].read_text(encoding="utf-8") == render_partial_html(frozen)
    with pdfium.PdfDocument(paths[2]) as pdf:
        texts = [page.get_textpage().get_text_range() for page in pdf]
    assert 4 <= len(texts) <= 6
    assert all("INCOMPLETE" in text and "simulated financial information" in text for text in texts)
    full = "\n".join(texts)
    for heading in ("Executive overview", "Business Overview", "Financial Overview", "Structural Overview", "Risks and next steps", "Sources and audit trail"):
        assert heading in full
    assert risk.claim in full.replace("\r\n", " ")
    assert "Compound Margin Compression Risk" in full and "SIMULATED FINANCIAL INFORMATION" in full
    assert "5.441" in full and "3.325" in full
    second = write_partial_artifacts(a, tmp_path / "again")
    with pdfium.PdfDocument(second[2]) as pdf:
        assert [page.get_textpage().get_text_range() for page in pdf] == texts
