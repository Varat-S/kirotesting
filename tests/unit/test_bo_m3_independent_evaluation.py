"""Frozen M3 semantic evaluation provenance and contract metrics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.services.bo.m3_semantic_eval import evaluate
from app.services.bo.semantic_entailment import (
    SemanticAssessment,
    SemanticEntailmentEvaluator,
    SemanticEvidence,
)


ROOT = Path(__file__).parents[2]
EVAL = ROOT / "examples/bo_m3/independent_evaluation"


def test_frozen_source_hashes_and_review_status_are_explicit():
    manifest = json.loads((EVAL / "manifest.json").read_text())
    assert manifest["review"]["status"] == "awaiting_external_review"
    assert manifest["label_status"] == "developer_authored_awaiting_external_review"
    for source in manifest["sources"]:
        content = (ROOT / source["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == source["sha256"]


def test_semantic_contract_is_fail_closed_on_frozen_cases():
    report = evaluate(EVAL / "cases.json")
    assert report["case_count"] >= 15
    assert report["actual_llm_semantic_accuracy"] == "not_measured"
    assert report["errors"] == []
    assert report["false_support"]["numerator"] == 0
    assert report["correct_support_retention"]["numerator"] == \
        report["correct_support_retention"]["denominator"]
    assert report["deterministic_fail_closed"]["numerator"] == \
        report["deterministic_fail_closed"]["denominator"]


def test_invented_reference_and_malformed_judgment_fail_closed():
    evaluator = SemanticEntailmentEvaluator(
        lambda _: {"assessment": "supported", "reasoning": "claimed",
                   "evidence_ids": ["invented"]},
        model="scripted-adversarial", prompt_version="offline-frozen-1")
    outside = evaluator.evaluate(
        "Claim", [SemanticEvidence("invented", "Text")],
        approved_evidence_ids={"approved"})
    malformed = evaluator.evaluate(
        "Claim", [SemanticEvidence("approved", "Text")],
        approved_evidence_ids={"approved"})

    assert outside.assessment is SemanticAssessment.INSUFFICIENT
    assert not outside.established
    assert malformed.assessment is SemanticAssessment.UNCERTAIN
    assert malformed.malformed_output and not malformed.established
