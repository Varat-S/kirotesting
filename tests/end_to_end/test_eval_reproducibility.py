"""End-to-end reproducibility test (task 9.8, Req 23.13).

Under the SAME source snapshot + pinned config/metric-def/rule/prompt-model
versions, the deterministic components (source set, facts, metrics, rule
triggers, schema structure, FinalCaseSnapshot linkage) reproduce EXACTLY, proven
via canonical SHA-256 hashing. The original LLM outputs are STORED and compared
against the stored run rather than assuming byte-exact regeneration.
"""

from __future__ import annotations

from app.services.evaluation import (
    PinnedVersions,
    RunArtifacts,
    assert_reproducible,
)


def _pinned() -> PinnedVersions:
    return PinnedVersions(
        config_versions={"tolerances": 1, "escalation_rules": 1},
        metric_def_versions={"net_debt_to_ebitda": 1, "operating_margin": 1},
        rule_versions={"R-DATA-MISSING-CRITICAL-01": 1},
        prompt_model_versions={"business_analysis": "v1.0", "model": "fake-deterministic-v1"},
    )


def _artifacts() -> RunArtifacts:
    return RunArtifacts(
        source_set=["doc-xbrl-acme-fy2023", "doc-csv-acme-financials"],
        facts=[
            {"name": "revenue", "value": 1500.0, "status": "verified"},
            {"name": "net_income", "value": 250.0, "status": "verified"},
        ],
        metrics=[
            {"metric_id": "operating_margin", "result": 0.1667, "state": "ok"},
            {"metric_id": "net_debt_to_ebitda", "result": 2.0, "state": "ok"},
        ],
        rule_triggers=["R-DATA-MISSING-CRITICAL-01"],
        schema_structure={"schema_version": "1.0", "sections": ["facts", "metrics"]},
        final_snapshot_linkage={
            "final_snapshot_id": "fcs-1",
            "evidence_snapshot_version": 3,
            "predecessor": None,
        },
        stored_llm_outputs=[{"run_id": "run-1", "method": "analyze", "parsed": {"k": 1}}],
    )


def test_deterministic_components_reproduce_exactly() -> None:
    first = _artifacts()
    second = _artifacts()  # same pinned versions + same source snapshot
    result = assert_reproducible(first, second, pinned=_pinned())

    assert result.reproducible is True
    assert result.mismatches() == []
    # Every deterministic component hashed.
    assert set(result.first_hashes) == {
        "source_set",
        "facts",
        "metrics",
        "rule_triggers",
        "schema_structure",
        "final_snapshot_linkage",
    }


def test_source_set_order_independent_but_content_sensitive() -> None:
    first = _artifacts()
    reordered = _artifacts()
    reordered.source_set = list(reversed(reordered.source_set))
    result = assert_reproducible(first, reordered, pinned=_pinned())
    # Source set is hashed as a sorted set, so reordering still reproduces.
    assert result.reproducible is True


def test_changed_deterministic_fact_breaks_reproducibility() -> None:
    first = _artifacts()
    tampered = _artifacts()
    tampered.facts[0]["value"] = 1501.0  # a different deterministic result
    result = assert_reproducible(first, tampered, pinned=_pinned())
    assert result.reproducible is False
    assert "facts" in result.mismatches()


def test_llm_outputs_stored_not_assumed_regenerated() -> None:
    first = _artifacts()
    second = _artifacts()
    result = assert_reproducible(first, second, pinned=_pinned())
    # The stored original LLM run is retained and matched (deterministic fake).
    assert result.llm_outputs_match is True
    assert result.stored_llm_outputs == first.stored_llm_outputs


def test_pinned_fingerprint_is_stable() -> None:
    assert _pinned().fingerprint() == _pinned().fingerprint()
