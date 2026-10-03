"""Versioned prompt registry (Req 19.1, 19.5; task 6.2).

Treats prompts as versioned, hashed artifacts stored in the ``prompt_versions``
table. Guarantees:

* **Identity (Req 19.1).** Every prompt has a ``name`` + integer ``version`` and
  a human-facing ``prompt_id`` of the form ``{name}_v{version}.0`` (e.g.
  ``business_analysis_v1.0``).
* **Hashing.** The canonical SHA-256 covers the template text AND the fixed
  response schema, so changing either produces a new hash.
* **Append-only versioning.** Registering identical content is idempotent
  (returns the existing version). Registering changed content allocates the next
  version; prior rows are never rewritten, so historical runs stay linked to the
  exact prompt version.
* **Prompt update -> regression signal (Req 19.5).** A newly-created version is
  flagged ``needs_regression=True``. This is a REAL, queryable signal (not a
  silently-applied change and not a separate CI run): callers list
  :meth:`prompts_needing_regression` and clear the flag via
  :meth:`clear_regression` once regression tests pass. A ``config_version_changed``
  audit event is emitted on every new version when an audit hook is supplied.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.hashing import content_hash
from app.models.orm import PromptVersion
from app.prompts.templates import PROMPT_CATALOGUE

# Audit hook: (event_type, prompt_id, version, before, after).
PromptAuditHook = Callable[[str, str, int, dict | None, dict], None]


def format_prompt_id(name: str, version: int) -> str:
    """Return the human-facing prompt id, e.g. ``business_analysis_v1.0``."""
    return f"{name}_v{version}.0"


@dataclass(frozen=True)
class RegisteredPrompt:
    """A read view of a registered prompt version."""

    name: str
    version: int
    prompt_id: str
    template: str
    response_schema: dict[str, Any]
    content_hash: str
    role: str | None
    needs_regression: bool


class PromptRegistry:
    """Versions and hashes prompt artifacts over a SQLAlchemy session."""

    def __init__(self, session: Session, audit_hook: PromptAuditHook | None = None):
        self._session = session
        self._audit_hook = audit_hook

    # -- registration ---------------------------------------------------------

    def register(
        self,
        name: str,
        *,
        template: str,
        response_schema: dict[str, Any],
        role: str | None = None,
        label: str | None = None,
    ) -> RegisteredPrompt:
        """Register (or re-version) a prompt. Returns the active version.

        Identical (template + schema) content is idempotent. Changed content
        allocates the next version and flags it ``needs_regression`` (Req 19.5).
        """
        new_hash = content_hash({"template": template, "response_schema": response_schema})
        latest = self._latest_row(name)

        if latest is not None and latest.content_hash == new_hash:
            return self._to_view(latest)

        next_version = 1 if latest is None else latest.version + 1
        prompt_id = format_prompt_id(name, next_version)
        row = PromptVersion(
            name=name,
            version=next_version,
            prompt_id=prompt_id,
            template=template,
            response_schema=response_schema,
            content_hash=new_hash,
            role=role,
            needs_regression=True,
            label=label,
        )
        self._session.add(row)
        self._session.flush()

        if self._audit_hook is not None:
            before = (
                {"version": latest.version, "content_hash": latest.content_hash}
                if latest is not None
                else None
            )
            self._audit_hook(
                "config_version_changed",
                prompt_id,
                next_version,
                before,
                {"version": next_version, "content_hash": new_hash},
            )
        return self._to_view(row)

    def register_catalogue(self) -> list[RegisteredPrompt]:
        """Register every prompt in :data:`PROMPT_CATALOGUE`."""
        return [
            self.register(
                entry["name"],
                template=entry["template"],
                response_schema=entry["response_schema"],
                role=entry.get("role"),
                label=entry.get("label"),
            )
            for entry in PROMPT_CATALOGUE
        ]

    # -- regression signal (Req 19.5) ----------------------------------------

    def prompts_needing_regression(self) -> list[RegisteredPrompt]:
        """Return every prompt version still awaiting regression clearance."""
        stmt = select(PromptVersion).where(PromptVersion.needs_regression.is_(True))
        return [self._to_view(r) for r in self._session.execute(stmt).scalars().all()]

    def clear_regression(self, name: str, version: int) -> RegisteredPrompt:
        """Clear the regression flag after regression tests pass for a version."""
        row = self._row(name, version)
        if row is None:
            raise ValueError(f"No prompt {name!r} version {version!r} to clear.")
        row.needs_regression = False
        self._session.flush()
        return self._to_view(row)

    # -- lookups --------------------------------------------------------------

    def latest(self, name: str) -> RegisteredPrompt | None:
        row = self._latest_row(name)
        return self._to_view(row) if row is not None else None

    def get(self, name: str, version: int) -> RegisteredPrompt | None:
        row = self._row(name, version)
        return self._to_view(row) if row is not None else None

    def _latest_row(self, name: str) -> PromptVersion | None:
        stmt = (
            select(PromptVersion)
            .where(PromptVersion.name == name)
            .order_by(PromptVersion.version.desc())
            .limit(1)
        )
        return self._session.execute(stmt).scalar_one_or_none()

    def _row(self, name: str, version: int) -> PromptVersion | None:
        stmt = select(PromptVersion).where(
            PromptVersion.name == name, PromptVersion.version == version
        )
        return self._session.execute(stmt).scalar_one_or_none()

    @staticmethod
    def _to_view(row: PromptVersion) -> RegisteredPrompt:
        return RegisteredPrompt(
            name=row.name,
            version=row.version,
            prompt_id=row.prompt_id,
            template=row.template,
            response_schema=row.response_schema,
            content_hash=row.content_hash,
            role=row.role,
            needs_regression=row.needs_regression,
        )
