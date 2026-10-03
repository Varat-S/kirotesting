"""Claim-grounding tests: citation presence vs entailment (task 6.5; Req 13.6-13.8)."""

from __future__ import annotations

from app.models.orm import ClaimGrounding as ClaimGroundingRow
from app.schemas.llm import AnalyticalClaim, EntailmentState, JudgeKind
from app.services.analysis.grounding import (
    EvidenceIndex,
    EvidenceItem,
    GroundingEvaluator,
)


def _claim(claim_id, text, evidence_ids, kind="fact"):
    return AnalyticalClaim(
        claim_id=claim_id, text=text, kind=kind, evidence_ids=evidence_ids
    )


def test_citation_present_but_not_entailed_is_not_grounded():
    """HEADLINE: a citation ID alone never makes a claim grounded (Req 13.8).

    The claim cites a real evidence object, so citation_present is True, but the
    cited number contradicts the claimed number, so entailment is contradictory
    and the claim is NOT grounded.
    """
    evidence = EvidenceIndex([EvidenceItem(evidence_id="m1", value=3.0)])
    evaluator = GroundingEvaluator(evidence)

    claim = _claim("c1", "Net debt to EBITDA is 5.0.", ["m1"])
    result = evaluator.evaluate_claim(claim)

    # The two signals are computed SEPARATELY.
    assert result.citation_present is True
    assert result.entailment_state is EntailmentState.CONTRADICTORY
    # A citation alone is NOT grounding.
    assert result.is_grounded is False


def test_citation_present_and_entailed_is_grounded():
    evidence = EvidenceIndex([EvidenceItem(evidence_id="m1", value=3.0)])
    evaluator = GroundingEvaluator(evidence)
    claim = _claim("c1", "Net debt to EBITDA is 3.0.", ["m1"])
    result = evaluator.evaluate_claim(claim)

    assert result.citation_present is True
    assert result.entailment_state is EntailmentState.SUPPORTED
    assert result.is_grounded is True


def test_no_citation_is_not_grounded_and_not_verifiable():
    evidence = EvidenceIndex([EvidenceItem(evidence_id="m1", value=3.0)])
    evaluator = GroundingEvaluator(evidence)
    claim = _claim("c1", "Leverage is manageable.", [], kind="interpretation")
    result = evaluator.evaluate_claim(claim)

    assert result.citation_present is False
    assert result.entailment_state is EntailmentState.NOT_VERIFIABLE
    assert result.is_grounded is False


def test_citation_to_nonexistent_evidence_is_not_present():
    """Citing an id that is not a real evidence object fails citation presence."""
    evidence = EvidenceIndex([EvidenceItem(evidence_id="m1", value=3.0)])
    evaluator = GroundingEvaluator(evidence)
    claim = _claim("c1", "Debt is 3.0.", ["does_not_exist"])
    result = evaluator.evaluate_claim(claim)

    assert result.citation_present is False
    assert result.is_grounded is False


def test_optional_llm_judge_used_for_narrative_claims():
    """A second structured LLM judge may decide entailment for narrative (Req 13.8)."""

    def judge(claim, evidence):
        return EntailmentState.PARTIALLY_SUPPORTED

    evidence = EvidenceIndex([EvidenceItem(evidence_id="s1", text="some narrative")])
    evaluator = GroundingEvaluator(evidence, llm_judge=judge)
    claim = _claim("c1", "The firm has a strong brand.", ["s1"], kind="interpretation")
    result = evaluator.evaluate_claim(claim)

    assert result.citation_present is True
    assert result.entailment_state is EntailmentState.PARTIALLY_SUPPORTED
    assert result.judge is JudgeKind.LLM
    assert result.is_grounded is False


def test_persist_stores_both_signals_independently(db_session):
    evidence = EvidenceIndex([EvidenceItem(evidence_id="m1", value=3.0)])
    evaluator = GroundingEvaluator(evidence, session=db_session, case_id="C1")
    claim = _claim("c1", "Net debt to EBITDA is 5.0.", ["m1"])
    result = evaluator.evaluate_claim(claim)
    evaluator.persist(result)

    row = db_session.query(ClaimGroundingRow).filter_by(claim_id="c1").one()
    assert row.citation_present is True
    assert row.entailment_state == "contradictory"
    assert row.judge == "deterministic"


def test_human_adjudication_is_additive():
    evidence = EvidenceIndex([EvidenceItem(evidence_id="m1", value=3.0)])
    evaluator = GroundingEvaluator(evidence)
    claim = _claim("c1", "Net debt to EBITDA is 5.0.", ["m1"])
    original = evaluator.evaluate_claim(claim)

    adjudicated = evaluator.adjudicate(
        original, entailment_state=EntailmentState.UNSUPPORTED, detail="reviewer call"
    )
    # Original verdict untouched; a NEW result carries the human verdict.
    assert original.entailment_state is EntailmentState.CONTRADICTORY
    assert original.adjudicated is False
    assert adjudicated.adjudicated is True
    assert adjudicated.judge is JudgeKind.HUMAN
    assert adjudicated.entailment_state is EntailmentState.UNSUPPORTED
