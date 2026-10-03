"""Blinded prompt/model A/B testing (task 9.5, Req 23.11, 23.14).

Runs the SAME case through two prompt/model variants, both via the deterministic
FakeLLMBackend with DIFFERENT scripted behavior, then scores them BLINDED against
the declared manifest on OBJECTIVE metrics (material-risk precision/recall,
omissions, unsupported claims, groundedness, corrections, review time) -- never
"which sounds better".

Proves blinding: the scorer receives only opaque labels ``A``/``B`` and never the
variant identity until AFTER scoring; and that the comparison is objective.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.prompts.registry import PromptRegistry
from app.services.audit.log import AuditLog
from app.services.evaluation import load_manifest, run_blinded_ab
from app.services.evaluation.ab_testing import (
    VariantOutput,
    score_with_variant_identity,
)
from app.services.llm.client import FakeLLMBackend, LLMClient

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "evals" / "manifests" / "acme_fy2023_manifest.json"


def _analysis_payload(risks: list[str], claims: list[dict]) -> dict:
    """A schema-valid AnalysisResponse surfacing the given risks + claims."""
    key_risks = [
        {
            "claim_id": f"risk-{i}",
            "text": risk,
            "kind": "interpretation",
            "evidence_ids": ["doc-pdf-acme-narrative"],
            "materiality": "high",
        }
        for i, risk in enumerate(risks)
    ]
    return {
        "business_overview": claims,
        "repayment_analysis": [],
        "key_risks": key_risks,
        "mitigants": [],
        "data_limitations": [],
        "questions_for_human": [],
    }


def _run_variant(db_session, key: str, payload: dict) -> dict:
    """Run one variant through the deterministic FakeLLMBackend + LLMClient."""
    prompts = PromptRegistry(db_session)
    prompts.register_catalogue()
    backend = FakeLLMBackend()
    backend.register("analyze", payload, key=key)
    client = LLMClient(
        backend, prompts=prompts, session=db_session, audit=AuditLog(db_session)
    )
    result = client.analyze({"facts": []}, key=key, prompt_name="business_analysis")
    assert result.is_valid  # schema-validated before use (Req 19.4)
    return result.parsed


def test_blinded_ab_objective_comparison(db_session) -> None:
    manifest = load_manifest(MANIFEST_PATH)
    # Expected material risks: fuel_price_exposure, near_term_debt_maturity.

    # Variant GOOD surfaces both risks, all claims supported/grounded.
    good_payload = _analysis_payload(
        ["fuel_price_exposure", "near_term_debt_maturity"],
        [
            {
                "claim_id": "c1",
                "text": "Liquidity adequate at T.",
                "kind": "interpretation",
                "evidence_ids": ["doc-xbrl-acme-fy2023"],
                "materiality": "medium",
            }
        ],
    )
    # Variant WEAK omits a risk and makes an unsupported claim.
    weak_payload = _analysis_payload(
        ["fuel_price_exposure"],
        [
            {
                "claim_id": "c2",
                "text": "Leverage is comfortable.",
                "kind": "interpretation",
                "evidence_ids": ["doc-xbrl-acme-fy2023"],
                "materiality": "medium",
            }
        ],
    )

    good_parsed = _run_variant(db_session, "good", good_payload)
    weak_parsed = _run_variant(db_session, "weak", weak_payload)

    def surfaced_risks(parsed: dict) -> list[str]:
        return [c["text"] for c in parsed["key_risks"]]

    variant_a = VariantOutput(
        variant_id="prompt_business_analysis_v1.0+model_good",
        surfaced_risks=surfaced_risks(good_parsed),
        claims=[{"grounded": True, "supported": True}],
        analyst_corrections=0,
        review_time_seconds=120.0,
    )
    variant_b = VariantOutput(
        variant_id="prompt_business_analysis_v1.0+model_weak",
        surfaced_risks=surfaced_risks(weak_parsed),
        claims=[{"grounded": False, "supported": False}],
        analyst_corrections=3,
        review_time_seconds=300.0,
    )

    result = run_blinded_ab(variant_a, variant_b, manifest=manifest)

    # Objective comparison on recall: the better variant wins by LABEL.
    assert result.blinded is True
    winner_label = result.winner_by("risk_recall")
    assert winner_label is not None
    # The winning label unblinds to the GOOD variant.
    assert "model_good" in result.unblinding[winner_label]

    # Objective metrics computed, not "which sounds better".
    good_score = result.scores[winner_label]
    assert good_score.risk_recall == pytest.approx(1.0)
    assert good_score.omissions == 0
    assert good_score.unsupported_claims == 0
    assert good_score.groundedness == pytest.approx(1.0)

    other_label = "B" if winner_label == "A" else "A"
    weak_score = result.scores[other_label]
    assert weak_score.omissions == 1
    assert weak_score.unsupported_claims == 1
    assert weak_score.risk_recall < good_score.risk_recall


def test_scoring_cannot_receive_variant_identity() -> None:
    """Blinding is structural: no scoring path accepts a variant identity."""
    with pytest.raises(NotImplementedError):
        score_with_variant_identity("A", variant_id="model_good")
