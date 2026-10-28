"""Validator 8 -- deterministic checks for Module 4 output.

Validator 8 is PURE and DETERMINISTIC: it never calls an LLM. It re-checks the
Module 4 specialist's numerical and structural claims against the reported
figures and the internal consistency of the deliverables. Same input always
yields the same report.

The six checks (per the Module 4 methodology):

1. **Reconciliation.** The sum of the itemized debt tranches (senior notes +
   non-note obligations, drawn principal only) plus the VERIFIED reconciling
   items (check 6) matches the reported Total Indebtedness in Note 8, within a
   small absolute tolerance.
2. **Subordination Ratio.** Recompute the Structural Subordination Drag Ratio
   from the tier figures and confirm the claimed ratio matches.
3. **Maturity Order.** The senior-note schedule's maturity years are in
   chronological (non-decreasing) order.
4. **Legal Entity.** Every tranche names a legal borrower, and the OpCo issuer
   vs HoldCo guarantor split is coherent (e.g. ``HCA Inc.`` issuer vs
   ``HCA Healthcare, Inc.`` guarantor) -- a tranche cannot name the same entity
   as both its sole borrower and its only credit support.
5. **Double-Counting.** Undrawn revolver limits (available facility limit, not
   drawn) and any intercompany borrowings are NOT folded into the reconciled
   total.
6. **Reconciling Items.** Every reconciling item (e.g. debt issuance costs and
   discounts) is shown by the filing itself: its quote appears verbatim in the
   supplied filing passages and states its amount, and its sign fits its kind.
   An item that fails is not counted in check 1. Verified items above
   ``RECONCILING_ITEMS_REVIEW_SHARE`` of the total also fail, for a reviewer.

Each check produces a :class:`DebtStructureValidatorCheck`; the aggregate
:class:`DebtStructureValidatorReport` is ``passed`` only when every check passes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.schemas.module4 import (
    Deliverable1,
    Module4Output,
    NonNoteDebtObligation,
    PriorityTier,
    ReconcilingItem,
    ReconcilingItemKind,
    SecurityStatus,
    SeniorNoteTranche,
)

# Stable check ids (auditable, referenced by escalations/tests).
CHECK_RECONCILIATION = "V8-01-RECONCILIATION"
CHECK_SUBORDINATION_RATIO = "V8-02-SUBORDINATION-RATIO"
CHECK_MATURITY_ORDER = "V8-03-MATURITY-ORDER"
CHECK_LEGAL_ENTITY = "V8-04-LEGAL-ENTITY"
CHECK_DOUBLE_COUNTING = "V8-05-DOUBLE-COUNTING"
CHECK_RECONCILING_ITEMS = "V8-06-RECONCILING-ITEMS"

# Verified reconciling items larger than this share of total indebtedness fail
# check 6 so a reviewer looks at them (HCA FY2025's $436M is 0.9%).
RECONCILING_ITEMS_REVIEW_SHARE = 0.02
# A quote shorter than this cannot show which line it comes from.
MIN_QUOTE_CHARS = 12
_QUOTE_TRANSLATE = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ",
})
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")

# Marker substrings that flag an obligation as an intercompany borrowing, which
# must be excluded from consolidated total indebtedness.
_INTERCOMPANY_MARKERS = ("intercompany", "inter-company", "affiliate loan")


@dataclass(frozen=True)
class DebtStructureValidatorCheck:
    """The outcome of one deterministic debt-structure validator check."""

    check_id: str
    name: str
    passed: bool
    detail: str
    observed: dict = field(default_factory=dict)


@dataclass(frozen=True)
class DebtStructureValidatorReport:
    """Aggregate debt-structure validator report over all checks."""

    checks: list[DebtStructureValidatorCheck]

    @property
    def passed(self) -> bool:
        """True only when EVERY check passed."""
        return all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[DebtStructureValidatorCheck]:
        return [c for c in self.checks if not c.passed]

    def check(self, check_id: str) -> DebtStructureValidatorCheck:
        for c in self.checks:
            if c.check_id == check_id:
                return c
        raise KeyError(check_id)


@dataclass(frozen=True)
class ReconcilingItemCheck:
    """Whether one reconciling item is shown by the filing."""

    item: ReconcilingItem
    verified: bool
    reason: str


def _normalise_quote(text: str) -> str:
    """Whitespace-insensitive form for verbatim matching.

    Filing tables are extracted one cell per line, so "(436" and ")" arrive on
    separate lines; brackets and currency signs are rejoined to their numbers.
    """
    text = re.sub(r"\s+", " ", (text or "").translate(_QUOTE_TRANSLATE)).strip()
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    return re.sub(r"\$\s+", "$", text)


def verify_reconciling_items(
    items: list[ReconcilingItem], filing_passages: dict[str, str] | None,
) -> list[ReconcilingItemCheck]:
    """Check each item against the filing text only; nothing is inferred."""
    passages = {k: _normalise_quote(v) for k, v in (filing_passages or {}).items()}
    checks: list[ReconcilingItemCheck] = []
    seen: set[tuple[str, float]] = set()
    for item in items:
        quote = _normalise_quote(item.quote)
        amount = abs(item.amount_m)
        amounts = [float(n.replace(",", "")) for n in _NUMBER.findall(quote)]
        reason = None
        if not passages:
            reason = "no filing passages were supplied to check its quote against"
        elif len(quote) < MIN_QUOTE_CHARS:
            reason = "its quote is too short to identify a line in the filing"
        elif not (quote in passages.get(item.evidence_id, "")
                  or any(quote in text for text in passages.values())):
            reason = "its quote does not appear verbatim in the filing"
        elif amount == 0 or not any(abs(a - amount) < 0.5 for a in amounts):
            reason = f"its quote does not state the amount {amount:,.0f}"
        elif (item.kind is ReconcilingItemKind.ISSUANCE_COSTS_AND_DISCOUNTS
              and item.amount_m > 0):
            reason = "issuance costs and discounts must reduce the total (negative amount)"
        elif item.kind is ReconcilingItemKind.UNAMORTIZED_PREMIUM and item.amount_m < 0:
            reason = "an unamortized premium must increase the total (positive amount)"
        elif (item.kind.value, item.amount_m) in seen:
            reason = "it repeats an item already counted"
        seen.add((item.kind.value, item.amount_m))
        checks.append(ReconcilingItemCheck(
            item=item, verified=reason is None,
            reason=reason or "its quote appears verbatim in the filing and states the amount"))
    return checks


def _is_intercompany(description: str) -> bool:
    text = description.lower()
    return any(marker in text for marker in _INTERCOMPANY_MARKERS)


class DebtStructureValidator:
    """Deterministic validator for Module 4 debt-structure output (Agent B).

    ``reconciliation_tolerance_m`` is an absolute tolerance in $M for the sum of
    tranches vs reported total; rounding across a large schedule makes an exact
    match unrealistic. ``ratio_tolerance`` guards the subordination ratio
    recomputation.
    """

    def __init__(
        self,
        *,
        reconciliation_tolerance_m: float = 1.0,
        ratio_tolerance: float = 0.0005,
    ) -> None:
        self._recon_tol = reconciliation_tolerance_m
        self._ratio_tol = ratio_tolerance

    def validate(
        self, output: Module4Output, *, filing_passages: dict[str, str] | None = None,
    ) -> DebtStructureValidatorReport:
        """``filing_passages`` (evidence id -> text) is what reconciling items are checked against."""
        d1 = output.deliverable_1
        items = verify_reconciling_items(d1.reconciling_items, filing_passages)
        adjustment = sum(c.item.amount_m for c in items if c.verified)
        checks = [
            self._check_reconciliation(d1, adjustment),
            self._check_subordination_ratio(d1),
            self._check_maturity_order(d1.senior_notes_schedule),
            self._check_legal_entity(d1),
            self._check_double_counting(d1, adjustment),
            self._check_reconciling_items(d1, items),
        ]
        return DebtStructureValidatorReport(checks=checks)

    # -- check 1: reconciliation ---------------------------------------------

    def _check_reconciliation(
        self, d1: Deliverable1, adjustment: float = 0.0,
    ) -> DebtStructureValidatorCheck:
        """Drawn tranche principal plus verified reconciling items matches reported total.

        Only DRAWN principal counts. Undrawn revolver limits and intercompany
        borrowings are excluded (see check 5). Debt issuance costs/discounts and
        similar lines count only as reconciling items the filing shows (check 6).
        """
        reported = d1.creditor_priority_framework.consolidated_total_indebtedness_m
        notes_sum = sum(t.principal_amount_m for t in d1.senior_notes_schedule)
        non_note_sum = sum(
            o.principal_drawn_m
            for o in d1.non_note_obligations_schedule
            if not _is_intercompany(o.obligation_facility)
        )
        tranche_sum = notes_sum + non_note_sum
        reconciled = tranche_sum + adjustment
        delta = abs(reconciled - reported)
        passed = delta <= self._recon_tol
        items = (f" plus verified reconciling items {adjustment:,.3f}M"
                 if adjustment else "")
        return DebtStructureValidatorCheck(
            check_id=CHECK_RECONCILIATION,
            name="Debt tranche reconciliation to reported total indebtedness",
            passed=passed,
            detail=(
                f"Sum of drawn tranches {tranche_sum:,.3f}M{items} vs reported total "
                f"{reported:,.3f}M (delta {delta:,.3f}M, tolerance "
                f"{self._recon_tol:,.3f}M)."
            ),
            observed={
                "senior_notes_sum_m": notes_sum,
                "non_note_drawn_sum_m": non_note_sum,
                "tranche_sum_m": tranche_sum,
                "reconciling_items_m": adjustment,
                "reported_total_m": reported,
                "delta_m": delta,
                # Signed: what further reconciling items would have to add to close the gap.
                "gap_m": reported - reconciled,
            },
        )

    # -- check 2: subordination ratio ----------------------------------------

    def _check_subordination_ratio(self, d1: Deliverable1) -> DebtStructureValidatorCheck:
        """Recompute Tier1 direct OpCo secured / consolidated total indebtedness."""
        f = d1.creditor_priority_framework
        total = f.consolidated_total_indebtedness_m
        if total <= 0:
            return DebtStructureValidatorCheck(
                check_id=CHECK_SUBORDINATION_RATIO,
                name="Structural subordination drag ratio recomputation",
                passed=False,
                detail="Consolidated total indebtedness is non-positive; ratio undefined.",
                observed={"consolidated_total_m": total},
            )
        recomputed = f.tier_1_direct_opco_secured_m / total
        claimed = f.structural_subordination_drag_ratio
        delta = abs(recomputed - claimed)
        passed = delta <= self._ratio_tol
        return DebtStructureValidatorCheck(
            check_id=CHECK_SUBORDINATION_RATIO,
            name="Structural subordination drag ratio recomputation",
            passed=passed,
            detail=(
                f"Recomputed drag ratio {recomputed:.4f} vs claimed {claimed:.4f} "
                f"(delta {delta:.4f}, tolerance {self._ratio_tol})."
            ),
            observed={
                "tier_1_m": f.tier_1_direct_opco_secured_m,
                "consolidated_total_m": total,
                "recomputed_ratio": recomputed,
                "claimed_ratio": claimed,
                "delta": delta,
            },
        )

    # -- check 3: maturity order ---------------------------------------------

    def _check_maturity_order(
        self, schedule: list[SeniorNoteTranche]
    ) -> DebtStructureValidatorCheck:
        """Senior-note maturities must be listed in chronological order."""
        years = [t.maturity_year for t in schedule]
        ordered = all(a <= b for a, b in zip(years, years[1:]))
        first_violation = None
        for i in range(1, len(years)):
            if years[i] < years[i - 1]:
                first_violation = {
                    "index": i,
                    "prev_year": years[i - 1],
                    "year": years[i],
                }
                break
        return DebtStructureValidatorCheck(
            check_id=CHECK_MATURITY_ORDER,
            name="Senior-note maturity chronological ordering",
            passed=ordered,
            detail=(
                "Maturity years are in chronological order."
                if ordered
                else f"Maturity ordering violated at index {first_violation['index']}: "
                f"{first_violation['prev_year']} then {first_violation['year']}."
            ),
            observed={"maturity_years": years, "first_violation": first_violation},
        )

    # -- check 4: legal entity ------------------------------------------------

    def _check_legal_entity(self, d1: Deliverable1) -> DebtStructureValidatorCheck:
        """Every tranche names a legal borrower with coherent credit support.

        Fails if any tranche omits a legal borrower, or names the SAME entity as
        both its only borrower and its only credit support (which would collapse
        the OpCo-issuer vs HoldCo-guarantor distinction the tier framework relies
        on). A guarantee by the parent of its own subsidiary's debt must name two
        distinct entities.
        """
        problems: list[str] = []
        tranches: list[tuple[str, str, str]] = [
            (t.senior_note_series, t.legal_borrower, t.guarantors_credit_support)
            for t in d1.senior_notes_schedule
        ] + [
            (o.obligation_facility, o.legal_borrower, o.guarantors_credit_support)
            for o in d1.non_note_obligations_schedule
        ]
        for name, borrower, support in tranches:
            if not borrower.strip():
                problems.append(f"{name!r}: missing legal borrower.")
                continue
            # A guaranteed tranche that names the parent as support must name a
            # distinct borrower; same-entity on both sides is incoherent.
            support_entities = _distinct_entities(support)
            if support_entities and borrower.strip() in support_entities and len(
                support_entities
            ) == 1:
                problems.append(
                    f"{name!r}: borrower and sole credit support are the same entity "
                    f"({borrower!r}); OpCo/HoldCo distinction collapses."
                )
        passed = not problems
        return DebtStructureValidatorCheck(
            check_id=CHECK_LEGAL_ENTITY,
            name="Legal entity / borrower-guarantor attribution",
            passed=passed,
            detail=(
                "Every tranche names a legal borrower with coherent credit support."
                if passed
                else " ".join(problems)
            ),
            observed={"problem_count": len(problems), "problems": problems},
        )

    # -- check 5: double-counting --------------------------------------------

    def _check_double_counting(
        self, d1: Deliverable1, adjustment: float = 0.0,
    ) -> DebtStructureValidatorCheck:
        """Undrawn revolver limits and intercompany borrowings stay out of totals.

        The reconciled total must not have absorbed (a) available (undrawn)
        facility limits or (b) intercompany borrowings: it fails only when the
        reported total tracks the figure WITH them. Any other gap is check 1's.
        """
        f = d1.creditor_priority_framework
        reported = f.consolidated_total_indebtedness_m
        undrawn_headroom = _undrawn_headroom(d1.non_note_obligations_schedule)
        intercompany = sum(
            o.principal_drawn_m
            for o in d1.non_note_obligations_schedule
            if _is_intercompany(o.obligation_facility)
        )
        drawn_total = sum(
            t.principal_amount_m for t in d1.senior_notes_schedule
        ) + sum(
            o.principal_drawn_m
            for o in d1.non_note_obligations_schedule
            if not _is_intercompany(o.obligation_facility)
        ) + adjustment
        inflated_total = drawn_total + undrawn_headroom + intercompany
        # Pass when the reported total matches the DRAWN total, not the inflated
        # one. If undrawn/intercompany were erroneously included, reported would
        # track the inflated figure instead.
        drawn_delta = abs(reported - drawn_total)
        includes_undrawn_or_intercompany = (
            (undrawn_headroom + intercompany) > self._recon_tol
            and abs(reported - inflated_total) <= self._recon_tol
            and drawn_delta > self._recon_tol
        )
        passed = not includes_undrawn_or_intercompany
        return DebtStructureValidatorCheck(
            check_id=CHECK_DOUBLE_COUNTING,
            name="No double-counting of undrawn limits / intercompany debt",
            passed=passed,
            detail=(
                f"Reported total {reported:,.3f}M does not include undrawn headroom "
                f"{undrawn_headroom:,.3f}M or intercompany {intercompany:,.3f}M "
                f"(drawn-only total {drawn_total:,.3f}M)."
                if passed
                else f"Reported total {reported:,.3f}M matches drawn principal plus "
                f"undrawn headroom {undrawn_headroom:,.3f}M and/or intercompany "
                f"{intercompany:,.3f}M (drawn-only total {drawn_total:,.3f}M)."
            ),
            observed={
                "reported_total_m": reported,
                "drawn_total_m": drawn_total,
                "undrawn_headroom_m": undrawn_headroom,
                "intercompany_m": intercompany,
                "inflated_total_m": inflated_total,
            },
        )


    # -- check 6: reconciling items -------------------------------------------

    def _check_reconciling_items(
        self, d1: Deliverable1, items: list[ReconcilingItemCheck],
    ) -> DebtStructureValidatorCheck:
        """Every reconciling item is shown by the filing, and together they are not material."""
        reported = d1.creditor_priority_framework.consolidated_total_indebtedness_m
        rejected = [c for c in items if not c.verified]
        verified_total = sum(abs(c.item.amount_m) for c in items if c.verified)
        share = verified_total / reported if reported > 0 else 0.0
        material = share > RECONCILING_ITEMS_REVIEW_SHARE
        problems = [f"{c.item.label!r} ({c.item.amount_m:,.3f}M) not counted: {c.reason}."
                    for c in rejected]
        if material:
            problems.append(
                f"Verified reconciling items total {verified_total:,.3f}M, "
                f"{share:.1%} of total indebtedness (review above "
                f"{RECONCILING_ITEMS_REVIEW_SHARE:.0%}).")
        passed = not problems
        if passed:
            detail = (f"{len(items)} reconciling item(s) verified against the filing text, "
                      f"{verified_total:,.3f}M ({share:.1%} of total indebtedness)."
                      if items else "No reconciling items.")
        else:
            detail = " ".join(problems)
        return DebtStructureValidatorCheck(
            check_id=CHECK_RECONCILING_ITEMS,
            name="Reconciling items shown verbatim by the filing",
            passed=passed,
            detail=detail,
            observed={
                "items": [{"kind": c.item.kind.value, "label": c.item.label,
                           "amount_m": c.item.amount_m, "quote": c.item.quote,
                           "evidence_id": c.item.evidence_id,
                           "verified": c.verified, "reason": c.reason} for c in items],
                "verified_total_m": verified_total,
                "share_of_total": share,
            },
        )


def _undrawn_headroom(obligations: list[NonNoteDebtObligation]) -> float:
    """Sum of available (undrawn) facility limit above drawn principal."""
    total = 0.0
    for o in obligations:
        if o.available_facility_limit_m is None:
            continue
        headroom = o.available_facility_limit_m - o.principal_drawn_m
        if headroom > 0:
            total += headroom
    return total


def _distinct_entities(credit_support: str) -> set[str]:
    """Best-effort split of a credit-support string into entity names.

    Splits on common separators (``and``, ``,``, ``;``, ``&``) and trims noise
    words so "fully and unconditionally guaranteed by HCA Healthcare, Inc."
    yields the guarantor entity. Deterministic and purely textual.
    """
    text = credit_support.strip()
    if not text:
        return set()
    # Remove common guarantee phrasing so the residual is the entity name(s).
    for phrase in (
        "fully and unconditionally guaranteed by",
        "unconditionally guaranteed by",
        "guaranteed on an unsecured basis by",
        "guaranteed by",
        "credit support from",
        "guarantor:",
        "guarantors:",
    ):
        text = re.sub(re.escape(phrase), ",", text, flags=re.IGNORECASE)
    parts = re.split(r"\band\b|,|;|&", text, flags=re.IGNORECASE)
    entities = {p.strip() for p in parts if p.strip()}
    # Drop pure-noise fragments.
    noise = {"none", "n/a", "na", "unsecured basis", "basis"}
    return {e for e in entities if e.lower() not in noise}
