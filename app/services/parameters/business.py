"""Business deterministic parameter definitions (Milestone 7).

Non-financial-metric Business calculations (Req 7.2): segment/geographic/
customer concentration + HHI, Top-N customer concentration, seasonality, revenue
volatility, KPI growth, management tenure/turnover, acquisition frequency/spend
ratios, goodwill growth, and capex ratios. Each maps to a versioned deterministic
formula; the LLM only supplies the structured inputs, never the arithmetic.
"""

from __future__ import annotations

from app.schemas.agentic import Topic
from app.services.parameters.registry import ParameterDefinition

_B = Topic.BUSINESS


def business_parameter_definitions() -> list[ParameterDefinition]:
    return [
        # Concentration / HHI
        ParameterDefinition("segment_hhi", _B, "hhi", "index",
                            description="Segment revenue concentration (HHI)."),
        ParameterDefinition("geographic_hhi", _B, "hhi", "index",
                            description="Geographic revenue concentration (HHI)."),
        ParameterDefinition("customer_hhi", _B, "hhi", "index",
                            description="Customer concentration (HHI)."),
        ParameterDefinition("top1_customer_concentration", _B, "top_n_concentration",
                            "ratio", description="Top-1 customer share of revenue."),
        ParameterDefinition("top5_customer_concentration", _B, "top_n_concentration",
                            "ratio", description="Top-5 customer share of revenue."),
        ParameterDefinition("top10_customer_concentration", _B, "top_n_concentration",
                            "ratio", description="Top-10 customer share of revenue."),
        ParameterDefinition("supplier_hhi", _B, "hhi", "index",
                            description="Supplier concentration (HHI)."),
        # Stability / seasonality
        ParameterDefinition("revenue_volatility", _B, "volatility", "ratio",
                            description="Coefficient of variation of revenue."),
        ParameterDefinition("seasonality_index", _B, "seasonality", "index",
                            description="Peak/mean seasonality index."),
        ParameterDefinition("kpi_growth", _B, "growth_rate", "ratio",
                            description="Operating KPI growth (e.g. load factor)."),
        # Management / governance
        ParameterDefinition("management_tenure", _B, "average", "number",
                            units="years",
                            description="Average management tenure (years)."),
        ParameterDefinition("management_turnover", _B, "turnover_rate", "ratio",
                            description="Management turnover rate over the window."),
        # M&A / capex / execution
        ParameterDefinition("acquisition_frequency", _B, "count_rate", "number",
                            units="per_year",
                            description="Acquisitions per year."),
        ParameterDefinition("acquisition_spend_to_ebitda", _B, "safe_ratio", "ratio",
                            description="Acquisition spend / EBITDA."),
        ParameterDefinition("acquisition_spend_to_fcf", _B, "safe_ratio", "ratio",
                            description="Acquisition spend / FCF."),
        ParameterDefinition("goodwill_growth", _B, "growth_rate", "ratio",
                            description="Goodwill growth period-over-period."),
        ParameterDefinition("capex_to_revenue", _B, "safe_ratio", "ratio",
                            description="Capex / revenue."),
        ParameterDefinition("capex_to_ebitda", _B, "safe_ratio", "ratio",
                            description="Capex / EBITDA."),
        ParameterDefinition("capex_to_dep_amort", _B, "safe_ratio", "ratio",
                            description="Capex / D&A (maintenance vs growth proxy)."),
        ParameterDefinition("price_volume_split", _B, "price_volume_decomposition",
                            "ratio", description="Price share of revenue change."),
    ]
