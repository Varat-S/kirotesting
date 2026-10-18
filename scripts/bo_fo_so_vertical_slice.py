"""Build an offline PDF from actual BO specialists, simulated FO, and governed SO.

Example:
    python -m scripts.bo_fo_so_vertical_slice \
      --bo-run output/bo/<case>/<run>/specialists_run.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.structural_overview.agentic_vertical_slice import (
    build_agentic_vertical_slice,
    write_agentic_vertical_slice_artifacts,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bo-run", type=Path, required=True)
    parser.add_argument(
        "--fo-fixture", type=Path,
        default=Path("examples/credit_memo/synthetic_fo_v1.json"),
    )
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    bo = json.loads(args.bo_run.read_text(encoding="utf-8"))
    fo = json.loads(args.fo_fixture.read_text(encoding="utf-8"))
    output = args.output_dir or args.bo_run.parent / "vertical_slice"
    payload = build_agentic_vertical_slice(bo, fo)
    json_path, pdf_path = write_agentic_vertical_slice_artifacts(payload, output)
    print(json.dumps({
        "case_id": payload["case"]["case_id"],
        "bo_run_id": payload["business_overview"]["meta"]["analysis_run_id"],
        "report_content_hash": payload["report_content_hash"],
        "borrower_status": payload["case"]["transaction_borrower_status"],
        "json": str(json_path.resolve()),
        "pdf": str(pdf_path.resolve()),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
