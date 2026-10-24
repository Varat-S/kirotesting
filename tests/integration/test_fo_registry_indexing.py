"""Fixes 3 & 4: FO artifact indexing + provisional rating presentation.

A completed/scored FO run must be indexed as a produced output (not "incomplete"
from wrong field mapping), expose its seven measures / composite / pre-checks /
triggers / review state, and mark the rating provisional while awaiting human
approval (machine-readable). Scores/weights are never changed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.hashing import canonical_json
from app.schemas.artifact_manifest import ExecutionStatus, HumanReviewStatus
from app.services.registry import build_manifest
from app.services.registry.explorer import inspect_node

MEASURE_KEYS = ["dscr", "icr", "leverage", "rcf", "ebitdar_margin", "dso", "liquidity"]


def _fo_payload(*, escalated: bool = False, score: float = 6.4) -> dict:
    """A schema-accurate completed/scored FO payload (deterministic, offline)."""
    metrics = [{"key": "composite", "title": "Composite",
                "columns": [{"heading": "FY2025", "score": score,
                             "reason": "weighted average of the seven measures"}]}]
    for i, key in enumerate(MEASURE_KEYS):
        metrics.append({"key": key, "title": key.upper(),
                        "columns": [{"heading": "FY2025", "score": 5 + (i % 4)}]})
    return {
        "meta": {"entity_name": "Example Health Corp", "ticker": "EHC",
                 "period_end": "2025-12-31", "fiscal_year": 2025, "files": ["10-K.htm"]},
        "filing": {"folder": "EHC 10-K FY2025", "signature": "sig_abc123",
                   "fact_count": 1200},
        "industry": {"industry_group": "Health Care Providers & Services"},
        "reference_hash": "ref_deadbeef",
        "precheck": {
            "going_concern": {"status": "pass", "decided_by": "agent", "mode": "scripted",
                              "reason": "no substantial doubt", "effect": "none",
                              "review_required": False},
            "covenants": {"status": "compliant", "outcome": "ok", "decided_by": "agent",
                          "mode": "scripted", "reason": "", "effect": "none",
                          "info_flags": []},
        },
        "gate": {"agent_triggers": "enabled", "measures_counted": True,
                 "going_concern": "pass"},
        "triggers": {
            "dscr_t1": {"status": "ok", "score": 6, "fired": False},
            "leverage_t2": {"status": "ok", "score": 5, "fired": True, "unresolved": False},
        },
        "composite": {"score": score, "rating": "BBB", "risk_level": "moderate",
                      "reason": "weighted average"},
        "metrics": metrics,
        "escalations": ([{"id": "e1", "type": "run_decision",
                          "reason": "going-concern disagreement"}] if escalated else []),
    }


def _write_fo(tmp_path: Path, case_id: str, run_id: str, payload: dict) -> Path:
    d = tmp_path / "fo" / case_id / run_id
    d.mkdir(parents=True)
    (d / "financial_overview.json").write_text(
        canonical_json(payload) + "\n", encoding="utf-8", newline="\n")
    return tmp_path


def test_completed_scored_fo_is_not_incomplete(tmp_path):
    out = _write_fo(tmp_path, "EHC", "fo-run-1", _fo_payload())
    m = build_manifest(out, "EHC", repo_root=out)
    ov = m.by_id("fo.overview.fo-run-1")
    assert ov is not None
    # Fix 4: a completed+scored run is a produced output, not "incomplete".
    assert ov.status == ExecutionStatus.QUALIFIED
    assert ov.status != ExecutionStatus.INCOMPLETE
    assert ov.validation_status == "scored"


def test_seven_measures_composite_prechecks_triggers_exposed(tmp_path):
    out = _write_fo(tmp_path, "EHC", "fo-run-2", _fo_payload())
    m = build_manifest(out, "EHC", repo_root=out)
    ov = m.by_id("fo.overview.fo-run-2")
    assert ov.fo_measures == MEASURE_KEYS           # all seven measures
    assert ov.composite_score == 6.4
    assert ov.credit_rating == "BBB"
    assert ov.risk_level == "moderate"
    assert "going_concern" in ov.fo_prechecks and "covenants" in ov.fo_prechecks
    assert "leverage_t2" in ov.fo_triggers_fired    # only fired triggers listed


def test_rating_is_provisional_pending_human_approval(tmp_path):
    out = _write_fo(tmp_path, "EHC", "fo-run-3", _fo_payload(score=7.2))
    m = build_manifest(out, "EHC", repo_root=out)
    ov = m.by_id("fo.overview.fo-run-3")
    # Fix 3: rating awaiting approval is explicitly provisional (score retained).
    assert ov.rating_provisional is True
    assert ov.composite_score == 7.2            # score NOT dropped
    assert ov.human_review_status == HumanReviewStatus.PENDING


def test_escalated_fo_is_qualified_and_provisional(tmp_path):
    out = _write_fo(tmp_path, "EHC", "fo-run-4", _fo_payload(escalated=True))
    m = build_manifest(out, "EHC", repo_root=out)
    ov = m.by_id("fo.overview.fo-run-4")
    assert ov.status == ExecutionStatus.QUALIFIED
    assert ov.rating_provisional is True
    assert ov.human_review_status == HumanReviewStatus.ESCALATED


def test_fo_stage_nodes_are_indexed_and_inspectable(tmp_path):
    out = _write_fo(tmp_path, "EHC", "fo-run-5", _fo_payload())
    m = build_manifest(out, "EHC", repo_root=out)
    ids = {a.artifact_id for a in m.artifacts}
    for stage in ("fo.precheck", "fo.measures", "fo.triggers", "fo.scoring"):
        assert f"{stage}.fo-run-5" in ids
    # The scoring stage is provisional; measures/precheck/triggers are recorded.
    scoring = m.by_id("fo.scoring.fo-run-5")
    assert scoring.rating_provisional is True
    # Inspecting the measures node returns the recorded metrics slice.
    info = inspect_node("EHC", "fo.measures.fo-run-5", out, repo_root=out)
    assert isinstance(info["payload"], list)
    assert any(row.get("key") == "dscr" for row in info["payload"])


def test_fo_pdf_and_json_integrity_preserved(tmp_path):
    from app.services.registry.explorer import load_artifact_bytes
    from app.services.registry.json_render import render_json_pdf
    import hashlib

    payload = _fo_payload()
    out = _write_fo(tmp_path, "EHC", "fo-run-6", payload)
    raw, sha, _ = load_artifact_bytes("EHC", "fo.overview.fo-run-6", out, repo_root=out)
    disk = (out / "fo" / "EHC" / "fo-run-6" / "financial_overview.json").read_bytes()
    assert raw == disk and sha == hashlib.sha256(disk).hexdigest()
    pdf_a = render_json_pdf(payload, title="FO")
    pdf_b = render_json_pdf(payload, title="FO")
    assert pdf_a[:4] == b"%PDF" and pdf_a == pdf_b   # deterministic
