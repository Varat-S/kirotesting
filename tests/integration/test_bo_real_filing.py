"""Acceptance H — Business Overview on REAL filings.

These tests only run when the corresponding SEC bundle has actually been
acquired; otherwise they are SKIPPED, and a skip is not a pass:

* Stryker FY2024 (secondary regression borrower): the bundle is fetched into the
  git-ignored ``data/`` directory, so this runs locally and skips in CI.
* HCA FY2025 (primary demonstration borrower): runs once
  ``examples/healthcare/hca-2025/filing/bundle.json`` exists.

No test here asserts a company figure that was not read from the processed
filing.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.bo.evaluation import load_manifest
from app.services.bo.hypotheses import quote_in_passage
from app.services.bo.offline import ScriptedBoBackend
from app.services.bo.runner import BusinessOverviewRunner
from app.services.extraction.sec.bundle import SecFilingBundle
from app.services.pipeline.package import SourcePackage
from app.services.pipeline.runner import CreditMemoPipeline
from app.services.pipeline.sec import sec_source_package

ROOT = Path(__file__).resolve().parents[2]
SYK_BUNDLE = ROOT / "data/sec/SYK/0000310764-25-000023/bundle.json"
SYK_SCRIPT = ROOT / "examples/healthcare/stryker-bo/offline_responses.json"
HCA_DIR = ROOT / "examples/healthcare/hca-2025"
HCA_BUNDLE = HCA_DIR / "filing/bundle.json"

needs_syk = pytest.mark.skipif(
    not SYK_BUNDLE.is_file(),
    reason="Stryker FY2024 SEC bundle not fetched (data/ is git-ignored).")
needs_hca = pytest.mark.skipif(
    not HCA_BUNDLE.is_file(),
    reason="HCA FY2025 Form 10-K has not been acquired; real-HCA validation "
           "is NOT performed.")


def _run(db_session, tmp_path, case_id, package, backend=None, manifest=None):
    prepared = CreditMemoPipeline(
        db_session, data_root=tmp_path / "data", output_root=tmp_path / "out",
    ).prepare_case(case_id, package=package)
    result = BusinessOverviewRunner(
        db_session, backend=backend, data_root=tmp_path / "data",
        output_root=tmp_path / "out",
    ).run_prepared(case_id, prepared.evidence_snapshot, prepared.metrics,
                   rejected_sources=prepared.rejected_sources, manifest=manifest)
    return result, prepared.evidence_snapshot


@needs_syk
def test_stryker_real_filing_runs_the_full_bo_path_offline(db_session, tmp_path):
    bundle = SecFilingBundle.load(SYK_BUNDLE)
    package = sec_source_package(
        bundle, cutoff=datetime(2025, 3, 1, tzinfo=timezone.utc), entity_id="SYK",
        legal_name="Stryker Corporation")
    backend = ScriptedBoBackend.from_file(SYK_SCRIPT)
    result, evidence = _run(db_session, tmp_path, "SYK_BO", package, backend)

    # The real source documents were admitted with their recorded hashes.
    documents = {d["filename"]: d for d in result.audit["sources"]["documents"]}
    primary = next(f for f in bundle.files if f.filename == bundle.primary_document)
    assert documents[primary.filename]["sha256"] == hashlib.sha256(
        primary.content).hexdigest()
    assert documents[primary.filename]["accession"] == "0000310764-25-000023"
    assert result.audit["sources"]["rejected_sources"] == []

    assert all(o["status"] == "validated" for o in result.record["agents"].values())
    assert result.execution_mode == "offline_simulation"
    assert [r["outcome"] for r in result.record["challenge_rounds"]] == [
        "rerun_required", "accepted_with_qualification"]
    assert result.conclusion_status == "accepted_with_qualification"
    assert backend.calls("bo_competition_pricing") == 2
    assert backend.calls("bo_regulation_external") == 1

    # Every quote behind an accepted finding is verbatim in the admitted filing.
    passages = {p["evidence_id"]: p["text"] for p in evidence.narrative_evidence}
    current = [h for h in result.audit["hypotheses"]["records"]
               if h["acceptance_state"] == "accepted"]
    quotes = [q for h in current for q in h["evidence_quotes"]]
    assert len(quotes) >= 15
    assert all(quote_in_passage(q["quote"], passages[q["evidence_id"]]) for q in quotes)
    notes = [a["note"] for agent in result.audit["agents"]
             for a in agent["validator_adjustments"]]
    assert not [n for n in notes if "not found verbatim" in n or "not verbatim" in n]
    # Generic risk-factor language is recorded as a disclosed risk, not a fact.
    reform = next(h for h in current if h["hypothesis_id"] == "BO5-H003")
    assert reform["evidence_basis"] == "generic_risk_disclosure"
    assert reform["admissibility"] == "qualified_narrative"
    # A "strong" position resting only on management's belief is held back.
    held = [p for p in result.audit["parameters"]
            if p["parameter_id"] == "competitive_position_assessment"
            and p["acceptance_state"] == "accepted"]
    assert held and all(p["status"] == "unavailable" for p in held)

    # Numbers in the memo are deterministic calculations with stated denominators.
    growth = next(c for c in result.memo["calculations"]
                  if c["parameter_id"] == "revenue_growth")
    assert growth["calculation_state"] == "ok" and growth["source_fact_ids"]
    share = next(c for c in result.memo["calculations"]
                 if c["parameter_id"].startswith("revenue_share."))
    assert share["denominator"]["name"] == "consolidated_revenue"

    # The corrected pricing claim replaced the overstated one.
    memo_text = json.dumps(result.memo)
    assert "protects pricing power" not in memo_text
    assert "no pricing benefit is evidenced" in memo_text
    follow_up = result.audit["retrieval"]["follow_up"]
    assert follow_up[0]["origin"] == "agent_request"
    assert result.memo["score"]["status"] in ("provisional", "unavailable")


@needs_hca
def test_hca_real_filing_is_processed_and_manifest_concepts_are_located(
        db_session, tmp_path):
    """Runs only once the HCA FY2025 10-K has been fetched and admitted."""
    package = SourcePackage.load(HCA_DIR / "package.json")
    manifest = load_manifest(HCA_DIR / "review_manifest.json")
    result, evidence = _run(db_session, tmp_path, "HCA_2025", package,
                            manifest=manifest)
    bundle = package.sec_bundles[0].bundle
    assert bundle.ticker == "HCA" and bundle.form == "10-K"
    assert bundle.report_date.isoformat() == "2025-12-31"
    assert len(evidence.narrative_evidence) > 50
    assert result.audit["sources"]["rejected_sources"] == []
    # The six anchor concepts must be locatable in the ADMITTED filing text.
    facts = result.evaluation["factual_correctness"]["source_confirmed_facts"]
    missing = [f["id"] for f in facts if not f["located_in_admitted_evidence"]]
    assert missing == [], f"Anchor concepts not found in the filing: {missing}"
    # The default backend asserts nothing, so no conclusion may be claimed.
    assert result.conclusion_status == "unavailable"
    assert result.evaluation["ground_truth_status"] in (
        "unverified_pending_filing", "reviewer_verified")


@needs_hca
def test_hca_manifest_must_be_reviewer_verified_before_it_counts(db_session):
    manifest = load_manifest(HCA_DIR / "review_manifest.json")
    if manifest["verification_status"] != "reviewer_verified":
        pytest.skip("HCA manifest is still an unverified template.")
    assert manifest["reviewer"]
    assert manifest["filing"]["accession"] and manifest["filing"]["cik"]
    assert all(f.get("verified_quote") for f in manifest["source_confirmed_facts"])


def test_hca_manifest_template_carries_no_unverified_claims():
    """The committed template must not pose as verified ground truth."""
    manifest = load_manifest(HCA_DIR / "review_manifest.json")
    if HCA_BUNDLE.is_file():
        pytest.skip("Filing acquired; the template rule no longer applies.")
    assert manifest["verification_status"] == "unverified_pending_filing"
    assert manifest["reviewer"] is None and manifest["recomputed_values"] == []
    assert all(f["verified_quote"] is None for f in manifest["source_confirmed_facts"])
    assert not any(ch.isdigit() for f in manifest["source_confirmed_facts"]
                   for ch in f["concept"])
    assert {t["id"] for t in manifest["trap_checks"]} == {
        "managed_care_rates_vs_volume_guarantees", "geography_revenue_denominator",
        "admissions_vs_revenue", "galen_existence_vs_effectiveness",
        "unit_revenue_vs_pricing_power", "cfo_less_capex_vs_debt_service_capacity"}
