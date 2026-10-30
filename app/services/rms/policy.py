"""The RMS layer's thresholds, in one place.

Every number here is a judgement-based starting point (ILLUSTRATIVE, NOT BANK POLICY): back-test the bands
against agency ratings and the bank's own credit policy before relying on them. Changing any of them changes
``RMS_POLICY_VERSION``'s meaning, so bump it with the change; each result records the version it used.
"""

from __future__ import annotations

from app.services.fo.scoring import COMPOSITE_CORE, COMPOSITE_FLOOR, COMPOSITE_FLOOR_TRIGGER

RMS_POLICY_VERSION = "rms-policy-1.4"  # 1.4: risk-based pricing (SOFR + credit spread + business premium); an
# accepted BO turns its gates into warnings; 1.3: no business-band notch (the BO no longer gives a band); 1.2: M3
# rebuild, structured distributions, financial policy, M4 rollover evidence, liquidity and borrower cross-checks;
# 1.1: each BO signal counted once; sizing at the adjusted grade

# Risk levels on the FO 1-10 scale (highest score in each level). FO's "Very low" folds into "low".
LEVELS = (("low", 3.5), ("moderate", 5.0), ("elevated", 7.5), ("high", 10.0))

# Each BO signal is counted once. The BO's credit-quality judgement (the M4 verdict) notches the grade,
# and the one adjusted grade then sets everything below: the sizing multiples, the tenor, the terms and the
# verdict. Structural facts that cut what a lender can reach (M3 findings, unproven mitigants) are a capacity
# haircut only. Refinancing distress acts only through the rollover rule.

# --- Debt capacity (FO's figures, sized at the adjusted grade) ---------------------------------------------
# Maximum Debt / EBITDAR by the adjusted grade's level, anchored on the hospital benchmark (3.61x in
# config/financial_overview/Benchmarks.json).
LEVERAGE_MULTIPLE = {"low": 4.5, "moderate": 4.0, "elevated": 3.6, "high": 3.1}
# Minimum DSCR (CFADS / total debt service, the new loan included) in the base case, by the adjusted level.
DSCR_MIN = {"low": 1.25, "moderate": 1.25, "elevated": 1.5, "high": 1.5}
# Downside: CFADS and EBITDAR cut by 20% (a reimbursement cut or a volume shock), DSCR at least 1.0x.
DOWNSIDE_SHOCK = -0.20
DOWNSIDE_DSCR_MIN = 1.0
# Existing maturities count as refinanced (only interest stays in the existing debt service) when FO's
# refinancing test scores this or better and M4 records no refinancing distress.
ROLLOVER_MAX_REFINANCING_SCORE = 3
# M4 Deliverable 2's refinancing record can veto the rollover (refinancing left late, or at a widening spread)
# or, when it is strong on every count, extend it one FO score further.
ROLLOVER_MIN_LEAD_TIME_MONTHS = 6
ROLLOVER_MAX_SPREAD_DELTA_BPS = 150
ROLLOVER_STRONG_LEAD_TIME_MONTHS = 9
ROLLOVER_STRONG_SPREAD_DELTA_BPS = 50
ROLLOVER_CONFIRMED_MAX_REFINANCING_SCORE = 4
# Sizing terms when the request doesn't give them. The new loan is always sized as straight-line amortising
# over its tenor (first-year principal + interest), whatever its final amortisation: the conservative case,
# and the same debt service the structuring engine's feasibility check uses.
DEFAULT_TENOR_YEARS = 5

# --- Pricing (when the request doesn't give a rate) ---------------------------------------------------------
# A floating rate: the base rate + a credit spread for FO's financial risk + a premium for the Business
# Overview's credit judgement (the M4 verdict). The all-in rate is today's coupon, and the loan is sized at it.
# The base is the latest published overnight SOFR (New York Fed): update the rate and its date together.
BASE_RATE = {"name": "SOFR (overnight)", "rate": 0.0394, "as_of": "2026-10-28"}
# Credit spread over the base, in bps, by the letter grade of FO's composite credit rating (its +/- ignored).
CREDIT_SPREAD_BPS = {"AAA": 60, "AA": 80, "A": 110, "BBB": 160, "BB": 275, "B": 425, "CCC": 700, "CC": 1000,
                     "C": 1300, "D": 1300}
# Business Overview premium, in bps, by the M4 credit verdict; without one, priced as "adequate".
BUSINESS_PREMIUM_BPS = {"strong": -25, "satisfactory": 0, "adequate": 50, "weak": 150}
BUSINESS_PREMIUM_DEFAULT = "adequate"

# --- BO haircut on capacity: structural facts only ----------------------------------------------------------
HAIRCUT_CAP = 0.40
M3_FINDING_HAIRCUT, M3_FINDINGS_COUNTED = 0.05, 2
UNPROVEN_MITIGANT_HAIRCUT = 0.05

