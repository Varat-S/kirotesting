"""Financial Overview measures over small synthetic fact sets."""

from __future__ import annotations

import pytest

from app.services.fo.measures import (
    PRETAX_CONCEPTS,
    build_financial_overview,
    interest_expense,
    pick,
    plain_values,
)
from app.services.fo.scoring import gap_score, score_financial_overview, scoring_settings

FY = "2025-01-01--2025-12-31"
BS = "2025-12-31"
HOSPITALS = "Hospitals/Healthcare Facilities"


def fact(concept, period, value, dims=None):
    return {"concept": concept, "period_key": period, "value": value, "dimensions": dims or {},
            "type": "numeric", "unit": "USD"}


def data(*facts):
    return {"facts": list(facts), "labels": {}, "sections": [], "document": [], "working_capital": {}}


def by_key(metrics):
    return {m["key"]: m for m in metrics}


def test_plain_values_skip_dimensional_facts_and_pick_follows_the_fallback_order():
    values = plain_values([
        fact("us-gaap:InterestExpenseNonoperating", FY, 80),
        fact("us-gaap:InterestExpense", FY, 100),
        fact("us-gaap:InterestExpense", FY, 5, {"srt:SegmentAxis": "x:SegMember"}),
    ])
    assert pick(values, ["us-gaap:InterestExpense", "us-gaap:InterestExpenseNonoperating"], FY)[:2] == \
        ("us-gaap:InterestExpense", 100)
    assert pick(values, ["us-gaap:Nothing"], FY) == (None, None, None)


def test_net_interest_counts_only_as_a_net_expense():
    income = plain_values([fact("us-gaap:InterestIncomeExpenseNet", FY, 40)])
    assert interest_expense(income, FY) == (None, None)
    expense = plain_values([fact("us-gaap:InterestIncomeExpenseNet", FY, -40)])
    assert interest_expense(expense, FY) == ("us-gaap:InterestIncomeExpenseNet", 40)


def _scored(*facts):
    return by_key(score_financial_overview(build_financial_overview(data(*facts)),
                                           scoring_settings({}, HOSPITALS)))


def test_negative_ebitdar_scores_as_the_worst_capped_gap():
    lev = _scored(
        fact(PRETAX_CONCEPTS[0], FY, -500), fact("us-gaap:InterestExpense", FY, 100),
        fact("us-gaap:DepreciationDepletionAndAmortization", FY, 50), fact("us-gaap:OperatingLeaseCost", FY, 10),
        fact("us-gaap:DebtLongtermAndShorttermCombinedAmount", BS, 1000),
        fact("us-gaap:CashAndCashEquivalentsAtCarryingValue", BS, 200),
    )["leverage"]
    col = lev["columns"][0]
    assert col["ebitdar"] == -340
    assert col["debt_to_ebitdar"]["g"] == 3.0
    assert col["debt_to_ebitdar"]["score"] == round(gap_score(3.0, 5, 2.5), 1)


def test_net_cash_scores_as_the_best_capped_gap_and_ebitdar_adds_rent():
    lev = _scored(
        fact(PRETAX_CONCEPTS[0], FY, 900), fact("us-gaap:InterestExpense", FY, 100),
        fact("us-gaap:DepreciationDepletionAndAmortization", FY, 100),
        fact("us-gaap:DebtLongtermAndShorttermCombinedAmount", BS, 100),
        fact("us-gaap:CashAndCashEquivalentsAtCarryingValue", BS, 500),
    )["leverage"]
    col = lev["columns"][0]
    assert (col["ebit"], col["ebitda"], col["rent"], col["ebitdar"]) == (1000, 1100, 0.0, 1100)
    assert col["debt_to_ebitdar"]["ratio"] == pytest.approx(100 / 1100)
    assert col["net_debt_to_ebitdar"]["g"] == -3.0
    assert col["net_debt_to_ebitdar"]["note"] == "Net cash: best capped gap"


def test_measures_report_why_when_nothing_is_tagged():
    scored = _scored(fact("us-gaap:Revenues", FY, 1000))
    assert scored["dscr"]["columns"] == []
    assert scored["overall"]["columns"][0]["score"] is None
    assert scored["dso"]["columns"][0]["reason"] == "No balance sheet at this year-end in the filing"


def test_year1_principal_is_the_maturity_table_year_1_plus_current_finance_lease_principal():
    from app.services.fo.measures import year1_principal

    debt_only = [fact("us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths", BS, 800),
                 fact("us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo", BS, 900),
                 fact("us-gaap:FinanceLeaseLiabilityCurrent", BS, 50), fact("us-gaap:DebtCurrent", BS, 2_300)]
    got = year1_principal(debt_only, plain_values(debt_only), {}, BS)
    assert (got["principal"], got["basis"], got["short_term"]) == (850, "maturity_table", False)
    assert "current finance-lease principal" in got["source"]
    with_leases = [fact("us-gaap:LongTermDebtAndCapitalLeaseObligationsMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",
                        BS, 820), fact("us-gaap:FinanceLeaseLiabilityCurrent", BS, 50)]
    assert year1_principal(with_leases, plain_values(with_leases), {}, BS)["principal"] == 820  # leases already in
    no_table = debt_only[2:]  # no maturity table: the current portion of debt (commercial paper included)
    got = year1_principal(no_table, plain_values(no_table), {}, BS)
    assert (got["principal"], got["basis"], got["short_term"]) == (2_300, "current_portion", True)


def test_interest_is_interest_incurred_for_both_dscr_and_icr():
    from app.services.fo.measures import interest_incurred

    expensed = [fact("us-gaap:InterestExpense", FY, 100), fact("us-gaap:InterestCostsCapitalized", FY, 20)]
    src, incurred, capitalised = interest_incurred(plain_values(expensed), {}, FY)
    assert (incurred, capitalised) == (120, 20) and "capitalised" in src
    tagged = [*expensed, fact("us-gaap:InterestCostsIncurred", FY, 125)]
    assert interest_incurred(plain_values(tagged), {}, FY)[1] == 125
    none_capitalised = [fact("us-gaap:InterestExpense", FY, 100)]
    assert interest_incurred(plain_values(none_capitalised), {}, FY)[1:] == (100, 0.0)
    filing = data(fact(PRETAX_CONCEPTS[0], FY, 500), *expensed)
    fo = by_key(build_financial_overview(filing))
    assert fo["icr"]["columns"][0]["interest"] == 120 and fo["dscr"]["columns"][-1]["interest"] == 120
