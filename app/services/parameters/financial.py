"""Financial deterministic parameter definitions (Milestone 7).

Financial calculations (Req 7.3). Core financial-statement ratios that the
existing ``MetricEngine`` already computes (revenue growth, margins, net debt,
leverage, interest coverage, cash conversion, FCF, liquidity, capex/revenue) are
REUSED via the metric adapter (see :mod:`app.services.parameters.metric_adapter`)
rather than reimplemented. This module adds the NON-metric-engine financial
parameters: working-capital days (DSO/DIO/DPO), the cash-conversion cycle,
maturity-profile ratios, DSCR, covenant headroom, rate/FX/commodity
sensitivities, downside stress and debt capacity.
"""

from __future__ import annotations

from app.schemas.agentic import Topic
from app.services.parameters.registry import ParameterDefinition

_F = Topic.FINANCIAL


def financial_parameter_definitions() -> list[ParameterDefinition]:
    return [
        # Growth / trend (CAGR + slope not in the metric engine)
        ParameterDefinition("revenue_cagr", _F, "cagr", "ratio",
                            description="Revenue CAGR over the window."),
        ParameterDefinition("margin_trend", _F, "trend_direction", "number",
                            description="Operating-margin trend slope."),
        # Cash conversion / working capital
        ParameterDefinition("fcf_conversion", _F, "safe_ratio", "ratio",
                            description="FCF / EBITDA conversion."),
        ParameterDefinition("dso", _F, "days_outstanding", "number", units="days",
                            description="Days sales outstanding."),
        ParameterDefinition("dio", _F, "days_outstanding", "number", units="days",
                            description="Days inventory outstanding."),
        ParameterDefinition("dpo", _F, "days_outstanding", "number", units="days",
                            description="Days payables outstanding."),
        ParameterDefinition("cash_conversion_cycle", _F, "cash_conversion_cycle",
                            "number", units="days",
                            description="DSO + DIO - DPO."),
        # Coverage / leverage (DSCR here; leverage/coverage reused from metrics)
        ParameterDefinition("dscr", _F, "coverage_ratio", "ratio",
                            description="Debt service coverage ratio (CFADS/service)."),
        ParameterDefinition("downside_dscr", _F, "coverage_ratio", "ratio",
                            description="DSCR under the downside scenario."),
        # Maturity profile
        ParameterDefinition("maturity_to_ebitda", _F, "safe_ratio", "ratio",
                            description="Upcoming maturities / EBITDA."),
        ParameterDefinition("maturity_to_fcf", _F, "safe_ratio", "ratio",
                            description="Upcoming maturities / FCF."),
        ParameterDefinition("maturity_to_liquidity", _F, "safe_ratio", "ratio",
                            description="Upcoming maturities / available liquidity."),
        # Covenant headroom
        ParameterDefinition("covenant_headroom", _F, "covenant_headroom", "ratio",
                            description="Headroom vs the binding covenant limit."),
        # Sensitivities (where structured inputs exist)
        ParameterDefinition("rate_sensitivity", _F, "sensitivity", "number",
                            description="Earnings sensitivity to a rate shock."),
        ParameterDefinition("fx_sensitivity", _F, "sensitivity", "number",
                            description="Earnings sensitivity to an FX shock."),
        ParameterDefinition("commodity_sensitivity", _F, "sensitivity", "number",
                            description="Earnings sensitivity to a commodity shock."),
        # Stress / capacity
        ParameterDefinition("downside_ebitda", _F, "downside_stress", "number",
                            description="Base-case EBITDA under a downside shock."),
        ParameterDefinition("debt_capacity", _F, "max_feasible_facility", "number",
                            description="Additional debt capacity within policy."),
    ]
