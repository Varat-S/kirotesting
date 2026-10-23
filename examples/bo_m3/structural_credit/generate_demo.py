"""Generate the committed, inspectable Module 3 Layer-2 demo outputs (OFFLINE).

This is a SCRIPTED, OFFLINE generator: it uses the fictional offline evidence
snapshot (``tests.bo_helpers``) and the committed scripted specialist-response
fixtures under ``examples/bo_m3/structural_credit/``. No model is ever called;
live analytical capability is UNVERIFIED.

It writes, into ``examples/bo_m3/structural_credit/demo_output/<case>/``, the six
inspectable files produced by :func:`app.services.bo.m3_demo.write_m3_demo`:

    m3_corporate_structure.json, m3_analysis.json, m3_structure_provenance.json,
    validation_report.json, evaluation_scorecard.json, run_manifest.json

and exercises five cases:

    * ``hca_simulated``                — the simulated HCA structure;
    * ``fictional_positive``           — a qualified positive (guarantor support);
    * ``fictional_negative``           — a defensible negative (cash restriction);
    * ``fictional_mixed``              — a mixed assessment;
    * ``fictional_unsupported_guarantee`` — an unsupported guarantee (rejected/qualified).

Run from the worktree root:

    DEBUG=false .venv/bin/python examples/bo_m3/structural_credit/generate_demo.py
"""

from __future__ import annotations

import json
from pathlib import Path

from app.models.base import create_engine_and_session, init_db
from app.services.bo import m3_demo
from app.services.bo.m3_standalone import run_m3
from tests import bo_helpers as h


FIXTURES = Path("examples/bo_m3/structural_credit")
HCA_SIMPLIFIED = Path("examples/bo_m3/Simplified HCA Corp Structure.json")
FICTIONAL_STRUCTURE = FIXTURES / "fictional_synthetic_structure.json"
OUTPUT_ROOT = FIXTURES / "demo_output"

# Synthetic admitted evidence for the fictional cases (offline, developer-authored).
FICTIONAL_PASSAGES = {
    "ev_board": ("Item 10", "The board of directors oversees governance."),
    "ev_guar": ("Item 15",
                "Pursuant to the guaranty agreement, Larkspur Freight Operations "
                "LLC guarantees the obligations under the proposed credit facility."),
    "ev_restrict": ("Item 15",
                    "Under the indenture, intercompany distributions are restricted "
                    "from the warehousing subsidiary to the borrower."),
}

# Developer-authored expected dispositions (for the validator-accuracy denominator).
EXPECTED_LABELS = {
    "hca_simulated": {"SCF-HCA-BORROWER": "unverified",
                      "SCF-HCA-GUARANTOR": "unverified",
                      "SCF-HCA-CASHFLOW": "unverified"},
    "fictional_positive": {"SCF-POS-GUARANTOR": "qualified"},
    "fictional_negative": {"SCF-NEG-CASHFLOW": "qualified"},
    "fictional_mixed": {"SCF-MIX-POS": "qualified", "SCF-MIX-NEG": "qualified"},
    "fictional_unsupported_guarantee": {"SCF-BAD-GUARANTEE": "unverified"},
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _session():
    engine, factory = create_engine_and_session("sqlite://")
    init_db(engine)
    return factory()


def _case(name: str, *, structure_path: Path, evidence, simplified):
    responses = {"single_pass": _load(FIXTURES / f"{name}_single_pass.json")}
    result = run_m3(
        _session(), case_id=h.CASE, evidence=evidence, metrics=h.metrics(),
        m3_responses=responses, corporate_structure_document=_load(structure_path))
    out_dir = OUTPUT_ROOT / name
    paths = m3_demo.write_m3_demo(
        result, out_dir, simplified_document=simplified,
        corporate_structure_path=str(structure_path),
        responses_path=str(FIXTURES / f"{name}_single_pass.json"),
        expected_labels=EXPECTED_LABELS.get(name))
    return {k: str(v) for k, v in paths.items()}


def main() -> None:
    written: dict[str, dict] = {}
    written["hca_simulated"] = _case(
        "hca_simulated", structure_path=HCA_SIMPLIFIED,
        evidence=h.evidence_snapshot(), simplified=_load(HCA_SIMPLIFIED))
    fictional_evidence = h.evidence_snapshot(FICTIONAL_PASSAGES)
    fictional_simplified = _load(FICTIONAL_STRUCTURE)
    for name in ("fictional_positive", "fictional_negative", "fictional_mixed",
                 "fictional_unsupported_guarantee"):
        written[name] = _case(
            name, structure_path=FICTIONAL_STRUCTURE, evidence=fictional_evidence,
            simplified=fictional_simplified)
    print(json.dumps({"output_root": str(OUTPUT_ROOT), "cases": written}, indent=2))


if __name__ == "__main__":
    main()
