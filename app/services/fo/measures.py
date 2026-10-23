"""Financial Overview measures: concept fallbacks, the fixed risk bands and every ratio.

Ported verbatim from the "10-K with FO" project (``credit_memo_app.py``, "Financial
overview metrics" and "Liquidity and debt maturity"). Every concept list, band and
formula is unchanged so a filing scores the same in both projects. The going-concern
measure is not ported yet (a later extra); ``scoring.composite_risk`` treats a
missing going-concern measure as a pass, exactly as the FO project does.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date, timedelta

from .parsing import (
    OPERATING_CASH_FLOW_CONCEPTS,
    concept_label,
    is_annual,
    item_ref,
    period_label,
    period_sort_key,
)

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z•(\"“])")

# Tried in order; the first concept tagged (undimensioned) for a period is used. EBIT is pre-tax income +
# interest, else operating income (most pharma has no operating-income subtotal).
OPERATING_INCOME_CONCEPTS = ["us-gaap:OperatingIncomeLoss", "ifrs-full:ProfitLossFromOperatingActivities"]
PRETAX_CONCEPTS = [
    "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    "ifrs-full:ProfitLossBeforeTax",
]
EBIT_SOURCE = "EBIT (pre-tax income + interest)"
INTEREST_CONCEPTS = [
    "us-gaap:InterestExpense", "us-gaap:InterestExpenseNonoperating", "us-gaap:InterestExpenseDebt",
    "us-gaap:InterestExpenseDebtExcludingAmortization", "us-gaap:InterestAndDebtExpense", "us-gaap:InterestExpenseOperating",
    "us-gaap:InterestIncomeExpenseNonoperatingNet",
    "us-gaap:InterestIncomeExpenseNet", "us-gaap:InterestPaidNet", "us-gaap:InterestPaid", "ifrs-full:InterestExpense",
]
NET_INTEREST_CONCEPTS = {"us-gaap:InterestIncomeExpenseNonoperatingNet", "us-gaap:InterestIncomeExpenseNet"}  # used only when net expense (< 0)
INTEREST_EXPENSE_CONCEPTS = [c for c in INTEREST_CONCEPTS if "InterestPaid" not in c]  # accrual, not cash paid
OPERATING_INTEREST_CONCEPTS = {"us-gaap:InterestExpenseOperating"}  # often a captive finance arm (ICR trigger T2)
# Interest incurred (expensed + capitalised): the tagged total, else expense + capitalised interest.
INTEREST_INCURRED_CONCEPTS = ["us-gaap:InterestCostsIncurred"]
CAPITALISED_INTEREST_CONCEPTS = ["us-gaap:InterestCostsCapitalized", "us-gaap:InterestPaidCapitalized"]
# Principal falling due within 12 months of a balance-sheet date.
PRINCIPAL_CONCEPTS = [
    "us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths", "us-gaap:LongTermDebtCurrent",
    "us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent", "us-gaap:DebtCurrent",
    "ifrs-full:CurrentPortionOfLongtermBorrowings",
]
# Cash flow statement. Filers don't tag maintenance CapEx, so total CapEx (purchases of PP&E) stands in for it.
CASH_TAX_CONCEPTS = ["us-gaap:IncomeTaxesPaidNet", "us-gaap:IncomeTaxesPaid",
                     "ifrs-full:IncomeTaxesPaidRefundClassifiedAsOperatingActivities"]
CAPEX_CONCEPTS = ["us-gaap:PaymentsToAcquirePropertyPlantAndEquipment", "us-gaap:PaymentsToAcquireProductiveAssets",
                  "us-gaap:PaymentsToAcquireOtherPropertyPlantAndEquipment",
                  "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
                  "us-gaap:PaymentsForProceedsFromProductiveAssets"]  # last: net of disposal proceeds
# Depreciation alone (DSCR trigger T2 compares CapEx with it): the tagged figure, else D&A less the amortisation of
# acquired intangibles.
DEPRECIATION_CONCEPTS = ["us-gaap:Depreciation", "us-gaap:DepreciationNonproduction",
                         "ifrs-full:DepreciationPropertyPlantAndEquipment"]
INTANGIBLE_AMORTISATION_CONCEPTS = ["us-gaap:AmortizationOfIntangibleAssets",
                                    "ifrs-full:AmortisationIntangibleAssetsOtherThanGoodwill"]
# Current tax expense (DSCR trigger T3 compares cash taxes paid with it): the tagged total, else federal + state and
# local + foreign.
CURRENT_TAX_CONCEPTS = ["us-gaap:CurrentIncomeTaxExpenseBenefit", "ifrs-full:CurrentTaxExpenseIncome"]
CURRENT_TAX_PARTS = [["us-gaap:CurrentFederalTaxExpenseBenefit"], ["us-gaap:CurrentStateAndLocalTaxExpenseBenefit"],
                     ["us-gaap:CurrentForeignTaxExpenseBenefit"]]
# One-offs in EBITDAR as tagged (Debt/EBITDAR trigger T1 compares their net with EBITDAR): the first tagged concept of
# each group. Charges are positive when an expense; gains are signed (a gain positive, a loss negative). Impairments
# are one group: filers' totals often include goodwill, so a second impairment tag could count it twice.
ONE_OFF_CHARGE_GROUPS = [
    ["us-gaap:GoodwillAndIntangibleAssetImpairment", "us-gaap:AssetImpairmentCharges", "us-gaap:GoodwillImpairmentLoss",
     "us-gaap:ImpairmentOfLongLivedAssetsHeldForUse", "us-gaap:ImpairmentOfLongLivedAssetsToBeDisposedOf"],
    ["us-gaap:RestructuringCharges", "us-gaap:RestructuringSettlementAndImpairmentProvisions",
     "us-gaap:RestructuringCosts"],
    ["us-gaap:LitigationSettlementExpense", "us-gaap:LossContingencyLossInPeriod"],
    ["us-gaap:BusinessCombinationAcquisitionRelatedCosts"],
    ["us-gaap:BusinessCombinationIntegrationRelatedCosts"],
]
ONE_OFF_GAIN_GROUPS = [
    ["us-gaap:GainLossOnSaleOfBusiness", "us-gaap:DisposalGroupNotDiscontinuedOperationGainLossOnDisposal"],
    ["us-gaap:GainLossOnSaleOfPropertyPlantEquipment", "us-gaap:GainLossOnDispositionOfAssets"],
    ["us-gaap:GainLossRelatedToLitigationSettlement"],
]

# Risk-score bands, (minimum ratio, score, range shown): a ratio gets the first band it reaches; the last band
# (floor None) catches everything below, including zero and negative ratios. 10 = highest risk. DSCR and ICR are
# fixed-charge ratios (rent on both sides), which run lower than plain coverage: these bands are provisional.
# DSCR's bands are this project's own (2026-10-22), not the "10-K with FO" project's: 9 is any ratio above 0x and
# below 0.5x (the floor is the smallest ratio above 0), so 0x and below score 10.
ABOVE_ZERO = math.nextafter(0.0, 1.0)
DSCR_BANDS = [(2.0, 1, "≥ 2.0x"), (1.5, 2, "1.5–2.0x"), (1.25, 3, "1.25–1.5x"), (1.0, 5, "1.0–1.25x"),
              (0.5, 7, "0.5–1.0x"), (ABOVE_ZERO, 9, "0–0.5x"), (None, 10, "≤ 0x")]
# ICR's bands are this project's own too (2026-10-22), set for interest incurred (expensed + capitalised).
ICR_BANDS = [(12.0, 1, "≥ 12x"), (8.0, 1.5, "8–12x"), (6.0, 2.5, "6–8x"), (4.0, 3.5, "4–6x"), (3.0, 5, "3–4x"),
             (2.0, 6, "2–3x"), (1.5, 7.5, "1.5–2x"), (1.0, 8.5, "1.0–1.5x"), (None, 10, "< 1.0x")]
# (highest score, label, CSS class) shared by every ratio, so the same score reads the same everywhere.
RISK_LEVELS = [(1.5, "Very low", "very-low"), (3.5, "Low", "low"), (5, "Moderate", "moderate"),
               (7.5, "Elevated", "elevated"), (9, "High", "high"), (10, "Very high", "very-high")]
CREDIT_RATINGS = ["AAA", "AA", "A", "BBB", "BB", "B", "CCC", "CC", "C", "D"]  # risk scores 1 to 10


def risk_score(ratio, bands):
    if ratio is None:
        return None
    return next(score for floor, score, _ in bands if floor is None or ratio >= floor)


def risk_level(score):
    """(label, CSS class) for a risk score."""
    return next((label, css) for top, label, css in RISK_LEVELS if score <= top)


def credit_rating(score):
    """The letter rating for a composite risk score: the whole number picks the grade (1 = AAA ... 10 = D), then the
    first decimal notches it: above .5 adds a minus, below .5 (a whole number included) a plus; exactly .5 or AAA
    stays as is."""
    tenths = round(score * 10)
    grade, frac = CREDIT_RATINGS[tenths // 10 - 1], tenths % 10
    if grade == "AAA":
        return grade
    return grade + ("-" if frac > 5 else "+" if frac < 5 else "")


def fmt_score(score):
    return f"{score:g}"


def bands_text(bands):
    return ", ".join(f"{label} → {fmt_score(score)}" for _, score, label in bands)


def plain_values(facts):
    """{(concept, period_key): (value, unit)} for the undimensioned numeric facts."""
    return {(f["concept"], f["period_key"]): (f["value"], f.get("unit")) for f in facts
            if f["type"] == "numeric" and not f["dimensions"] and isinstance(f["value"], (int, float))}


def pick(values, concepts, period):
    """(concept, value, unit) for the first of `concepts` tagged for `period`, or (None, None, None)."""
    for c in concepts:
        if (c, period) in values:
            return (c, *values[(c, period)])
    return None, None, None


def instant_near(values, concepts, day, slack=7):
    """The instant period key of the first of `concepts` tagged on, or up to `slack` days before, `day`."""
    for back in range(slack + 1):
        key = (day - timedelta(days=back)).isoformat()
        if any((c, key) in values for c in concepts):
            return key
    return None


def annual_periods(values, concepts):
    return sorted({p for c, p in values if c in concepts and is_annual(p)}, key=period_sort_key, reverse=True)


def interest_expense(values, period, concepts=INTEREST_CONCEPTS):
    """(concept, expense): the first tagged of `concepts`; a net interest figure only counts when it's a net expense."""
    concepts = [c for c in concepts
                if c not in NET_INTEREST_CONCEPTS or (c, period) in values and values[(c, period)][0] < 0]
    c, v, _ = pick(values, concepts, period)
    return c, abs(v) if v is not None else None


