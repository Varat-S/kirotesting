"""HCA Healthcare FY2025 10-K fixture for Module 4 tests.

A reduced-but-faithful Module 4 output built from the HCA Healthcare, Inc. FY2025
Form 10-K (Item 8 Note 8 Debt, Item 7 MD&A, Item 15 Exhibit Index 4.x). It is
designed so Validator 8 passes cleanly:

* The itemized tranches sum to the reported consolidated total indebtedness of
  $46,492M once the $(436)M debt issuance costs / discounts adjustment is
  included as an explicit non-note reconciling line.
* Legal borrower (HCA Inc., the OpCo issuer) is kept distinct from the guarantor
  (HCA Healthcare, Inc., the HoldCo parent).
* Undrawn revolver headroom ($8,000M limit, $0 drawn) is reported but NOT folded
  into the total.

All figures are expressed in US$ millions.
"""

from __future__ import annotations

# Representative senior unsecured notes (HCA Inc. issuer, HCA Healthcare, Inc.
# guarantor). The schedule aggregates the note population to the Note 8 total of
# $43,700M ($43,250M notes + $450M debentures) while listing representative
# tranches in chronological maturity order.
_SENIOR_NOTES = [
    {
        "senior_note_series": "5.000% Senior Notes",
        "legal_borrower": "HCA Inc.",
        "principal_amount_m": 700.0,
        "coupon_interest_rate": "5.000% fixed",
        "maturity_year": 2028,
        "collateral_security_status": "unsecured",
        "guarantors_credit_support": (
            "Fully and unconditionally guaranteed on an unsecured basis by "
            "HCA Healthcare, Inc."
        ),
        "priority_ranking_tier": "tier_2_senior_unsecured_guaranteed",
    },
    {
        "senior_note_series": "5.250% Senior Notes",
        "legal_borrower": "HCA Inc.",
        "principal_amount_m": 750.0,
        "coupon_interest_rate": "5.250% fixed",
        "maturity_year": 2030,
        "collateral_security_status": "unsecured",
        "guarantors_credit_support": (
            "Fully and unconditionally guaranteed on an unsecured basis by "
            "HCA Healthcare, Inc."
        ),
        "priority_ranking_tier": "tier_2_senior_unsecured_guaranteed",
    },
    {
        "senior_note_series": "5.750% Senior Notes",
        "legal_borrower": "HCA Inc.",
        "principal_amount_m": 1500.0,
        "coupon_interest_rate": "5.750% fixed",
        "maturity_year": 2035,
        "collateral_security_status": "unsecured",
        "guarantors_credit_support": (
            "Fully and unconditionally guaranteed on an unsecured basis by "
            "HCA Healthcare, Inc."
        ),
        "priority_ranking_tier": "tier_2_senior_unsecured_guaranteed",
    },
    {
        "senior_note_series": "6.200% Senior Notes",
        "legal_borrower": "HCA Inc.",
        "principal_amount_m": 1250.0,
        "coupon_interest_rate": "6.200% fixed",
        "maturity_year": 2055,
        "collateral_security_status": "unsecured",
        "guarantors_credit_support": (
            "Fully and unconditionally guaranteed on an unsecured basis by "
            "HCA Healthcare, Inc."
        ),
        "priority_ranking_tier": "tier_2_senior_unsecured_guaranteed",
    },
    {
        # Residual note population to reconcile to the Note 8 total of $43,700M.
        "senior_note_series": "Other senior unsecured notes (2026-2064)",
        "legal_borrower": "HCA Inc.",
        "principal_amount_m": 39050.0,
        "coupon_interest_rate": "various fixed, 5.1% effective",
        "maturity_year": 2064,
        "collateral_security_status": "unsecured",
        "guarantors_credit_support": (
            "Fully and unconditionally guaranteed on an unsecured basis by "
            "HCA Healthcare, Inc."
        ),
        "priority_ranking_tier": "tier_2_senior_unsecured_guaranteed",
    },
    {
        "senior_note_series": "Debentures (maturities through 2095)",
        "legal_borrower": "HCA Inc.",
        "principal_amount_m": 450.0,
        "coupon_interest_rate": "7.19% weighted average",
        "maturity_year": 2095,
        "collateral_security_status": "unsecured",
        "guarantors_credit_support": (
            "Fully and unconditionally guaranteed on an unsecured basis by "
            "HCA Healthcare, Inc."
        ),
        "priority_ranking_tier": "tier_2_senior_unsecured_guaranteed",
    },
]

