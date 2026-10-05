"""Declared statement rows and periods for PDFs whose native tables collapse."""

import re

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.enums import ExtractionMethod, FactStatus
from app.services.extraction.base import ParserError, ParseResult, ParserMetadata
from app.services.extraction.xlsx_csv import TableParser


class StatementRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str
    mapping_label: str
    unit: str | None = None
    accounting_basis: str | None = None
    occurrence: int = Field(default=0, ge=0)
    expected_matches: int = Field(default=1, ge=1)


class StatementRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(ge=1, le=200)
    required_text: list[str] = Field(min_length=1)
    value_count: int = Field(ge=1, le=16)
    columns: dict[str, int]
    periods: dict[str, dict]
    rows: list[StatementRow]


METADATA = ParserMetadata(
    name="pdf_statement_recipe",
    version="2.0.0",
    extraction_method=ExtractionMethod.PDF_TEXT,
)


def parse_statement_recipes(
    pdf, recipes, *, document_id, scale, currency, target_scale, captured_pages=None
):
    result = ParseResult(metadata=METADATA)
    mapper = TableParser(METADATA)
    number = r"(\(?-?\d[\d,]*(?:\.\d+)?\)?|—|–|NM)"
    for index, raw in enumerate(recipes):
        recipe = StatementRecipe.model_validate(raw)
        if recipe.page > len(pdf.pages):
            raise ParserError("Statement recipe references a missing PDF page.")
        if set(recipe.columns) != set(recipe.periods) or any(
            c < 0 or c >= recipe.value_count for c in recipe.columns.values()
        ):
            raise ParserError("Statement recipe columns and periods do not agree.")
        captured = (captured_pages or {}).get(recipe.page)
        text = (
            captured["text"]
            if captured
            else (pdf.pages[recipe.page - 1].extract_text() or "")
        )
        # OCR may put whitespace around commas/decimal marks. Preserve the
        # untouched page and boxes in the capture artifact; normalize for lookup only.
        text = re.sub(r"(?<=\d)\s+(?=[,.]\d)|(?<=[,.])\s+(?=\d)", "", text)
        if any(
            re.sub(r"(?<=\d)\s+(?=[,.]\d)|(?<=[,.])\s+(?=\d)", "", required) not in text
            for required in recipe.required_text
        ):
            raise ParserError(
                f"Statement heading / period check failed on page {recipe.page}."
            )
        page_facts = []
        for row in recipe.rows:
            pattern = (
                r"^"
                + re.escape(row.label)
                + r"\s+(?:\$\s*)?"
                + r"\s+(?:%\s+)?(?:\$\s*)?".join([number] * recipe.value_count)
                + r"\s*%?\s*$"
            )
            matches = list(re.finditer(pattern, text, re.M))
            if len(matches) != row.expected_matches or row.occurrence >= len(matches):
                raise ParserError(
                    f"Expected one complete {row.label!r} row on page {recipe.page}; found {len(matches)}."
                )
            values = matches[row.occurrence].groups()
            table = [
                ["Line", *recipe.columns],
                [
                    row.mapping_label,
                    *(
                        values[c] if values[c] not in {"—", "–", "NM"} else ""
                        for c in recipe.columns.values()
                    ),
                ],
            ]
            parsed = mapper.parse_rows(
                table,
                document_id=document_id,
                sheet_name=f"page{recipe.page}:statement{index}",
                scale=scale,
                currency=currency,
                target_scale=target_scale,
                period_headers=recipe.periods,
            )
            for fact, header in zip(parsed.facts, recipe.columns, strict=True):
                if (
                    (
                        fact.normalized_value is None
                        and fact.status != FactStatus.MISSING
                    )
                    or fact.fiscal_year is None
                    or fact.period_end is None
                ):
                    raise ParserError(
                        "Statement recipe produced unresolved amount or period metadata."
                    )
                fact.original_name = row.label
                if fact.status != FactStatus.MISSING:
                    fact.status = FactStatus.UNVERIFIED
                if captured and captured["method"] == "ocr":
                    fact.extraction_method = ExtractionMethod.OCR
                    fact.normalization_method = "ocr-row:punctuation-whitespace:v1"
                if row.unit:
                    fact.raw_unit = fact.normalized_unit = row.unit
                    fact.currency = None
                    fact.scale = "millions" if row.unit.endswith("million") else "units"
                    fact.normalization_method = "declared-nonmonetary-unit:v1"
                if row.accounting_basis:
                    fact.accounting_basis = row.accounting_basis
                fact.source_refs = [
                    ref.model_copy(
                        update={
                            "page": recipe.page,
                            "row_label": row.label,
                            "cell": f"value-column:{recipe.columns[header] + 1}",
                        }
                    )
                    for ref in fact.source_refs
                ]
                page_facts.append(fact)
        result.facts.extend(page_facts)
    return result
