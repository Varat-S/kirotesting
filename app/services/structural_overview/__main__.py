"""Command-line entry point for the deterministic BO-to-SO adapter."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.structural_overview import adapt_business_overview, write_artifacts


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a governed Structural Overview assessment")
    parser.add_argument("handoff", type=Path, help="Path to a bo-handoff-1.2 or 1.3 JSON artifact")
    parser.add_argument("output_dir", type=Path, help="Directory for assessment.json and assessment.html")
    parser.add_argument("--expected-case-id", help="Reject a handoff for any other case")
    args = parser.parse_args()
    source = json.loads(args.handoff.read_text(encoding="utf-8"))
    assessment = adapt_business_overview(source, expected_case_id=args.expected_case_id)
    json_path, html_path = write_artifacts(assessment, args.output_dir)
    print(json.dumps({
        "assessment_id": assessment.assessment_id,
        "content_hash": assessment.content_hash,
        "json": str(json_path),
        "html": str(html_path),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
