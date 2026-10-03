"""Source-package completeness via a required-source profile (Requirement 22).

A case/industry source-criticality **profile** classifies each expected source
type as ``critical``, ``important``, or ``optional``. The profile is
*configuration*, not code: it is stored and versioned through the existing
:class:`~app.core.config_registry.ConfigRegistry` under the ``source_profiles``
artifact kind, so the determination is deterministic and reproducible, and the
exact profile version is recorded per case (Req 22.1, 22.4, 19.6). The airline
profile is only an illustrative example; nothing here hardcodes it.

Completeness assessment is **deterministic** and based solely on which source
types are present versus the configured profile -- never on LLM confidence
(Req 22.4). Outcomes:

* a missing ``critical`` source -> a mandatory escalation condition or an
  explicit inability-to-conclude (Req 22.2);
* a missing ``important`` source -> recorded as reduced coverage;
* a missing ``optional`` source -> recorded as reduced coverage without
  escalation (Req 22.3).

Escalation boundary (Milestone 5 owns the escalation engine): we represent the
"mandatory escalation" condition minimally and forward-compatibly by returning a
structured :class:`CompletenessAssessment` (with ``mandatory_escalation`` set and
the exact missing critical source types listed) that a later escalation engine
can consume. We do NOT build the escalation engine here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable

from app.core.config_registry import ConfigRegistry


class Criticality(str, Enum):
    """How critical a source type is for a complete package (Req 22.1)."""

    CRITICAL = "critical"
    IMPORTANT = "important"
    OPTIONAL = "optional"


@dataclass(frozen=True)
class CompletenessAssessment:
    """Deterministic result of a source-package completeness check.

    * ``mandatory_escalation`` is True iff a ``critical`` source is missing
      (Req 22.2). ``missing_critical`` lists exactly which ones.
    * ``missing_important`` / ``missing_optional`` capture reduced coverage
      (Req 22.3). ``reduced_coverage`` is True iff anything non-critical is
      absent.
    * ``profile_version`` records the exact ``source_profiles`` config version
      used (Req 19.6), so the determination is reproducible.
    """

    complete: bool
    mandatory_escalation: bool
    reduced_coverage: bool
    missing_critical: list[str] = field(default_factory=list)
    missing_important: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    profile_version: int | None = None

    @property
    def inability_to_conclude(self) -> bool:
        """Alias of ``mandatory_escalation`` for the Req 22.2 wording.

        A missing critical source may be surfaced either as a mandatory
        escalation or as an explicit inability to conclude; the condition is the
        same deterministic flag.
        """
        return self.mandatory_escalation


class SourceProfileError(ValueError):
    """Raised when no source profile is configured (no silent defaults, Req 21)."""


def _profile_entries(content: dict) -> dict[str, str]:
    """Extract ``{source_type: criticality}`` from a profile config payload.

    Accepts either a flat ``{source_type: criticality}`` mapping or a
    ``{"sources": {source_type: criticality}}`` wrapper.
    """
    sources = content.get("sources", content)
    if not isinstance(sources, dict):
        raise SourceProfileError("source profile has no 'sources' mapping.")
    return {str(k): str(v) for k, v in sources.items()}


class CompletenessEvaluator:
    """Assess source-package completeness against a versioned profile."""

    def __init__(self, registry: ConfigRegistry) -> None:
        self._registry = registry

    def assess(
        self,
        present_source_types: Iterable[str],
        *,
        profile_version: int | None = None,
    ) -> CompletenessAssessment:
        """Assess completeness of ``present_source_types`` against the profile.

        Uses the latest registered ``source_profiles`` version unless an
        explicit ``profile_version`` is given (supporting reproducible historical
        runs). Raises :class:`SourceProfileError` if no profile is configured --
        the system never silently assumes a default profile (Req 21.1).
        """
        if profile_version is not None:
            row = self._registry.get("source_profiles", profile_version)
            if row is None:
                raise SourceProfileError(
                    f"source_profiles version {profile_version} is not registered."
                )
            content, used_version = row.content, row.version
        else:
            latest = self._registry.latest("source_profiles")
            if latest is None:
                raise SourceProfileError(
                    "No 'source_profiles' configuration has been registered; "
                    "a source-criticality profile is required (no silent default)."
                )
            full = self._registry.get("source_profiles", latest.version)
            assert full is not None  # latest() guarantees existence
            content, used_version = full.content, full.version

        profile = _profile_entries(content)
        present = {str(s) for s in present_source_types}

        missing_critical: list[str] = []
        missing_important: list[str] = []
        missing_optional: list[str] = []

        for source_type, raw_crit in profile.items():
            if source_type in present:
                continue
            crit = Criticality(raw_crit)
            if crit is Criticality.CRITICAL:
                missing_critical.append(source_type)
            elif crit is Criticality.IMPORTANT:
                missing_important.append(source_type)
            else:
                missing_optional.append(source_type)

        reduced = bool(missing_important or missing_optional)
        mandatory = bool(missing_critical)
        complete = not (missing_critical or missing_important or missing_optional)

        return CompletenessAssessment(
            complete=complete,
            mandatory_escalation=mandatory,
            reduced_coverage=reduced,
            missing_critical=sorted(missing_critical),
            missing_important=sorted(missing_important),
            missing_optional=sorted(missing_optional),
            profile_version=used_version,
        )
