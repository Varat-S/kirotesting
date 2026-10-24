"""BO <-> FO evidence provenance and cross-module numerical consistency.

Establishes the five-level distinction (same case / filing+period / source docs /
canonical snapshot / metric definition) and the lightweight consistency check
that flags (never overwrites) inconsistent overlapping facts.
"""

from __future__ import annotations

from app.schemas.artifact_manifest import SnapshotEquivalence
from app.services.registry.provenance import (
    ConsistencyReport,
    consistency_from_payloads,
    cross_module_consistency,
    extract_overlapping_facts,
    provenance_from_payload,
)


# -- provenance extraction ----------------------------------------------------

def test_bo_payload_yields_verified_snapshot_provenance():
    payload = {
        "evidence_snapshot": {"snapshot_version": 3, "content_hash": "abc123"},
        "meta": {"entity_name": "HCA Inc.", "period_end": "2025-12-31"},
    }
    p = provenance_from_payload(payload, case_id="HCA", producer_module="BO")
    assert p.snapshot_equivalence == SnapshotEquivalence.VERIFIED
    assert p.canonical_snapshot_version == 3
    assert p.canonical_snapshot_hash == "abc123"


def test_fo_payload_is_unverified_with_preserved_filing_identity():
    payload = {
        "filing": {"folder": "HCA 10-K FY2025", "signature": "sig_deadbeef"},
        "meta": {"entity_name": "HCA Inc.", "period_end": "2025-12-31"},
    }
    p = provenance_from_payload(payload, case_id="HCA", producer_module="FO")
    # No canonical snapshot link -> UNVERIFIED, never assumed from case id.
    assert p.snapshot_equivalence == SnapshotEquivalence.UNVERIFIED
    assert p.canonical_snapshot_version is None
    assert p.filing_identifier == "HCA 10-K FY2025"
    assert p.source_signature == "sig_deadbeef"
    assert p.reporting_period == "2025-12-31"
    assert "UNVERIFIED" in (p.equivalence_note or "")


def test_missing_fo_snapshot_identity_is_unverified_not_fabricated():
    payload = {"meta": {"entity_name": "HCA Inc."}}  # no snapshot, no filing id
    p = provenance_from_payload(payload, case_id="HCA", producer_module="FO")
    assert p.snapshot_equivalence == SnapshotEquivalence.UNVERIFIED
    assert p.canonical_snapshot_version is None


# -- cross-module consistency -------------------------------------------------

def _bo(value, unit="usd_million", period="2025-12-31", entity="HCA Inc."):
    return {"evidence_snapshot": {"snapshot_version": 1, "content_hash": "h"},
            "meta": {"entity_name": entity, "period_end": period},
            "overlapping_facts": {"total_indebtedness": {"value": value, "unit": unit}}}


def _fo(value, unit="usd_million", period="2025-12-31", entity="HCA Inc."):
    return {"filing": {"folder": "HCA 10-K FY2025", "signature": "sig"},
            "meta": {"entity_name": entity, "period_end": period},
            "overlapping_facts": {"total_indebtedness": {"value": value, "unit": unit}}}


def test_matching_case_and_consistent_values():
    report = consistency_from_payloads("HCA", _bo(46492.0), _fo(46492.0))
    assert not report.has_unresolved
    finding = next(f for f in report.findings if f.fact == "total_indebtedness")
    assert finding.status == "consistent"


def test_equivalent_values_in_compatible_units():
    # $46,492M (BO) vs $46.492B (FO) -> equivalent after unit normalisation.
    report = consistency_from_payloads(
        "HCA", _bo(46492.0, "usd_million"), _fo(46.492, "usd_billion"))
    finding = next(f for f in report.findings if f.fact == "total_indebtedness")
    assert finding.status == "consistent"


def test_conflicting_debt_values_are_flagged_not_overwritten():
    report = consistency_from_payloads("HCA", _bo(46492.0), _fo(52000.0))
    assert report.has_unresolved
    finding = next(f for f in report.findings if f.fact == "total_indebtedness")
    assert finding.status == "inconsistent"
    # Neither value is changed; both are preserved for review.
    assert finding.bo_value == 46492.0 and finding.fo_value == 52000.0


def test_matching_case_but_different_periods_blocks_comparison():
    report = consistency_from_payloads(
        "HCA", _bo(46492.0, period="2025-12-31"),
        _fo(44000.0, period="2024-12-31"))
    assert report.same_reporting_period is False
    assert report.has_unresolved  # the period mismatch itself is unresolved
    money = next(f for f in report.findings if f.fact == "total_indebtedness")
    assert money.status == "incomparable"  # not compared across periods


def test_incompatible_units_are_incomparable_not_false_consistent():
    report = cross_module_consistency(
        "HCA",
        {"total_indebtedness": {"value": 46492.0, "unit": "usd_million"}},
        {"total_indebtedness": {"value": 46492.0, "unit": "ratio"}},
        bo_period="2025-12-31", fo_period="2025-12-31",
    )
    finding = report.findings[0]
    assert finding.status == "incomparable"


def test_different_reporting_entity_is_flagged():
    report = consistency_from_payloads(
        "HCA", _bo(46492.0, entity="HCA Inc."),
        _fo(46492.0, entity="Other Corp"))
    assert report.same_reporting_entity is False
    assert report.has_unresolved


def test_one_sided_fact_is_not_an_inconsistency():
    report = cross_module_consistency(
        "HCA",
        {"cash_and_equivalents": {"value": 1000.0, "unit": "usd_million"}},
        {},  # FO does not report it
        bo_period="2025-12-31", fo_period="2025-12-31",
    )
    finding = report.findings[0]
    assert finding.status == "one_sided"
    assert not report.has_unresolved


def test_report_serialises_with_review_flag():
    report = consistency_from_payloads("HCA", _bo(46492.0), _fo(52000.0))
    d = report.to_dict()
    assert d["schema_version"] == "bo-fo-consistency-1.0"
    assert d["requires_review"] is True
    assert d["snapshot_equivalence"] == "unverified"


def test_incompatible_source_versions_keep_snapshot_unverified():
    # BO on snapshot v1, FO filing with a different source signature: the report
    # carries the UNVERIFIED equivalence so SO cannot assume a shared source.
    report = consistency_from_payloads(
        "HCA", _bo(46492.0), _fo(46492.0),
        snapshot_equivalence=SnapshotEquivalence.UNVERIFIED.value)
    assert report.snapshot_equivalence == "unverified"
