"""Deterministic BO + replaceable FO integration; never a final credit decision."""
from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from app.core.hashing import canonical_json, content_hash
from app.schemas.credit_memo import (
    CrossDomainFinding, FinancialCalculation, FinancialOverview,
    PartialCreditAssessment, StructuralItem,
)
from app.schemas.enums import FactStatus
from app.schemas.structural_overview import DownstreamSection
from app.services.metrics.definitions import DEFAULT_METRIC_DEFINITIONS, ResolvedMetricDefinition
from app.services.metrics.engine import MetricEngine, MetricInput
from app.services.reporting.memo import PdfBackendUnavailableError, _render_pdf_with, detect_pdf_backend
from app.services.structural_overview.adapter import adapt_business_overview, assessment_content_hash


FORMULAS = {
    "revenue_growth": "(revenue - revenue_prior) / revenue_prior",
    "operating_margin": "operating_income / revenue",
    "net_debt_to_ebitda": "(total_debt - unrestricted_cash) / ebitda",
    "interest_coverage": "ebitda / interest_expense",
    "free_cash_flow": "cfo - capex",
    "liquidity": "unrestricted_cash + undrawn_revolver",
}
DISCLOSURE = "INCOMPLETE - includes simulated financial information. Prototype only; no final credit rating or approval."


def calculate_financials(packet: FinancialOverview) -> list[FinancialCalculation]:
    """Reuse the existing numerical authority without admitting fixture values as evidence."""
    result = []
    engine = MetricEngine()
    prior = None
    for period in packet.periods:
        facts = dict(period.facts)
        refs = {name: f"{packet.source_run_id}:FY{period.fiscal_year}:{name}" for name in facts}
        if prior is not None:
            facts["revenue_prior"] = prior.facts["revenue"]
            refs["revenue_prior"] = f"{packet.source_run_id}:FY{prior.fiscal_year}:revenue"
        for metric, formula in FORMULAS.items():
            if metric == "revenue_growth" and prior is None:
                continue
            components = DEFAULT_METRIC_DEFINITIONS[metric]
            definition = ResolvedMetricDefinition(
                metric_definition_id=metric, version=1, formula_id=components["formula_id"],
                components=components, content_hash=content_hash(components), label="Illustrative integration",
            )
            inputs = {name: MetricInput(name, facts[name].value, FactStatus.UNVERIFIED, refs[name])
                      for name in definition.inputs}
            computed = engine.compute(definition, inputs, period=f"FY{period.fiscal_year}")
            result.append(FinancialCalculation(
                metric_id=metric, period=f"FY{period.fiscal_year}", value=computed.result,
                state=computed.state.value, units="USD million" if computed.units == "currency" else computed.units,
                formula_id=computed.formula_id, formula=formula,
                input_values={k: v.value for k, v in inputs.items()},
                input_refs=[refs[k] for k in definition.inputs], provenance=packet.provenance,
                engine_version=computed.engine_version, definition_version=computed.metric_definition_version,
            ))
        prior = period
    return result


