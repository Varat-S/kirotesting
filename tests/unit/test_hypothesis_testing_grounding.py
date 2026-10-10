"""Task 6C: numerical grounding integrity (concept/entity/period/scale/currency).

A number match alone must never corroborate a premise. A metric corroborates a
premise ONLY via an explicit grounding_metric_id link whose trusted metadata
(concept + any claimed entity/period/scale/currency/basis) matches. Any mismatch
or missing required concept metadata fails closed.

Validation-logic tests (service enforcement) are what matter here; FakeLLMBackend
only supplies a scripted proposed verdict. Two industries used.
"""

from __future__ import annotations

import io

from reportlab.pdfgen.canvas import Canvas

from app.prompts.registry import PromptRegistry
from app.schemas.llm import (
    CreditDimension,
    CreditHypothesis,
    CreditHypothesisSet,
    EvidenceEnquiryPlan,
    HypothesisVerificationStatus,
)
from app.services.analysis.enquiry_planning import EvidenceEnquiryCoordinator
from app.services.analysis.evidence_retrieval import (
    EvidenceSearchService,
    build_corpus_from_pdf,
)
from app.services.analysis.grounding import EvidenceItem
from app.services.analysis.hypothesis_testing import (
    HypothesisTestingInputs,
    HypothesisTestingService,
)
from app.services.audit.log import AuditLog
from app.services.llm.client import FakeLLMBackend, LLMClient


def _pdf(pages):
    buffer = io.BytesIO()
    canvas = Canvas(buffer)
    for lines in pages:
        y = 800
        for line in lines:
            canvas.drawString(72, y, line)
            y -= 18
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


PAGES = [
    ["S1", "The borrower reports financial results and operating metrics."],
    ["S2", "Several figures are disclosed across periods and entities."],
]


def _service(db_session):
    registry = PromptRegistry(db_session)
    registry.register_catalogue()
    backend = FakeLLMBackend()
    audit = AuditLog(db_session)
    client = LLMClient(backend, prompts=registry, session=db_session, audit=audit)
    return HypothesisTestingService(
        client, audit=audit, case_id="C1", review_lookup=db_session
    ), backend


def _hyp(hid="h1"):
    return CreditHypothesis(
        hypothesis_id=hid,
        dimension=CreditDimension.PRICING_POWER_REVENUE_REALISATION,
        business_characteristic="Revenue dynamics.",
        statement="Revenue growth supports repayment resilience.",
        credit_direction="positive",
        economic_reasoning="Growing revenue widens debt-service headroom.",
        potential_credit_impact="More headroom.",
        classification="inference",
        verification_status="unverified",
    )


def _plan(hid="h1"):
    def enq(kind, intent, eid):
        return {
            "enquiry_id": eid, "hypothesis_id": hid,
            "dimension": CreditDimension.PRICING_POWER_REVENUE_REALISATION.value,
            "requirement_kind": kind, "search_intent": intent,
            "investigative_question": "?", "search_query": "figures",
            "rationale": "r", "evidence_requirement": "req", "priority": 2,
            "permitted_document_ids": ["D"],
        }
    return EvidenceEnquiryPlan.model_validate({
        "enquiries": [
            enq("supporting", "supporting_evidence", "s"),
            enq("contradictory", "contradictory_evidence", "c"),
        ],
        "planner_mode": "llm", "incomplete_coverage": [],
    })


def _bundle():
    corpus = build_corpus_from_pdf("D", _pdf(PAGES))
    return EvidenceEnquiryCoordinator(EvidenceSearchService(corpus)).execute(_plan())


def _result(premise: dict, **ov):
    base = {
        "hypothesis_id": "h1",
        "dimension": CreditDimension.PRICING_POWER_REVENUE_REALISATION.value,
        "hypothesis_statement": "Revenue growth supports repayment resilience.",
        "evidence_assessments": [], "relevant_factual_claims": [],
        "proposed_mechanism": "Revenue growth widens headroom.",
        "mechanism_premises": [premise], "alternative_explanations": [],
        "offsetting_capabilities": [], "offset_effectiveness_evidence": [],
        "proposed_status": "supported", "justification": "j",
        "unresolved_evidence_requirements": [], "interpretation_confidence": 0.8,
    }
    base.update(ov)
    return {"results": [base]}


def _run(db_session, premise, item):
    service, backend = _service(db_session)
    bundle = _bundle()
    backend.register("analyze", _result(premise), key="k")
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hyp()]),
            bundle=bundle, plan=_plan(), grounding_items=[item],
        ),
        key="k",
    ).governed[0]
    return gov.premise_corroboration[0], gov


