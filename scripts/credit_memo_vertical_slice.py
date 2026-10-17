"""Render a real local BO handoff plus an explicitly synthetic FO fixture, offline.

python -m scripts.credit_memo_vertical_slice --bo-handoff <local handoff_v1_2.json>
"""
import argparse
import json
from pathlib import Path

from app.services.structural_overview.credit_memo import build_partial_assessment, write_partial_artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bo-handoff", type=Path, required=True)
    parser.add_argument("--fo-fixture", type=Path, default=Path("examples/credit_memo/synthetic_fo_v1.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("output/thin_slice/HCA_2025"))
    args = parser.parse_args()
    assessment = build_partial_assessment(
        json.loads(args.bo_handoff.read_text(encoding="utf-8")),
        json.loads(args.fo_fixture.read_text(encoding="utf-8")),
    )
    for path in write_partial_artifacts(assessment, args.output_dir):
        print(path.resolve())


if __name__ == "__main__":
    main()
