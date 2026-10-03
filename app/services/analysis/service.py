"""Generative credit analysis service (task 6.4; Req 12.1-12.7).

The analysis step interprets ALREADY-VERIFIED evidence; it never recreates
numbers and never invents facts. This service:

* assembles the ALLOWED inputs ONLY (Req 12.1): canonical facts, deterministic
  metrics, trends, benchmarks, needed snippets, data limitations, open
  conflicts, plus the approved prompt (resolved by the LLMClient);
* runs the ``business_analysis`` prompt through the single LLMClient, which
  validates the output against the fixed schema BEFORE use (Req 12.5);
* returns the schema-validated :class:`AnalysisResponse` with
  ``business_overview, repayment_analysis, key_risks, mitigants,
  data_limitations, questions_for_human`` (Req 12.2); every factual claim
  carries evidence IDs (enforced by the schema, Req 12.3/12.6);
* when CRITICAL evidence is missing, produces a caveat and (optionally) a
  mandatory escalation rather than confident prose (Req 12.7). The caveat is
  added to ``data_limitations``/``questions_for_human`` and the escalation is
  raised through the Milestone 5 engine with an explicit ``rule_id``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas.llm import AnalysisResponse
from app.services.escalation.engine import EscalationEngine
from app.services.escalation.rules import EscalationCategory, Severity
from app.services.llm.client import LLMClient, ModelRunResult

# Explicit rule id for a missing-critical-evidence analysis caveat/escalation.
RULE_MISSING_CRITICAL_EVIDENCE = "R-EVIDENCE-CRITICAL-01"


class AnalysisRejectedError(ValueError):
    """Raised when the analysis LLM output fails schema validation."""


@dataclass
class AnalysisInputs:
    """The ONLY inputs the analysis LLM may receive (Req 12.1).

    Nothing beyond these fields is forwarded to the model, so the analysis
    cannot smuggle in un-vetted context.
    """

    canonical_facts: list[dict] = field(default_factory=list)
    metrics: list[dict] = field(default_factory=list)
    trends: list[dict] = field(default_factory=list)
    benchmarks: list[dict] = field(default_factory=list)
    snippets: list[dict] = field(default_factory=list)
    data_limitations: list[str] = field(default_factory=list)
    open_conflicts: list[dict] = field(default_factory=list)
    missing_critical_evidence: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "canonical_facts": self.canonical_facts,
            "metrics": self.metrics,
            "trends": self.trends,
            "benchmarks": self.benchmarks,
            "snippets": self.snippets,
            "data_limitations": self.data_limitations,
            "open_conflicts": self.open_conflicts,
            "missing_critical_evidence": self.missing_critical_evidence,
        }

    def evidence_ids(self) -> list[str]:
        """Evidence IDs supplied to the model, logged on the model run."""
        ids: list[str] = []
        for fact in self.canonical_facts:
            fid = fact.get("fact_id")
            if fid:
                ids.append(str(fid))
        for metric in self.metrics:
            mid = metric.get("metric_name") or metric.get("id")
            if mid:
                ids.append(str(mid))
        return ids


@dataclass
class AnalysisResult:
    """A completed analysis plus its model-run provenance."""

    analysis: AnalysisResponse
    run: ModelRunResult
    escalation_id: str | None = None


class AnalysisService:
    """Run schema-validated generative credit analysis."""

    def __init__(
        self,
        client: LLMClient,
        *,
        escalation_engine: EscalationEngine | None = None,
        case_id: str | None = None,
    ) -> None:
        self._client = client
        self._escalation = escalation_engine
        self._case_id = case_id

    def analyze(
        self,
        inputs: AnalysisInputs,
        *,
        case_version: int | None = None,
        key: str | None = None,
    ) -> AnalysisResult:
        """Produce schema-validated analysis from the allowed inputs only.

        Raises :class:`AnalysisRejectedError` if the LLM output fails schema
        validation (the rejection is logged by the client). When critical
        evidence is missing, a caveat is injected and a mandatory escalation is
        raised (Req 12.7).
        """
        run = self._client.analyze(
            inputs.to_payload(),
            evidence_ids=inputs.evidence_ids(),
            case_id=self._case_id,
            case_version=case_version,
            key=key,
        )
        if not run.is_valid or run.parsed is None:
            raise AnalysisRejectedError(
                "Analysis LLM output failed schema validation and was rejected "
                "before use (Req 12.5)."
            )

        analysis = AnalysisResponse.model_validate(run.parsed)
        escalation_id = self._handle_missing_critical(inputs, analysis)
        return AnalysisResult(
            analysis=analysis, run=run, escalation_id=escalation_id
        )

    def _handle_missing_critical(
        self, inputs: AnalysisInputs, analysis: AnalysisResponse
    ) -> str | None:
        """Caveat + escalate on missing critical evidence (Req 12.7).

        Mutates the analysis to add a data limitation and a human question so
        the memo carries a caveat instead of confident prose, and raises a
        MANDATORY escalation via the Milestone 5 engine (explicit rule id).
        """
        if not inputs.missing_critical_evidence:
            return None

        caveat = (
            "Critical evidence missing: "
            + ", ".join(inputs.missing_critical_evidence)
            + ". Conclusions are caveated and require human review."
        )
        if caveat not in analysis.data_limitations:
            analysis.data_limitations.append(caveat)
        question = (
            "Obtain or confirm the missing critical evidence before relying on "
            "this analysis: " + ", ".join(inputs.missing_critical_evidence) + "."
        )
        if question not in analysis.questions_for_human:
            analysis.questions_for_human.append(question)

        if self._escalation is None:
            return None
        escalation = self._escalation.raise_escalation(
            rule_id=RULE_MISSING_CRITICAL_EVIDENCE,
            category=EscalationCategory.EVIDENCE,
            severity=Severity.MANDATORY,
            reason=caveat,
            evidence_refs=list(inputs.missing_critical_evidence),
            case_id=self._case_id,
        )
        return escalation.escalation_id
