"""Mitigant rules: each signal that fires adds one protection to the structure.

A rule reads FO measure risk scores (1-10), FO trigger and pre-check outcomes, and BO findings. Rules are
deterministic and independent: the same inputs always give the same list, in rule order. Each BO material risk
gets one category: the one the BO declares (``risk_category``), else the first whose words its title holds, else
the first in its text. A category read from words is a heuristic, flagged ``heuristic: true``.
"""

from __future__ import annotations

from .policy import (
    DSCR_SCORE_TRIGGER, DSO_SCORE_TRIGGER, GUARANTOR_COVERAGE_MIN, ICR_SCORE_TRIGGER, LIQUIDITY_SCORE_TRIGGER,
    MARGIN_SCORE_TRIGGER, MAX_LTV, RCF_SCORE_TRIGGER, SUBORDINATION_DRAG_SECURED,
)

# In priority order: a risk is filed under the first category that matches.
RISK_CATEGORIES = (
    ("litigation", ("litigation", "lawsuit", "settlement", "investigation", "legal proceeding")),
    ("regulatory", ("regulat", "reimburse", "medicare", "medicaid", "government")),
    ("geographic_concentration", ("geographic", "regional concentration", "hurricane", "weather")),
    ("payer_concentration", ("payer", "payor", "narrow network", "managed care", "commercial insur")),
)


def _at_least(score, trigger):
    return score is not None and score >= trigger


def risk_category(bo, claim_id, title, text):
    """(category, heuristic) of one BO material risk, or (None, False)."""
    declared = dict(bo.risk_categories).get(claim_id)
    if declared:
        return declared, False
    for haystack in (title.lower(), text.lower()):
        for category, words in RISK_CATEGORIES:
            if any(w in haystack for w in words):
                return category, True
    return None, False


def _risks(bo, category):
    """(claim ids, heuristic) of the BO material risks filed under ``category``."""
    found = [(cid, heuristic) for cid, title, text in bo.vulnerabilities
             for cat, heuristic in [risk_category(bo, cid, title, text)] if cat == category]
    return [cid for cid, _ in found], any(h for _, h in found)


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
    debt_funded = bo.distributions_debt_funded
    if _at_least(s.get("rcf"), RCF_SCORE_TRIGGER) or debt_funded:
        total = "" if bo.distributions_3y_m is None else f" (${bo.distributions_3y_m:,.0f}M over 3 years)"
        add("RMS-M05", "covenant",
            f"FO RCF / debt score {s.get('rcf')}" if not debt_funded
            else f"M4 Deliverable 3: shareholder distributions partly debt-funded{total}",
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
    structure = bo.m3_structure
    simulated = " (simulated M3 structure)" if structure and structure.simulated else ""
    leakage = [d for _, d in bo.m3_findings if d in ("minority_or_jv", "cash_flow_access")]
    partly = list(structure.partly_owned) if structure else []
    nci = sorted(fo.triggers_fired & {"rcf_t3", "rcf_t4"})
    if leakage or partly or nci:
        add("RMS-M12", "structure",
            "; ".join([*(f"M3 {d} finding" for d in leakage),
                       *([f"M3 structure: {len(partly)} entities held below 100%{simulated}"] if partly else []),
                       *(f"FO {t.upper()} (NCI distributions)" for t in nci)]),
            "Ring-fencing: limits on cash leaving to JVs and minority partners; NCI distributions inside the "
            "restricted-payments basket"
            + ("" if not partly else "; covers " + ", ".join(f"{n} ({p:g}%)" for n, p in partly)))
    weak_guarantees = any(d == "guarantor_coverage" for _, d in bo.m3_findings)
    outside = list(structure.non_guarantor_opcos) if structure else []
    drag = bo.subordination_drag_ratio
    if weak_guarantees or outside or (drag is not None and drag > SUBORDINATION_DRAG_SECURED):
        add("RMS-M13", "structure",
            "M3 guarantor-coverage finding" if weak_guarantees
            else f"M3 structure: {len(outside)} operating companies outside the guarantor group{simulated}"
            if outside else f"M4 structural subordination drag ratio {drag:.2f}",
            f"Upstream guarantees from the operating subsidiaries that hold the EBITDA; guarantors at least "
            f"{GUARANTOR_COVERAGE_MIN:.0%} of consolidated EBITDA, tested annually"
            + ("" if not outside else "; non-guarantors: " + ", ".join(outside)))
    regulatory, heuristic = _risks(bo, "regulatory")
    if regulatory:
        add("RMS-M14", "reporting", f"BO material risk(s) {', '.join(regulatory)}: regulatory or reimbursement",
            "Event of default on loss of Medicare / Medicaid certification; reporting of CMS survey findings and "
            "regulatory actions", heuristic=heuristic)
    litigation, heuristic = _risks(bo, "litigation")
    if litigation:
        add("RMS-M15", "reporting", f"BO material risk(s) {', '.join(litigation)}: litigation",
            "Litigation and settlement reporting; event of default on a material unpaid judgment",
            heuristic=heuristic)
    geographic, heuristic = _risks(bo, "geographic_concentration")
    if geographic:
        add("RMS-M16", "covenant", f"BO material risk(s) {', '.join(geographic)}: geographic concentration",
            "Property and business-interruption insurance (named-windstorm cover) on the concentrated markets, "
            "this lender as loss payee; revenue and EBITDAR reported by state", heuristic=heuristic)
    payer, heuristic = _risks(bo, "payer_concentration")
    if payer:
        add("RMS-M17", "reporting", f"BO material risk(s) {', '.join(payer)}: payer concentration",
            "Quarterly payer-mix reporting; notice of the loss or non-renewal of a material commercial payer "
            "contract", heuristic=heuristic)
    if bo.m4_cp_outstanding_m and bo.m4_revolver_available_m is not None:
        add("RMS-M18", "covenant", f"M4: ${bo.m4_cp_outstanding_m:,.0f}M commercial paper backstopped by the revolver",
            "Undrawn committed revolver kept at least equal to commercial paper outstanding; CP outstanding "
            "reported monthly")
    if bo.fo_questions:
        add("RMS-M19", "condition_precedent",
            f"{len(bo.fo_questions)} BO question(s) for FO unanswered: "
            + ", ".join(v or h for h, v, _ in bo.fo_questions),
            "Before drawdown, the borrower's answers to: " + " ".join(q for _, _, q in bo.fo_questions if q))
    return out
