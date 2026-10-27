"""Orchestrated Module 4 debt-structure specialist (within the BO run).

This wires Ernest's independent :class:`~app.services.modules.module4.specialist.Module4Specialist`
into the normal Business Overview execution path, alongside the roster-v3
``m4_strategy_capital`` agent, so that **both** Module 4 capabilities contribute
to one governed Module 4 output.

Design constraints honoured:

* **Independence contract.** The debt specialist receives ONLY admitted source
  evidence from the canonical snapshot (SEC narrative passages + structured
  financial facts). It is NOT given Modules 1/2/3 outputs. Verified M3 entity
  relationships may be supplied *read-only* through a narrow, opt-in channel; we
  pass them as context-labelled snippets only when explicitly provided and never
  as fact — the specialist's own ``legal_entity`` Validator-8 check still
  arbitrates borrower-vs-guarantor attribution.
* **Shared infra, no duplicate model calls.** It runs through the run's existing
  ``LLMClient`` and ``EscalationEngine``; it is invoked once per BO run.
* **Governed, never silently successful.** Schema rejection, a Validator-8
  failure, or missing essential evidence each produce an explicit non-validated
  status ( ``rejected`` / ``failed`` / ``unavailable`` ), and a Validator-8
  failure raises the specialist's mandatory escalation (``R-MODULE4-VALIDATOR8-01``)
  which drives the case to ``escalated`` and forces human review downstream.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from app.services.modules.module4.specialist import (
    Module4Inputs,
    Module4RejectedError,
    Module4Specialist,
)

M4_DEBT_ASSESSMENT_VERSION = "bo-m4-debt-assessment-1.0"
M4_DEBT_AGENT_ID = "module4_debt_structure_specialist"
M4_DEBT_VALIDATOR_ID = "validator_8_debt_structure"

# Lexical cues used to select ADMITTED debt-relevant passages/facts. This is
# evidence SELECTION over already-admitted material, not fabrication: nothing is
# created, and the specialist's own deterministic checks still govern the result.
# Cues are deliberately debt-SPECIFIC (multi-word or unambiguous) so generic
# business passages (e.g. "outpatient facilities") do not trigger a debt run.
_DEBT_CUES = (
    "long-term debt", "total debt", "total indebtedness", "indebtedness",
    "senior notes", "senior unsecured", "senior secured", "revolving credit",
    "credit facility", "credit agreement", "debt maturit", "maturities",
    "refinanc", "term loan", "notes due", "principal amount", "debt tranche",
    "guarantor", "guaranteed by", "structural subordination", "debt covenant",
    "interest expense", "finance lease",
)
_DEBT_FACT_CUES = (
    "totalindebtedness", "total indebtedness", "longtermdebt", "long-term debt",
    "seniornotes", "senior notes", "debtmaturit", "debt maturit",
    "revolvingcredit", "revolving credit", "termloan", "term loan",
    "financelease", "finance lease", "interestexpense", "interest expense",
    "principal",
)


@dataclass
class M4DebtResult:
    """The governed outcome of the orchestrated debt specialist."""

    status: str                       # validated | qualified | rejected | failed | unavailable | error
    assessment: Optional[dict[str, Any]] = None       # parsed Module4Output (json)
    validator_report: Optional[dict[str, Any]] = None  # DebtStructureValidatorReport (json)
    validated: bool = False
    escalation_id: Optional[str] = None
    evidence_ids: list[str] = None                    # type: ignore[assignment]
    reason: Optional[str] = None
    run_id: Optional[str] = None
    model_id: Optional[str] = None
    model_invoked: Optional[bool] = None
    validator_issues: list[str] = None                # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.evidence_ids is None:
            self.evidence_ids = []
        if self.validator_issues is None:
            self.validator_issues = []

    def to_module_conclusion(self) -> dict[str, Any]:
        """The block attached to ``module_conclusions['M4']['m4_debt_assessment']``."""
        return {
            "version": M4_DEBT_ASSESSMENT_VERSION,
            "agent_id": M4_DEBT_AGENT_ID,
            "validator_id": M4_DEBT_VALIDATOR_ID,
            "status": self.status,
            "validated": self.validated,
            "assessment": self.assessment,
            "validator_report": self.validator_report,
            "validator_issues": self.validator_issues,
            "escalation_id": self.escalation_id,
            "evidence_ids": self.evidence_ids,
            "reason": self.reason,
            "model_invoked": self.model_invoked,
        }


def _contains_cue(text: str, cues: tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return any(cue in low for cue in cues)


def build_debt_inputs(
    evidence,
    *,
    verified_m3_relationships: Optional[list[dict]] = None,
) -> Module4Inputs:
    """Assemble :class:`Module4Inputs` from ADMITTED snapshot evidence only.

    Selects debt-relevant narrative passages and structured financial facts. If
    no debt-relevant material is admitted, ``missing_critical_evidence`` records
    that so the result is reported ``unavailable`` rather than guessed.
    """
    snippets: list[dict] = []
    for passage in getattr(evidence, "narrative_evidence", []) or []:
        if not isinstance(passage, dict):
            continue
        eid = passage.get("evidence_id")
        if not eid:
            continue
        text = passage.get("text") or ""
        if _contains_cue(text, _DEBT_CUES):
            snippets.append({
                "fact_id": eid,
                "text": text,
                "document_id": passage.get("document_id"),
                "section": passage.get("section"),
                "page": passage.get("page"),
            })

    structured: list[dict] = []
    debt_facts: list[dict] = []
    for fact in getattr(evidence, "facts", []) or []:
        if not isinstance(fact, dict):
            continue
        fid = fact.get("fact_id") or fact.get("id")
        if not fid:
            continue
        concept = str(fact.get("concept") or fact.get("name") or fact.get("metric") or "")
        label = str(fact.get("label") or "")
        blob = f"{concept} {label}"
        if _contains_cue(blob, _DEBT_FACT_CUES):
            entry = {"fact_id": fid, "concept": concept, "value": fact.get("value"),
                     "unit": fact.get("unit"), "period": fact.get("period")}
            debt_facts.append(entry)
            structured.append(entry)

    missing: list[str] = []
    if not snippets:
        missing.append("No admitted SEC narrative passage references debt structure.")
    if not structured:
        missing.append("No admitted structured financial fact references debt.")

    # Verified M3 relationships are passed read-only and clearly context-labelled,
    # never as debt facts. Supplied only when the caller explicitly opts in.
    if verified_m3_relationships:
        for rel in verified_m3_relationships:
            if isinstance(rel, dict):
                structured.append({
                    "fact_id": rel.get("fact_id") or rel.get("relationship_id")
                    or "m3_verified_relationship",
                    "context": "verified_m3_entity_relationship",
                    **{k: v for k, v in rel.items() if k not in {"fact_id"}},
                })

    return Module4Inputs(
        sec_filing_snippets=snippets,
        structured_financials=structured,
        debt_schedule_facts=debt_facts,
        missing_critical_evidence=missing,
    )


def run_debt_specialist(
    client,
    evidence,
    *,
    case_id: str,
    snapshot_version: int,
    escalation_engine=None,
    verified_m3_relationships: Optional[list[dict]] = None,
    key: str = "m4_debt",
) -> M4DebtResult:
    """Run the orchestrated debt specialist and return a governed result.

    Never raises on a governance outcome: schema rejection -> ``rejected``;
    Validator-8 failure -> ``failed`` (+ mandatory escalation); missing essential
    evidence -> ``unavailable``. A clean run is ``validated``.
    """
    inputs = build_debt_inputs(
        evidence, verified_m3_relationships=verified_m3_relationships)

    # Fail CLOSED when no admitted debt evidence exists: report unavailable,
    # do not invent a successful analysis or call the model on empty inputs.
    if inputs.missing_critical_evidence and not inputs.sec_filing_snippets \
            and not inputs.structured_financials:
        return M4DebtResult(
            status="unavailable",
            evidence_ids=[],
            reason="; ".join(inputs.missing_critical_evidence),
            model_invoked=False,
        )

    specialist = Module4Specialist(
        client, escalation_engine=escalation_engine, case_id=case_id)
    try:
        result = specialist.run(inputs, case_version=snapshot_version, key=key)
    except Module4RejectedError as exc:
        return M4DebtResult(
            status="rejected",
            evidence_ids=inputs.evidence_ids(),
            reason=str(exc),
            model_invoked=True,
        )
    except Exception as exc:  # a provider/call failure fails this specialist, not the whole BO
        return M4DebtResult(
            status="error",
            evidence_ids=inputs.evidence_ids(),
            reason=type(exc).__name__,
            model_invoked=True,
        )

    # DebtStructureValidatorReport / ...Check are frozen dataclasses -> asdict.
    report_json = {
        "passed": result.validator8.passed,
        "checks": [asdict(c) for c in result.validator8.checks],
    }
    issues = [f"[{c.check_id}] {c.detail}" for c in result.validator8.failures]
    status = "validated" if result.validated else "failed"
    return M4DebtResult(
        status=status,
        assessment=result.output.model_dump(mode="json"),
        validator_report=report_json,
        validated=result.validated,
        escalation_id=result.escalation_id,
        evidence_ids=inputs.evidence_ids(),
        run_id=getattr(result.run, "run_id", None),
        model_id=getattr(result.run, "model_id", None),
        model_invoked=True,
        validator_issues=issues,
    )
