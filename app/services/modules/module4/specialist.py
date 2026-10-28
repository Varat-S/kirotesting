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
   and attach the report. When its reconciliation leaves a gap, ask the model
   ONCE to explain the gap from the filing passages it was given; each item it
   returns counts only if its quote is verbatim in those passages (check 6).
   When the validator still fails, a MANDATORY escalation is raised (explicit
   rule id) so the failure is a real, queryable signal rather than silent prose.

This service never drafts term sheets, covenants or security agreements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas.module4 import Module4Output, ReconciliationFollowUp
from app.services.escalation.engine import EscalationEngine
from app.services.escalation.rules import EscalationCategory, Severity
from app.services.llm.client import LLMClient, ModelRunResult
from app.services.modules.module4.prompt import MODULE4_AGENT_NAME, MODULE4_RECONCILIATION_NAME
from app.services.modules.module4.debt_structure_validator import (
    CHECK_RECONCILIATION,
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

    def filing_passages(self) -> dict[str, str]:
        """The filing text reconciling items are checked against (evidence id -> text)."""
        return {str(s["fact_id"]): s["text"] for s in self.sec_filing_snippets
                if s.get("fact_id") and s.get("text")}

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
    # The reconciliation follow-up, when one was made (see Module4Specialist._reconcile).
    reconciliation_followup: dict[str, Any] | None = None

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
        enforce_validator: bool = True,
    ) -> None:
        self._client = client
        self._validator = validator or DebtStructureValidator()
        self._escalation = escalation_engine
        self._case_id = case_id
        # False (advisory): Validator 8 still reports, but its failure neither
        # triggers the reconciliation follow-up nor raises an escalation.
        self._enforce = enforce_validator

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

        output = _in_maturity_order(Module4Output.model_validate(run.parsed))
        passages = inputs.filing_passages()
        report = self._validator.validate(output, filing_passages=passages)
        followup = None
        if self._enforce and not report.check(CHECK_RECONCILIATION).passed and passages:
            output, report, followup = self._reconcile(
                inputs, output, report, case_version=case_version, key=key)
        escalation_id = self._handle_validator_failure(report) if self._enforce else None
        return Module4Result(
            output=output,
            validator8=report,
            run=run,
            escalation_id=escalation_id,
            reconciliation_followup=followup,
        )

    def _reconcile(self, inputs: Module4Inputs, output: Module4Output,
                   report: DebtStructureValidatorReport, *, case_version, key):
        """One follow-up asking the model to explain the reconciliation gap from the filing.

        Only the reconciling items it returns are taken (the schedule is never
        changed), and Validator 8 counts each one only if its quote is verbatim in
        the filing passages and states its amount. Any failure of the call keeps
        the first answer and its report as they were.
        """
        observed = report.check(CHECK_RECONCILIATION).observed
        d1 = output.deliverable_1
        payload = {
            "reported_total_m": observed["reported_total_m"],
            "tranche_sum_m": observed["tranche_sum_m"] + observed["reconciling_items_m"],
            "gap_m": observed["gap_m"],
            "debt_schedule": {
                "senior_notes_schedule": [t.model_dump(mode="json") for t in d1.senior_notes_schedule],
                "non_note_obligations_schedule": [
                    o.model_dump(mode="json") for o in d1.non_note_obligations_schedule],
                "reconciling_items": [i.model_dump(mode="json") for i in d1.reconciling_items],
            },
            "sec_filing_snippets": inputs.sec_filing_snippets,
        }
        record: dict[str, Any] = {"gap_m": observed["gap_m"]}
        try:
            run = self._client.analyze(
                payload, evidence_ids=inputs.evidence_ids(), case_id=self._case_id,
                case_version=case_version, prompt_name=MODULE4_RECONCILIATION_NAME,
                key=f"{key or 'm4_debt'}_reconcile")
        except Exception as exc:  # a provider failure leaves the first answer standing
            record.update(status="error", reason=type(exc).__name__)
            return output, report, record
        record["run_id"] = getattr(run, "run_id", None)
        if not run.is_valid or run.parsed is None:
            record.update(status="rejected", reason="The answer failed schema validation.")
            return output, report, record
        answer = ReconciliationFollowUp.model_validate(run.parsed)
        d1 = d1.model_copy(update={"reconciling_items": [*d1.reconciling_items,
                                                         *answer.reconciling_items]})
        output = output.model_copy(update={"deliverable_1": d1})
        report = self._validator.validate(output, filing_passages=inputs.filing_passages())
        after = report.check(CHECK_RECONCILIATION)
        record.update(status="answered", proposed_items=len(answer.reconciling_items),
                      unexplained=answer.unexplained, reconciled=after.passed,
                      gap_after_m=after.observed["gap_m"])
        return output, report, record

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


def _in_maturity_order(output: Module4Output) -> Module4Output:
    """List the senior notes by maturity year (stable), as Validator 8's check 3 expects.

    The order is presentation only: the model sometimes lists tranches as the
    filing's table does, which is not always chronological.
    """
    d1 = output.deliverable_1
    ordered = sorted(d1.senior_notes_schedule, key=lambda t: t.maturity_year)
    if ordered == d1.senior_notes_schedule:
        return output
    return output.model_copy(update={
        "deliverable_1": d1.model_copy(update={"senior_notes_schedule": ordered})})
