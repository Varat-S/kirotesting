"""Evaluate the semantic-entailment contract on a frozen offline case file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.bo.semantic_entailment import (
    DeterministicCheck,
    SemanticEntailmentEvaluator,
    SemanticEvidence,
)


def evaluate(path: Path) -> dict:
    dataset = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for case in dataset["cases"]:
        # Scripted expected responses test the contract, not model accuracy.
        judge = lambda payload, c=case: {
            "assessment": c["expected"], "reasoning": c["reason"],
            "evidence_ids": [c["evidence_id"]]}
        evaluator = SemanticEntailmentEvaluator(
            judge, model="scripted-expected-label", prompt_version="offline-frozen-1")
        result = evaluator.evaluate(
            case["claim"], [SemanticEvidence(case["evidence_id"], case["passage"])],
            approved_evidence_ids={case["evidence_id"]},
            deterministic_checks=tuple(DeterministicCheck(**c)
                                       for c in case.get("checks", [])))
        rows.append({"id": case["id"], "category": case["category"],
                     "expected": case["expected"], "actual": result.assessment.value,
                     "correct": result.assessment.value == case["expected"],
                     "malformed": result.malformed_output,
                     "deterministic_hard_failure": any(
                         not c.get("passed", True) for c in case.get("checks", [])),
                     "reasoning": result.reasoning})

    def subset(predicate):
        return [r for r in rows if predicate(r)]

    unsupported = subset(lambda r: r["expected"] != "supported")
    supported = subset(lambda r: r["expected"] == "supported")
    contradicted = subset(lambda r: r["expected"] == "contradicted")
    inference = subset(lambda r: r["category"] in {
        "unwarranted_causal_interpretation", "intention_or_action_as_outcome",
        "outside_specialist_remit", "unsupported_positive_credit_conclusion",
        "unproven_mitigant"})
    numeric = subset(lambda r: r["category"] in {
        "changed_numeric_value", "incorrect_denominator", "wrong_fiscal_period"})
    hard = subset(lambda r: r["deterministic_hard_failure"])
    report = {
        "dataset_id": dataset["dataset_id"],
        "evaluation_kind": "deterministic contract plus scripted expected judgments",
        "actual_llm_semantic_accuracy": "not_measured",
        "case_count": len(rows),
        "false_support": {"numerator": sum(r["actual"] == "supported" for r in unsupported),
                          "denominator": len(unsupported)},
        "correct_support_retention": {
            "numerator": sum(r["actual"] == "supported" for r in supported),
            "denominator": len(supported)},
        "contradiction_detection": {
            "numerator": sum(r["actual"] == "contradicted" for r in contradicted),
            "denominator": len(contradicted)},
        "unsupported_inference_rejection": {
            "numerator": sum(r["actual"] != "supported" for r in inference),
            "denominator": len(inference)},
        "incorrect_numeric_rejection": {
            "numerator": sum(r["actual"] != "supported" for r in numeric),
            "denominator": len(numeric)},
        "uncertain_frequency": {
            "numerator": sum(r["actual"] == "uncertain" for r in rows),
            "denominator": len(rows)},
        "deterministic_fail_closed": {
            "numerator": sum(r["actual"] != "supported" for r in hard),
            "denominator": len(hard)},
        "errors": [r for r in rows if not r["correct"]],
        "cases": rows,
    }
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cases", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    report = evaluate(args.cases)
    text = json.dumps(report, indent=2) + "\n"
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
