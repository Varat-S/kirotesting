"""Deterministic Module 4 output assembler (``m4_analysis.json``).

Module 4 has TWO independent agents (neither reads the other; both read only the
admitted source evidence):

* **Agent B — Debt Structure Specialist** (``app/services/modules/module4``):
  one schema-validated LLM call that produces the full four-deliverable
  :class:`~app.schemas.module4.Module4Output` (D1 debt & creditor priority,
  D2 refinancing & maturity, D3 debt utilisation & leverage impact, D4 credit
  conclusion). Governed by the deterministic ``DebtStructureValidator``.

* **Agent A — Strategy & Capital Allocation** (roster-v3 ``m4_strategy_capital``):
  governed hypotheses on acquisition strategy, integration outcomes and capex
  strategy (qualitative *merit*). It is explicitly walled off from leverage /
  debt-service conclusions (those are Financial Overview's).

This module is the **deterministic assembly layer** (no LLM, no new agent). It
takes agent B's validated four deliverables as the spine and FOLDS agent A's
governed strategy findings into the Deliverable 4 credit conclusion, so the final
``m4_analysis.json`` reflects BOTH agents without either depending on the other.

Design rules honoured:

* **No fabrication.** Agent A findings are only folded when actually present and
  usable; when A did not run (e.g. the standalone debt-only path), B's four
  deliverables pass through UNCHANGED with an explicit note that no strategy
  supplement was present. Nothing is invented to fill the gap.
* **No new judgement.** The assembler never writes a new credit opinion; it
  appends A's already-governed strategy findings as additional synthesis-matrix
  rows and a strategy-context block, preserving each finding's direction,
  materiality and evidence ids. B's D4 verdict is not overwritten.
* **Independence preserved.** A and B each read source evidence; the join happens
  here, deterministically, exactly like the cross-module synthesizer joins the
  modules one level up.
"""

from __future__ import annotations

from typing import Any

from app.schemas.module4 import Module4Output

M4_ANALYSIS_VERSION = "bo-m4-analysis-1.0"
M4_ANALYSIS_ARTIFACT_KIND = "m4_analysis"

# Agent A hypotheses at or above this materiality are surfaced as their own
# synthesis-matrix rows; lower-materiality strategy findings are still carried in
# the strategy_context block but do not clutter the headline matrix.
_MATRIX_MATERIALITY = {"high", "medium"}


