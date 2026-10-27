"""Replaceable FO boundary and partial Structural Overview v1.1; v1.0 is unchanged."""
from __future__ import annotations

from typing import Literal
from pydantic import Field, model_validator

from app.schemas.structural_overview import StrictModel, StructuralOverviewAssessment


class FinancialValue(StrictModel):
    value: float = Field(allow_inf_nan=False)
    units: Literal["USD million"]
    provenance: Literal["synthetic_fixture", "validated_fo"]
    evidence_ids: list[str] = Field(default_factory=list)


class FinancialPeriod(StrictModel):
    fiscal_year: int = Field(ge=1900, le=2200)
    facts: dict[str, FinancialValue]

    @model_validator(mode="after")
    def consistent(self):
        required = {"revenue", "operating_income", "operating_expenses", "ebitda",
                    "total_debt", "unrestricted_cash", "undrawn_revolver", "cfo", "capex", "interest_expense"}
        if set(self.facts) != required:
            raise ValueError(f"Expected financial facts: {sorted(required)}")
        f = {k: v.value for k, v in self.facts.items()}
        if abs(f["revenue"] - f["operating_income"] - f["operating_expenses"]) > 0.000001:
            raise ValueError("Revenue must equal operating income plus operating expenses")
        if any(f[k] < 0 for k in ("revenue", "operating_expenses", "total_debt", "unrestricted_cash", "undrawn_revolver", "capex", "interest_expense")):
            raise ValueError("Financial balances/expenses must use positive-value convention")
        return self


class FinancialObservation(StrictModel):
    finding_id: str
    kind: Literal["strength", "vulnerability"]
    text: str
    provenance: Literal["synthetic_fixture", "validated_fo"]
    metric_refs: list[str]


class FinancialOverview(StrictModel):
    schema_version: Literal["fo-adapter-1.0"]
    source_run_id: str
    case_id: str
    borrower_name: str
    provenance: Literal["synthetic_fixture", "validated_fo"]
    notice: str
    periods: list[FinancialPeriod] = Field(min_length=2, max_length=2)
    findings: list[FinancialObservation]
    assumptions: list[str] = Field(min_length=1)
    missing_information: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sources(self):
        if self.periods[1].fiscal_year != self.periods[0].fiscal_year + 1:
            raise ValueError("Expected consecutive fiscal years in ascending order")
        for period in self.periods:
            for fact in period.facts.values():
                if fact.provenance != self.provenance:
                    raise ValueError("Mixed provenance within FO packet")
                if self.provenance == "validated_fo" and not fact.evidence_ids:
                    raise ValueError("Real FO requires admitted evidence references")
                if self.provenance == "synthetic_fixture" and fact.evidence_ids:
                    raise ValueError("Synthetic figures must not claim admitted evidence")
        allowed = set(self.periods[0].facts) | {"operating_margin", "revenue_growth", "net_debt_to_ebitda", "interest_coverage", "free_cash_flow", "liquidity"}
        for finding in self.findings:
            if finding.provenance != self.provenance or not finding.metric_refs or set(finding.metric_refs) - allowed:
                raise ValueError("Invalid FO finding provenance or metric references")
        if len({f.finding_id for f in self.findings}) != len(self.findings):
            raise ValueError("Duplicate FO finding IDs")
        return self


class FinancialCalculation(StrictModel):
    metric_id: str
    period: str
    value: float | None
    state: str
    units: str
    formula_id: str
    formula: str
    input_values: dict[str, float]
    input_refs: list[str]
    provenance: Literal["synthetic_fixture", "validated_fo"]
    information_type: Literal["derived_calculation"] = "derived_calculation"
    engine_version: str
    definition_version: int


class StructuralItem(StrictModel):
    topic: str
    status: Literal["partial", "unavailable"]
    description: str
    source_refs: list[str] = Field(default_factory=list)
    provenance: Literal["bo_analytical_finding", "synthetic_fixture", "validated_fo", "unavailable"]


class CrossDomainFinding(StrictModel):
    finding_id: str
    text: str
    simulation_dependent: bool
    certainty: Literal["unverified"] = "unverified"
    bo_hypothesis_ids: list[str]
    financial_metric_refs: list[str]
    evidence_ids: list[str]
    limitations: str


class PartialCreditAssessment(StructuralOverviewAssessment):
    schema_version: Literal["structural-overview-assessment-1.2"] = "structural-overview-assessment-1.2"
    status: Literal["partial"] = "partial"
    final_credit_score: None = None
    facility_approval: None = None
    financial_overview: FinancialOverview
    financial_calculations: list[FinancialCalculation]
    source_fo_content_hash: str
    structural_relationships: list[StructuralItem]
    cross_domain_findings: list[CrossDomainFinding]
    missing_dependencies: dict[str, list[str]]
    disclosure: str

    @model_validator(mode="after")
    def validate_integration(self):
        synthetic = self.financial_overview.provenance == "synthetic_fixture"
        if "INCOMPLETE" not in self.disclosure or (synthetic and "simulated financial information" not in self.disclosure):
            raise ValueError("Incomplete/synthetic disclosure is required")
        if (self.financial_overview.case_id != self.borrower.case_id
                or self.financial_overview.borrower_name
                != self.borrower.reporting_entity_name):
            raise ValueError("BO/FO identity mismatch")
        metric_refs = {f"{c.period}:{c.metric_id}" for c in self.financial_calculations}
        findings = [f for group in (self.business_assessment.business_model_and_revenue_drivers,
                    self.business_assessment.industry_and_competitive_risks,
                    self.business_assessment.management_and_operational_findings,
                    self.business_assessment.strategy_and_capital_allocation_findings) for f in group]
        hypotheses = {f.provenance.source_hypothesis_id for f in findings}
        evidence = {e.evidence_id for e in self.evidence_references}
        for cross in self.cross_domain_findings:
            if cross.simulation_dependent != synthetic:
                raise ValueError("Cross-domain simulation dependency must match FO provenance")
            if (not cross.bo_hypothesis_ids or not cross.financial_metric_refs
                    or set(cross.bo_hypothesis_ids) - hypotheses
                    or set(cross.financial_metric_refs) - metric_refs
                    or set(cross.evidence_ids) - evidence):
                raise ValueError("Unresolved cross-domain references")
        return self