# Non-note obligations. Commercial paper + other senior secured debt are DRAWN;
# the revolver has $0 drawn against an $8,000M limit (undrawn headroom reported
# separately). The debt issuance cost / discount is an explicit reconciling line.
_NON_NOTE = [
    {
        "obligation_facility": "Commercial paper program",
        "legal_borrower": "HCA Inc.",
        "principal_drawn_m": 2207.0,
        "available_facility_limit_m": 4000.0,
        "interest_rate_terms": "4.3% weighted average; 18-day average life",
        "maturity": "Short-term",
        "collateral_security_status": "unsecured",
        "guarantors_credit_support": "HCA Healthcare, Inc. guarantee",
        "priority_ranking_tier": "tier_2_senior_unsecured_guaranteed",
    },
    {
        "obligation_facility": "Other senior secured debt (finance leases + other)",
        "legal_borrower": "HCA Inc.",
        "principal_drawn_m": 1021.0,
        "available_facility_limit_m": None,
        "interest_rate_terms": "4.6% effective",
        "maturity": "Various",
        "collateral_security_status": "secured",
        "guarantors_credit_support": "Secured at operating subsidiary level",
        "priority_ranking_tier": "tier_1_direct_opco_secured",
    },
    {
        "obligation_facility": "Senior unsecured revolving credit facility (5-year)",
        "legal_borrower": "HCA Inc.",
        "principal_drawn_m": 0.0,
        "available_facility_limit_m": 8000.0,
        "interest_rate_terms": "SOFR + 1.125%",
        "maturity": "2030",
        "collateral_security_status": "unsecured",
        "guarantors_credit_support": "HCA Healthcare, Inc. guarantee",
        "priority_ranking_tier": "tier_2_senior_unsecured_guaranteed",
    },
    {
        "obligation_facility": "Debt issuance costs and discounts",
        "legal_borrower": "HCA Inc.",
        "principal_drawn_m": -436.0,
        "available_facility_limit_m": None,
        "interest_rate_terms": "n/a (contra-liability)",
        "maturity": "n/a",
        "collateral_security_status": "unsecured",
        "guarantors_credit_support": "n/a",
        "priority_ranking_tier": "tier_2_senior_unsecured_guaranteed",
    },
]

# Tier totals for the subordination framework. Tier 1 direct OpCo secured is the
# $1,021M other senior secured debt. Consolidated total indebtedness = $46,492M.
_TIER_1 = 1021.0
_CONSOLIDATED_TOTAL = 46492.0
_TIER_3 = 0.0  # all senior notes carry the HoldCo guarantee -> none unguaranteed
_TIER_2 = _CONSOLIDATED_TOTAL - _TIER_1 - _TIER_3


