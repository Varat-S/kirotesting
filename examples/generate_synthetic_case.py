"""Generate labelled synthetic XLSX/XBRL/PDF/CSV sources, never real Delta data.

Run with development extras installed: python examples/generate_synthetic_case.py
"""

import csv
import json
from pathlib import Path

import openpyxl
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle


def generate(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    years = [2022, 2023, 2024]
    labels = [
        "Operating Revenue",
        "Operating Income",
        "Total Debt",
        "Unrestricted Cash",
        "EBITDA",
        "Interest Expense",
        "Operating Cash Flow",
        "Capital Expenditures",
        "Undrawn Revolver",
        "Operating Expense",
    ]
    values = {
        "Operating Revenue": [800, 900, 1000],
        "Operating Income": [160, 180, 200],
        "Total Debt": [500, 600, 700],
        "Unrestricted Cash": [100, 100, 100],
        "EBITDA": [200, 200, 200],
        "Interest Expense": [40, 40, 40],
        "Operating Cash Flow": [150, 160, 170],
        "Capital Expenditures": [60, 70, 80],
        "Undrawn Revolver": [100, 100, 100],
        "Operating Expense": [640, 720, 800],
    }
    rows = [["Line item", *map(str, years)]] + [
        [label, *values[label]] for label in labels
    ]
    book = openpyxl.Workbook()
    for row in rows:
        book.active.append(row)
    book.save(root / "financials.xlsx")
    concept_names = [
        "us-gaap:Revenues",
        "us-gaap:OperatingIncomeLoss",
        "poc:TotalDebt",
        "poc:UnrestrictedCash",
        "poc:EBITDA",
        "poc:InterestExpense",
        "poc:OperatingCashFlow",
        "poc:CapitalExpenditures",
        "poc:UndrawnRevolver",
    ]
    xml = [
        '<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:us-gaap="http://fasb.org/us-gaap/2023" xmlns:poc="http://example.com/poc">'
    ]
    for year in years:
        xml.append(
            f'<xbrli:context id="FY{year}"><xbrli:entity><xbrli:identifier scheme="synthetic">DAL</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>{year}-01-01</xbrli:startDate><xbrli:endDate>{year}-12-31</xbrli:endDate></xbrli:period></xbrli:context>'
        )
    xml.append('<xbrli:unit id="USD"><xbrli:measure>USD</xbrli:measure></xbrli:unit>')
    for label, concept in zip(labels, concept_names):
        for index, year in enumerate(years):
            xml.append(
                f'<{concept} contextRef="FY{year}" unitRef="USD" scale="6">{values[label][index]}</{concept}>'
            )
    xml.append("</xbrli:xbrl>")
    (root / "filing.xbrl").write_text("\n".join(xml), encoding="utf-8")
    table = Table(
        [
            ["Line item", "2024"],
            ["Operating Revenue", "1000"],
            ["Operating Income", "200"],
        ]
    )
    table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 1, colors.black)]))
    SimpleDocTemplate(str(root / "financials.pdf")).build([table])

    def csv_file(name, rows):
        with (root / name).open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerows(rows)

    csv_file(
        "operations.csv",
        [
            ["Line item", "2024"],
            ["Revenue Passenger Miles", 800],
            ["Available Seat Miles", 1000],
        ],
    )
    csv_file(
        "industry.csv",
        [
            ["Line item", "2024"],
            ["Operating Revenue", 900],
            ["Operating Income", 90],
            ["Total Debt", 300],
            ["Unrestricted Cash", 100],
            ["EBITDA", 200],
        ],
    )
    csv_file("fuel.csv", [["Line item", "2024"], ["Jet Fuel Price", 3]])
    csv_file("debt.csv", [["Line item", "2024"], ["Debt Due Within One Year", 80]])
    csv_file(
        "interim.csv",
        [["Line item", "Q3"], ["Operating Revenue", 250], ["Operating Income", 50]],
    )
    options = {
        "scale": "millions",
        "currency": "USD",
        "period_headers": {str(year): {"fiscal_year": year} for year in years},
    }

    def source(filename, parser, tags, entity="DAL", parse_options=None):
        return {
            "filename": filename,
            "path": filename,
            "parser": parser,
            "tags": tags,
            "entity_id": entity,
            "scope": "consolidated",
            "accounting_basis": "GAAP",
            "available_at": "2025-01-10T00:00:00Z",
            "parse_options": parse_options if parse_options is not None else options,
        }

    sources = [
        source("financials.xlsx", "xlsx", ["xlsx", "financial_statements"]),
        source(
            "filing.xbrl", "xbrl", ["xbrl", "financial_statements"], parse_options={}
        ),
        source("financials.pdf", "pdf_table", ["pdf", "financial_statements"]),
        source(
            "operations.csv",
            "csv",
            ["xlsx", "operations"],
            parse_options={**options, "currency": None},
        ),
        source("industry.csv", "csv", ["industry"], entity="PEER_A"),
        source("fuel.csv", "csv", ["fuel"]),
        source("debt.csv", "csv", ["debt_maturity"]),
        source(
            "interim.csv",
            "csv",
            ["interim"],
            parse_options={
                "scale": "millions",
                "currency": "USD",
                "period_headers": {
                    "Q3": {"period_start": "2024-07-01", "period_end": "2024-09-30"}
                },
            },
        ),
    ]
    package = {
        "as_of_date": "2024-12-31",
        "evidence_cutoff_timestamp": "2025-01-15T00:00:00Z",
        "borrower_entity_id": "DAL",
        "entities": [
            {
                "entity_id": "DAL",
                "legal_name": "Delta Synthetic Fixture",
                "entity_type": "borrower",
                "borrower_flag": True,
                "expected_consolidation_scope": "consolidated",
            },
            {
                "entity_id": "PEER_A",
                "legal_name": "Synthetic Peer A",
                "entity_type": "other",
            },
        ],
        "sources": sources,
    }
    (root / "package.json").write_text(
        json.dumps(package, indent=2) + "\n", encoding="utf-8"
    )
    expected = {
        "revenue": 1000,
        "operating_income": 200,
        "total_debt": 700,
        "unrestricted_cash": 100,
        "ebitda": 200,
        "interest_expense": 40,
        "cfo": 170,
        "capex": 80,
        "undrawn_revolver": 100,
        "operating_expense": 800,
        "rpm": 800,
        "asm": 1000,
    }
    manifest = {
        "case_id": "SYNTHETIC_2024",
        "as_of_date": package["as_of_date"],
        "evidence_cutoff_timestamp": package["evidence_cutoff_timestamp"],
        "manifest_version": "synthetic-1.0",
        "verified_facts": [
            {"name": k, "field_type": "financial", "expected_value": v, "tolerance": 0}
            for k, v in expected.items()
        ],
        "expected_metric_outputs": [
            {"metric_id": k, "expected_result": v, "tolerance": 1e-9}
            for k, v in {
                "operating_margin": 0.2,
                "net_debt": 600,
                "net_debt_to_ebitda": 3,
                "interest_coverage": 5,
                "cash_conversion": 0.85,
                "free_cash_flow": 90,
                "liquidity": 200,
                "capex_to_revenue": 0.08,
                "load_factor": 0.8,
                "casm": 0.8,
                "revenue_growth": 100 / 900,
            }.items()
        ],
        "adjudicated_material_risks": ["R-EVIDENCE-WEAK-01"],
        "expected_rule_triggers": ["R-EVIDENCE-WEAK-01"],
        "annotator_metadata": {
            "source": "Synthetic generator; values are deliberately illustrative"
        },
    }
    (root / "ground_truth.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return root / "package.json"


if __name__ == "__main__":
    print(generate(Path(__file__).resolve().parent / "synthetic-case"))
