"""Fail-closed semantic entailment contract for BO factual premises.

The service is deliberately narrow.  It never retrieves evidence and it never
overrides deterministic source, entity, period, numeric or denominator checks.
The caller supplies the approved evidence and a structured judge callable.  A
malformed response, an unknown evidence id or a failed deterministic check
leaves the premise unestablished.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any


SEMANTIC_ENTAILMENT_POLICY_VERSION = "bo-semantic-entailment-1"


class SemanticAssessment(str, Enum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    INSUFFICIENT = "insufficient"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class DeterministicCheck:
    name: str
    passed: bool
    detail: str = ""
    contradiction: bool = False


@dataclass(frozen=True)
class SemanticEvidence:
    evidence_id: str
    text: str


@dataclass(frozen=True)
class SemanticEntailmentResult:
    premise: str
    assessment: SemanticAssessment
    evidence_ids: tuple[str, ...]
    reasoning: str
    judge_model: str
    prompt_version: str
    policy_version: str = SEMANTIC_ENTAILMENT_POLICY_VERSION
    deterministic_checks: tuple[DeterministicCheck, ...] = ()
    malformed_output: bool = False
    adjudicated: bool = False
    adjudicated_by: str | None = None

    @property
    def established(self) -> bool:
        return self.assessment is SemanticAssessment.SUPPORTED


Judge = Callable[[dict[str, Any]], dict[str, Any]]


class SemanticEntailmentEvaluator:
    """Evaluate one premise against only its approved cited evidence."""

    def __init__(self, judge: Judge, *, model: str, prompt_version: str) -> None:
        self._judge = judge
        self._model = model
        self._prompt_version = prompt_version

    def evaluate(
        self,
        premise: str,
        evidence: list[SemanticEvidence],
        *,
        approved_evidence_ids: set[str],
        deterministic_checks: tuple[DeterministicCheck, ...] = (),
    ) -> SemanticEntailmentResult:
        cited = tuple(item.evidence_id for item in evidence)
        base = dict(
            premise=premise,
            evidence_ids=cited,
            judge_model=self._model,
            prompt_version=self._prompt_version,
            deterministic_checks=deterministic_checks,
        )
        if not premise.strip() or not evidence:
            return SemanticEntailmentResult(
                **base, assessment=SemanticAssessment.INSUFFICIENT,
                reasoning="A premise and at least one cited evidence item are required.")
        unknown = sorted(set(cited) - approved_evidence_ids)
        if unknown:
            return SemanticEntailmentResult(
                **base, assessment=SemanticAssessment.INSUFFICIENT,
                reasoning=f"Evidence is outside the approved packet: {', '.join(unknown)}.")
        failed = [check for check in deterministic_checks if not check.passed]
        if failed:
            state = (SemanticAssessment.CONTRADICTED
                     if any(check.contradiction for check in failed)
                     else SemanticAssessment.INSUFFICIENT)
            return SemanticEntailmentResult(
                **base, assessment=state,
                reasoning="Deterministic check failed: " + "; ".join(
                    f"{check.name}: {check.detail or 'failed'}" for check in failed))

        payload = {
            "premise": premise,
            "evidence": [item.__dict__ for item in evidence],
            "allowed_evidence_ids": list(cited),
            "question": (
                "Does the evidence support the premise as written without adding "
                "facts or causal assertions?"),
        }
        try:
            raw = self._judge(payload)
            assessment = SemanticAssessment(raw["assessment"])
            reasoning = raw["reasoning"]
            used = tuple(raw["evidence_ids"])
            if (not isinstance(reasoning, str) or not reasoning.strip()
                    or not used or set(used) - set(cited)):
                raise ValueError("invalid reasoning or evidence_ids")
        except (KeyError, TypeError, ValueError):
            return SemanticEntailmentResult(
                **base, assessment=SemanticAssessment.UNCERTAIN,
                reasoning="Judge output was invalid; human adjudication is required.",
                malformed_output=True)
        return SemanticEntailmentResult(
            **{**base, "evidence_ids": used}, assessment=assessment,
            reasoning=reasoning.strip())

    @staticmethod
    def adjudicate(
        result: SemanticEntailmentResult,
        *,
        assessment: SemanticAssessment,
        reasoning: str,
        adjudicated_by: str,
    ) -> SemanticEntailmentResult:
        if not reasoning.strip() or not adjudicated_by.strip():
            raise ValueError("Human adjudication requires a reviewer and reasoning.")
        return replace(result, assessment=assessment, reasoning=reasoning.strip(),
                       adjudicated=True, adjudicated_by=adjudicated_by.strip())
