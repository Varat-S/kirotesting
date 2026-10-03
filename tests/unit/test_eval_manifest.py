"""Unit tests for the GroundTruthManifest + loader (task 9.1, Req 23.4-23.6).

Covers: the manifest carries every Req 23.4 field; contemporaneous ground truth
is stored SEPARATELY from future outcome; the loader validates and refuses a
missing/invalid manifest (no silent defaults); and the no-scored-metric-without-
a-manifest guard (Req 23.5) is enforced by every scorer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.evaluation import (
    GroundTruthManifest,
    ManifestError,
    NoDeclaredManifestError,
    load_manifest,
    load_manifest_dict,
    run_ablations,
    run_blinded_ab,
    score_contemporaneous_accuracy,
    score_extraction,
)
from app.services.evaluation.ab_testing import VariantOutput

MANIFEST_PATH = (
    Path(__file__).resolve().parents[2]
    / "evals"
    / "manifests"
    / "acme_fy2023_manifest.json"
)


def test_manifest_has_all_required_fields() -> None:
    m = load_manifest(MANIFEST_PATH)
    # Req 23.4 fields present.
    assert m.case_id == "acme-fy2023"
    assert m.as_of_date is not None
    assert m.evidence_cutoff_timestamp is not None
    assert m.approved_document_ids
    assert m.verified_facts
    assert m.expected_normalized_values
    assert m.expected_metric_outputs
    assert m.known_missing_items
    assert m.expected_rule_triggers
    assert m.expected_escalations
    assert m.adjudicated_material_risks
    assert m.contemporaneous_ground_truth
    assert m.annotator_metadata
    assert m.manifest_version == "1.0.0"


def test_contemporaneous_and_future_are_separate_objects() -> None:
    """Req 23.6: the two live in distinct sub-objects, never mixed."""
    m = load_manifest(MANIFEST_PATH)
    contemp_keys = set(m.contemporaneous_ground_truth.keys())
    future_keys = set(m.future_outcome.model_dump().keys())
    # No field name is shared between the two surfaces.
    assert contemp_keys.isdisjoint(future_keys)
    # The future outcome is NOT reachable from the contemporaneous dict.
    assert "default_occurred" not in m.contemporaneous_ground_truth
    assert "rating_downgrade" not in m.contemporaneous_ground_truth


def test_loader_rejects_missing_file() -> None:
    with pytest.raises(ManifestError):
        load_manifest("evals/manifests/does_not_exist.json")


def test_loader_rejects_invalid_structure() -> None:
    with pytest.raises(ManifestError):
        load_manifest_dict({"case_id": "x"})  # missing required fields


def test_scorers_refuse_without_declared_manifest() -> None:
    """Req 23.5: no scored metric without a declared GroundTruthManifest."""
    with pytest.raises(NoDeclaredManifestError):
        score_extraction({}, manifest=None)
    with pytest.raises(ValueError):
        score_contemporaneous_accuracy({}, manifest=None)
    with pytest.raises(ValueError):
        run_ablations([], lambda v, s: None, manifest=None)
    with pytest.raises(ValueError):
        run_blinded_ab(
            VariantOutput("a", [], []),
            VariantOutput("b", [], []),
            manifest=None,
        )


def test_manifest_round_trips_from_dict() -> None:
    m = load_manifest(MANIFEST_PATH)
    again = load_manifest_dict(m.model_dump(mode="json"))
    assert isinstance(again, GroundTruthManifest)
    assert again.case_id == m.case_id
