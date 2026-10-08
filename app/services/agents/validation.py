"""Deterministic validation layer (Milestone 8 / Req 8, 10, 34).

Every LLM result passes through this layer BEFORE it becomes a ParameterResult
or reaches orchestration. The layer makes an explicit split (Remediation 10):

**Bucket A — deterministic evidence validation** (the only checks that GATE
storage; all genuinely deterministic):

* schema validity (JSON shape),
* evidence-ID existence (every cited id is in the routed packet / admitted set),
* admitted-source membership,
* entity / period / unit compatibility (where the output carries them),
* citation validity (claims that assert facts must cite evidence),
* exact numeric / structured-term correspondence — a quoted number must equal a
  referenced ParameterResult value (this is also how orchestrator
  quote-not-calculate is enforced, Req 34),
* allowed-enum checks,
* parameter-ownership — an agent may only emit parameters it owns in the registry.

**Bucket B — narrative semantic support** is explicitly NOT performed here as a
deterministic gate: arbitrary narrative entailment is not deterministic. It is
handled by the grounding infrastructure / LLM judge / challenge agent / human,
and is surfaced as advisory, never used to silently pass or fail storage. The
baseline principle holds: citation presence alone is never sufficient support.

This module is deterministic and never calls an LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import jsonschema

from app.schemas.agentic import EvidencePacket


class ValidationCheck(str, Enum):
    SCHEMA = "schema"
    EVIDENCE_EXISTS = "evidence_exists"
    ADMITTED_SOURCE = "admitted_source"
    ENTITY_COMPATIBLE = "entity_compatible"
    PERIOD_COMPATIBLE = "period_compatible"
    UNIT_COMPATIBLE = "unit_compatible"
    CITATION_VALID = "citation_valid"
    NUMERIC_CORRESPONDENCE = "numeric_correspondence"
    ENUM_ALLOWED = "enum_allowed"
    PARAMETER_OWNERSHIP = "parameter_ownership"


@dataclass(frozen=True)
class ValidationIssue:
    check: ValidationCheck
    detail: str


@dataclass
class ValidationOutcome:
    """Result of deterministic (bucket A) validation."""

    valid: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    # Bucket B advisory notes (never gate storage).
    semantic_notes: list[str] = field(default_factory=list)

    def reasons(self) -> str:
        return "; ".join(f"{i.check.value}: {i.detail}" for i in self.issues)


@dataclass(frozen=True)
class ParameterRef:
    """A known, already-validated ParameterResult value for correspondence checks."""

    parameter_id: str
    value: Any


class DeterministicValidator:
    """Bucket-A deterministic validation of one agent output (Req 8)."""

    def __init__(
        self,
        *,
        admitted_evidence_ids: set[str] | None = None,
        owned_parameters: set[str] | None = None,
        known_parameters: dict[str, Any] | None = None,
        expected_entity: str | None = None,
        expected_fiscal_year: int | None = None,
        numeric_tolerance: float = 1e-9,
    ) -> None:
        self._admitted = admitted_evidence_ids or set()
        self._owned = owned_parameters
        self._known = known_parameters or {}
        self._entity = expected_entity
        self._fiscal_year = expected_fiscal_year
        self._tol = numeric_tolerance

    @classmethod
    def for_packet(
        cls,
        packet: EvidencePacket,
        *,
        owned_parameters: set[str] | None = None,
        known_parameters: dict[str, Any] | None = None,
        expected_entity: str | None = None,
        expected_fiscal_year: int | None = None,
    ) -> "DeterministicValidator":
        """Build a validator whose admitted evidence IDs come from the packet."""
        return cls(
            admitted_evidence_ids=set(packet.evidence_ids),
            owned_parameters=owned_parameters,
            known_parameters=known_parameters,
            expected_entity=expected_entity,
            expected_fiscal_year=expected_fiscal_year,
        )

    def validate(
        self,
        parsed: dict[str, Any] | None,
        *,
        schema: dict[str, Any] | None = None,
    ) -> ValidationOutcome:
        issues: list[ValidationIssue] = []
        notes: list[str] = []

        if parsed is None:
            return ValidationOutcome(
                valid=False,
                issues=[ValidationIssue(ValidationCheck.SCHEMA, "No parsed output.")],
            )

        # --- schema ---
        if schema is not None:
            try:
                jsonschema.validate(instance=parsed, schema=schema)
            except jsonschema.ValidationError as exc:
                issues.append(
                    ValidationIssue(ValidationCheck.SCHEMA, exc.message)
                )
                return ValidationOutcome(valid=False, issues=issues)

        claims = _iter_claims(parsed)
        emitted_params = _iter_emitted_parameters(parsed)

        # --- parameter ownership ---
        if self._owned is not None:
            for pid in emitted_params:
                if pid not in self._owned:
                    issues.append(
                        ValidationIssue(
                            ValidationCheck.PARAMETER_OWNERSHIP,
                            f"Agent emitted parameter {pid!r} it does not own.",
                        )
                    )

        for claim in claims:
            cid = claim.get("claim_id", "?")
            evidence_ids = claim.get("evidence_ids") or claim.get("evidence_refs") or []

            # --- citation validity: a factual claim must cite evidence ---
            kind = claim.get("kind") or claim.get("category")
            if kind in {"fact"} and not evidence_ids:
                issues.append(
                    ValidationIssue(
                        ValidationCheck.CITATION_VALID,
                        f"Factual claim {cid!r} cites no evidence.",
                    )
                )

            # --- evidence existence / admitted source ---
            for eid in evidence_ids:
                if self._admitted and eid not in self._admitted:
                    issues.append(
                        ValidationIssue(
                            ValidationCheck.EVIDENCE_EXISTS,
                            f"Claim {cid!r} cites unknown/unadmitted evidence {eid!r}.",
                        )
                    )

            # --- entity / period compatibility (only when asserted) ---
            if self._entity and claim.get("entity_id") not in (None, self._entity):
                issues.append(
                    ValidationIssue(
                        ValidationCheck.ENTITY_COMPATIBLE,
                        f"Claim {cid!r} references entity {claim.get('entity_id')!r} "
                        f"!= expected {self._entity!r}.",
                    )
                )
            if (
                self._fiscal_year is not None
                and claim.get("fiscal_year") not in (None, self._fiscal_year)
            ):
                issues.append(
                    ValidationIssue(
                        ValidationCheck.PERIOD_COMPATIBLE,
                        f"Claim {cid!r} references FY{claim.get('fiscal_year')} "
                        f"!= expected FY{self._fiscal_year}.",
                    )
                )

            # --- numeric correspondence (quote-not-calculate, Req 34) ---
            issues.extend(self._check_quoted_numbers(claim, cid))

            # Bucket B: narrative support is advisory, never a deterministic gate.
            if kind in {"interpretation", "assessment", "driver", "risk"} and evidence_ids:
                notes.append(
                    f"Claim {cid!r} requires narrative semantic support review "
                    "(bucket B; not a deterministic gate)."
                )

        return ValidationOutcome(valid=not issues, issues=issues, semantic_notes=notes)

    def _check_quoted_numbers(
        self, claim: dict[str, Any], cid: str
    ) -> list[ValidationIssue]:
        """A quoted number must match its referenced ParameterResult (Req 34).

        A claim may carry ``quoted_values``: a mapping of parameter_id -> number.
        Each referenced parameter must exist in the known validated set and the
        quoted value must match exactly (within tolerance). An invented or
        mismatched number is rejected — this is how orchestrators are prevented
        from calculating.
        """
        issues: list[ValidationIssue] = []
        quoted = claim.get("quoted_values") or {}
        if not isinstance(quoted, dict):
            return issues
        for pid, number in quoted.items():
            if pid not in self._known:
                issues.append(
                    ValidationIssue(
                        ValidationCheck.NUMERIC_CORRESPONDENCE,
                        f"Claim {cid!r} quotes {number!r} for parameter {pid!r} that "
                        "has no validated ParameterResult (invented number).",
                    )
                )
                continue
            known = self._known[pid]
            if not _numbers_match(number, known, self._tol):
                issues.append(
                    ValidationIssue(
                        ValidationCheck.NUMERIC_CORRESPONDENCE,
                        f"Claim {cid!r} quotes {number!r} for {pid!r} but the "
                        f"validated value is {known!r} (no LLM arithmetic allowed).",
                    )
                )
        return issues


def _iter_claims(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect claim-like dicts from common response shapes."""
    claims: list[dict[str, Any]] = []
    for key in (
        "overall_assessment",
        "strengths",
        "weaknesses",
        "key_drivers",
        "material_risks",
        "business_overview",
        "repayment_analysis",
        "key_risks",
        "mitigants",
        "claims",
        "facts",
    ):
        value = parsed.get(key)
        if isinstance(value, dict):
            claims.append(value)
        elif isinstance(value, list):
            claims.extend(c for c in value if isinstance(c, dict))
    return claims


def _iter_emitted_parameters(parsed: dict[str, Any]) -> list[str]:
    params = parsed.get("parameters")
    out: list[str] = []
    if isinstance(params, list):
        for p in params:
            if isinstance(p, dict) and "parameter_id" in p:
                out.append(p["parameter_id"])
            elif isinstance(p, str):
                out.append(p)
    return out


def _numbers_match(a: Any, b: Any, tol: float) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return a == b