def build_partial_assessment(bo_payload: dict, fo_payload: dict) -> PartialCreditAssessment:
    bo = adapt_business_overview(bo_payload)
    fo = FinancialOverview.model_validate(fo_payload)
    if (fo.case_id != bo.borrower.case_id
            or fo.borrower_name != bo.borrower.reporting_entity_name):
        raise ValueError("BO/FO borrower or case identity mismatch")
    if bo.borrower.as_of_date and str(fo.periods[-1].fiscal_year) != bo.borrower.as_of_date[:4]:
        raise ValueError("FO latest fiscal year must match the BO as-of year")
    synthetic = fo.provenance == "synthetic_fixture"
    calculations = calculate_financials(fo)
    findings = [f for key in ("business_model_and_revenue_drivers", "industry_and_competitive_risks",
                              "management_and_operational_findings", "strategy_and_capital_allocation_findings")
                for f in getattr(bo.business_assessment, key)]
    # Link the actual compound risk, never assume arbitrary BO contains the HCA finding.
    matched = [f for f in findings if f.provenance.source_section == "M1A6"
               and "margin" in f.claim.lower() and "labor" in f.claim.lower()]
    cross = []
    if matched:
        cross.append(CrossDomainFinding(
            finding_id="BO-FO-001", simulation_dependent=synthetic,
            text="BO identifies labor-cost pressure and limited pricing flexibility. The " + ("illustrative " if synthetic else "supplied ") + "FO margin and expense trends demonstrate how this risk could be monitored. Potential operating-margin pressure remains unverified in magnitude and future effect.",
            bo_hypothesis_ids=[f.provenance.source_hypothesis_id for f in matched],
            financial_metric_refs=[f"{c.period}:{c.metric_id}" for c in calculations if c.metric_id == "operating_margin"],
            evidence_ids=sorted({e for f in matched for e in f.supporting_evidence_ids}),
            limitations=("Simulation-dependent relationship. Synthetic FO does not increase BO certainty. " if synthetic else "Supplied FO inputs require independent source and governance verification. ") + "No independent confirmation or causal attribution; original BO qualifications and human review remain in force.",
        ))
    borrower_identified = bo.borrower.identification_status == "identified"
    relationships = [StructuralItem(
        topic="Borrower legal identity",
        status="partial" if borrower_identified else "unavailable",
        description=(
            f"{bo.borrower.legal_name} ({bo.borrower.entity_id}); governed M3 identification. "
            "SO legal verification remains outstanding."
            if borrower_identified else
            (bo.borrower.identification_limitation
             or "Transaction borrower is unavailable; SO must establish the obligor.")
        ),
        source_refs=[bo.source_handoff_id],
        provenance="bo_analytical_finding" if borrower_identified else "unavailable",
    )]
    entities = bo.corporate_structure.legal_entities
    ownership = bo.corporate_structure.ownership_relationships
    cash_locations = bo.corporate_structure.cash_flow_locations
    relationships.extend([
        StructuralItem(
            topic="Material legal entities",
            status="partial" if entities else "unavailable",
            description=(
                "; ".join(
                    f"{entity.legal_name} ({entity.classification}; {entity.verification_status})"
                    for entity in entities
                ) if entities else "No validator-admitted legal entities are available."
            ),
            source_refs=[entity.entity_id for entity in entities],
            provenance="bo_analytical_finding" if entities else "unavailable",
        ),
        StructuralItem(
            topic="Parent/subsidiary and ownership relationships",
            status="partial" if ownership else "unavailable",
            description=(
                "; ".join(
                    f"{edge.parent_entity_id} -> {edge.subsidiary_entity_id} ({edge.confirmation_status})"
                    for edge in ownership
                ) if ownership else "No validator-admitted ownership edges are available."
            ),
            source_refs=[edge.relationship_id for edge in ownership],
            provenance="bo_analytical_finding" if ownership else "unavailable",
        ),
        StructuralItem(
            topic="Cash-flow locations",
            status="partial" if cash_locations else "unavailable",
            description=(
                "; ".join(
                    f"{item.entity_id}: {item.measure_type} ({item.reporting_scope}; {item.provenance_status})"
                    for item in cash_locations
                ) if cash_locations else "No validator-admitted cash-flow locations are available."
            ),
            source_refs=[item.cash_flow_id for item in cash_locations],
            provenance="bo_analytical_finding" if cash_locations else "unavailable",
        ),
    ])
    for topic in ("Debt composition and existing obligations", "Maturity profile",
                  "Guarantees and collateral", "Facility terms and covenants"):
        relationships.append(StructuralItem(topic=topic, status="unavailable",
            description="Unavailable in the integrated contracts; documentary verification required.", provenance="unavailable"))
    current = fo.periods[-1]
    relationships.append(StructuralItem(
        topic="Liquidity and financing sources", status="partial",
        description=f"{'Illustrative' if synthetic else 'FO-supplied'} unrestricted cash {current.facts['unrestricted_cash'].value:g} and undrawn revolver {current.facts['undrawn_revolver'].value:g} USD million. Availability and legal access unverified.",
        source_refs=[f"{fo.source_run_id}:FY{current.fiscal_year}:{k}" for k in ("unrestricted_cash", "undrawn_revolver")],
        provenance=fo.provenance,
    ))
    gaps = {
        "full_structural_overview": [r.topic for r in relationships if r.status == "unavailable"],
        "facility_underwriting": ["Obligor, facility amount, ranking, security, guarantees, covenants and repayment terms"],
        "credit_orchestrator": ["Accepted real FO conclusion and score in a common analysis run", "Accepted Structuring conclusion and required obligor/facility scores", "Cross-topic challenge and reconciliation"],
        "final_scoring": ["Verify admitted financial evidence and accepted FO analysis", "Complete structural and facility analysis"],
        "human_approval": ["Resolve or explicitly accept BO challenges and qualifications", "Named reviewer approval and recorded review history"],
    }
    sections = dict(bo.downstream_dependencies.sections)
    sections["financial_overview"] = DownstreamSection(status="partial", reason="Synthetic fixture only; real financial analysis unavailable." if synthetic else "FO packet supplied; source admission and governance must be independently verified.", source_ids=[fo.source_run_id])
    sections["structural_overview"] = DownstreamSection(
        status="partial" if bo.corporate_structure.source_status == "validated" else "unavailable",
        reason=(
            "Governed M3 corporate structure is present; obligor, ranking, restrictions, and facility terms still require SO verification."
            if bo.corporate_structure.source_status == "validated" else
            "No governed M3 corporate structure is available; legal and facility analysis remain unavailable."
        ),
        source_ids=[bo.source_handoff_id]
        if bo.corporate_structure.source_status == "validated" else [],
    )
    sections["facility_underwriting"] = DownstreamSection(status="blocked", reason=gaps["facility_underwriting"][0])
    sections["human_approval"] = DownstreamSection(status="blocked", reason="BO review pending; full credit assessment incomplete.")
    data = bo.model_dump(mode="json")
    data.update(schema_version=bo.schema_version, financial_overview=fo.model_dump(mode="json"),
                financial_calculations=[c.model_dump(mode="json") for c in calculations],
                source_fo_content_hash=content_hash(fo.model_dump(mode="json")),
                structural_relationships=[r.model_dump(mode="json") for r in relationships],
                cross_domain_findings=[c.model_dump(mode="json") for c in cross],
                missing_dependencies=gaps, disclosure=DISCLOSURE if synthetic else "INCOMPLETE - FO inputs supplied; source admission and human review outstanding. No final credit rating or approval.")
    data["downstream_dependencies"]["sections"] = {k: s.model_dump(mode="json") for k, s in sections.items()}
    data["audit_metadata"]["transformation_version"] = "bo-fo-to-so-1.0"
    data["assessment_id"] = "soa_" + content_hash({"bo": bo.content_hash, "fo": data["source_fo_content_hash"], "transform": "bo-fo-to-so-1.0"})[:16]
    data["content_hash"] = assessment_content_hash(data)
    return PartialCreditAssessment.model_validate(data)