# --- Adjusted grade (FO composite, notched by the M4 credit verdict) ---------------------------------------
M4_VERDICT_NOTCH = {"strong": -0.5, "satisfactory": 0.0, "adequate": 0.5, "weak": 1.5}
# FO's own severity floor carries over: leverage, liquidity or DSCR at 9 or more keeps the grade at 7 or worse.
SEVERITY_MEASURES, SEVERITY_TRIGGER, SEVERITY_FLOOR = COMPOSITE_CORE, COMPOSITE_FLOOR_TRIGGER, COMPOSITE_FLOOR
# M3 structural findings that count: negative, high materiality, usable, on one of these dimensions.
M3_DIMENSIONS = ("guarantor_coverage", "cash_flow_access", "minority_or_jv")

# --- Gates --------------------------------------------------------------------------------------------------
# FO total debt and M4 consolidated total indebtedness further apart than this (as a share of the larger)
# refer the case: FO and BO don't share a verified evidence snapshot, so neither figure is picked silently.
DEBT_MISMATCH_TOLERANCE = 0.10
# BO handoff escalations (unresolved challenger findings) at these severities refer the case.
BLOCKING_BO_SEVERITIES = ("high", "material")
# FO's undrawn revolver and M4's revolver availability (limit less drawn, read either gross or net of the
# commercial paper it backstops) further apart than this refer the case.
LIQUIDITY_MISMATCH_TOLERANCE = 0.10
# A reviewer's acceptance of the BO handoff stands in for these gates: the BO's own state (an incomplete or
# unvalidated M4, unresolved challenges) and its disagreements with FO. They stay on the result as warnings and the
# verdict follows the matrix. FO's own gates still hold: its going concern, escalations (cleared by an FO
# decision), covenant breach, missing composite or capacity, and the structuring engine. G05 without any M4
# output still holds: there is nothing on the debt structure to accept.
BO_ACCEPTANCE_WAIVES = ("RMS-G05", "RMS-G06", "RMS-G07", "RMS-G11", "RMS-G12")

# --- Financial policy -----------------------------------------------------------------------------------------
# Debt-funded shareholder distributions (M4 Deliverable 3) of at least this share of M4 total indebtedness over
# three years make an aggressive financial policy: a maintenance leverage covenant replaces an incurrence test.
# It changes the terms, not the grade (the M4 verdict already carries it).
AGGRESSIVE_DISTRIBUTIONS_TO_DEBT = 0.25
FINANCIAL_POLICY_COVENANT_HEADROOM = 1.25

# --- Verdict matrix -----------------------------------------------------------------------------------------
# Utilisation (requested / derived capacity) band edges: <= 0.75, <= 1.0, <= 1.25, above.
UTILISATION_EDGES = (0.75, 1.0, 1.25)
VERDICT_MATRIX = {
    "low": ("approve", "approve", "counter_offer", "counter_offer"),
    "moderate": ("approve", "approve_with_conditions", "counter_offer", "decline"),
    "elevated": ("approve_with_conditions", "approve_with_conditions_secured", "decline", "decline"),
    "high": ("refer", "decline", "decline", "decline"),
}

# --- Terms by the adjusted level ----------------------------------------------------------------------------
TENOR_CAP_YEARS = {"low": 7, "moderate": 5, "elevated": 4, "high": 3}
# (type, share of principal amortised over the tenor, excess-cash-flow sweep)
AMORTISATION = {"low": ("bullet", 0.0, None), "moderate": ("partial", 0.25, None),
                "elevated": ("straight_line", 1.0, 0.50), "high": ("straight_line", 1.0, 0.75)}
# Maximum Debt / EBITDAR covenant = pro forma leverage x this headroom; None: incurrence test only.
LEVERAGE_COVENANT_HEADROOM = {"low": None, "moderate": 1.25, "elevated": 1.15, "high": 1.10}
DSCR_COVENANT = {"elevated": 1.25, "high": 1.35}
SECURED_LEVELS = ("elevated", "high")
# A Tier-1 (secured) share of total debt above this subordinates an unsecured lender: the loan goes secured.
SUBORDINATION_DRAG_SECURED = 0.25
GUARANTOR_COVERAGE_MIN = 0.80  # share of consolidated EBITDA the guarantors must generate
MAX_LTV = 0.80

# --- Mitigant triggers (FO measure risk scores, 1-10) ------------------------------------------------------
LIQUIDITY_SCORE_TRIGGER = 7
DSCR_SCORE_TRIGGER = 7
ICR_SCORE_TRIGGER = 6
RCF_SCORE_TRIGGER = 7
MARGIN_SCORE_TRIGGER = 7
DSO_SCORE_TRIGGER = 7
