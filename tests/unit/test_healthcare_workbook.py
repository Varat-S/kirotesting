"""Healthcare benchmark workbook import + validation (handoff section 2 / 14).

The importer reads the original workbook without modifying it, classifies every
value, re-derives every formula independently and excludes anything that cannot
be validated — with a machine-readable reason.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from app.core.hashing import content_hash
from app.services.benchmarking.formula import ExcelError, evaluate, relative_form
from app.services.benchmarking.sector_config import (
    SectorConfigError,
    validate_sector_config,
)
from app.services.benchmarking.workbook import (
    VALUE_STATUSES,
    WorkbookImportError,
    industry_values,
)
from tests.healthcare_helpers import (
    HEALTHCARE,
    WORKBOOK,
    WORKBOOK_SHA256,
    edited_workbook,
    imported,
    sector_content,
    variant_config,
    with_cached_error,
    workbook_bytes,
)

from tests.healthcare_helpers import requires_workbook  # noqa: E402

pytestmark = requires_workbook

INDUSTRIES = [
    "Drugs (Biotechnology)",
    "Drugs (Pharmaceutical)",
    "Healthcare Products",
    "Healthcare Support Services",
    "Healthcare Information and Technology",
    "Hospitals/Healthcare Facilities",
]


@pytest.fixture(scope="module")
def result():
    return imported()


def _issues(report, code):
    return [i for i in report["issues"] if i["code"] == code]


# -- structure ---------------------------------------------------------------


def test_original_workbook_is_not_modified_and_hash_is_recorded(result):
    before = hashlib.sha256(WORKBOOK.read_bytes()).hexdigest()
    imported()
    assert hashlib.sha256(WORKBOOK.read_bytes()).hexdigest() == before == WORKBOOK_SHA256
    assert result.dataset["source"]["sha256"] == WORKBOOK_SHA256
    assert result.report["source_sha256"] == WORKBOOK_SHA256
    assert result.dataset["correction_log"]["original_modified"] is False
    assert result.dataset["correction_log"]["parent_sha256"] == WORKBOOK_SHA256


def test_imports_all_four_worksheets(result):
    assert result.dataset["source"]["sheets"] == [
        "Healthcare SIC Codes", "Individual companies", "Benchmarks", "Assumptions"]
    assert len(result.dataset["sic_codes"]["records"]) == 41
    assert result.dataset["companies"]["count"] == 870
    assert len(result.dataset["metrics"]) == 67
    assert [a["key"] for a in result.dataset["assumptions"]] == [
        "short_term_debt_share", "icr_basis_switch", "days_per_year"]


def test_identifies_the_six_industry_categories(result):
    assert result.dataset["industries"] == INDUSTRIES
    assert result.report["industries"] == INDUSTRIES


def test_selects_healthcare_products_column(result):
    values = industry_values(result.dataset, "Healthcare Products")
    # Column E of the Benchmarks sheet, for every mapped metric.
    assert {v["cell"][0] for v in values.values()} == {"E"}
    assert values["gross_debt_to_ebitda"]["cell"] == "E33"
    assert values["gross_debt_to_ebitda"]["value"] == 2.74
    with pytest.raises(KeyError):
        industry_values(result.dataset, "Airlines")


def test_missing_sheet_is_rejected():
    content = variant_config(
        lambda c: c["workbook_mapping"]["sheets"].update(benchmarks="No Such Sheet"))
    with pytest.raises(WorkbookImportError, match="missing required sheet"):
        imported(content=content)
    with pytest.raises(WorkbookImportError, match="Unreadable"):
        imported(data=b"not a workbook")


# -- status taxonomy ---------------------------------------------------------


def test_every_value_has_exactly_one_status_and_a_use_or_reason(result):
    for metric in result.dataset["metrics"]:
        for value in metric["values"].values():
            assert value["status"] in VALUE_STATUSES
            assert value["eligible_for_comparison"] or value["exclusion_reason"]
    assert result.report["acceptance"]["every_value_classified"] is True
    assert result.report["acceptance"]["eligible_count"] + \
        result.report["acceptance"]["excluded_count"] == 67 * 6


def test_source_aggregates_derived_and_unavailable_are_distinguished(result):
    hp = industry_values(result.dataset, "Healthcare Products")
    assert hp["ebitda_margin"]["status"] == "source_aggregate"
    assert hp["ebitda_margin"]["formula"] is None
    assert hp["net_debt_to_ebitda"]["status"] == "derived"
    assert hp["net_debt_to_ebitda"]["formula"] == "=E57/E50"
    assert hp["gross_margin_change"]["status"] == "unavailable"
    assert hp["gross_margin_change"]["value"] is None


def test_aggregates_are_distinguished_from_company_records(result):
    companies = result.dataset["companies"]
    assert companies["record_type"] == "company_identity"
    assert all(r["record_type"] == "company_identity" for r in companies["records"])
    assert not any(r["has_financial_observations"] for r in companies["records"])
    # A company record carries identity fields only — never a metric value.
    assert set(companies["records"][0]) == {
        "name", "ticker", "industry_group", "sector", "sic_code", "sic_title",
        "row", "record_type", "status", "has_financial_observations"}
    assert "sum of numerators" in result.dataset["aggregate_nature"]
    stryker = [r for r in companies["records"] if r["ticker"] == "NYSE:SYK"]
    assert len(stryker) == 1 and stryker[0]["industry_group"] == "Healthcare Products"


def test_placeholder_assumption_taints_debt_service_ratios_as_assumed(result):
    share = result.dataset["assumptions"][0]
    assert share["key"] == "short_term_debt_share" and share["kind"] == "placeholder"
    assert share["status"] == "assumed" and share["value"] == 0.1
    for industry in INDUSTRIES:
        values = industry_values(result.dataset, industry)
        for metric_id in ("workbook_dscr_proxy", "workbook_fccr_proxy"):
            value = values[metric_id]
            assert value["status"] == "assumed"
            assert "depends_on_placeholder_assumption:short_term_debt_share" in value["reasons"]
            assert value["eligible_for_comparison"] is False
    # "Principal due" itself is the placeholder proxy — never an observed repayment.
    principal = next(m for m in result.dataset["metrics"] if m["label"].startswith("Principal due"))
    assert {v["status"] for v in principal["values"].values()} == {"assumed"}


def test_methodology_switch_is_tracked_but_does_not_make_a_value_assumed(result):
    hp = industry_values(result.dataset, "Healthcare Products")
    assert hp["ebit_interest_coverage"]["status"] == "derived"
    assert "depends_on_methodology_switch:icr_basis_switch" in hp["ebit_interest_coverage"]["reasons"]
    assert "icr_basis_switch" in hp["ebit_interest_coverage"]["dependencies"]["assumptions"]


# -- independent reconciliation ---------------------------------------------


def test_every_formula_is_independently_reconciled_to_its_cached_value(result):
    formulas = [v for m in result.dataset["metrics"] for v in m["values"].values()
                if v["formula"] and v["status"] in ("derived", "assumed")]
    assert len(formulas) == 240
    assert all(v["reconciliation"]["matched"] for v in formulas)
    assert result.report["status_counts"]["invalid"] == 0
    assert result.report["issue_counts"]["error"] == 0


def test_external_workbook_dependencies_are_detected_and_rebound_with_a_log(result):
    links = result.dataset["source"]["external_links"]
    assert len(links) == 1 and links[0]["sheet_names"] == ["Benchmarks", "Assumptions"]
    assert _issues(result.report, "external_workbook_link")
    hp = industry_values(result.dataset, "Healthcare Products")
    for metric_id in ("dso", "dio", "dpo", "cash_conversion_cycle"):
        assert "external_reference_rebound_to_local" in hp[metric_id]["reasons"]
        assert hp[metric_id]["dependencies"]["external"] == ["[1]Assumptions!B7"]
    corrections = result.dataset["correction_log"]["corrections"]
    assert {c["reference"] for c in corrections} == {
        "[1]Assumptions!B5", "[1]Assumptions!B6", "[1]Assumptions!B7"}
    for correction in corrections:
        assert correction["kind"] == "external_reference_rebound_to_local"
        assert correction["external_cached_value"] == correction["local_value"]


def test_external_reference_that_cannot_be_reconciled_is_invalid_not_guessed():
    # The local assumption no longer matches the value cached for the external
    # workbook, so the importer cannot know which is right and refuses both.
    data = edited_workbook({("Assumptions", "B7"): 360})
    result = imported(data=data)
    hp = industry_values(result.dataset, "Healthcare Products")
    assert hp["dso"]["status"] == "invalid" and hp["dso"]["value"] is None
    assert any(r.startswith("unresolved_external_dependency") for r in hp["dso"]["reasons"])
    assert hp["dso"]["eligible_for_comparison"] is False
    assert hp["cash_conversion_cycle"]["status"] == "invalid"  # depends on DSO
    assert _issues(result.report, "unresolved_external_dependency")
    # A value that does not depend on the broken reference is unaffected.
    assert hp["net_debt_to_ebitda"]["status"] == "derived"


def test_cached_formula_error_is_detected_and_repaired_from_documented_inputs():
    result = imported(data=with_cached_error("E13", "#NAME?"))
    errors = _issues(result.report, "formula_error")
    assert [(i["sheet"], i["cell"]) for i in errors] == [("Benchmarks", "E13")]
    dso = industry_values(result.dataset, "Healthcare Products")["dso"]
    assert dso["cached_value"] == "#NAME?"
    assert "cached_error_repaired:#NAME?" in dso["reasons"]
    assert dso["status"] == "derived" and dso["value"] == pytest.approx(60.2615)
    repaired = [c for c in result.dataset["correction_log"]["corrections"]
                if c["kind"] == "cached_error_recomputed"]
    assert repaired and repaired[0]["reference"] == "Benchmarks!E13"
    assert repaired[0]["cached_value"] == "#NAME?"


def test_formula_that_evaluates_to_an_error_is_invalid_and_excluded():
    data = edited_workbook({("Benchmarks", "E12"): "=NOSUCHFUNCTION(E62)/E50"})
    result = imported(data=data)
    cfo = industry_values(result.dataset, "Healthcare Products")["cfo_to_ebitda"]
    assert cfo["status"] == "invalid" and cfo["value"] is None
    assert "formula_error:#NAME?" in cfo["reasons"]
    assert cfo["eligible_for_comparison"] is False and cfo["exclusion_reason"] == "status:invalid"
    excluded = [e for e in result.report["excluded_values"]
                if e["cell"] == "E12" and e["industry"] == "Healthcare Products"]
    assert excluded and excluded[0]["status"] == "invalid"  # preserved in diagnostics


def test_cached_value_that_does_not_reconcile_is_not_promoted():
    # A cached <v> that disagrees with what its own formula yields from the
    # workbook's inputs (e.g. a stale or hand-edited result).
    import re
    import zipfile
    import io

    source = zipfile.ZipFile(io.BytesIO(workbook_bytes()))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "xl/worksheets/sheet3.xml":
                text = data.decode("utf-8")
                text, count = re.subn(
                    r'(<c r="E13"[^>]*><f>[^<]*</f><v>)[^<]*(</v></c>)', r"\g<1>99.9\2", text)
                assert count == 1
                data = text.encode("utf-8")
            target.writestr(item, data)
    result = imported(data=out.getvalue())
    value = industry_values(result.dataset, "Healthcare Products")["dso"]
    assert value["status"] == "invalid" and value["value"] is None
    assert value["cached_value"] == 99.9
    assert "recalculation_mismatch" in value["reasons"]
    assert value["reconciliation"]["matched"] is False
    assert _issues(result.report, "recalculation_mismatch")


def test_inconsistent_formula_across_industry_columns_is_flagged():
    data = edited_workbook({("Benchmarks", "E7"): "=E64/E50"})  # others use /E49
    result = imported(data=data)
    issues = _issues(result.report, "inconsistent_formula")
    assert [i["cell"] for i in issues] == ["E7"]
    assert industry_values(result.dataset, "Healthcare Products")["ufcf_to_sales"]["status"] == "invalid"
    assert industry_values(result.dataset, "Drugs (Pharmaceutical)")["ufcf_to_sales"]["status"] == "derived"


def test_blank_mandatory_and_inappropriate_values_are_invalid():
    data = edited_workbook({
        ("Benchmarks", "E26"): None,          # blank source aggregate
        ("Benchmarks", "E28"): "about 20%",   # text where a number is required
        ("Benchmarks", "E29"): 7.5,           # an "operating margin" of 750%
    })
    result = imported(data=data)
    hp = industry_values(result.dataset, "Healthcare Products")
    assert hp["gross_margin"]["status"] == "invalid"
    assert "blank_mandatory_cell" in hp["gross_margin"]["reasons"]
    assert hp["ebitda_margin"]["status"] == "invalid"
    assert hp["ebitda_margin"]["reasons"] == ["inappropriate_value:non_numeric_literal"]
    assert hp["operating_margin"]["status"] == "invalid"
    assert any(r.startswith("inappropriate_value:outside_plausible_range")
               for r in hp["operating_margin"]["reasons"])
    assert all(not hp[m]["eligible_for_comparison"]
               for m in ("gross_margin", "ebitda_margin", "operating_margin"))
    assert _issues(result.report, "blank_mandatory_cell")
    assert len(_issues(result.report, "inappropriate_value")) == 2


# -- documentation, dates, diagnostics --------------------------------------


def test_source_dates_and_documentation_are_validated(result):
    assert result.dataset["vintage"]["data_as_of"] == "2026-01"
    assert result.dataset["vintage"]["sic_list_last_updated"] == "2026-06-18"
    sources = result.dataset["documentation"]["sources"]
    assert len(sources) == 6 and all(s["url"].startswith("https://") for s in sources)
    assert len(result.dataset["documentation"]["limitations"]) == 15


def test_missing_source_date_or_documentation_is_reported():
    data = edited_workbook({
        ("Benchmarks", "A22"): "B. Source parameters (as published by Damodaran)",
        ("Assumptions", "B28"): None,
    })
    result = imported(data=data)
    assert [(i["sheet"], i["cell"]) for i in _issues(result.report, "missing_source_date")] == [
        ("Benchmarks", "A22")]
    assert [(i["sheet"], i["cell"]) for i in _issues(result.report, "missing_source_documentation")] == [
        ("Assumptions", "B28")]


def test_diagnostics_note_data_quality_observations(result):
    titles = _issues(result.report, "duplicate_industry_title")
    assert len(titles) == 1 and "[7370, 7373]" in titles[0]["message"]
    assert len(_issues(result.report, "company_list_differs_from_sample")) == 6
    normalized = _issues(result.report, "industry_label_normalized")
    assert len(normalized) == 1 and normalized[0]["message"].startswith("81 company rows")


def test_validation_report_is_machine_readable_and_complete(result):
    report = json.loads(json.dumps(result.report))  # JSON round trip
    assert report["schema_version"] == "workbook-validation-report-1.0"
    assert sum(report["status_counts"].values()) == 67 * 6 + 41 + 870 + 3
    assert all({"severity", "code", "message", "sheet", "cell"} == set(i) for i in report["issues"])
    assert all(e["exclusion_reason"] for e in report["excluded_values"])
    assert report["dataset_hash"] == result.dataset_hash


# -- identity / invalidation -------------------------------------------------


def test_import_is_deterministic(result):
    again = imported()
    assert again.dataset == result.dataset and again.report == result.report
    body = {k: v for k, v in result.dataset.items() if k != "dataset_hash"}
    assert content_hash(body) == result.dataset_hash


def test_changed_source_changes_the_dataset_hash():
    base = imported()
    changed = imported(data=edited_workbook({("Benchmarks", "E33"): 2.9}))
    assert changed.source_sha256 != base.source_sha256
    assert changed.dataset_hash != base.dataset_hash
    assert industry_values(changed.dataset, "Healthcare Products")["gross_debt_to_ebitda"]["value"] == 2.9


def test_changed_mapping_changes_the_dataset_hash():
    base = imported()
    content = variant_config(
        lambda c: c["workbook_mapping"]["reconciliation"].update(relative_tolerance=1e-8))
    changed = imported(content=content)
    assert changed.source_sha256 == base.source_sha256
    assert changed.dataset["mapping_hash"] != base.dataset["mapping_hash"]
    assert changed.dataset_hash != base.dataset_hash


def test_checked_in_reference_artifacts_match_a_fresh_import(result):
    reference = HEALTHCARE / "reference"
    dataset = json.loads((reference / "normalized_dataset.json").read_text(encoding="utf-8"))
    report = json.loads((reference / "validation_report.json").read_text(encoding="utf-8"))
    assert dataset == json.loads(json.dumps(result.dataset))
    assert report == json.loads(json.dumps(result.report))


# -- configuration -----------------------------------------------------------


def test_sector_configuration_holds_no_benchmark_values_or_scoring_keys():
    content = sector_content()
    validate_sector_config(content)
    assert content["label"].startswith("ILLUSTRATIVE")
    assert content["analytical_status"] == "illustrative"
    # The published Healthcare Products aggregates live in the workbook only.
    text = json.dumps({k: content[k] for k in ("benchmark_metrics", "comparisons", "benchmark")})
    for published in ("2.74", "0.2034", "0.1534", "0.0485", "6.8"):
        assert published not in text
    for forbidden in ("parameter_bands", "financial_weights", "floors", "rubrics"):
        bad = variant_config(lambda c, k=forbidden: c.update({k: {}}))
        with pytest.raises(SectorConfigError, match="scoring"):
            validate_sector_config(bad)


def test_python_source_does_not_hard_code_industry_averages():
    from tests.healthcare_helpers import REPO

    for path in (REPO / "app").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for published in ("0.2034", "0.1534", "9.4734", "7.1446", "2.0313"):
            assert published not in source, f"{published} hard-coded in {path}"


# -- formula evaluator -------------------------------------------------------


def test_formula_evaluator_supports_the_workbook_subset():
    cells = {"A1": 10.0, "A2": 4.0, "B1": 0.0, "C1": None}
    lookup = lambda ref: cells[ref.cell]  # noqa: E731
    assert evaluate("=A1/A2", lookup) == 2.5
    assert evaluate("=MAX(0,A2-A1)*2", lookup) == 0.0
    assert evaluate('=IFERROR(A1/B1,"n/a")', lookup) == "n/a"
    assert evaluate('=IF(A2<=0,"n/a (neg. equity)",A1/(A1+A2))', lookup) == pytest.approx(10 / 14)
    assert evaluate("=A1+C1", lookup) == 10.0  # blank is zero, as in Excel
    assert evaluate("=A1/B1", lookup) == ExcelError("#DIV/0!")
    assert evaluate("=UNKNOWN(A1)+1", lookup) == ExcelError("#NAME?")
    assert evaluate('=A1+"text"', lookup) == ExcelError("#VALUE!")


def test_relative_form_normalizes_only_the_own_column():
    assert relative_form("=E57/E50", "E") == "={col}57/{col}50"
    assert relative_form("=[1]Assumptions!$B$5*C55", "C") == "=[1]Assumptions!$B$5*{col}55"
    assert relative_form("=D64/D49", "D") == relative_form("=H64/H49", "H")
