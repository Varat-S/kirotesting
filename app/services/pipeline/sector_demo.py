"""A scripted, offline stand-in for the Financial Orchestrator (NOT a model).

``ScriptedBenchmarkBackend`` is a deterministic test double used by the test
suite and the offline pilot. It extends the safe-empty agentic offline backend
with ONE behaviour: when the Financial Orchestrator's input carries a
``benchmark_context``, it writes a fixed-template interpretation that quotes
only the parameter-result ids found in that context.

It exists to exercise the real path end to end without a network call — the
bounded input contract, quote-by-``parameter_result_id`` validation, provenance
checks and promotion. It says nothing about how a real model would reason, and
its sentences are templates, not analysis. Live-provider behaviour remains an
optional, separate validation step.
"""

from __future__ import annotations

import json

from app.services.llm.client import FakeLLMBackend, LLMRawResult, LLMRequest

SCRIPTED_MODEL_ID = "scripted-benchmark-double-v1"
_PRIORITY = ("net_debt_to_ebitda", "ebit_interest_coverage", "cfo_to_ebitda",
             "operating_margin", "cash_conversion_cycle")
_LABELS = {
    "net_debt_to_ebitda": "net debt/EBITDA",
    "gross_debt_to_ebitda": "gross debt/EBITDA",
    "ebit_interest_coverage": "EBIT interest coverage",
    "ebitda_interest_coverage": "EBITDA interest coverage",
    "cfo_to_ebitda": "CFO/EBITDA",
    "operating_margin": "operating margin",
    "ebitda_margin": "EBITDA margin",
    "gross_margin": "gross margin",
    "cash_conversion_cycle": "cash conversion cycle",
}
_POSITION = {
    "stronger_than_industry_aggregate": "stronger than",
    "weaker_than_industry_aggregate": "weaker than",
    "in_line_with_industry_aggregate": "in line with",
}


def _fmt(value: float, units: str | None) -> tuple[str, float]:
    """Display text plus the exact rounded number shown."""
    if units == "ratio":
        shown = round(value * 100.0, 1)
        return f"{shown}%", shown
    if units == "days":
        shown = round(value, 1)
        return f"{shown} days", shown
    shown = round(value, 2)
    return f"{shown}x", shown


def scripted_financial_conclusion(inputs: dict) -> dict:
    """Build a template TopicConclusion from a ``benchmark_context``."""
    context = inputs["benchmark_context"]
    industry = context["industry_label"]
    usable = {
        c["comparison_id"]: c for c in context["comparisons"]
        if c["comparison_state"] in ("comparable", "comparable_with_caveats")
    }
    ordered = [usable[k] for k in _PRIORITY if k in usable] or list(usable.values())[:3]
    claims = {"strengths": [], "weaknesses": [], "material_risks": []}
    tensions = []
    for comparison in ordered:
        cid = comparison["comparison_id"]
        label = _LABELS.get(cid, cid.replace("_", " "))
        borrower, aggregate = comparison["borrower"], comparison["industry_aggregate"]
        b_text, _ = _fmt(borrower["value"], borrower["units"])
        a_text, _ = _fmt(aggregate["value"], aggregate["units"])
        position = _POSITION.get(comparison["position_vs_aggregate"], "not assessed against")
        history = comparison["own_history"]["direction"]
        years = comparison["own_history"]["observed_fiscal_years"]
        span = f"FY{years[0]}-FY{years[-1]}" if len(years) > 1 else "the available history"
        text = (
            f"The borrower's {label} of {b_text} is {position} the {industry} "
            f"industry aggregate of {a_text}; over {span} the borrower's own "
            f"{label} trend is {history}. The comparison is contextual rather than "
            "evidence of default risk."
        )
        claim = {
            "claim_id": f"bench_{cid}",
            "category": "strength" if position == "stronger than" else "weakness",
            "text": text,
            "parameter_result_ids": [borrower["parameter_result_id"],
                                     aggregate["parameter_result_id"]],
            "evidence_ids": [aggregate["reference_id"]],
            "materiality": "medium",
            "uncertainty": "Industry aggregate, not a peer distribution; "
                           + "; ".join(comparison["caveats"][:2]),
            "quoted_values": [
                {"parameter_result_id": borrower["parameter_result_id"],
                 "value": borrower["value"]},
                {"parameter_result_id": aggregate["parameter_result_id"],
                 "value": aggregate["value"]},
            ],
        }
        bucket = "strengths" if claim["category"] == "strength" else "weaknesses"
        claims[bucket].append(claim)
        if position == "stronger than" and history == "deteriorating":
            tensions.append(
                f"{label}: favourable versus the industry aggregate but "
                "deteriorating versus the borrower's own history."
            )
    first = ordered[0] if ordered else None
    assessment = {
        "claim_id": "bench_overall",
        "category": "assessment",
        "text": (
            f"Position versus the {industry} industry aggregate is contextual. "
            "Where a favourable comparison coincides with a deteriorating own "
            "trend, the trend warrants further review before any reliance on the "
            "comparison."
        ),
        "parameter_result_ids": (
            [first["borrower"]["parameter_result_id"]] if first else []),
        "evidence_ids": [first["industry_aggregate"]["reference_id"]] if first else [],
        "materiality": "medium",
        "uncertainty": "Aggregate-versus-company comparison; "
                       + context["standing_limitations"][0],
    }
    skipped = sorted(
        c["comparison_id"] for c in context["comparisons"]
        if c["comparison_state"] in ("not_comparable", "unavailable")
    )
    return {
        "overall_assessment": assessment,
        **claims,
        "key_drivers": [],
        "open_questions": (
            ["Not compared with the industry (not comparable or unavailable): "
             + ", ".join(skipped) + "."] if skipped else []),
        "unresolved_contradictions": tensions,
    }


class ScriptedBenchmarkBackend(FakeLLMBackend):
    """Offline fake backend that also answers benchmark-context requests."""

    def __init__(self) -> None:
        super().__init__(model_id=SCRIPTED_MODEL_ID)
        # Imported here: agentic_runner imports nothing from this module.
        from app.services.pipeline.agentic_runner import agentic_offline_backend

        self._scripts = dict(agentic_offline_backend()._scripts)  # noqa: SLF001

    def generate(self, request: LLMRequest) -> LLMRawResult:
        if request.method == "orchestrate" and "benchmark_context" in request.inputs:
            return LLMRawResult(
                raw_response=json.dumps(
                    scripted_financial_conclusion(request.inputs), sort_keys=True),
                model_id=self._model_id,
                model_config={"temperature": request.temperature, "backend": "scripted"},
            )
        return super().generate(request)
