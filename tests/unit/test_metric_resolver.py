"""Task 11 — AuthoritativeMetricResolver unit coverage (Metric-backed path).

The integration suite covers Fact-backed resolution against the real NVIDIA
fixture; this file covers the deterministic ``Metric``-record path and the
batch/disposition surface in isolation. A persisted ``Metric`` corroborates ONLY
when it belongs to the case, carries a real result, needs no review and has
``evidence_quality == "verified"``. Everything else fails closed with an
explicit reason -- the resolver never invents a value or broadens accepted
quality states.
"""

from __future__ import annotations

from app.models.orm import Case, Metric
from app.services.analysis.metric_resolver import AuthoritativeMetricResolver

CASE = "C1"


def _metric(session, *, name, case_id=CASE, result=1.25,
            evidence_quality="verified", review_required=False,
            explicit_state=None):
    session.add(Metric(
        case_id=case_id, metric_name=name, metric_definition_id="d",
        metric_definition_version=1, formula_id="f", engine_version="1.0.0",
        inputs={}, input_fact_ids=[], result=result, explicit_state=explicit_state,
        period="FY2026", units="x", evidence_quality=evidence_quality,
        review_required=review_required,
    ))
    session.flush()


def _case(session):
    session.add(Case(case_id=CASE, status="open"))
    session.flush()


def test_metric_resolves_only_when_verified_and_review_clear(db_session):
    _case(db_session)
    _metric(db_session, name="interest_coverage")
    resolver = AuthoritativeMetricResolver(db_session)
    resolution = resolver.resolve(["interest_coverage"], case_id=CASE)

    assert resolution.usable_ids == {"interest_coverage"}
    item = resolution.items[0]
    assert item.evidence_id == "interest_coverage"
    assert item.value == 1.25
    assert item.unit == "x"
    assert item.concept == "interest_coverage"
    assert item.period == "FY2026"
    assert resolution.rejected() == []


def test_metric_rejected_when_review_required_or_unverified_or_no_result(db_session):
    _case(db_session)
    _metric(db_session, name="needs_review", review_required=True)
    _metric(db_session, name="unverified_quality", evidence_quality="unverified")
    _metric(db_session, name="not_meaningful", result=None,
            explicit_state="not_meaningful")
    resolver = AuthoritativeMetricResolver(db_session)
    resolution = resolver.resolve(
        ["needs_review", "unverified_quality", "not_meaningful"], case_id=CASE)

    assert resolution.usable_ids == set()
    reasons = {r.requested_id: r.reasons for r in resolution.rejected()}
    assert any("requires review" in m for m in reasons["needs_review"])
    assert any("evidence_quality" in m for m in reasons["unverified_quality"])
    assert any("no usable result" in m for m in reasons["not_meaningful"])


def test_metric_from_other_case_is_rejected(db_session):
    _case(db_session)
    db_session.add(Case(case_id="OTHER", status="open"))
    db_session.flush()
    _metric(db_session, name="liquidity", case_id="OTHER")
    resolver = AuthoritativeMetricResolver(db_session)
    # The resolver looks up a metric by name scoped to the requested case, so an
    # other-case metric is simply not found for CASE -> unknown-record rejection.
    resolution = resolver.resolve(["liquidity"], case_id=CASE)
    assert resolution.usable_ids == set()
    assert resolution.rejected()[0].requested_id == "liquidity"


def test_resolve_is_deterministic_and_batched(db_session):
    _case(db_session)
    _metric(db_session, name="a")
    _metric(db_session, name="b")
    resolver = AuthoritativeMetricResolver(db_session)
    first = resolver.resolve(["b", "a", "a"], case_id=CASE)
    second = resolver.resolve(["a", "b"], case_id=CASE)
    # De-duplicated and order-independent usable set.
    assert first.usable_ids == second.usable_ids == {"a", "b"}
    assert [i.evidence_id for i in first.items] == ["a", "b"]  # sorted request ids


def test_latest_metric_row_wins_for_a_name(db_session):
    """When a metric name has multiple persisted rows (append-only re-runs), the
    resolver binds to the most recent -- it never silently averages or merges."""
    _case(db_session)
    _metric(db_session, name="roe", result=0.1)
    _metric(db_session, name="roe", result=0.2)  # later row
    resolver = AuthoritativeMetricResolver(db_session)
    resolution = resolver.resolve(["roe"], case_id=CASE)
    assert resolution.usable_ids == {"roe"}
    assert resolution.items[0].value == 0.2
