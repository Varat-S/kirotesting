"""Downstream handoff contract tests (Phase E1).

The BO handoff (`AcceptedBusinessOverview`, schema `bo-handoff-1.x`) is the ONLY
surface FO, SO and the global Credit Orchestrator should depend on. These tests
pin that contract so a downstream consumer can rely on it WITHOUT reaching into
BO's internal agent implementation:

* every field the downstream interface promises is present and typed;
* the schema is versioned and rejects unknown fields (`extra='forbid'`);
* the content hash makes the analytical content tamper-evident while allowing
  the human-review fields to change;
* no internal agent id / roster detail is required to interpret the handoff.
"""

from __future__ import annotations

from app.schemas.bo import AcceptedBusinessOverview
from tests import bo_helpers as h
from tests.integration import test_bo_v3_complete as v3


# The fields a downstream (FO/SO/Credit Orchestrator) consumer is entitled to
# read. If any is removed/renamed, this list must change deliberately.
REQUIRED_HANDOFF_FIELDS = {
    "handoff_id", "schema_version", "analysis_run_id", "case_id",
    "borrower_name", "borrower_entity_id", "as_of_date",
    "evidence_cutoff_timestamp",
    "evidence_snapshot_version", "business_topic_conclusion_id", "conclusion_status",
    "accepted_finding_ids", "material_risk_ids", "business_strengths",
    "operational_vulnerabilities", "validated_mitigants", "unproven_mitigants",
    "unresolved_challenges", "open_questions", "module_conclusions",
    "compound_business_risks", "fo_handoffs",
    "evidence_limitations",
    "escalations", "canonical_references", "human_review_status",
    "execution_mode", "agent_registry_version", "agent_registry_hash",
    "prompt_hashes", "scoring_config_version", "scoring_config_hash",
    "config_versions", "content_hash",
}


def test_handoff_exposes_the_full_downstream_contract(db_session, tmp_path):
    result, _ = v3._run(db_session, tmp_path)
    handoff = result.handoff
    missing = REQUIRED_HANDOFF_FIELDS - set(handoff)
    assert not missing, f"handoff is missing downstream contract fields: {missing}"
    # Versioned, so a consumer can gate on the schema version.
    assert handoff["schema_version"].startswith("bo-handoff-")
    assert handoff["borrower_name"] == "Northfield Health Systems"
    assert handoff["borrower_entity_id"] == "NFH"
    assert handoff["as_of_date"] == "2025-12-31"
    assert handoff["evidence_cutoff_timestamp"].startswith("2026-03-01")
    # Canonical references point back to the run's immutable artifacts.
    refs = handoff["canonical_references"]
    assert refs["analysis_run_id"] == result.analysis_run_id
    assert "audit_stage" in refs
    # Governance state is explicit for a consumer.
    assert handoff["human_review_status"] == "pending"
    assert handoff["conclusion_status"] in {
        "accepted", "accepted_with_qualification", "human_review_required",
        "unavailable", "clean", "reran"}


def test_handoff_schema_forbids_unknown_fields_and_roundtrips(db_session, tmp_path):
    result, _ = v3._run(db_session, tmp_path)
    handoff = result.handoff
    # The payload validates back into the versioned schema (stable contract).
    model = AcceptedBusinessOverview.model_validate(handoff)
    assert model.analysis_run_id == result.analysis_run_id
    # extra='forbid' — an unknown field from a drifting producer is rejected.
    bad = dict(handoff)
    bad["unexpected_downstream_field"] = 1
    try:
        AcceptedBusinessOverview.model_validate(bad)
        raised = False
    except Exception:
        raised = True
    assert raised, "handoff schema must forbid unknown fields"


def test_handoff_is_interpretable_without_internal_agent_detail(db_session, tmp_path):
    """A downstream consumer should not need BO's internal agent ids to use the
    module conclusions. Module keys are the stable M1-M4 labels, not agent ids."""
    result, _ = v3._run(db_session, tmp_path)
    modules = result.handoff["module_conclusions"]
    assert set(modules) == {"M1", "M2", "M3", "M4"}
    for m in modules.values():
        assert "status" in m  # complete | incomplete — a consumer can gate on this


def test_handoff_content_hash_is_tamper_evident(db_session, tmp_path):
    from app.services.bo.reporting import handoff_content_hash

    result, _ = v3._run(db_session, tmp_path)
    handoff = result.handoff
    recomputed = handoff_content_hash(handoff)
    assert recomputed == handoff["content_hash"]
    # Changing analytical content changes the hash...
    mutated = dict(handoff)
    mutated["material_risk_ids"] = [*handoff["material_risk_ids"], "tampered"]
    assert handoff_content_hash(mutated) != handoff["content_hash"]
    # ...but changing only the human-review fields does NOT (they are excluded).
    review_only = dict(handoff)
    review_only["human_review_status"] = "accepted"
    review_only["reviewer"] = "credit.officer"
    assert handoff_content_hash(review_only) == handoff["content_hash"]
