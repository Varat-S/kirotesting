"""Regenerate the binary golden fixtures (XLSX / PDF) for Milestone 3 parsers.

Run with: ``python3.11 tests/golden/fixtures/generate_fixtures.py``

All data here is synthetic/illustrative (Req 25.1). The generated files are
committed under ``tests/golden/fixtures/`` so the parser golden tests have
stable inputs; this script lets a developer rebuild them deterministically
without hand-editing binaries.

Requires ``openpyxl`` (XLSX) and ``reportlab`` (PDF). If a library is missing the
corresponding file is skipped with a message.
"""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parent


def build_xlsx() -> None:
    try:
        import openpyxl
    except ImportError:
        print("openpyxl not installed; skipping XLSX fixture.")
        return
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "IncomeStatement"
    rows = [
        ["Line Item", "FY2022", "FY2023"],
        ["Revenue", "1,200", "1,500"],
        ["Net Income", 180, 250],
        ["Total Debt", "(900)", "(750)"],
        ["Cash", None, 320],
    ]
    for row in rows:
        ws.append(row)
    out = FIXTURES / "sample_financials.xlsx"
    wb.save(out)
    print(f"wrote {out}")


def build_pdf_table() -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    except ImportError:
        print("reportlab not installed; skipping PDF table fixture.")
        return
    out = FIXTURES / "sample_table.pdf"
    doc = SimpleDocTemplate(str(out), pagesize=letter)
    data = [
        ["Line Item", "FY2022", "FY2023"],
        ["Revenue", "1,200", "1,500"],
        ["Net Income", "180", "250"],
        ["Total Debt", "(900)", "(750)"],
    ]
    table = Table(data)
    # Grid lines so pdfplumber's line-based table detection finds the table.
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 1, colors.black),
                ("BOX", (0, 0), (-1, -1), 1, colors.black),
            ]
        )
    )
    doc.build([table])
    print(f"wrote {out}")


def build_pdf_text() -> None:
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except ImportError:
        print("reportlab not installed; skipping PDF text fixture.")
        return
    out = FIXTURES / "sample_narrative.pdf"
    c = canvas.Canvas(str(out), pagesize=letter)
    text = c.beginText(72, 720)
    for line in [
        "ACME Corporation - Management Discussion",
        "The Borrower is ACME Corporation, a Delaware corporation.",
        "Total Revenue for the fiscal year was USD 1,500 million.",
        "The company reported Net Income of USD 250 million.",
        "No dividends were declared during the period.",
    ]:
        text.textLine(line)
    c.drawText(text)
    c.showPage()
    c.save()
    print(f"wrote {out}")


if __name__ == "__main__":
    build_xlsx()
    build_pdf_table()
    build_pdf_text()
