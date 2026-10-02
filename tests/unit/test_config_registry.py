"""Configuration registry versioning + hashing tests (Req 19.6, 23.1)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.core.config_registry import ARTIFACT_KINDS, ConfigRegistry
from app.core.hashing import content_hash
from app.models.orm import ConfigVersion
from app.services.audit.log import AuditLog, EventType


def test_register_assigns_version_one_and_hash(db_session: Session) -> None:
    reg = ConfigRegistry(db_session)
    result = reg.register("tolerances", {"revenue": 0.005, "debt": 0.005})
    assert result.version == 1
    assert result.content_hash == content_hash({"revenue": 0.005, "debt": 0.005})


def test_identical_content_is_idempotent(db_session: Session) -> None:
    """Re-registering identical content keeps the same version (no churn)."""
    reg = ConfigRegistry(db_session)
    first = reg.register("policy", {"net_debt_to_ebitda": 3.5})
    second = reg.register("policy", {"net_debt_to_ebitda": 3.5})
    assert first.version == second.version == 1
    assert db_session.query(ConfigVersion).filter_by(artifact_kind="policy").count() == 1


def test_changed_content_bumps_version_and_changes_hash(db_session: Session) -> None:
    """Req 19.6: changing content yields a new version and a new hash."""
    reg = ConfigRegistry(db_session)
    v1 = reg.register("policy", {"net_debt_to_ebitda": 3.5})
    v2 = reg.register("policy", {"net_debt_to_ebitda": 4.0})
    assert v2.version == 2
    assert v1.content_hash != v2.content_hash
    # Prior row is preserved (append-only), not rewritten.
    assert reg.get("policy", 1).content == {"net_debt_to_ebitda": 3.5}
    assert reg.get("policy", 2).content == {"net_debt_to_ebitda": 4.0}


def test_hash_is_order_independent(db_session: Session) -> None:
    reg = ConfigRegistry(db_session)
    a = reg.register("peers", {"UAL": 1, "AAL": 2})
    b = reg.register("peers", {"AAL": 2, "UAL": 1})
    assert a.version == b.version == 1  # same logical content -> no new version


def test_unknown_kind_raises_no_silent_default(db_session: Session) -> None:
    """Req 21.1: unknown artifact kinds are rejected, not silently defaulted."""
    reg = ConfigRegistry(db_session)
    with pytest.raises(ValueError):
        reg.register("not_a_real_kind", {"x": 1})


def test_config_versions_map_records_latest_per_kind(db_session: Session) -> None:
    reg = ConfigRegistry(db_session)
    reg.register("tolerances", {"revenue": 0.005})
    reg.register("tolerances", {"revenue": 0.01})  # -> version 2
    reg.register("metric_defs", {"ebitda": ["op_income", "d_and_a"]})
    mapping = reg.config_versions_map()
    assert mapping["tolerances"] == 2
    assert mapping["metric_defs"] == 1
    # Unregistered kinds are not fabricated.
    assert set(mapping).issubset(ARTIFACT_KINDS)


def test_change_emits_audit_event(db_session: Session) -> None:
    """Req 8.8 / 18.2: config + metric-def changes create audit events."""
    audit = AuditLog(db_session)
    events: list[tuple] = []

    def hook(event_type, kind, version, before, after):  # noqa: ANN001
        audit.record(
            event_type,
            after={"kind": kind, "version": version, **after},
            before=before,
            reason=f"{kind} -> v{version}",
        )
        events.append((event_type, kind, version))

    reg = ConfigRegistry(db_session, audit_hook=hook)
    reg.register("metric_defs", {"ebitda": ["op_income"]})
    reg.register("metric_defs", {"ebitda": ["op_income", "d_and_a"]})
    reg.register("tolerances", {"revenue": 0.005})

    kinds_and_types = {(et, k) for (et, k, _v) in events}
    assert (EventType.METRIC_DEFINITION_CHANGED.value, "metric_defs") in kinds_and_types
    assert (EventType.CONFIG_VERSION_CHANGED.value, "tolerances") in kinds_and_types
    # Idempotent re-register does not emit a change event.
    reg.register("tolerances", {"revenue": 0.005})
    assert len([e for e in events if e[1] == "tolerances"]) == 1
