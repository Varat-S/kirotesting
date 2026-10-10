"""Sector-benchmarking deterministic parameter definitions.

Financial ratios needed to compare a borrower with industry aggregates that are
NOT already produced by the ``MetricEngine`` (reused through the metric adapter)
or by an existing parameter (``dso`` / ``dio`` / ``dpo`` /
``cash_conversion_cycle`` / ``revenue_cagr`` / ``dscr`` are reused as-is).

Every ratio here uses ``positive_denominator_ratio``: a missing input stays
missing, a near-zero denominator needs review and a negative denominator (e.g.
negative EBITDA) is not meaningful. None of these parameter ids appears in the
scoring configuration, so none of them can move an official risk score.
"""

from __future__ import annotations

from app.schemas.agentic import Topic
from app.services.parameters.registry import ParameterDefinition

_F = Topic.FINANCIAL
_RATIO = "positive_denominator_ratio"


def sector_parameter_definitions() -> list[ParameterDefinition]:
    return [
        ParameterDefinition("gross_debt_to_ebitda", _F, _RATIO, "ratio", units="x",
                            description="Total (gross) debt / EBITDA."),
        ParameterDefinition("ebit_interest_coverage", _F, _RATIO, "ratio", units="x",
                            description="Operating income (EBIT) / interest expense."),
        ParameterDefinition("ebitda_margin", _F, _RATIO, "ratio",
                            description="EBITDA / revenue."),
        ParameterDefinition("gross_margin", _F, _RATIO, "ratio",
                            description="Gross profit / revenue."),
        ParameterDefinition("fcf_to_revenue", _F, _RATIO, "ratio",
                            description="(CFO - CapEx) / revenue."),
        ParameterDefinition("rnd_to_revenue", _F, _RATIO, "ratio",
                            description="R&D expense / revenue."),
        ParameterDefinition("cash_tax_burden", _F, _RATIO, "ratio",
                            description="Income taxes paid / pre-tax income."),
        ParameterDefinition("goodwill_intangibles_to_total_assets", _F, _RATIO, "ratio",
                            description="(Goodwill + other intangibles) / total assets."),
        ParameterDefinition("acquisitions_to_cfo", _F, _RATIO, "ratio",
                            description="Acquisition spend / operating cash flow."),
    ]
