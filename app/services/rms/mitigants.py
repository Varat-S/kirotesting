"""Mitigant rules: each signal that fires adds one protection to the structure.

A rule reads FO measure risk scores (1-10), FO trigger and pre-check outcomes, and BO findings. Rules are
deterministic and independent: the same inputs always give the same list, in rule order. Keyword rules on the
BO's material risks are heuristic pattern checks, flagged ``heuristic: true``.
"""

from __future__ import annotations

from .policy import (
    DSCR_SCORE_TRIGGER, DSO_SCORE_TRIGGER, GUARANTOR_COVERAGE_MIN, ICR_SCORE_TRIGGER, LIQUIDITY_SCORE_TRIGGER,
    MARGIN_SCORE_TRIGGER, MAX_LTV, RCF_SCORE_TRIGGER, SUBORDINATION_DRAG_SECURED,
)

REGULATORY_WORDS = ("regulat", "reimburse", "medicare", "medicaid", "payer", "payor")
LITIGATION_WORDS = ("litigation", "lawsuit", "settlement", "investigation", "legal proceeding")


def _at_least(score, trigger):
    return score is not None and score >= trigger


def _mentions(bo, words):
    """Claim ids of the BO material risks / weaknesses whose title or text mentions one of ``words``."""
    return [cid for cid, title, text in bo.vulnerabilities
            if any(w in f"{title} {text}".lower() for w in words)]


def mitigants(fo, bo, *, secured):
    """[{rule_id, category, trigger, mitigant}] for the signals that fire. ``category`` is covenant, structure,
    collateral, reporting or condition_precedent."""
    out = []

    def add(rule_id, category, trigger, mitigant, **extra):
        out.append({"rule_id": rule_id, "category": category, "trigger": trigger, "mitigant": mitigant, **extra})

    s = fo.scores
    if _at_least(s.get("liquidity"), LIQUIDITY_SCORE_TRIGGER):
        add("RMS-M01", "covenant", f"FO liquidity and maturity score {s['liquidity']}",
            "Minimum liquidity covenant (cash + undrawn committed revolver), tested quarterly")
    if "liquidity_t3" in fo.triggers_fired:
        add("RMS-M02", "structure", "FO Liquidity T3: a single year holds 25% or more of the maturities in years 2-5",
            "Final maturity set away from the maturity-wall year, never co-terminous with it")
    if _at_least(s.get("dscr"), DSCR_SCORE_TRIGGER):
        add("RMS-M03", "covenant", f"FO DSCR score {s['dscr']}",
            "Minimum DSCR covenant and a debt-service reserve account (6 months of debt service)")
    if _at_least(s.get("icr"), ICR_SCORE_TRIGGER):
        add("RMS-M04", "covenant", f"FO ICR score {s['icr']}",
            "Interest cover floor; at least 50% of floating-rate debt hedged")
    debt_funded = "debt-funded" in bo.distributions_vs_leverage.lower()
    if _at_least(s.get("rcf"), RCF_SCORE_TRIGGER) or debt_funded:
        add("RMS-M05", "covenant",
            f"FO RCF / debt score {s.get('rcf')}" if not debt_funded
            else "M4 Deliverable 3: shareholder distributions partly debt-funded",
            "Restricted-payments basket: dividends and buybacks only while pro forma Debt / EBITDAR stays inside "
            "the leverage covenant")
    if _at_least(s.get("margin"), MARGIN_SCORE_TRIGGER):
        add("RMS-M06", "reporting", f"FO EBITDAR margin score {s['margin']}",
            "Quarterly EBITDAR margin and capex reporting; a pricing step-up if the margin falls")
    if _at_least(s.get("dso"), DSO_SCORE_TRIGGER):
        add("RMS-M07", "reporting", f"FO DSO change score {s['dso']}",
            "Monthly A/R ageing by payer; any revolving tranche limited to an A/R borrowing base")
    if secured:
        add("RMS-M08", "collateral", "Secured placement",
            "First-lien mortgages on owned hospital campuses and a pledge of commercial-payer receivables. "
            "Medicare and Medicaid receivables can't be assigned to a lender (anti-assignment rules): capture "
            "them through a deposit-account control (double-lockbox) arrangement")
        add("RMS-M09", "condition_precedent", "Secured placement",
            f"Independent collateral valuation before drawdown; loan-to-value at most {MAX_LTV:.0%}")
    if fo.covenant_status == "relief" or bo.distress_events:
        add("RMS-M10", "reporting",
            "FO covenant pre-check: relief or a long waiver" if fo.covenant_status == "relief"
            else "M4: historical refinancing distress",
            "Monthly compliance certificates; covenant waivers and amendments need this lender's consent")
    if fo.acquisitions_status in ("adjusted", "unresolved") or bo.adverse_strategy_findings:
        add("RMS-M11", "covenant",
            f"FO acquisitions pre-check {fo.acquisitions_status}" if fo.acquisitions_status in
            ("adjusted", "unresolved") else "M4 strategy findings on acquisitions or integration",
            "Acquisition basket; a pro forma leverage test before each acquisition")
    leakage = [d for _, d in bo.m3_findings if d in ("minority_or_jv", "cash_flow_access")]
    nci = sorted(fo.triggers_fired & {"rcf_t3", "rcf_t4"})
    if leakage or nci:
        add("RMS-M12", "structure",
            "; ".join([*(f"M3 {d} finding" for d in leakage), *(f"FO {t.upper()} (NCI distributions)" for t in nci)]),
            "Ring-fencing: limits on cash leaving to JVs and minority partners; NCI distributions inside the "
            "restricted-payments basket")
    weak_guarantees = any(d == "guarantor_coverage" for _, d in bo.m3_findings)
    drag = bo.subordination_drag_ratio
    if weak_guarantees or (drag is not None and drag > SUBORDINATION_DRAG_SECURED):
        add("RMS-M13", "structure",
            "M3 guarantor-coverage finding" if weak_guarantees
            else f"M4 structural subordination drag ratio {drag:.2f}",
            f"Upstream guarantees from the operating subsidiaries that hold the EBITDA; guarantors at least "
            f"{GUARANTOR_COVERAGE_MIN:.0%} of consolidated EBITDA, tested annually")
    regulatory = _mentions(bo, REGULATORY_WORDS)
    if regulatory:
        add("RMS-M14", "reporting", f"BO material risk(s) {', '.join(regulatory)}: regulatory or reimbursement",
            "Event of default on loss of Medicare / Medicaid certification or a material payer contract; "
            "reporting of CMS survey findings and regulatory actions", heuristic=True)
    litigation = _mentions(bo, LITIGATION_WORDS)
    if litigation:
        add("RMS-M15", "reporting", f"BO material risk(s) {', '.join(litigation)}: litigation",
            "Litigation and settlement reporting; event of default on a material unpaid judgment", heuristic=True)
    return out