def ebit(values, period, interest_c, interest):
    """(source, value, unit): pre-tax income + interest expense (never cash interest paid)."""
    if interest is None or "InterestPaid" in interest_c:
        return None, None, None
    _, pretax, unit = pick(values, PRETAX_CONCEPTS, period)
    return (EBIT_SOURCE, pretax + interest, unit) if pretax is not None else (None, None, None)


def ebit_for(values, period):
    """(source, EBIT, unit): pre-tax income + interest expense, else operating income."""
    int_c, interest = interest_expense(values, period, INTEREST_EXPENSE_CONCEPTS)
    src, v, unit = ebit(values, period, int_c, interest)
    if v is None:  # no pre-tax income (or no interest to add back): operating income
        src, v, unit = pick(values, OPERATING_INCOME_CONCEPTS, period)
    return src, v, unit


def ebitda_for(values, labels, period):
    """EBIT, D&A from the cash flow statement (else depreciation + amortisation of intangibles) and their sum."""
    ebit_c, ebit_v, unit = ebit_for(values, period)
    da_c, da, _ = pick(values, DA_CONCEPTS, period)
    if da_c is None:
        da_c, da = summed(values, labels, [[c] for c in DA_PARTS], period)
    return {"unit": unit, "ebit": ebit_v, "ebit_concept": ebit_c, "da": da, "da_concept": da_c,
            "ebitda": ebit_v + da if ebit_v is not None and da is not None else None}


def concept_ref(labels, c):
    return f"{concept_label(labels, c)} [{c}]"


def ratio_sources(cols, parts, labels):
    """Each part's sources across the columns. A source with a space in it is already a description (EBIT,
    or a sum of concepts); anything else is a concept name and gets its label."""
    used = defaultdict(list)
    for col in cols:
        for part in parts:
            c = col.get(f"{part}_concept")
            if c and c not in used[part]:
                used[part].append(c)
    return {part: [c if " " in c else concept_ref(labels, c) for c in cs] for part, cs in used.items()}


def finish_column(col, parts, numerator, denominator, bands, nothing_to_cover):
    """Add the ratio, its score and, when there's no ratio, why."""
    num, den = col[numerator], col[denominator]
    col["ratio"] = num / den if num is not None and den else None
    col["score"] = risk_score(col["ratio"], bands)
    missing = [name for key, name in parts if col[key] is None]
    col["reason"] = None if col["ratio"] is not None else (
        "No " + " or ".join(missing) + " tagged" if missing else nothing_to_cover)
    return col


def depreciation_for(values, labels, period):
    """(source, depreciation): the tagged depreciation, else D&A less the amortisation of acquired intangibles (none
    tagged = none); (None, None) without either."""
    c, v, _ = pick(values, DEPRECIATION_CONCEPTS, period)
    if c:
        return concept_ref(labels, c), abs(v)
    da_c, da, _ = pick(values, DA_CONCEPTS, period)
    if da_c is None:
        return None, None
    am_c, am, _ = pick(values, INTANGIBLE_AMORTISATION_CONCEPTS, period)
    return (f"{concept_ref(labels, da_c)} − amortisation of intangibles: "
            + (concept_ref(labels, am_c) if am_c else "none tagged (taken as 0)")), abs(da) - abs(am or 0.0)


def current_tax_for(values, labels, period):
    """(source, current tax expense): the tagged total, else its federal, state and local, and foreign parts;
    (None, None) without any."""
    c, v, _ = pick(values, CURRENT_TAX_CONCEPTS, period)
    if c:
        return concept_ref(labels, c), v
    return summed(values, labels, CURRENT_TAX_PARTS, period)


def one_offs_for(values, labels, period):
    """The year's one-offs as tagged: charges (impairments, restructuring, litigation, acquisition and integration
    costs), gains (on disposals, litigation settlements; signed) and net one-offs = charges − gains. None tagged =
    none (net 0)."""
    charges = [(c, abs(v)) for c, v, _ in (pick(values, g, period) for g in ONE_OFF_CHARGE_GROUPS) if c]
    gains = [(c, v) for c, v, _ in (pick(values, g, period) for g in ONE_OFF_GAIN_GROUPS) if c]
    source = " − ".join(filter(None, [" + ".join(concept_ref(labels, c) for c, _ in charges),
                                      " + ".join(concept_ref(labels, c) for c, _ in gains)])) or "none tagged (taken as 0)"
    return {"one_off_charges": sum(v for _, v in charges), "one_off_gains": sum(v for _, v in gains),
            "net_one_offs": sum(v for _, v in charges) - sum(v for _, v in gains), "net_one_offs_concept": source}


def rent_for(values, period):
    """(source, rent): the year's operating lease cost, from the lease-cost table (never cash paid for leases). Without
    it: no operating lease liabilities at the year-end = no leases, rent 0; liabilities tagged = rent can't be found
    (None), so EBITDAR is n/a rather than understated."""
    c, v, _ = pick(values, RENT_CONCEPTS, period)
    if c:
        return c, abs(v)
    lease_concepts = OPERATING_LEASE_CONCEPTS + OPERATING_LEASE_PARTS
    as_at = instant_near(values, lease_concepts, date.fromisoformat(period.split("--")[1]))
    if as_at and any(values[(x, as_at)][0] for x in lease_concepts if (x, as_at) in values):
        return None, None
    return "none tagged (taken as 0)", 0.0


def interest_incurred(values, labels, period):
    """(source, interest incurred, capitalised): expensed + capitalised interest, the figure DSCR and ICR both use.
    The tagged total (Interest Costs Incurred) wins; else the income-statement expense (never cash paid) plus
    capitalised interest (none tagged = none)."""
    c, v, _ = pick(values, INTEREST_INCURRED_CONCEPTS, period)
    if c:
        return f"{concept_ref(labels, c)} (expensed + capitalised)", abs(v), None
    exp_c, expense = interest_expense(values, period, INTEREST_EXPENSE_CONCEPTS)
    if expense is None:
        return None, None, None
    cap_c, cap, _ = pick(values, CAPITALISED_INTEREST_CONCEPTS, period)
    cap = abs(cap) if cap is not None else 0.0
    return (f"{concept_ref(labels, exp_c)} + capitalised: "
            + (concept_ref(labels, cap_c) if cap_c else "none tagged (taken as 0)")), expense + cap, cap


def year1_principal(facts, values, labels, as_at, bs_lines=frozenset()):
    """Principal due within 12 months of a balance-sheet date: year 1 of the debt maturity table (scheduled
    amortisation) plus the current finance-lease principal, unless the table already includes finance leases.
    Without a tagged maturity table, the current portion of debt (Debt, Current also counts short-term borrowings
    such as commercial paper), with finance leases added when the balance sheet shows them apart from debt."""
    ladder, ladder_src = maturity_ladder(facts, as_at)
    if "y1" in ladder:
        principal, src = ladder["y1"], f"Debt maturity table, year 1: {ladder_src}"
        if "Lease" not in (ladder_src or ""):
            fl_c, fl, _ = pick(values, FINANCE_LEASE_PARTS[:1], as_at)
            if fl_c:
                principal += abs(fl)
                src += f" + current finance-lease principal: {concept_ref(labels, fl_c)}"
        return {"principal": principal, "source": src, "basis": "maturity_table", "short_term": False}
    c, v, _ = pick(values, PRINCIPAL_CONCEPTS, as_at)
    if c is None:
        return {"principal": None, "source": None, "basis": None, "short_term": False}
    principal, src = abs(v), concept_ref(labels, c)
    if FINANCE_LEASE_PARTS[0] in bs_lines:  # shown apart from debt: add it, as for debt
        fl_c, fl, _ = pick(values, FINANCE_LEASE_PARTS[:1], as_at)
        if fl_c:
            principal, src = principal + abs(fl), f"{src} + {concept_ref(labels, fl_c)}"
    return {"principal": principal, "source": f"{src} (no maturity table tagged: current portion of debt)",
            "basis": "current_portion", "short_term": c == "us-gaap:DebtCurrent"}


