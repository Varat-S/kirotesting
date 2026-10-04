"""Regressions for the deterministic integrity gaps in the hardening handoff."""

from itertools import permutations

import pytest

from app.core.config_registry import ConfigRegistry
from app.models.orm import Case
from app.schemas.enums import ExtractionMethod, FactStatus
from app.schemas.evidence import CanonicalFact, EntityRecord, SourceRef
from app.services.audit.log import AuditLog
from app.services.entity.registry import EntityRegistry
from app.services.metrics.definitions import MetricDefinitionRegistry
from app.services.metrics.engine import MetricEngine, MetricInput
from app.services.reconciliation.compatibility import check_compatibility
from app.services.reconciliation.service import (
    DEFAULT_TOLERANCES,
    Reconciler,
    ToleranceConfig,
)
from app.services.reconciliation.snapshot import SnapshotAssembler, _to_data_quality
from app.services.review.finalization import FinalSnapshotAssembler
from app.services.review.workflow import (
    HumanReviewWorkflow,
    ReviewAction,
    SignOffRequiredError,
)


def fact(fid, value, status=FactStatus.UNVERIFIED, method=ExtractionMethod.XBRL, **kw):
    return CanonicalFact(
        fact_id=fid,
        name="revenue",
        normalized_value=value,
        status=status,
        extraction_method=method,
        source_refs=[SourceRef(document_id=fid)],
        **kw,
    )


def reconciler():
    return Reconciler(ToleranceConfig(DEFAULT_TOLERANCES, 1))


@pytest.mark.parametrize(
    "name,a,b,expected",
    [
        ("operating_margin", 0.2, 0.8, "conflicting"),
        ("operating_margin", 0.2, 0.202, "verified"),
        ("operating_margin", 0, 0, "verified"),
        ("operating_margin", 0, 0.002, "verified"),
        ("operating_margin", -0.2, -0.8, "conflicting"),
        ("unknown", 0.2, 0.8, "conflicting"),
    ],
)
def test_explicit_tolerances(name, a, b, expected):
    result = reconciler().compare_values(name, a, b)
    assert result.resolved_state.value == expected
    assert result.tolerance_version == 1


@pytest.mark.parametrize("status", list(FactStatus)[1:])
def test_single_source_semantic_state(status):
    value = (
        None
        if status
        in {FactStatus.MISSING, FactStatus.NOT_DISCLOSED, FactStatus.NOT_APPLICABLE}
        else 100
    )
    assert (
        reconciler()
        .reconcile_field("revenue", [fact("a", value, status)])
        .resolved_state
        == status
    )


def test_three_source_verification_is_order_independent():
    facts = [fact("a", 1000), fact("b", 1004), fact("c", 1008)]
    for order in permutations(facts):
        assert (
            reconciler().reconcile_field("revenue", list(order)).resolved_state
            == FactStatus.CONFLICTING
        )


def test_selected_value_is_order_independent():
    a = fact("a", 1000)
    b = fact("b", 1001, method=ExtractionMethod.PDF_TABLE)
    for facts in ([a, b], [b, a]):
        result = reconciler().reconcile_field("revenue", facts)
        assert result.selected_fact_id == "a"
        assert result.selected_value == 1000
        assert _to_data_quality(result).value == 1000


def test_consolidation_scope_is_compatibility_dimension():
    a = fact("a", 1000, consolidation_scope="consolidated")
    b = fact("b", 1000, consolidation_scope="standalone")
    assert not check_compatibility(a, b).compatible
    assert (
        reconciler().reconcile_field("revenue", [a, b]).resolved_state
        == FactStatus.CONFLICTING
    )


@pytest.mark.parametrize(
    "state,value",
    [
        (FactStatus.NOT_DISCLOSED, None),
        (FactStatus.NOT_APPLICABLE, None),
        (FactStatus.STALE, 100),
        (FactStatus.CONFLICTING, 100),
    ],
)
def test_pair_interface_preserves_semantic_state(state, value):
    a = fact("a", value, state)
    b = fact("b", 100)
    assert reconciler().reconcile_pair("revenue", a, b).resolved_state == state


@pytest.mark.parametrize(
    "status", [FactStatus.VERIFIED, FactStatus.UNVERIFIED, FactStatus.STALE]
)
def test_metrics_propagate_evidence_quality(db_session, status):
    defs = MetricDefinitionRegistry(db_session)
    defs.register_defaults()
    result = MetricEngine(session=db_session).compute(
        defs.latest("operating_margin"),
        {
            "revenue": MetricInput("revenue", 1000, status, "r"),
            "operating_income": MetricInput("operating_income", 200, status, "i"),
        },
    )
    assert result.result == 0.2
    assert result.evidence_quality == status.value
    assert result.review_required == (status != FactStatus.VERIFIED)
    row = MetricEngine(session=db_session).persist(result)
    assert row.evidence_quality == status.value


def draft(session):
    session.add(Case(case_id="C"))
    session.flush()
    registry = ConfigRegistry(session)
    evidence = SnapshotAssembler(session, registry)
    evidence.persist(evidence.assemble(case_id="C", facts=[], reconciliations=[]))
    final = FinalSnapshotAssembler(session, registry, audit=AuditLog(session))
    return final, final.assemble(case_id="C", evidence_snapshot_version=1)


def test_finalization_requires_persisted_bound_review(db_session):
    service, snapshot = draft(db_session)
    with pytest.raises(SignOffRequiredError):
        service.finalize(snapshot, signed_off_by="officer")
    with pytest.raises(TypeError):
        service.finalize(snapshot, signed_off_by="officer", has_sign_off=True)
    wf = HumanReviewWorkflow(db_session, case_id="C")
    wf.record_action(
        ReviewAction.APPROVE_ASSUMPTIONS, reviewer="officer", signed_off=True
    )
    with pytest.raises(SignOffRequiredError):
        service.finalize(snapshot, signed_off_by="officer")
    wf.sign_off_recommendation(reviewer="officer", snapshot=snapshot)
    with pytest.raises(SignOffRequiredError):
        service.finalize(snapshot, signed_off_by="other")
    changed = snapshot.model_copy(update={"metrics": {"tampered": 42}})
    with pytest.raises(SignOffRequiredError):
        service.finalize(changed, signed_off_by="officer")
    row = service.finalize(snapshot, signed_off_by="officer")
    assert row.finalized
    assert row.payload["audit_metadata"]["sign_off_review_id"]


def test_persisted_draft_finalizes_same_row(db_session):
    service, snapshot = draft(db_session)
    row = service.persist_draft(snapshot)
    HumanReviewWorkflow(db_session, case_id="C").sign_off_recommendation(
        reviewer="officer", snapshot=snapshot
    )
    assert service.finalize(snapshot, signed_off_by="officer").id == row.id


def test_global_entity_has_case_specific_roles(db_session):
    db_session.add_all([Case(case_id="2023"), Case(case_id="2024")])
    db_session.flush()
    registry = EntityRegistry(db_session)
    first = registry.register(
        EntityRecord(
            entity_id="DAL",
            legal_name="Delta",
            entity_type="borrower",
            borrower_flag=True,
        ),
        "2023",
    )
    second = registry.register(
        EntityRecord(
            entity_id="DAL",
            legal_name="Delta",
            entity_type="guarantor",
            guarantor_flag=True,
        ),
        "2024",
    )
    assert first is second
    assert registry.for_case("2023")[0].borrower_flag
    assert registry.for_case("2024")[0].guarantor_flag
    assert not registry.for_case("2024")[0].borrower_flag
