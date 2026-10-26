"""M1S2 — controlled offline comparison on HCA FY2025 (specialist M1S2).

Runs only when the HCA filing has been fetched (examples/healthcare/hca-2025/
filing/bundle.json; git-ignored). Scenarios were committed before the first run
(389b265). Scripted, equally wrong model in both arms; no provider is called.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.orm import Case
from app.services.bo.comparison import prepare_specialist, run_comparison
from app.services.bo.roster_v3 import V3_SPEC_BY_CODE
from app.services.bo.sectors import load_case_checks
from app.services.pipeline.package import SourcePackage
from app.services.pipeline.runner import CreditMemoPipeline

HCA_DIR = Path("examples/healthcare/hca-2025")
pytestmark = pytest.mark.skipif(
    not (HCA_DIR / "filing/bundle.json").exists(),
    reason="HCA FY2025 Form 10-K has not been acquired; HCA M1S2 comparison NOT performed.")


@pytest.fixture()
def report(db_session, tmp_path):
    prepared = CreditMemoPipeline(
        db_session, data_root=tmp_path / "data", output_root=tmp_path / "out",
    ).prepare_case("HCA_2025_M2", package=SourcePackage.load(HCA_DIR / "package.json"))
    if db_session.get(Case, "HCA_2025_M2") is None:
        db_session.add(Case(case_id="HCA_2025_M2", status="open"))
        db_session.flush()
    prep = prepare_specialist(
        db_session, case_id="HCA_2025_M2", evidence=prepared.evidence_snapshot,
        metrics=prepared.metrics, spec=V3_SPEC_BY_CODE["M1S2"],
        coverage_checks=load_case_checks(HCA_DIR / "coverage_checks.json"))
    return run_comparison(db_session, prep, "examples/bo_m1s2/hca_m1s2")


def _o(report, arm):
    return {s["scenario"][:2]: s for s in report["arms"][arm]["scenarios"]}


def test_hca_single_pass(report):
    arm, o = report["arms"]["single_pass"], _o(report, "single_pass")
    assert arm["defects_held_back"] == 2 and arm["controls_usable"] == 2
    # Fix R4 is lexical: an ER-volume table with figures and "increased" passes
    # as outcome evidence for an unrelated mitigant (known limit).
    assert o["H5"]["mitigant_effectiveness"] == "demonstrated"
    assert arm["parameters"]["contract_termination_risk_assessment"]["value"] == "low"


def test_hca_investigation(report):
    arm, o = report["arms"]["investigation"], _o(report, "investigation")
    assert arm["defects_held_back"] == 5
    assert o["H1"]["disposition"] == "qualified"  # R3 cap
    # Fix R1a: the borrower's own name is equivalent to the filing's "us".
    assert o["H7"]["investigation"]["effective_status"] == "supported"
    assert o["H7"]["disposition"] == "qualified" and o["H7"]["usable"]  # R3 cap
    assert arm["controls_usable"] == 2
    assert o["H5"]["mitigant_effectiveness"] == "unproven"
    assert any("inferential step is not declared" in r
               for r in o["H3"]["investigation"]["downgrade_reasons"])
    assert arm["parameters"]["customer_dependence_assessment"]["status"] == "ok"
    assert arm["parameters"]["contract_termination_risk_assessment"]["status"] == "unavailable"
    assert report["investigation"]["progression"] == "blocked_pending_evidence"