def _strategy_rows(strategy_findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn agent A's governed strategy findings into D4 synthesis-matrix rows.

    Purely mechanical: it reformats already-governed findings into the matrix row
    shape. It does not re-judge them or change any disposition.
    """
    rows: list[dict[str, Any]] = []
    for f in strategy_findings:
        if f.get("materiality") not in _MATRIX_MATERIALITY:
            continue
        claim = (f.get("claim") or "").strip()
        mechanism = (f.get("credit_mechanism") or "").strip()
        evidence = f.get("supporting_evidence_ids") or []
        rows.append({
            "analytical_scope_pillar": "Strategy & Capital Allocation (Agent A)",
            "core_empirical_evidence": claim + (f" [{len(evidence)} cited]" if evidence else ""),
            "deliverable_verdict": f.get("direction") or "neutral",
            "bank_lender_implications": mechanism or (
                "Strategy/capital-allocation observation; leverage and debt-service "
                "implications are assessed by the debt specialist (Agent B) and "
                "Financial Overview, not here."),
        })
    return rows


def assemble_m4_output(
    debt_output: Module4Output | dict[str, Any] | None,
    *,
    strategy_findings: list[dict[str, Any]] | None = None,
    debt_status: str = "validated",
    debt_validated: bool = True,
    strategy_status: str = "not_run",
) -> dict[str, Any]:
    """Assemble the governed Module 4 analysis from both agents (deterministic).

    Parameters
    ----------
    debt_output:
        Agent B's four-deliverable output (a :class:`Module4Output` or its
        ``model_dump``). ``None`` when the debt specialist produced no usable
        output (unavailable / rejected / failed) — the four deliverables are then
        absent and the analysis records that honestly.
    strategy_findings:
        Agent A's governed, usable strategy hypotheses (``hypothesis_view`` dicts)
        from ``module_conclusions["M4"]["findings"]``. ``None`` / empty when
        agent A did not run or produced nothing usable.
    debt_status / debt_validated / strategy_status:
        Governance status carried through for traceability.

    Returns the ``m4_analysis`` dict (the content of ``m4_analysis.json``).
    """
    findings = list(strategy_findings or [])
    deliverables = (
        debt_output.model_dump(mode="json")
        if isinstance(debt_output, Module4Output)
        else (debt_output or None)
    )

    # Contributor provenance — who supplied what, never implied.
    contributors = {
        "agent_b_debt_structure": {
            "agent_id": "module4_debt_structure_specialist",
            "produces": "deliverables_1_to_4",
            "status": debt_status,
            "validated": bool(debt_validated),
        },
        "agent_a_strategy_capital": {
            "agent_id": "m4_strategy_capital",
            "produces": "strategy_hypotheses",
            "status": strategy_status,
            "usable_findings": len(findings),
        },
    }

    analysis: dict[str, Any] = {
        "schema_version": M4_ANALYSIS_VERSION,
        "artifact_kind": M4_ANALYSIS_ARTIFACT_KIND,
        "module": "M4",
        "contributors": contributors,
        "requires_human_review": True,
        "forced_outcome": False,
        "note": (
            "Deterministically assembled from the two independent Module 4 agents; "
            "no new credit judgement is introduced at this layer. Draft analytical "
            "work for human review."),
    }

    if deliverables is None:
        # Agent B produced no usable four-deliverable output. Do not fabricate.
        analysis["status"] = "incomplete"
        analysis["deliverable_1"] = None
        analysis["deliverable_2"] = None
        analysis["deliverable_3"] = None
        analysis["deliverable_4"] = None
        analysis["strategy_context"] = findings
        analysis["missing_information"] = [
            "Agent B (debt specialist) produced no usable four-deliverable output "
            f"(status: {debt_status}); the Module 4 credit conclusion cannot be "
            "assembled on the admitted evidence."]
        return analysis

    # Agent B's four deliverables are the spine, passed through unchanged.
    analysis["deliverable_1"] = deliverables["deliverable_1"]
    analysis["deliverable_2"] = deliverables["deliverable_2"]
    analysis["deliverable_3"] = deliverables["deliverable_3"]
    d4 = dict(deliverables["deliverable_4"])

    # Fold agent A's governed strategy findings into D4, additively.
    strategy_rows = _strategy_rows(findings)
    if strategy_rows:
        d4 = dict(d4)
        d4["synthesis_matrix"] = [*d4.get("synthesis_matrix", []), *strategy_rows]
        d4["strategy_capital_supplement"] = {
            "source_agent": "m4_strategy_capital",
            "contributes": "strategy / capital-allocation merit findings "
                           "(acquisition, integration, capex strategy)",
            "note": "Supplements the debt specialist's conclusion; does NOT draw "
                    "leverage or debt-service conclusions (those are the debt "
                    "specialist's and Financial Overview's).",
            "findings": findings,
        }
    else:
        d4 = dict(d4)
        d4["strategy_capital_supplement"] = {
            "source_agent": "m4_strategy_capital",
            "contributes": "none",
            "note": ("No usable strategy/capital-allocation findings were "
                     "available from Agent A for this run; the Module 4 credit "
                     "conclusion rests on the debt specialist's deliverables only. "
                     "This is reported, not silently treated as neutral."),
            "findings": [],
        }

    analysis["deliverable_4"] = d4
    analysis["strategy_context"] = findings
    analysis["status"] = "complete" if debt_validated else "incomplete"
    if not debt_validated:
        analysis["missing_information"] = [
            f"Agent B debt deliverables did not fully validate (status: {debt_status}); "
            "Module 4 requires human review before use."]
    return analysis