def debt_service_coverage(facts, labels, sections=(), n_periods=3):
    """Fixed-charge DSCR: (EBITDAR − cash taxes − CapEx − NCI distributions) / (interest + principal + rent)."""
    values = plain_values(facts)
    bs_lines = balance_sheet_lines(sections)
    periods = annual_periods(values, PRETAX_CONCEPTS + OPERATING_INCOME_CONCEPTS)
    parts = (("ebit", "EBIT"), ("da", "D&A"), ("rent", "operating lease cost"), ("cash_taxes", "cash taxes"),
             ("capex", "CapEx"), ("interest", "interest"), ("principal", "principal due"))

    def column(heading, period, principal_day, basis):
        col = ebitda_for(values, labels, period)
        rent_c, rent = rent_for(values, period)
        if basis == "forward":  # rent falling due in the next 12 months, from the lease maturity table
            fwd_c, fwd, _ = pick(values, RENT_NEXT_12M_CONCEPTS, period.split("--")[1])
            if fwd_c:
                rent_c, rent = fwd_c, abs(fwd)
        ebitdar = col["ebitda"] + rent if col["ebitda"] is not None and rent is not None else None
        tax_c, cash_taxes, _ = pick(values, CASH_TAX_CONCEPTS, period)  # signed: a net refund adds to cash flow
        capex_c, capex, _ = pick(values, CAPEX_CONCEPTS, period)
        capex = abs(capex) if capex is not None else None
        dep_c, dep = depreciation_for(values, labels, period)
        cur_c, current_tax = current_tax_for(values, labels, period)
        nci_c, nci, _ = pick(values, NCI_DISTRIBUTION_CONCEPTS, period)
        nci = abs(nci) if nci is not None else 0.0
        cash_flow = (ebitdar - cash_taxes - capex - nci if None not in (ebitdar, cash_taxes, capex) else None)
        int_c, interest, capitalised = interest_incurred(values, labels, period)
        as_at = instant_near(values, PRINCIPAL_CONCEPTS, principal_day) or principal_day.isoformat()
        prin = year1_principal(facts, values, labels, as_at, bs_lines)
        principal = prin["principal"]
        service = (interest + principal + rent if None not in (interest, principal, rent) else None)
        col.update({"heading": heading, "period": period, "basis": basis, "rent": rent, "rent_concept": rent_c,
                    "ebitdar": ebitdar, "cash_taxes": cash_taxes, "cash_taxes_concept": tax_c, "capex": capex,
                    "capex_concept": capex_c, "depreciation": dep, "depreciation_concept": dep_c,
                    "current_tax": current_tax, "current_tax_concept": cur_c,
                    "nci_distributions": nci,
                    "nci_distributions_concept": nci_c or "none tagged (taken as 0)", "cash_flow": cash_flow,
                    "interest": interest, "interest_concept": int_c, "interest_capitalised": capitalised,
                    "principal": principal, "principal_concept": prin["source"],
                    "principal_basis": prin["basis"], "principal_includes_short_term": prin["short_term"],
                    "principal_as_at": as_at if principal is not None else None, "debt_service": service})
        return finish_column(col, parts, "cash_flow", "debt_service", DSCR_BANDS, "No debt service to cover")

    cols = []
    for p in periods[:n_periods]:  # principal due in the year = what was due within 12 months at its start
        start = date.fromisoformat(p.split("--")[0])
        cols.append(column(period_label(p), p, start - timedelta(days=1), "historical"))
    if periods:  # latest year's cash flow and interest against principal and rent due in the next 12 months
        end = periods[0].split("--")[1]
        cols.insert(0, column(f"Next 12M from {end}", periods[0], date.fromisoformat(end), "forward"))
    return {"columns": cols, "sources": ratio_sources(cols, [k for k, _ in parts] + ["rent", "nci_distributions",
                                                                                     "depreciation", "current_tax"],
                                                      labels)}


def interest_coverage(facts, labels, n_periods=3):
    """Fixed-charge cover: (EBIT + rent) / (interest incurred + rent). Interest incurred is expensed + capitalised,
    the same figure as DSCR's; EBIT adds back only the expensed part (pre-tax income + interest expense)."""
    values = plain_values(facts)
    periods = annual_periods(values, PRETAX_CONCEPTS + OPERATING_INCOME_CONCEPTS)
    parts = (("ebit", "EBIT"), ("rent", "operating lease cost"), ("interest", "interest incurred"))
    cols = []
    for p in periods[:n_periods]:
        int_c, interest, capitalised = interest_incurred(values, labels, p)
        # The expense tag behind interest incurred (none when the total is tagged), for ICR trigger T2: a net or an
        # operating tag; and a net interest figure that is income (no expense to cover).
        total_c = pick(values, INTEREST_INCURRED_CONCEPTS, p)[0]
        exp_c, expense = interest_expense(values, p, INTEREST_EXPENSE_CONCEPTS)
        net_income = next((values[(c, p)][0] for c in sorted(NET_INTEREST_CONCEPTS)
                           if (c, p) in values and values[(c, p)][0] > 0), None) if exp_c is None else None
        ebit_c, ebit_v, unit = ebit_for(values, p)
        rent_c, rent = rent_for(values, p)
        cols.append(finish_column({"heading": period_label(p), "period": p, "basis": "historical", "unit": unit,
                                   "ebit": ebit_v, "ebit_concept": ebit_c, "rent": rent, "rent_concept": rent_c,
                                   "ebitr": ebit_v + rent if ebit_v is not None and rent is not None else None,
                                   "interest": interest, "interest_concept": int_c,
                                   "interest_capitalised": capitalised,
                                   "interest_expense": None if total_c else expense,
                                   "interest_expense_concept": None if total_c else exp_c,
                                   "net_interest_income": net_income,
                                   "fixed_charges": (interest + rent if interest is not None and rent is not None
                                                     else None)},
                                  parts, "ebitr", "fixed_charges", ICR_BANDS, "No interest expense to cover"))
    return {"columns": cols, "sources": ratio_sources(cols, ("ebit", "interest", "rent"), labels)}


# Debt at a balance-sheet date = borrowings + finance leases: a tagged total, else current + non-current debt. Many
# filers present debt and finance leases as one line (the "...AndCapitalLeaseObligations" concepts); finance lease
# liabilities are added only when the balance sheet shows them as lines of their own, as otherwise they are already
# inside debt or sit in other liabilities. Leverage and RCF then add operating lease liabilities (lease-adjusted debt).
DEBT_TOTAL_CONCEPTS = ["us-gaap:DebtLongtermAndShorttermCombinedAmount", "us-gaap:DebtAndCapitalLeaseObligations",
                       "us-gaap:LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities", "ifrs-full:Borrowings"]
DEBT_CURRENT_CONCEPTS = ["us-gaap:DebtCurrent", "us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent",
                         "ifrs-full:CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings"]
DEBT_NONCURRENT_CONCEPTS = ["us-gaap:LongTermDebtNoncurrent", "us-gaap:LongTermDebtAndCapitalLeaseObligations",
                            "ifrs-full:NoncurrentPortionOfNoncurrentBorrowings"]
SHORT_TERM_DEBT_CONCEPTS = ["us-gaap:ShortTermBorrowings", "us-gaap:CommercialPaper"]  # when DebtCurrent isn't tagged
FINANCE_LEASE_PARTS = ["us-gaap:FinanceLeaseLiabilityCurrent", "us-gaap:FinanceLeaseLiabilityNoncurrent"]
# Lease adjustment: operating lease liabilities count as debt and rent (operating lease cost, else cash paid) is
# added back to earnings: EBITDAR = EBITDA + rent. The forward DSCR uses the rent due in the next 12 months from the
# lease maturity table.
OPERATING_LEASE_CONCEPTS = ["us-gaap:OperatingLeaseLiability"]
OPERATING_LEASE_PARTS = ["us-gaap:OperatingLeaseLiabilityCurrent", "us-gaap:OperatingLeaseLiabilityNoncurrent"]
RENT_CONCEPTS = ["us-gaap:OperatingLeaseCost"]  # the lease-cost table; never cash paid for operating leases
# The EBITDAR margin alone falls back to cash paid for operating leases when the lease-cost table is missing.
RENT_CASH_CONCEPTS = ["us-gaap:OperatingLeasePayments"]
RENT_CASH_LABEL = "cash lease payments: lease-cost table missing"
RENT_NEXT_12M_CONCEPTS = ["us-gaap:LesseeOperatingLeaseLiabilityPaymentsDueNextTwelveMonths"]
# Unrestricted cash only: restricted cash (and the totals that include it) is never netted against debt.
CASH_CONCEPTS = ["us-gaap:CashAndCashEquivalentsAtCarryingValue", "ifrs-full:CashAndCashEquivalents"]
DA_CONCEPTS = ["us-gaap:DepreciationDepletionAndAmortization", "us-gaap:DepreciationAmortizationAndAccretionNet",
               "us-gaap:DepreciationAndAmortization", "ifrs-full:DepreciationAndAmortisationExpense"]
DA_PARTS = ["us-gaap:Depreciation", "us-gaap:AmortizationOfIntangibleAssets"]
# (key, short name, numerator, denominator)
LEVERAGE_MEASURES = [("debt_to_ebitdar", "Debt / EBITDAR", "adj_debt", "ebitdar"),
                     ("net_debt_to_ebitdar", "Net debt / EBITDAR", "adj_net_debt", "ebitdar")]
# RCF / debt: retained cash flow (operating cash flow − dividends − distributions to noncontrolling interests) /
# year-end lease-adjusted debt. None tagged = none paid.
# One-off cash inflows tagged in (or alongside) operating cash flow, for RCF/Debt trigger T2: (concept, category).
ONE_OFF_INFLOW_CONCEPTS = [("us-gaap:ProceedsFromIncomeTaxRefunds", "tax_refund"),
                           ("us-gaap:ProceedsFromInsuranceSettlementOperatingActivities", "insurance")]
# NCI materiality, for RCF/Debt trigger T3 (distributions to NCI not tagged under a standard concept).
NCI_INCOME_CONCEPTS = ["us-gaap:NetIncomeLossAttributableToNoncontrollingInterest",
                       "ifrs-full:ProfitLossAttributableToNoncontrollingInterests"]
CONSOLIDATED_NET_INCOME_CONCEPTS = [
    "us-gaap:ProfitLoss", "us-gaap:IncomeLossFromContinuingOperationsIncludingPortionAttributableToNoncontrollingInterest",
    "ifrs-full:ProfitLoss"]
