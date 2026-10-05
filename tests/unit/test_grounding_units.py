import pytest

from app.schemas.llm import AnalyticalClaim
from app.services.analysis.grounding import (
    EvidenceIndex,
    EvidenceItem,
    GroundingEvaluator,
)


@pytest.mark.parametrize(
    "text,value,unit,expected",
    [
        ("Leverage is 3.1x.", 3.1, "ratio", "supported"),
        ("Margin is 20%.", 0.2, "ratio", "supported"),
        ("Margin is 20%.", 0.8, "ratio", "contradictory"),
        ("Revenue is $50 billion.", 50000, "USD_million", "supported"),
        ("Revenue is $1,000 million.", 1000, "USD_million", "supported"),
        ("Revenue grew 5% to $50 billion.", 50000, "USD_million", "not_verifiable"),
        ("Leverage is 3.1x and coverage is 4.0x.", 3.1, "ratio", "not_verifiable"),
        ("Margin is 20%.", 20, "USD_million", "not_verifiable"),
    ],
)
def test_numeric_grounding_requires_unambiguous_unit_semantics(
    text, value, unit, expected
):
    item = EvidenceItem(evidence_id="m", value=value, unit=unit)
    claim = AnalyticalClaim(claim_id="c", text=text, kind="fact", evidence_ids=["m"])
    result = GroundingEvaluator(EvidenceIndex([item])).evaluate_claim(claim)
    assert result.entailment_state.value == expected


def test_multiple_numeric_evidence_items_cannot_be_matched_by_chance():
    claim = AnalyticalClaim(
        claim_id="c", text="Debt is 50.", kind="fact", evidence_ids=["revenue", "debt"]
    )
    evidence = EvidenceIndex(
        [EvidenceItem("revenue", value=50), EvidenceItem("debt", value=100)]
    )
    assert (
        GroundingEvaluator(evidence).evaluate_claim(claim).entailment_state.value
        == "not_verifiable"
    )
