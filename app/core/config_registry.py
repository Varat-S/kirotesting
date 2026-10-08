"""Configuration registry: versions and hashes config artifacts (Req 19.6).

The registry treats every replaceable configuration artifact as a versioned,
hashed object. Supported artifact kinds (design.md "Configuration"):

* ``tolerances``        -- reconciliation tolerances & near-zero floor
* ``policy``            -- illustrative policy thresholds
* ``peers``             -- peer cohort membership
* ``metric_defs``       -- metric definitions (components in/out)
* ``trend_rules``       -- adverse-direction / trend rules
* ``escalation_rules``  -- escalation rules
* ``parser_precedence`` -- deterministic parser precedence
* ``source_profiles``   -- source-criticality profiles

Guarantees:

* Registering an artifact computes a canonical SHA-256 content hash.
* Registering identical content returns the existing version (idempotent).
* Registering changed content allocates a new, incremented version and a new
  hash; prior rows are never rewritten (append-only versioning).
* Each snapshot can record the exact ``config_versions`` it used via
  :meth:`ConfigRegistry.config_versions_map`.
* Changing a metric definition or any config artifact emits an audit event
  (``config_version_changed`` / ``metric_definition_changed``) when an audit
  logger is supplied (Req 8.8, 18.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.hashing import content_hash
from app.models.orm import ConfigVersion

# Canonical set of configuration artifact kinds the registry understands.
ARTIFACT_KINDS: frozenset[str] = frozenset(
    {
        "tolerances",
        "policy",
        "peers",
        "metric_defs",
        "trend_rules",
        "escalation_rules",
        "parser_precedence",
        "source_profiles",
        "financial_mappings",
        # Agentic deterministic scoring configuration (Milestone 9).
        "scoring",
    }
)

# An audit callback accepts (event_type, artifact_kind, version, before, after).
AuditHook = Callable[[str, str, int, dict | None, dict], None]


@dataclass(frozen=True)
class RegisteredConfig:
    """Lightweight view of a registered configuration version row."""

    artifact_kind: str
    version: int
    content_hash: str
    label: str | None


class ConfigRegistry:
    """Versions and hashes configuration artifacts over a SQLAlchemy session."""

    def __init__(self, session: Session, audit_hook: AuditHook | None = None) -> None:
        self._session = session
        self._audit_hook = audit_hook

    # -- registration ---------------------------------------------------------

    def register(
        self,
        artifact_kind: str,
        content: dict[str, Any],
        label: str | None = None,
    ) -> RegisteredConfig:
        """Register ``content`` for ``artifact_kind``.

        Returns the existing version if the content hash is unchanged;
        otherwise allocates the next version. Raises ``ValueError`` for an
        unknown artifact kind (no silent defaults, Req 21.1).
        """
        if artifact_kind not in ARTIFACT_KINDS:
            raise ValueError(
                f"Unknown configuration artifact kind: {artifact_kind!r}. "
                f"Known kinds: {sorted(ARTIFACT_KINDS)}"
            )

        new_hash = content_hash(content)
        latest = self._latest_row(artifact_kind)

        if latest is not None and latest.content_hash == new_hash:
            # Idempotent: identical content, same version.
            return self._to_view(latest)

        next_version = 1 if latest is None else latest.version + 1
        row = ConfigVersion(
            artifact_kind=artifact_kind,
            version=next_version,
            content_hash=new_hash,
            content=content,
            label=label,
        )
        self._session.add(row)
        self._session.flush()

        self._emit_change_event(artifact_kind, next_version, latest, content)
        return self._to_view(row)

    def _emit_change_event(
        self,
        artifact_kind: str,
        version: int,
        previous: ConfigVersion | None,
        content: dict[str, Any],
    ) -> None:
        if self._audit_hook is None:
            return
        event_type = (
            "metric_definition_changed"
            if artifact_kind == "metric_defs"
            else "config_version_changed"
        )
        before = (
            {"version": previous.version, "content_hash": previous.content_hash}
            if previous is not None
            else None
        )
        after = {"version": version, "content_hash": content_hash(content)}
        self._audit_hook(event_type, artifact_kind, version, before, after)

    # -- lookups --------------------------------------------------------------

    def _latest_row(self, artifact_kind: str) -> ConfigVersion | None:
        stmt = (
            select(ConfigVersion)
            .where(ConfigVersion.artifact_kind == artifact_kind)
            .order_by(ConfigVersion.version.desc())
            .limit(1)
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def get(self, artifact_kind: str, version: int) -> ConfigVersion | None:
        """Return the row for a specific artifact kind + version, if present."""
        stmt = select(ConfigVersion).where(
            ConfigVersion.artifact_kind == artifact_kind,
            ConfigVersion.version == version,
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def latest(self, artifact_kind: str) -> RegisteredConfig | None:
        """Return the newest registered version for an artifact kind."""
        row = self._latest_row(artifact_kind)
        return self._to_view(row) if row is not None else None

    def config_versions_map(self, kinds: Iterable[str] | None = None) -> dict[str, int]:
        """Return ``{artifact_kind: latest_version}`` for recording on snapshots.

        A snapshot records exactly which configuration versions it used
        (Req 19.6). Only kinds that have at least one registered version are
        included.
        """
        selected = set(kinds) if kinds is not None else set(ARTIFACT_KINDS)
        result: dict[str, int] = {}
        for kind in sorted(selected):
            latest = self._latest_row(kind)
            if latest is not None:
                result[kind] = latest.version
        return result

    @staticmethod
    def _to_view(row: ConfigVersion) -> RegisteredConfig:
        return RegisteredConfig(
            artifact_kind=row.artifact_kind,
            version=row.version,
            content_hash=row.content_hash,
            label=row.label,
        )