def validate_assessment(payload: dict | PartialCreditAssessment) -> PartialCreditAssessment:
    model = PartialCreditAssessment.model_validate(payload.model_dump(mode="json") if isinstance(payload, PartialCreditAssessment) else payload)
    if assessment_content_hash(model) != model.content_hash:
        raise ValueError("Assessment content hash is invalid")
    if content_hash(model.financial_overview.model_dump(mode="json")) != model.source_fo_content_hash:
        raise ValueError("FO content hash is invalid")
    if model.financial_calculations != calculate_financials(model.financial_overview):
        raise ValueError("Financial calculations do not match source inputs")
    return model


def render_partial_html(payload: dict | PartialCreditAssessment) -> str:
    model = validate_assessment(payload)
    env = Environment(loader=FileSystemLoader(str(Path(__file__).resolve().parents[1] / "reporting" / "templates")),
                      autoescape=True, undefined=StrictUndefined, keep_trailing_newline=True)
    labels = {ref.evidence_id: f"E{i:02d}" for i, ref in enumerate(model.evidence_references, 1)}
    return env.get_template("credit_memo_partial.html.j2").render(a=model.model_dump(mode="json"), labels=labels)


def write_partial_artifacts(payload: dict | PartialCreditAssessment, output_dir: str | Path) -> tuple[Path, Path, Path]:
    model = validate_assessment(payload)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    json_path, html_path, pdf_path = [output / f"credit_memo_partial.{ext}" for ext in ("json", "html", "pdf")]
    json_path.write_text(canonical_json(model.model_dump(mode="json")) + "\n", encoding="utf-8")
    html = render_partial_html(json.loads(json_path.read_text(encoding="utf-8")))
    html_path.write_text(html, encoding="utf-8")
    backend = detect_pdf_backend()
    if backend is None:
        raise PdfBackendUnavailableError("PDF required: install the existing [pdf] extra and Pango; on Windows set WEASYPRINT_DLL_DIRECTORIES.")
    pdf_path.write_bytes(_render_pdf_with(backend, html))
    return json_path, html_path, pdf_path