PARENT_NET_INCOME_CONCEPTS = ["us-gaap:NetIncomeLoss", "ifrs-full:ProfitLossAttributableToOwnersOfParent"]
NCI_EQUITY_CONCEPTS = ["us-gaap:MinorityInterest", "ifrs-full:NoncontrollingInterests"]
REDEEMABLE_NCI_CONCEPTS = ["us-gaap:RedeemableNoncontrollingInterestEquityCarryingAmount"]
TOTAL_EQUITY_CONCEPTS = ["us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                         "ifrs-full:Equity", "us-gaap:StockholdersEquity"]  # the parent's equity when all NCI is redeemable
# For RCF/Debt trigger T4: the equity statement's distributions to NCI, and purchases of NCI ownership.
NCI_EQUITY_DISTRIBUTION_CONCEPTS = ["us-gaap:MinorityInterestDecreaseFromDistributionsToNoncontrollingInterestHolders"]
NCI_PURCHASE_CONCEPTS = ["us-gaap:PaymentsToAcquireAdditionalInterestInSubsidiaries",
                         "us-gaap:PaymentsForRepurchaseOfRedeemableNoncontrollingInterest",
                         "us-gaap:MinorityInterestDecreaseFromRedemptions",
                         "us-gaap:PaymentsForRepurchaseOfNoncontrollingInterest"]
DIVIDEND_CONCEPTS = ["us-gaap:PaymentsOfDividends", "us-gaap:PaymentsOfDividendsCommonStock",
                     "ifrs-full:DividendsPaidClassifiedAsFinancingActivities"]
NCI_DISTRIBUTION_CONCEPTS = ["us-gaap:PaymentsOfDividendsMinorityInterest", "us-gaap:PaymentsToMinorityShareholders",
                             "us-gaap:MinorityInterestDecreaseFromDistributionsToNoncontrollingInterestHolders"]
BENCHMARK_KEYS = [m[0] for m in LEVERAGE_MEASURES] + ["rcf_to_debt", "ebitdar_margin", "days_sales_outstanding"]
REVENUE_CONCEPTS = ["us-gaap:Revenues", "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                    "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax", "ifrs-full:Revenue"]


def summed(values, labels, concept_groups, period):
    """(source, total) adding the first tagged concept of each group; (None, None) if none is tagged."""
    found = [pick(values, group, period)[:2] for group in concept_groups]
    found = [(c, v) for c, v in found if c]
    if not found:
        return None, None
    return " + ".join(concept_ref(labels, c) for c, _ in found), sum(v for _, v in found)


def balance_sheet_lines(sections):
    """The concepts the balance sheet presents as lines (from the presentation linkbase)."""
    return {li["concept"] for s in sections if s["statement_type"] == "balance_sheet" for li in s["line_items"]}


def total_debt(values, labels, as_at, bs_lines=frozenset()):
    """(source, debt, fallback): borrowings, plus finance leases when the balance sheet shows them separately. The
    current and non-current lines win over a tagged total when the balance sheet presents them (a total can come from
    a note). ``fallback`` says how the debt departs from a tagged total ({"total_skipped": the total's concept and
    value, "finance_leases_added": the amount, "borrowings": the debt before them}), or None (Debt/EBITDAR T4)."""
    current_c, _, _ = pick(values, DEBT_CURRENT_CONCEPTS, as_at)
    groups = ([DEBT_CURRENT_CONCEPTS] if current_c else [["us-gaap:LongTermDebtCurrent"], SHORT_TERM_DEBT_CONCEPTS]) \
        + [DEBT_NONCURRENT_CONCEPTS]
    c, v, _ = pick(values, DEBT_TOTAL_CONCEPTS, as_at)
    skipped = None
    if c and not bs_lines & {x for g in groups for x in g}:
        src = concept_ref(labels, c)
    else:
        total = (concept_ref(labels, c), v) if c else (None, None)
        src, v = summed(values, labels, groups, as_at)
        if src and c:
            skipped = {"concept": concept_ref(labels, c), "value": total[1]}
        if not src:
            src, v = total if total[0] else summed(values, labels, [["us-gaap:LongTermDebt"], SHORT_TERM_DEBT_CONCEPTS],
                                                   as_at)
    borrowings, added = v, None
    if src and bs_lines & set(FINANCE_LEASE_PARTS):
        lease_src, leases = summed(values, labels, [[c] for c in FINANCE_LEASE_PARTS], as_at)
        if lease_src:
            src, v, added = f"{src} + {lease_src}", v + leases, {"concept": lease_src, "value": leases}
    fallback = ({"total_skipped": skipped, "finance_leases_added": added, "borrowings": borrowings}
                if skipped or added else None)
    return src, v, fallback


def operating_lease_liability(values, labels, as_at):
    """(source, operating lease liabilities) at a balance-sheet date; none tagged = none."""
    c, liab, _ = pick(values, OPERATING_LEASE_CONCEPTS, as_at)
    if c:
        return concept_ref(labels, c), liab
    c, liab = summed(values, labels, [[p] for p in OPERATING_LEASE_PARTS], as_at)
    return (c, liab) if c else ("none tagged (taken as 0)", 0.0)


def year_end_balances(values, labels, period, bs_lines=frozenset()):
    """Debt, operating lease liabilities, their sum (lease-adjusted debt) and cash at the balance sheet nearest the
    period's end."""
    balance_concepts = (DEBT_TOTAL_CONCEPTS + DEBT_CURRENT_CONCEPTS + DEBT_NONCURRENT_CONCEPTS + ["us-gaap:LongTermDebt"]
                        + CASH_CONCEPTS)
    as_at = instant_near(values, balance_concepts, date.fromisoformat(period.split("--")[1]))
    debt_c, debt, fallback = total_debt(values, labels, as_at, bs_lines) if as_at else (None, None, None)
    lease_c, leases = operating_lease_liability(values, labels, as_at) if as_at else (None, None)
    cash_c, cash, _ = pick(values, CASH_CONCEPTS, as_at) if as_at else (None, None, None)
    adj_debt = debt + leases if debt is not None else None
    return {"heading": period_label(period), "period": period, "basis": "historical", "as_at": as_at,
            "debt": debt, "debt_concept": debt_c, "debt_fallback": fallback, "op_leases": leases,
            "op_leases_concept": lease_c,
            "adj_debt": adj_debt, "cash": cash, "cash_concept": cash_c}


def debt_to_ebitdar(facts, labels, sections=(), n_periods=3):
    """EBITDAR for each year and lease-adjusted debt / cash at its year-end. Scored later, against the industry
    benchmark."""
    values = plain_values(facts)
    bs_lines = balance_sheet_lines(sections)
    cols = []
    for p in annual_periods(values, PRETAX_CONCEPTS + OPERATING_INCOME_CONCEPTS)[:n_periods]:
        col = ebitda_for(values, labels, p)
        col.update(year_end_balances(values, labels, p, bs_lines))
        ebitda, as_at, adj_debt, cash = col["ebitda"], col["as_at"], col["adj_debt"], col["cash"]
        col["rent_concept"], col["rent"] = rent_for(values, p)
        col["ebitdar"] = ebitda + col["rent"] if ebitda is not None and col["rent"] is not None else None
        col.update(one_offs_for(values, labels, p))
        col["adj_net_debt"] = adj_debt - cash if adj_debt is not None and cash is not None else None
        for key, _, numerator, denominator in LEVERAGE_MEASURES:
            missing = [name for k, name in (("ebit", "EBIT"), ("da", "D&A"), ("rent", "operating lease cost"),
                                             ("debt", "debt"), ("cash", "cash"))
                       if col[k] is None and (k != "cash" or key == "net_debt_to_ebitdar")]
            num, den = col[numerator], col[denominator]
            reason = "No " + " or ".join(missing) + " tagged" if missing else None
            if as_at is None and ebitda is not None:  # a 10-K has only two balance sheets
                reason = "No balance sheet at this year-end in the filing"
            col[key] = {"ratio": num / den if num is not None and den and den > 0 else None,
                        "negative_ebitda": den is not None and den <= 0, "reason": reason}
        cols.append(col)
    return {"columns": cols, "sources": ratio_sources(cols, ("ebit", "da", "rent", "debt", "op_leases", "cash",
                                                             "net_one_offs"), labels)}


def rcf_to_debt(facts, labels, sections=(), wc_lines=None, n_periods=3):
    """RCF (from FFO) / lease-adjusted debt for each year, with the CFO-based figure beside it. Scored later, against
    the industry benchmark."""
    values = plain_values(facts)
    bs_lines = balance_sheet_lines(sections)
    cols = []
    for p in annual_periods(values, OPERATING_CASH_FLOW_CONCEPTS)[:n_periods]:
        col = year_end_balances(values, labels, p, bs_lines)
        col.update(cash_flow_measures(values, p, col["as_at"], col["debt"], col["adj_debt"], wc_lines, labels))
        col["revenue_concept"], col["revenue"], _ = pick(values, REVENUE_CONCEPTS, p)  # RCF/Debt T2's growth test
        cols.append(col)
    return {"columns": cols, "sources": ratio_sources(cols, ("cfo", "wc_change", "dividends", "nci_distributions",
                                                             "debt", "op_leases"), labels)}


def working_capital_change(values, labels, period, wc_lines):
    """(source, change): the cash the year's working-capital changes added to operating cash flow (negative when
    working capital absorbed cash), from the lines the calculation linkbase adds into it; (None, None) if none are
    tagged for the year."""
    found = [(c, w * values[(c, period)][0]) for c, w in (wc_lines or {}).items() if (c, period) in values]
    if not found:
        return None, None
    return (f"{len(found)} working-capital lines of the cash flow statement: "
            + ", ".join(concept_label(labels, c) for c, _ in found)), sum(v for _, v in found)


def ebitdar_margin(facts, labels, n_periods=3):
    """Consolidated EBITDAR / net operating revenue for each year. Scored later, against the industry benchmark."""
    values = plain_values(facts)
    cols = []
    for p in annual_periods(values, PRETAX_CONCEPTS + OPERATING_INCOME_CONCEPTS)[:n_periods]:
        col = ebitda_for(values, labels, p)
        col["rent_concept"], col["rent"] = rent_for(values, p)
        if col["rent"] is None:  # the margin only: without the lease-cost table, cash lease payments, labelled
            paid_c, paid, _ = pick(values, RENT_CASH_CONCEPTS, p)
            if paid_c:
                col["rent_concept"], col["rent"] = f"{paid_c} ({RENT_CASH_LABEL})", abs(paid)
                col["rent_basis"] = RENT_CASH_LABEL
        col["ebitdar"] = (col["ebitda"] + col["rent"] if col["ebitda"] is not None and col["rent"] is not None
                          else None)
        rev_c, revenue, _ = pick(values, REVENUE_CONCEPTS, p)
        missing = [name for k, name in (("ebit", "EBIT"), ("da", "D&A"), ("rent", "operating lease cost"))
                   if col[k] is None] + \
                  (["revenue"] if revenue is None else [])
        col.update({"heading": period_label(p), "period": p, "basis": "historical",
                    "revenue": revenue, "revenue_concept": rev_c,
                    "ratio": col["ebitdar"] / revenue if col["ebitdar"] is not None and revenue and revenue > 0 else None,
                    "reason": "No " + " or ".join(missing) + " tagged" if missing
                    else "No revenue" if not revenue or revenue <= 0 else None})
        cols.append(col)
    return {"columns": cols, "sources": ratio_sources(cols, ("ebit", "da", "rent", "revenue"), labels)}


def cash_flow_measures(values, period, as_at, debt, adj_debt, wc_lines=None, labels=None):
    """RCF / lease-adjusted debt for one year. RCF = FFO − dividends − NCI distributions, FFO being operating cash
    flow before working-capital changes; without tagged working-capital lines it falls back to CFO and says so.
    The CFO-based RCF / debt is kept for comparison."""
    cfo_c, cfo, unit = pick(values, OPERATING_CASH_FLOW_CONCEPTS, period)
    div_c, dividends, _ = pick(values, DIVIDEND_CONCEPTS, period)
    dividends = abs(dividends) if dividends is not None else 0.0
    nci_c, nci, _ = pick(values, NCI_DISTRIBUTION_CONCEPTS, period)
    nci = abs(nci) if nci is not None else 0.0
    wc_c, wc = working_capital_change(values, labels or {}, period, wc_lines) if cfo is not None else (None, None)
    ffo = cfo - wc if wc is not None else cfo
    rcf_cfo = cfo - dividends - nci if cfo is not None else None
    eq_dist_c, eq_dist, _ = pick(values, NCI_EQUITY_DISTRIBUTION_CONCEPTS, period)
    purchases = [c for c in NCI_PURCHASE_CONCEPTS if (c, period) in values and values[(c, period)][0]]
    nci_income_c, nci_income, _ = pick(values, NCI_INCOME_CONCEPTS, period)
    ni_c, net_income, _ = pick(values, CONSOLIDATED_NET_INCOME_CONCEPTS, period)
    if ni_c is None and nci_income is not None:  # parent net income + the NCI share
        parent_c, parent, _ = pick(values, PARENT_NET_INCOME_CONCEPTS, period)
        if parent_c:
            ni_c, net_income = f"{parent_c} + {nci_income_c}", parent + nci_income
    nci_eq = [pick(values, g, as_at)[:2] for g in (NCI_EQUITY_CONCEPTS, REDEEMABLE_NCI_CONCEPTS)] if as_at else []
    nci_eq = [(c, v) for c, v in nci_eq if c]
    eq_c, total_equity, _ = pick(values, TOTAL_EQUITY_CONCEPTS, as_at) if as_at else (None, None, None)
    inflows = [{"concept": c, "category": cat, "value": abs(values[(c, period)][0])}
               for c, cat in ONE_OFF_INFLOW_CONCEPTS if (c, period) in values and values[(c, period)][0]]
    rcf = ffo - dividends - nci if ffo is not None else None
    if cfo is None:
        rcf_reason = "No operating cash flow tagged"
    elif as_at is None:
        rcf_reason = "No balance sheet at this year-end in the filing"
    elif debt is None:
        rcf_reason = "No debt tagged"
    else:
        rcf_reason = None
    return {"unit": unit, "cfo": cfo, "cfo_concept": cfo_c, "dividends": dividends,
            "dividends_concept": div_c or "none tagged (taken as 0)",
            "nci_distributions": nci, "nci_distributions_concept": nci_c or "none tagged (taken as 0)",
            "wc_change": wc, "wc_change_concept": wc_c or ("none tagged: FFO taken as CFO" if cfo is not None else None),
            "ffo": ffo, "ffo_basis": "FFO" if wc is not None else "CFO (no working-capital lines tagged)",
            "tagged_inflows": inflows,
            "nci_distributions_tagged": nci_c is not None,
            "nci_equity_distributions": abs(eq_dist) if eq_dist is not None else None,
            "nci_equity_distributions_concept": eq_dist_c, "nci_purchases_tagged": purchases,
            "nci_income": nci_income, "nci_income_concept": nci_income_c,
            "consolidated_net_income": net_income, "consolidated_net_income_concept": ni_c,
            "nci_equity": sum(v for _, v in nci_eq) if nci_eq else None,
            "nci_equity_concept": " + ".join(c for c, _ in nci_eq) or None,
            "total_equity": total_equity, "total_equity_concept": eq_c,
            "rcf": rcf, "rcf_cfo": rcf_cfo,
            "rcf_to_debt_cfo": rcf_cfo / adj_debt if rcf_reason is None and adj_debt else None,
            "rcf_to_debt": {"ratio": rcf / adj_debt if rcf_reason is None and adj_debt else None, "reason": rcf_reason,
                            "no_debt": rcf_reason is None and not adj_debt}}


# DSO: year-end net (patient) receivables / (net revenue / 365). Scored on the change from the prior year-end, against
# the industry benchmark for the change. The balance sheet's own receivables line wins when several are tagged.
RECEIVABLE_CONCEPTS = ["us-gaap:AccountsReceivableNetCurrent", "us-gaap:ReceivablesNetCurrent",
                       "us-gaap:AccountsReceivableAfterAllowanceForCreditLossCurrent",
                       "ifrs-full:TradeAndOtherCurrentReceivables", "ifrs-full:CurrentTradeReceivables"]


def days_sales_outstanding(facts, labels, sections=(), n_periods=3, earlier=()):
    """DSO at each fiscal year-end and its change from the year before. A 10-K has two balance sheets, so one
    change; `earlier` ((facts, sections) of other years' 10-Ks) adds their year-ends, with this filing's figures
    winning where both have one (they include any restatement)."""
    values = {}
    for other_facts, _ in earlier:
        values.update(plain_values(other_facts))
    values.update(plain_values(facts))
    bs_lines = balance_sheet_lines(sections).union(*(balance_sheet_lines(s) for _, s in earlier))
    concepts = [c for c in RECEIVABLE_CONCEPTS if c in bs_lines] + [c for c in RECEIVABLE_CONCEPTS if c not in bs_lines]
    n_periods += 2 * len(earlier)
    cols = []
    for p in annual_periods(values, REVENUE_CONCEPTS)[:n_periods + 1]:
        as_at = instant_near(values, concepts, date.fromisoformat(p.split("--")[1]))
        rec_c, receivables, unit = pick(values, concepts, as_at) if as_at else (None, None, None)
        rev_c, revenue, rev_unit = pick(values, REVENUE_CONCEPTS, p)
        dso = receivables / (revenue / 365) if receivables is not None and revenue and revenue > 0 else None
        cols.append({"heading": period_label(p), "period": p, "basis": "historical", "unit": unit or rev_unit,
                     "as_at": as_at, "receivables": receivables, "receivables_concept": rec_c, "revenue": revenue,
                     "revenue_concept": rev_c, "dso": dso})
    for col, prior in zip(cols, cols[1:] + [None]):
        col["prior_dso"] = prior["dso"] if prior else None
        col["change"] = col["dso"] - col["prior_dso"] if col["dso"] is not None and col["prior_dso"] is not None else None
        col["reason"] = (None if col["change"] is not None else
                         "No balance sheet at this year-end in the filing" if col["as_at"] is None else
                         "No receivables tagged" if col["receivables"] is None else
                         "No revenue" if col["dso"] is None else
                         "No prior-year DSO: add the prior year's 10-K as a comparison year")
    cols = cols[:n_periods]
    return {"columns": cols, "sources": ratio_sources(cols, ("receivables", "revenue"), labels)}


# Liquidity and debt maturity. Liquidity coverage is S&P's 12-month sources / uses test (Liquidity Descriptors For
# Global Corporate Issuers): ≥2.0x exceptional, ≥1.5x strong, ≥1.2x adequate, ≥1.0x less than adequate, below weak.
# Refinancing coverage sets what the company can call on (cash + revolver availability + two years of free cash
# flow) against the debt maturing in years 1-3 (full weight) and 4-5 (half weight); its bands are internal and
# provisional. The section scores the worse of the two.
MATURITY_BUCKETS = [("y1", "Year 1", r"InNext(?:Rolling)?TwelveMonths|(?:In)?(?:Rolling)?YearOne"),
                    ("y2", "Year 2", r"(?:In)?(?:Rolling)?YearTwo"), ("y3", "Year 3", r"(?:In)?(?:Rolling)?YearThree"),
                    ("y4", "Year 4", r"(?:In)?(?:Rolling)?YearFour"), ("y5", "Year 5", r"(?:In)?(?:Rolling)?YearFive"),
                    ("after", "After year 5", r"After(?:Rolling)?YearFive|Thereafter")]
MATURITY_WEIGHTS = {"y1": 1.0, "y2": 1.0, "y3": 1.0, "y4": 0.5, "y5": 0.5, "after": 0.0}
LIQUIDITY_BANDS = [(2.0, 1, "≥ 2.0x"), (1.5, 3, "1.5–2.0x"), (1.2, 5, "1.2–1.5x"), (1.0, 7, "1.0–1.2x"),
                   (0.8, 9, "0.8–1.0x"), (None, 10, "< 0.8x")]
REFINANCING_BANDS = [(1.5, 1, "≥ 1.5x"), (1.0, 3, "1.0–1.5x"), (0.75, 5, "0.75–1.0x"), (0.5, 7, "0.5–0.75x"),
                     (0.25, 9, "0.25–0.5x"), (None, 10, "< 0.25x")]
REFINANCING_FCF_YEARS = 2  # years of free cash flow counted towards meeting maturities
REVOLVER_CAPACITY_CONCEPTS = ["us-gaap:LineOfCreditFacilityRemainingBorrowingCapacity"]
MANUAL_REVOLVER = "Manual override (Scoring settings)"  # the revolver source when the settings give the availability
_AMOUNT = r"\$\s?([\d,]+(?:\.\d+)?)\s*(million|billion)"
REVOLVER_TEXT = [  # "$824 million available", "$786 million of additional borrowing capacity", "availability of $5.8 billion"
    re.compile(_AMOUNT + r"\s+(?:was\s+|is\s+|were\s+|remained\s+)?(?:available|undrawn|"
               r"of\s+(?:additional\s+|remaining\s+|unused\s+)?(?:borrowing\s+capacity|availability|capacity)|"
               r"in\s+(?:additional\s+)?(?:available\s+)?(?:borrowing\s+)?(?:availability|capacity))", re.I),
    re.compile(r"availab\w*\s+of\s+" + _AMOUNT, re.I)]
# "$5.779 billion and $5.664 billion available as of December 31, 2025 and January 31, 2026": one amount per date.
REVOLVER_PAIR = re.compile(_AMOUNT + r"\s+and\s+" + _AMOUNT + r"\s+(?:of\s+)?availab[^.;]*?(\d{4})[^.;]*?(\d{4})", re.I)
REVOLVER_CONTEXT = re.compile(r"(?i)revolv|asset-based|\bABL\b|credit facilit")
MATURITY_MEMBER = re.compile(r"Face|Principal|Total|Gross")
MATURITY_CONCEPT = re.compile(r"Maturit|RepaymentsOfPrincipal")  # ...AndLeaseObligationsRepaymentsOfPrincipal... (THC)


def maturity_ladder(facts, as_at):
    """({bucket: principal}, source) from the debt maturity table at a balance-sheet date. Tried in order: the
    standard concepts undimensioned, another concept family undimensioned (company-specific tags), then a family
    tagged on one face / principal / total member. Debt and finance leases may be combined, as the filer tags it."""
    families = defaultdict(dict)  # (concept family, dimensions) -> {bucket: value}
    for f in facts:
        local = f["concept"].split(":")[-1]
        if (f["period_key"] != as_at or not isinstance(f["value"], (int, float)) or not MATURITY_CONCEPT.search(local)
                or "Debt" not in local or "AvailableForSale" in local):
            continue
        dims = tuple(sorted(f["dimensions"].items()))
        if dims and (len(dims) > 1 or not MATURITY_MEMBER.search(dims[0][1])):
            continue
        for key, _, pattern in MATURITY_BUCKETS[-1:] + MATURITY_BUCKETS[:-1]:  # "AfterYearFive" before "YearFive"
            hit = re.search(rf"(?:{pattern})$", local)
            if hit:
                families[(f["concept"][:len(f["concept"]) - len(hit.group(0))], dims)].setdefault(key, abs(f["value"]))
                break
    if not families:
        return {}, None
    (family, dims), buckets = min(families.items(), key=lambda it: (
        bool(it[0][1]), not it[0][0].endswith("LongTermDebtMaturitiesRepaymentsOfPrincipal"), -len(it[1])))
    return buckets, f"{family}… (Year 1 … After year 5)" + (f" [{dims[0][1].split(':')[-1]}]" if dims else "")


def revolver_availability(data, as_at):
    """(availability, source) at the year-end: the tagged remaining borrowing capacity, else the amount the 10-K text
    says is available under its revolving / asset-based facilities (Items 7, 1A, 1, 8, in that order), else
    (None, why)."""
    facts = [f for f in data["facts"] if f["concept"] in REVOLVER_CAPACITY_CONCEPTS and f["period_key"] == as_at
             and isinstance(f["value"], (int, float))]
    if facts:
        chosen = [f for f in facts if not f["dimensions"]] or list(
            {tuple(sorted(f["dimensions"].items())): f for f in facts}.values())
        src = f"Tagged: {concept_ref(data['labels'], chosen[0]['concept'])}"
        if len(chosen) > 1:
            src += f", {len(chosen)} facilities summed"
        return sum(f["value"] for f in chosen), src
    year = as_at[:4]

    def amount(num, scale):
        return float(num.replace(",", "")) * (1e9 if scale.lower() == "billion" else 1e6)

    # Sentences that name the year-end's year first, then sentences in a paragraph that does.
    for dated in ("sentence", "paragraph"):
        for item in ("7", "1A", "1", "8"):
            for sec in (s for s in data["document"] if s["item"] == item):
                for line in sec["text"].split("\n"):
                    if year not in line:
                        continue
                    for sent in SENTENCE_SPLIT.split(line):
                        if (dated == "sentence") != (year in sent) or not REVOLVER_CONTEXT.search(sent):
                            continue
                        quote = f"10-K text, {item_ref(item)}: \"{sent.strip()}\""
                        pair = REVOLVER_PAIR.search(sent)
                        if pair:
                            years = pair.group(5), pair.group(6)
                            if year in years:
                                i = years.index(year)
                                return amount(pair.group(1 + 2 * i), pair.group(2 + 2 * i)), quote
                            continue
                        hit = next((h for rx in REVOLVER_TEXT for h in [rx.search(sent)] if h), None)
                        if hit:
                            return amount(hit.group(1), hit.group(2)), quote
    return None, "Not tagged and not found in the 10-K text: left out (conservative)"


def liquidity_and_maturity(data):
    """The latest year-end's 12-month liquidity sources and uses and its debt maturity table. Coverage and the scores
    are worked out at scoring time, so the revolver availability can be overridden later."""
    facts, labels = data["facts"], data["labels"]
    values = plain_values(facts)
    periods = annual_periods(values, OPERATING_CASH_FLOW_CONCEPTS)
    if not periods:
        return {"columns": [], "sources": {}}
    p = periods[0]
    col = year_end_balances(values, labels, p, balance_sheet_lines(data["sections"]))
    as_at = col["as_at"]
    col.update(cash_flow_measures(values, p, as_at, col["debt"], col["adj_debt"]))
    col.pop("rcf_to_debt", None)
    capex_c, capex, _ = pick(values, CAPEX_CONCEPTS, p)
    ladder, ladder_src = maturity_ladder(facts, as_at) if as_at else ({}, None)
    y1_src = ladder_src if "y1" in ladder else None
    if "y1" not in ladder and as_at:  # no year 1 in the table: the current portion of debt
        c, v, _ = pick(values, PRINCIPAL_CONCEPTS, as_at)
        if c:
            ladder["y1"], y1_src = abs(v), f"{concept_ref(labels, c)} (year 1 isn't in the maturity table)"
    revolver, revolver_src = revolver_availability(data, as_at) if as_at else (None, None)
    set_ladder(col, ladder, ladder_src, y1_src)
    col.update({"heading": f"Next 12M from {as_at or p.split('--')[1]}", "capex": abs(capex) if capex is not None else None,
                "capex_concept": capex_c, "revolver_found": revolver, "revolver_found_source": revolver_src})
    return {"columns": [col], "sources": ratio_sources([col], ("cash", "cfo", "capex", "dividends", "nci_distributions",
                                                               "debt"), labels)}


def set_ladder(col, ladder, ladder_src, y1_src):
    """Put a maturity table ({bucket: principal}) on a liquidity column, with its weighted years 1-5 and their share
    of the total."""
    col.update({key: ladder.get(key) for key, _, _ in MATURITY_BUCKETS})
    weighted = sum(MATURITY_WEIGHTS[k] * v for k, v in ladder.items())
    # The table's total when it runs to "after year 5"; otherwise year-end total debt.
    total = sum(ladder.values()) if "after" in ladder else col["debt"]
    col.update({"ladder_source": ladder_src, "y1_source": y1_src,
                "weighted_maturities": weighted if ladder else None, "ladder_total": total if ladder else None,
                "concentration": weighted / total if ladder and len(ladder) > 1 and total else None})


def score_liquidity(col, override, blocked=None):
    """Coverage, both scores and the section's score (the worse of the two) for one column; `override` is a
    revolver availability in millions, or None. `blocked` (why) sets the revolver availability to 0 in both tests:
    lenders can block revolver draws after a covenant breach."""
    if blocked:
        col["revolver"], col["revolver_source"] = 0.0, f"Set to 0: {blocked} (found: " + (
            f"{col['revolver_found'] / 1e6:,.1f} million, {col['revolver_found_source']})"
            if col["revolver_found"] is not None else f"{col['revolver_found_source']})")
    elif override is not None:
        col["revolver"], col["revolver_source"] = override * 1e6, MANUAL_REVOLVER
    else:
        col["revolver"], col["revolver_source"] = col["revolver_found"], col["revolver_found_source"]
    rev = col["revolver"] or 0.0
    have_sources = None not in (col["cash"], col["cfo"])
    have_uses = None not in (col["y1"], col["capex"])
    col["sources_total"] = col["cash"] + rev + col["cfo"] if have_sources else None
    col["uses_total"] = col["y1"] + col["capex"] + col["dividends"] + col["nci_distributions"] if have_uses else None
    if not col["as_at"]:
        col["coverage"], col["coverage_reason"] = None, "No balance sheet at this year-end in the filing"
    elif not have_sources or not have_uses:
        missing = [n for k, n in (("cash", "cash"), ("cfo", "operating cash flow"), ("y1", "debt due in 12 months"),
                                  ("capex", "CapEx")) if col[k] is None]
        col["coverage"], col["coverage_reason"] = None, "No " + " or ".join(missing) + " tagged"
    else:
        col["coverage"] = col["sources_total"] / col["uses_total"] if col["uses_total"] > 0 else float("inf")
        col["coverage_reason"] = None
    col["coverage_score"] = risk_score(col["coverage"], LIQUIDITY_BANDS)
    # Refinancing: cash + revolver + two years of free cash flow (negative FCF draws liquidity down) against the
    # weighted maturities of years 1-5.
    col["fcf"] = (col["cfo"] - col["capex"] - col["dividends"] - col["nci_distributions"]
                  if None not in (col["cfo"], col["capex"]) else None)
    col["refi_sources"] = (col["cash"] + rev + REFINANCING_FCF_YEARS * col["fcf"]
                           if None not in (col["cash"], col["fcf"]) else None)
    weighted = col["weighted_maturities"]
    if weighted is None or col["concentration"] is None:
        col["refinancing"], col["refinancing_reason"] = None, "No debt maturity table tagged"
    elif col["refi_sources"] is None:
        col["refinancing"], col["refinancing_reason"] = None, "No cash, operating cash flow or CapEx tagged"
    else:
        col["refinancing"] = col["refi_sources"] / weighted if weighted > 0 else float("inf")
        col["refinancing_reason"] = None
    col["refinancing_score"] = risk_score(col["refinancing"], REFINANCING_BANDS)
    scores = [s for s in (col["coverage_score"], col["refinancing_score"]) if s is not None]
    col["score"] = max(scores) if scores else None
    col["reason"] = None if scores else "Neither score available"
    col["ratio"] = col["coverage"]


def build_financial_overview(data):
    """The unscored measures, in the FO project's order (going concern not yet ported)."""
    facts, labels = data["facts"], data["labels"]
    return [
        {
            "key": "dscr",
            "title": "Debt service coverage ratio (DSCR)",
            "short": "DSCR",
            "formula": "(EBITDAR − cash taxes − maintenance CapEx − NCI distributions) / "
                       "(interest incurred + scheduled principal + rent)",
            "bands": DSCR_BANDS,
            **debt_service_coverage(facts, labels, data["sections"]),
            "rows": [("ebitda", "EBITDA"), ("rent", "Rent"), ("ebitdar", "EBITDAR"), ("cash_taxes", "Cash taxes"),
                     ("capex", "Maintenance CapEx"), ("nci_distributions", "Distributions to NCI"),
                     ("cash_flow", "Cash flow for debt service"), ("interest", "Interest incurred"),
                     ("interest_capitalised", "of which capitalised"), ("principal", "Principal due"),
                     ("debt_service", "Debt service + rent")],
            "extra_rows": [("principal_as_at", "Principal due as at", "not tagged")],
            "source_parts": [("ebit", "EBIT"), ("da", "D&A"), ("rent", "Rent"), ("cash_taxes", "Cash taxes"),
                             ("capex", "Maintenance CapEx"), ("nci_distributions", "Distributions to NCI"),
                             ("interest", "Interest incurred"), ("principal", "Principal due"),
                             ("depreciation", "Depreciation (trigger T2)"),
                             ("current_tax", "Current tax expense (trigger T3)")],
            "empty": "No annual pre-tax income or operating income is tagged in this filing.",
            "notes": "A fixed-charge DSCR: rent is treated as a fixed obligation alongside debt service. Cash flow for "
                     "debt service is EBITDAR (EBIT + D&A + rent) less income taxes paid (net of refunds, from the cash "
                     "flow statement), maintenance CapEx and distributions to noncontrolling interests (none tagged = "
                     "none). Rent is the operating lease cost from the lease-cost table (never cash paid); with no "
                     "operating leases it is none, and with leases but no lease cost tagged it can't be found (n/a). "
                     "Filers don't tag maintenance CapEx, so total CapEx (purchases of PP&E) is used, which understates "
                     "DSCR for a company investing for growth (DSCR trigger T2 checks this when CapEx is above 1.5× "
                     "depreciation). "
                     "Interest is interest incurred (expensed + capitalised), the same figure as ICR. Year-1 principal "
                     "is the scheduled amortisation in year 1 of the debt maturity table plus the current finance-lease "
                     "principal (unless the table already includes finance leases). 364-day and securitisation "
                     "facilities maturing within 12 months stay in principal unless a committed term runs beyond 12 "
                     "months (DSCR trigger T1 checks this). Without a tagged maturity table, the current portion of "
                     "debt is used. Historical columns set each year's cash flow, interest and rent against the "
                     "principal that was due within 12 months at the start of that year; the forward column sets the "
                     "latest year against principal and operating lease payments due in the 12 months after it. "
                     f"Risk score (10 = highest risk): {bands_text(DSCR_BANDS)}. These bands are provisional.",
            "gaps": "A 10-K tags only its own year-end's maturity table, so historical columns usually fall back to the "
                    "prior year-end's current portion of debt; where that is Debt, Current it also counts short-term "
                    "borrowings such as commercial paper. Capitalised interest under a company's own tag is not "
                    "picked up: check the debt or PP&E note. Covenant DSCR definitions (often EBITDA-based, with "
                    "add-backs) are in the credit agreement, filed as an 8-K or 10-K exhibit.",
        },
        {
            "key": "icr",
            "title": "Interest coverage ratio (ICR)",
            "short": "ICR",
            "formula": "(EBIT + rent) / (interest incurred + rent)",
            "bands": ICR_BANDS,
            **interest_coverage(facts, labels),
            "rows": [("ebit", "EBIT"), ("rent", "Rent"), ("ebitr", "EBIT + rent"), ("interest", "Interest incurred"),
                     ("fixed_charges", "Interest + rent")],
            "extra_rows": [],
            "source_parts": [("ebit", "EBIT"), ("rent", "Rent"), ("interest", "Interest incurred")],
            "empty": "No annual pre-tax income or operating income is tagged in this filing.",
            "notes": "Fixed-charge cover: rent is added to both sides, so a company that leases its facilities "
                     "compares with one that owns them and borrows. EBIT is pre-tax income from continuing operations "
                     "plus interest expense, so it includes non-operating items such as investment income; where "
                     "pre-tax income isn't tagged, operating income is used. Interest is interest incurred: the tagged "
                     "Interest Costs Incurred, else the income-statement (accrual) expense plus capitalised interest "
                     "(none tagged = none), never cash interest paid; DSCR uses the same figure. Rent is the operating "
                     "lease cost (never cash paid); n/a when leases are tagged but their cost isn't. Risk score (10 = "
                     "highest "
                     f"risk): {bands_text(ICR_BANDS)}. These bands are provisional.",
            "gaps": "Interest expense may be shown net of interest income by some filers (check the label in Sources). "
                    "Capitalised interest under a company's own tag is not picked up: check the debt or PP&E note. "
                    "Covenant ICR definitions (often EBITDA / net cash interest) are in the credit agreement.",
        },
        {
            "key": "leverage",
            "title": "Debt to EBITDAR",
            "short": "Debt / EBITDAR",
            "formula": "(Total debt + operating lease liabilities) / EBITDAR and "
                       "(total debt + operating lease liabilities − unrestricted cash) / EBITDAR",
            **debt_to_ebitdar(facts, labels, data["sections"]),
            "rows": [("ebit", "EBIT"), ("da", "D&A"), ("ebitda", "EBITDA"), ("rent", "Rent"), ("ebitdar", "EBITDAR"),
                     ("debt", "Total debt"), ("op_leases", "Operating lease liab."), ("adj_debt", "Debt + op. leases"),
                     ("cash", "Unrestricted cash"), ("adj_net_debt", "Net debt + op. leases"),
                     ("net_one_offs", "Net one-offs as tagged (trigger T1)")],
            "source_parts": [("ebit", "EBIT"), ("da", "D&A"), ("rent", "Rent"), ("debt", "Total debt"),
                             ("op_leases", "Operating lease liabilities"), ("cash", "Cash"),
                             ("net_one_offs", "Net one-offs (trigger T1)")],
            "empty": "No annual pre-tax income or operating income is tagged in this filing.",
            "notes": "EBITDAR is EBIT (as for ICR: pre-tax income + interest expense, else operating income) plus "
                     "depreciation and amortisation from the cash flow statement plus rent (the year's operating lease "
                     "cost from the lease-cost table, never cash paid; n/a when leases are tagged but their cost isn't). "
                     "Debt, leases and cash are at the year-end, at carrying amount: total "
                     "debt is borrowings + finance leases: the tagged total (debt, or debt and finance leases "
                     "combined), else current + non-current debt (incl. commercial paper and short-term borrowings); "
                     "finance lease liabilities are added only when the balance sheet shows them as separate lines. "
                     "Operating lease liabilities (ASC 842) are added to debt (none tagged = none), so lessees and owners of "
                     "their facilities compare on the same footing. Unrestricted cash is cash and cash equivalents "
                     "(restricted cash is never netted against debt). Each ratio is scored against the benchmark for the "
                     "company's industry group (config/financial_overview/Benchmarks.json) with "
                     "r = 1 + 9 / (1 + e^-(k·g + c)), where g = ln(company / benchmark) / ln(m), capped at ±cap; "
                     "c = L(p), k = L(p + Δ) − L(p) when g > 0 and L(p) − L(p − Δ) when g < 0, L(x) = ln((x − 1) / "
                     "(10 − x)). At the benchmark the score is p; m times worse scores p + Δ; m times better p − Δ. "
                     "Net debt ≤ 0 (net cash) scores 1 and no ratio is computed (debt ≤ 0 the same); EBITDAR ≤ 0 (after "
                     "any trigger T1 normalisation) scores 10 on both ratios, no ratio is computed, and the column is "
                     "flagged \"EBITDAR ≤ 0\". A negative value is a real value, not n/a: n/a only when an input "
                     "isn't tagged. "
                     "Net one-offs as tagged (impairments, restructuring, litigation, acquisition and integration "
                     "costs, less gains on disposals and litigation settlements) are shown for trigger T1: above 5% "
                     "of EBITDAR, or with EBITDAR ≤ 0, the Trigger Agent checks them and EBITDAR may be normalised "
                     "or replaced by the 3-year average (2-year for a smaller reporting company). When the Debt / EBITDAR and Net debt / EBITDAR scores are 2 or more apart, trigger T2 checks the cash for earmarks and trapping: quantified acquisition or payout earmarks and trapped cash come out of cash. "
                     "Composite risk score = net debt score + 1 if (debt score − net debt score) ≥ 2, else the "
                     "net debt score (capped at 10).",
            "gaps": "Finance leases inside other liabilities (not shown on the balance sheet) and failed sale-leaseback "
                    "financing obligations (often custom-tagged) aren't counted; receivables securitisation facilities "
                    "and lease guarantees are in the debt and lease notes. Short-term investments (e.g. captive "
                    "insurance) aren't netted against debt. Adjusted EBITDA (excluding one-offs such as acquired IPR&D "
                    "or impairments) is in earnings releases; covenant definitions are in the credit agreement.",
        },
        {
            "key": "rcf",
            "title": "RCF to debt",
            "short": "RCF / Debt",
            "formula": "(FFO − dividends − NCI distributions) / (total debt + operating lease liabilities), "
                       "FFO = CFO − working-capital changes",
            **rcf_to_debt(facts, labels, data["sections"], data.get("working_capital")),
            "rows": [("cfo", "Operating cash flow (CFO)"), ("wc_change", "Working-capital changes"),
                     ("ffo", "FFO (CFO − WC changes)"), ("dividends", "Dividends paid"),
                     ("nci_distributions", "Distributions to NCI"), ("rcf", "RCF (FFO-based)"), ("debt", "Total debt"),
                     ("op_leases", "Operating lease liab."), ("adj_debt", "Debt + op. leases"),
                     ("rcf_cfo", "RCF (CFO-based)")],
            "source_parts": [("cfo", "Operating cash flow"), ("wc_change", "Working-capital changes"),
                             ("dividends", "Dividends paid"), ("nci_distributions", "Distributions to NCI"),
                             ("debt", "Total debt"), ("op_leases", "Operating lease liabilities")],
            "empty": "No annual operating cash flow is tagged in this filing.",
            "notes": "RCF / debt is (FFO − dividends paid − distributions to noncontrolling interests) / year-end "
                     "(total debt + operating lease liabilities); none tagged = none paid. FFO, as the rating agencies "
                     "define it, is operating cash flow before working-capital changes: CFO less the changes in "
                     "operating assets and liabilities, i.e. the IncreaseDecreaseIn... lines the filing's calculation "
                     "linkbase adds into CFO, each signed by its calculation weight (a working-capital subtotal counts "
                     "once). Without tagged working-capital lines FFO falls back to CFO, as the FFO row's source says. "
                     "The CFO-based RCF / debt is shown for comparison. Debt and leases are as for Debt to EBITDAR. It "
                     "is scored against the industry benchmark in percentage points: g = (benchmark − company) / D, "
                     "capped at ±cap, then the same curve as Debt to EBITDAR (at the benchmark the score is p; D points "
                     "worse scores p + Δ). Higher RCF / debt is lower risk. No debt with positive RCF scores as the "
                     "best capped gap.",
            "gaps": "Which cash-flow lines count as working capital follows the filer's statement layout. S&P also adds "
                    "the depreciation-like part of rent back to cash flow when it lease-adjusts debt; this doesn't, so "
                    "RCF is conservative for heavy lessees. NCI distributions under a company's own tag aren't picked "
                    "up: check the financing section of the cash flow statement.",
        },
        {
            "key": "liquidity",
            "title": "Liquidity and debt maturity",
            "short": "Liquidity",
            "formula": "Worse of: liquidity coverage = (cash + revolver availability + CFO) / (debt due in 12 months + "
                       "CapEx + dividends + NCI distributions), and refinancing coverage = (cash + revolver "
                       "availability + 2 × free cash flow) / (debt due in years 1–3 + ½ × years 4–5)",
            **liquidity_and_maturity(data),
            "rows": [("cash", "Cash"), ("revolver", "Revolver availability"), ("cfo", "Operating cash flow (CFO)"),
                     ("sources_total", "Liquidity sources"), ("y1", "Debt due in 12 months"), ("capex", "CapEx"),
                     ("dividends", "Dividends paid"), ("nci_distributions", "Distributions to NCI"),
                     ("uses_total", "Liquidity uses")],
            "source_parts": [("cash", "Cash"), ("cfo", "Operating cash flow"), ("capex", "CapEx"),
                             ("dividends", "Dividends paid"), ("nci_distributions", "Distributions to NCI"),
                             ("debt", "Total debt")],
            "empty": "No annual operating cash flow is tagged in this filing.",
            "notes": "Two tests, and the section scores the worse (liquidity is a weakest-link test). Liquidity "
                     "coverage follows S&P Global Ratings' sources / uses test over the 12 months after the year-end: "
                     "sources are year-end cash, revolver availability and the latest year's operating cash flow; uses "
                     "are debt due within 12 months (year 1 of the maturity table, else the current portion of debt), "
                     "CapEx, dividends and distributions to noncontrolling interests. Revolver availability is the "
                     "tagged remaining borrowing capacity, else the amount the 10-K text states is available (MD&A "
                     "first); if neither is found it's left out, which understates liquidity. Bands: "
                     + ", ".join(f"{r} → {s}" for _, s, r in LIQUIDITY_BANDS)
                     + ". Refinancing coverage: cash + revolver availability "
                     f"+ {REFINANCING_FCF_YEARS} years of free cash flow (CFO − CapEx − dividends − NCI distributions) "
                     "over the debt maturity table's years 1–3 at full weight and years 4–5 at half weight. Bands "
                     "(internal, provisional): " + ", ".join(f"{r} → {s}" for _, s, r in REFINANCING_BANDS) + ".",
            "gaps": "Simplified against S&P: CFO stands in for FFO, total CapEx for maintenance CapEx, and S&P's "
                    "stress tests, covenant headroom and bank relationships are left out. Revolver availability can be "
                    "capped by a borrowing base or by covenants. Year 1 maturities include short-term borrowings that "
                    "are usually rolled over. Refinancings after the year-end aren't reflected: check Subsequent Events "
                    "and the MD&A liquidity section.",
        },
        {
            "key": "margin",
            "title": "EBITDAR margin",
            "short": "EBITDAR margin",
            "formula": "(EBIT + D&A + operating lease cost) / net operating revenue",
            **ebitdar_margin(facts, labels),
            "rows": [("ebit", "EBIT"), ("da", "D&A"), ("ebitda", "EBITDA"), ("rent", "Rent"), ("ebitdar", "EBITDAR"),
                     ("revenue", "Net operating revenue")],
            "source_parts": [("ebit", "EBIT"), ("da", "D&A"), ("rent", "Rent"), ("revenue", "Net operating revenue")],
            "empty": "No annual pre-tax income or operating income is tagged in this filing.",
            "notes": "Consolidated EBITDAR, as for Debt to EBITDAR, over net operating revenue (the income statement's "
                     "revenue line, undimensioned). Unlike Debt to EBITDAR, the margin falls back to cash paid for "
                     "operating leases when the lease-cost table is missing (labelled on the column). Adding back rent puts companies that lease their facilities on the "
                     "same footing as owners. The margin is scored against the industry benchmark in percentage "
                     "points, as for RCF to debt: g = (benchmark − company) / D, capped at ±cap, then the same curve. "
                     "A higher margin is lower risk; a negative margin is scored on the same scale (capped).",
            "gaps": "EBIT here includes non-operating items such as investment income (pre-tax income + interest). "
                    "Revenue includes the noncontrolling partners' share of consolidated joint ventures. Adjusted "
                    "EBITDA margins are in earnings releases; segment margins are in the segment note.",
        },
        {
            "key": "dso",
            "title": "Days sales outstanding (DSO)",
            "short": "DSO change",
            "formula": "Net patient receivables / (net patient revenue / 365); scored on the change from the prior year",
            **days_sales_outstanding(facts, labels, data["sections"]),
            "rows": [("receivables", "Net receivables"), ("revenue", "Net revenue")],
            "source_parts": [("receivables", "Net receivables"), ("revenue", "Net revenue")],
            "empty": "No annual revenue is tagged in this filing.",
            "notes": "DSO is year-end net receivables (the balance sheet's line, net of allowances) / (the year's net "
                     "revenue / 365). The score uses the change in days from the prior year-end (current DSO − prior "
                     "DSO): rising DSO flags collection problems. It is scored against the industry benchmark for the "
                     "change: g = (change − benchmark) / D, with D in days, capped at ±cap, then the same curve as "
                     "Debt to EBITDAR. More days is higher risk. A 10-K has two balance sheets, so on its own only the "
                     "latest year has a change.",
            "gaps": "Receivables and revenue are the totals, so non-patient revenue and receivables are included. "
                    "Acquisitions or divestitures near year-end distort it. Receivables sold or securitised off balance "
                    "sheet lower DSO. For operators paid in advance, DSO is small and barely informative. Comparison "
                    "years (earlier 10-Ks) are a later extra.",
        },
    ]