def hca_module4_output() -> dict:
    """A complete, Validator-8-clean Module 4 output dict for HCA FY2025."""
    return {
        "deliverable_1": {
            "extraction_source": {
                "narrative": (
                    "Debt population drawn from Item 8 Financial Statements, "
                    "Note 8 (Long-Term Debt), cross-referenced to Item 7 MD&A "
                    "Liquidity & Capital Resources and the Item 15 Exhibit Index "
                    "supplemental indentures (Exhibits 4.44-4.53)."
                ),
                "item_references": ["Item 7 MD&A", "Item 8 Note 8", "Item 15 Exhibit 4.x"],
                "page_references": ["F-18", "F-19", "57"],
            },
            "senior_notes_schedule": _SENIOR_NOTES,
            "non_note_obligations_schedule": _NON_NOTE,
            "creditor_priority_framework": {
                "tier_1_direct_opco_secured_m": _TIER_1,
                "tier_2_senior_unsecured_guaranteed_m": _TIER_2,
                "tier_3_unguaranteed_holdco_m": _TIER_3,
                "consolidated_total_indebtedness_m": _CONSOLIDATED_TOTAL,
                "structural_subordination_drag_ratio": round(
                    _TIER_1 / _CONSOLIDATED_TOTAL, 4
                ),
                "prospective_loan_secured_placement": (
                    "A secured bank loan would rank in Tier 1 ahead of the "
                    "$43.7B senior unsecured notes, benefiting from direct OpCo "
                    "collateral."
                ),
                "prospective_loan_senior_unsecured_placement": (
                    "A senior unsecured bank loan would rank pari passu with the "
                    "existing Tier 2 senior unsecured notes, sharing the HCA "
                    "Healthcare, Inc. upstream guarantee."
                ),
            },
        },
        "deliverable_2": {
            "extraction_source": {
                "narrative": (
                    "Refinancing activity reconstructed from Note 8 issuance and "
                    "repayment disclosures and the MD&A Capital Resources "
                    "discussion for FY2023-FY2025."
                ),
                "item_references": ["Item 7 MD&A", "Item 8 Note 8"],
                "page_references": ["F-18", "55"],
            },
            "refinancing_log": [
                {
                    "transaction_category": "new_issuance",
                    "obligation_series_description": "February 2025 senior notes ($5.25B)",
                    "principal_amount_m": 5250.0,
                    "coupon_interest_terms": "5.000%-6.200% fixed + FRN",
                    "execution_date_fiscal_period": "Feb 2025",
                    "original_retired_maturity": None,
                    "new_extended_maturity": "2028-2055",
                    "use_of_proceeds_impact": (
                        "Repay senior unsecured credit facility + general "
                        "corporate purposes."
                    ),
                },
                {
                    "transaction_category": "new_issuance",
                    "obligation_series_description": "October 2025 senior notes ($3.25B)",
                    "principal_amount_m": 3250.0,
                    "coupon_interest_terms": "4.300%-5.700% fixed",
                    "execution_date_fiscal_period": "Oct 2025",
                    "original_retired_maturity": None,
                    "new_extended_maturity": "2030-2055",
                    "use_of_proceeds_impact": "Repay commercial paper + GCP.",
                },
                {
                    "transaction_category": "repayment_at_maturity",
                    "obligation_series_description": "5.375% senior notes",
                    "principal_amount_m": 2600.0,
                    "coupon_interest_terms": "5.375% fixed",
                    "execution_date_fiscal_period": "FY2025",
                    "original_retired_maturity": "2025",
                    "new_extended_maturity": None,
                    "use_of_proceeds_impact": "Repaid at scheduled maturity.",
                },
                {
                    "transaction_category": "early_redemption",
                    "obligation_series_description": "5.875% senior notes due 2026",
                    "principal_amount_m": 1500.0,
                    "coupon_interest_terms": "5.875% fixed",
                    "execution_date_fiscal_period": "FY2025",
                    "original_retired_maturity": "2026",
                    "new_extended_maturity": None,
                    "use_of_proceeds_impact": "Redeemed early ahead of maturity.",
                },
            ],
            "metrics": {
                "weighted_avg_refinancing_lead_time_months": 11.0,
                "refinancing_cost_spread_delta_bps": 25.0,
                "weighted_avg_maturity_extension_years": 7.5,
                "historical_refinancing_distress_events": 0,
            },
            "refinancing_capacity_discipline_verdict": (
                "Deep, repeated capital-markets access with proactive pre-maturity "
                "refinancing and zero distress events demonstrates strong "
                "refinancing capacity and discipline."
            ),
        },
        "deliverable_3": {
            "extraction_source": {
                "narrative": (
                    "Capital deployment reconciled from the Consolidated "
                    "Statements of Cash Flows and MD&A Capital Resources, FY2023-"
                    "FY2025."
                ),
                "item_references": ["Item 7 MD&A", "Item 8 Cash Flow Statements"],
                "page_references": ["F-7", "54"],
            },
            "capital_deployment_reconciliation": [
                {
                    "capital_allocation_stream": "Capital expenditures",
                    "specific_use_description": "Facility expansion, beds, technology",
                    "cumulative_amount_3y_m": 14563.0,
                    "primary_funding_source": "Operating cash flow",
                    "leverage_impact": (
                        "Self-funded from operating cash flow; leverage-neutral."
                    ),
                },
                {
                    "capital_allocation_stream": "Acquisitions",
                    "specific_use_description": "Bolt-on hospital/ASC acquisitions",
                    "cumulative_amount_3y_m": 1298.0,
                    "primary_funding_source": "Operating cash flow",
                    "leverage_impact": "Self-funded; immaterial effect on net debt.",
                },
                {
                    "capital_allocation_stream": "Share repurchases",
                    "specific_use_description": "Buybacks under board authorizations",
                    "cumulative_amount_3y_m": 19920.0,
                    "primary_funding_source": "Operating cash flow + debt",
                    "leverage_impact": (
                        "Partly debt-funded; added to net debt but leverage kept "
                        "within policy."
                    ),
                },
                {
                    "capital_allocation_stream": "Dividends",
                    "specific_use_description": "Quarterly common dividends",
                    "cumulative_amount_3y_m": 2030.0,
                    "primary_funding_source": "Operating cash flow",
                    "leverage_impact": "Self-funded from OCF; leverage-neutral.",
                },
            ],
            "debt_utilisation_matrix": {
                "debt_funded_vs_internally_funded_deployment": (
                    "CAPEX, M&A and dividends self-funded by $32.6B of 3-year "
                    "operating cash flow; only part of the $19.9B of buybacks was "
                    "debt-funded, so most deployment did not add to net debt."
                ),
                "shareholder_distributions_vs_leverage_balance": (
                    "Aggressive buybacks ($19.9B/3y) partly debt-funded, but net "
                    "debt / EBITDA kept within policy while EBITDA grew."
                ),
            },
            "debt_utilisation_leverage_verdict": (
                "Capital deployment is predominantly internally funded; the "
                "debt-funded portion of distributions added to net debt but "
                "leverage remained within a controlled policy range."
            ),
        },
        "deliverable_4": {
            "evidence_synthesis_narrative": (
                "Deliverables 1-3 show a predominantly senior-unsecured capital "
                "structure guaranteed by the HoldCo, a disciplined and deep "
                "refinancing track record, and internally funded growth alongside "
                "large but controlled shareholder distributions."
            ),
            "core_credit_assessment": (
                "HCA presents a strong, well-managed debt structure with proven "
                "refinancing access and prudent stewardship; a prospective bank "
                "lender ranks pari passu with existing senior unsecured notes "
                "absent collateral."
            ),
            "synthesis_matrix": [
                {
                    "analytical_scope_pillar": "Debt structure & priority",
                    "core_empirical_evidence": (
                        "$46.49B total debt; 97.8% senior unsecured guaranteed; "
                        "drag ratio 0.022."
                    ),
                    "deliverable_verdict": "Satisfactory",
                    "bank_lender_implications": (
                        "Unsecured loan is pari passu Tier 2; collateral would "
                        "lift to Tier 1."
                    ),
                },
                {
                    "analytical_scope_pillar": "Refinancing history",
                    "core_empirical_evidence": (
                        "$8.5B issued FY2025; proactive pre-maturity refinancing; "
                        "0 distress events."
                    ),
                    "deliverable_verdict": "Strong",
                    "bank_lender_implications": "Low refinancing/rollover risk.",
                },
                {
                    "analytical_scope_pillar": "Financial stewardship",
                    "core_empirical_evidence": (
                        "CAPEX self-funded; $19.9B buybacks partly debt-funded; "
                        "leverage within policy."
                    ),
                    "deliverable_verdict": "Satisfactory",
                    "bank_lender_implications": (
                        "Monitor distribution appetite vs leverage headroom."
                    ),
                },
            ],
            "executive_credit_summary_box": {
                "target_entity_and_period": "HCA Healthcare, Inc. - FY2025 (10-K)",
                "overall_credit_rating_verdict": "satisfactory",
                "executive_takeaways": [
                    "Predominantly senior unsecured structure guaranteed by the HoldCo.",
                    "Proven, disciplined refinancing access with no distress events.",
                    "Internally funded growth; distributions large but controlled.",
                ],
                "recommended_loan_placement": "senior_unsecured_tier_2",
            },
        },
    }
