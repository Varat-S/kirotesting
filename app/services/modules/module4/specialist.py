"""Module 4 Specialist Agent orchestration (Business Overview Architecture V5).

Ties the three pieces together:

1. Assemble the ALLOWED inputs only -- the shared SEC 10-K evidence + structured
   financial data. Module 4 does NOT receive Modules 1/2/3 outputs.
2. Run the registered ``module4_debt_structure_specialist`` prompt through the
   single :class:`LLMClient` via its existing ``analyze`` method (same pattern
   the other Business Overview specialist prompts use -- no new client method),
   which validates the raw output against the fixed
   :data:`app.schemas.module4.MODULE4_JSON_SCHEMA` BEFORE use.
3. Run the deterministic :class:`DebtStructureValidator` over the parsed output
   and attach the report. When it fails, a MANDATORY escalation is raised
   (explicit rule id) so the failure is a real, queryable signal rather than
   silent prose.

This service never drafts term sheets, covenants or security agreements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas.module4 import Module4Output
from app.services.escalation.engine import EscalationEngine
from app.services.escalation.rules import EscalationCategory, Severity
from app.services.llm.client import LLMClient, ModelRunResult
from app.services.modules.module4.prompt import MODULE4_AGENT_NAME
from app.services.modules.module4.debt_structure_validator import (
    DebtStructureValidator,
    DebtStructureValidatorReport,
)

# Explicit rule id for a Validator-8 failure on Module 4 output.
RULE_VALIDATOR8_FAILURE = "R-MODULE4-VALIDATOR8-01"


class Module4RejectedError(ValueError):
    """Raised when the Module 4 LLM output fails schema validation."""


@dataclass
class Module4Inputs:
    """The ONLY inputs the Module 4 specialist may receive.

    These are the shared, independent inputs: the 10-K evidence snippets and the
    structured financial data (debt schedule facts, cash-flow facts, etc.). No
    Module 1/2/3 output is included -- Module 4 is independent.
    """

    sec_filing_snippets: list[dict] = field(default_factory=list)
    structured_financials: list[dict] = field(default_factory=list)
    debt_schedule_facts: list[dict] = field(default_factory=list)
    missing_critical_evidence: list[str] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "sec_filing_snippets": self.sec_filing_snippets,
            "structured_financials": self.structured_financials,
            "debt_schedule_facts": self.debt_schedule_facts,
            "missing_critical_evidence": self.missing_critical_evidence,
        }

    def evidence_ids(self) -> list[str]:
        ids: list[str] = []
        for item in (
            *self.sec_filing_snippets,
            *self.structured_financials,
            *self.debt_schedule_facts,
        ):
            fid = item.get("fact_id") or item.get("id") or item.get("source_id")
            if fid:
                ids.append(str(fid))
        return ids


@dataclass
class Module4Result:
    """A completed Module 4 run: parsed output, validator report, provenance."""

    output: Module4Output
    validator8: DebtStructureValidatorReport
    run: ModelRunResult
    escalation_id: str | None = None

    @property
    def validated(self) -> bool:
        """True when Validator 8 passed every deterministic check."""
        return self.validator8.passed


class Module4Specialist:
    """Run the Module 4 specialist agent + Validator 8."""

    def __init__(
        self,
        client: LLMClient,
        *,
        validator: DebtStructureValidator | None = None,
        escalation_engine: EscalationEngine | None = None,
        case_id: str | None = None,
    ) -> None:
        self._client = client
        self._validator = validator or DebtStructureValidator()
        self._escalation = escalation_engine
        self._case_id = case_id

    def run(
        self,
        inputs: Module4Inputs,
        *,
        case_version: int | None = None,
        key: str | None = None,
    ) -> Module4Result:
        """Produce schema-validated Module 4 output + a Validator 8 report.

        Raises :class:`Module4RejectedError` if the LLM output fails schema
        validation (rejection is logged by the client). A Validator 8 failure
        does NOT raise -- it is reported on the result and raises a mandatory
        escalation so a human reviews it.
        """
        run = self._client.analyze(
            inputs.to_payload(),
            evidence_ids=inputs.evidence_ids(),
            case_id=self._case_id,
            case_version=case_version,
            prompt_name=MODULE4_AGENT_NAME,
            key=key,
        )
        if not run.is_valid or run.parsed is None:
            raise Module4RejectedError(
                "Module 4 LLM output failed schema validation and was rejected "
                "before use."
            )

        output = Module4Output.model_validate(run.parsed)
        report = self._validator.validate(output)
        escalation_id = self._handle_validator_failure(report)
        return Module4Result(
            output=output,
            validator8=report,
            run=run,
            escalation_id=escalation_id,
        )

    def _handle_validator_failure(
        self, report: DebtStructureValidatorReport
    ) -> str | None:
        """Raise a mandatory escalation when Validator 8 fails (explicit rule id)."""
        if report.passed or self._escalation is None:
            return None
        reason = "Validator 8 failed: " + "; ".join(
            f"[{c.check_id}] {c.detail}" for c in report.failures
        )
        escalation = self._escalation.raise_escalation(
            rule_id=RULE_VALIDATOR8_FAILURE,
            category=EscalationCategory.DATA_INTEGRITY,
            severity=Severity.MANDATORY,
            reason=reason,
            evidence_refs=[c.check_id for c in report.failures],
            case_id=self._case_id,
        )
        return escalation.escalation_id
