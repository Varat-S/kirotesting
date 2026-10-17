"""Reproducible metrics for the three BO investigation configurations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


M3_EVALUATION_VERSION = "bo-m3-evaluation-1"


def evaluate_architecture(
    cases: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    *,
    usage: dict[str, float | int | None],
) -> dict[str, Any]:
    """Score explicit case outcomes without treating finding count as quality."""
    by_case = {row["case_id"]: row for row in findings}
    material = [c for c in cases if c.get("material") and c.get("expected_detected")]
    unsupported = [c for c in cases if not c.get("expected_accepted", True)]
    correct = [c for c in cases if c.get("expected_accepted", True)]
    contradictions = [c for c in cases if c.get("requires_contradiction_search")]

    def count(rows, predicate):
        return sum(bool(by_case.get(c["case_id"])) and predicate(by_case[c["case_id"]])
                   for c in rows)

    duplicate_count = sum(1 for f in findings if f.get("duplicate_of"))
    return {
        "evaluation_version": M3_EVALUATION_VERSION,
        "case_count": len(cases),
        "material_risk_recall": {
            "numerator": count(material, lambda f: f.get("detected", False)),
            "denominator": len(material),
        },
        "unsupported_claim_acceptance": {
            "numerator": count(unsupported, lambda f: f.get("accepted", False)),
            "denominator": len(unsupported),
        },
        "correct_finding_retention": {
            "numerator": count(correct, lambda f: f.get("accepted", False)),
            "denominator": len(correct),
        },
        "evidence_completeness": {
            "numerator": count(cases, lambda f: f.get("evidence_complete", False)),
            "denominator": len(cases),
        },
        "contradictory_evidence_coverage": {
            "numerator": count(contradictions,
                               lambda f: f.get("contradiction_checked", False)),
            "denominator": len(contradictions),
        },
        "off_dimension_leakage": sum(
            1 for f in findings if f.get("accepted") and f.get("off_dimension")),
        "duplicate_finding_rate": {
            "numerator": duplicate_count,
            "denominator": len(findings),
        },
        "usage": dict(usage),
        "case_outcomes": [{
            "case_id": case["case_id"],
            "expected_detected": case.get("expected_detected"),
            "expected_accepted": case.get("expected_accepted"),
            **(by_case.get(case["case_id"]) or {"detected": False, "accepted": False}),
        } for case in cases],
    }


def evaluate_fixture(path: Path) -> dict[str, Any]:
    spec = json.loads(path.read_text(encoding="utf-8"))
    return {
        "evaluation_version": M3_EVALUATION_VERSION,
        "case_version": spec["case_version"],
        "roster_version": spec["roster_version"],
        "policy_versions": spec["policy_versions"],
        "arms": {
            name: evaluate_architecture(spec["cases"], arm["findings"],
                                        usage=arm["usage"])
            for name, arm in spec["arms"].items()
        },
    }


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("fixture", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    result = evaluate_fixture(args.fixture)
    rendered = json.dumps(result, indent=2)
    if args.out:
        args.out.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