def _premise(**ov):
    base = {
        "premise_id": "p", "premise": "a figure premise", "status": "verified",
        "grounding_metric_id": "metric", "grounding_claim": "The figure is 8.",
    }
    base.update(ov)
    return base


# 1. Revenue growth 8% cited for operating-cost growth 8%.
def test_wrong_concept_same_number(db_session):
    pc, gov = _run(
        db_session,
        _premise(claimed_concept="operating cost growth"),
        EvidenceItem(evidence_id="metric", value=8.0, unit="percent",
                     concept="revenue growth"),
    )
    assert pc.corroborated is False
    assert gov.effective_status is not HypothesisVerificationStatus.SUPPORTED


# 2. Correct concept, wrong period.
def test_wrong_period(db_session):
    pc, _ = _run(
        db_session,
        _premise(claimed_concept="revenue growth", claimed_period="FY2024"),
        EvidenceItem(evidence_id="metric", value=8.0, unit="percent",
                     concept="revenue growth", period="FY2023"),
    )
    assert pc.corroborated is False


# 3. Correct figure, wrong entity.
def test_wrong_entity(db_session):
    pc, _ = _run(
        db_session,
        _premise(claimed_concept="revenue growth", claimed_entity="OpCo"),
        EvidenceItem(evidence_id="metric", value=8.0, unit="percent",
                     concept="revenue growth", entity="Parent"),
    )
    assert pc.corroborated is False


# 4. Percentage with an incorrect denominator (modelled as currency/unit basis).
def test_wrong_denominator_basis(db_session):
    pc, _ = _run(
        db_session,
        _premise(claimed_concept="margin", claimed_accounting_basis="gross"),
        EvidenceItem(evidence_id="metric", value=8.0, unit="percent",
                     concept="margin", accounting_basis="net"),
    )
    assert pc.corroborated is False


# 5. Correct currency, wrong scale.
def test_wrong_scale(db_session):
    pc, _ = _run(
        db_session,
        _premise(claimed_concept="revenue", claimed_currency="USD",
                 claimed_scale="billions"),
        EvidenceItem(evidence_id="metric", value=8.0, unit="USD",
                     concept="revenue", currency="USD", scale="millions"),
    )
    assert pc.corroborated is False


# 6. Metric missing required concept metadata.
def test_missing_concept_metadata(db_session):
    pc, _ = _run(
        db_session,
        _premise(claimed_concept="revenue growth"),
        EvidenceItem(evidence_id="metric", value=8.0, unit="percent"),  # no concept
    )
    assert pc.corroborated is False
    assert any("concept metadata" in n for n in pc.notes)


# 7. Correctly matched metric with sufficient metadata.
def test_correct_match_corroborates(db_session):
    pc, gov = _run(
        db_session,
        _premise(claimed_concept="revenue growth", claimed_entity="OpCo",
                 claimed_period="FY2024", claimed_scale="percent",
                 claimed_currency="USD", claimed_accounting_basis="GAAP",
                 grounding_claim="revenue growth is 8.0 percent"),
        EvidenceItem(evidence_id="metric", value=8.0, unit="percent",
                     concept="revenue growth", entity="OpCo", period="FY2024",
                     scale="percent", currency="USD", accounting_basis="GAAP"),
    )
    assert pc.corroborated is True
    assert "deterministic_grounding" in pc.corroboration_basis


# 8. Valid numeric premise + unsupported causal premise.
def test_valid_fact_with_unsupported_causal_premise(db_session):
    service, backend = _service(db_session)
    bundle = _bundle()
    fig = _premise(premise_id="fig", claimed_concept="revenue growth",
                   grounding_claim="revenue growth is 8.0 percent")
    cause = {
        "premise_id": "cause",
        "premise": "revenue growth was caused by a new pricing strategy",
        "status": "verified", "is_outcome_premise": True,
        "supporting_passage_ids": [],
    }
    result = _result(fig)
    result["results"][0]["mechanism_premises"] = [fig, cause]
    backend.register("analyze", result, key="k")
    gov = service.test(
        HypothesisTestingInputs(
            hypotheses=CreditHypothesisSet(hypotheses=[_hyp()]),
            bundle=bundle, plan=_plan(),
            grounding_items=[EvidenceItem(evidence_id="metric", value=8.0,
                                          unit="percent", concept="revenue growth")],
        ),
        key="k",
    ).governed[0]
    by_id = {pc.premise_id: pc for pc in gov.premise_corroboration}
    assert by_id["fig"].corroborated is True
    assert by_id["cause"].corroborated is False
    assert gov.effective_status is not HypothesisVerificationStatus.SUPPORTED
    assert "revenue growth was caused by a new pricing strategy" in (
        gov.uncorroborated_material_premises
    )
