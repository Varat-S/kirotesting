"""Validator 7 checks for the versioned Module 3 structured extension."""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import date
from typing import Any

from pydantic import ValidationError

from app.schemas.bo import Admissibility, ClaimType, Disposition
from app.schemas.bo_m3 import M3CorporateAssessment
from app.services.bo.hypotheses import derive_admissibility
from app.services.bo.semantic_entailment import DeterministicCheck


# Numeric tokens (e.g. 1,234.5 , 45% , $3.2) and fiscal-period tokens
# (e.g. 2024 , FY2023 , Q3 2022) asserted in a claim. These are the only
# machine-checkable surface forms; everything else stays conservative.
_NUMBER_RE = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?")
_PERIOD_RE = re.compile(r"\b(?:FY\s?\d{4}|Q[1-4]\s?\d{4}|20\d{2}|19\d{2})\b", re.IGNORECASE)


def _normalise_number(token: str) -> str:
    return token.replace("$", "").replace(",", "").rstrip("%").strip()


# Markers that make a claim CAUSAL or LEGALLY significant. Numeric agreement
# never proves a causal attribution or a legal conclusion, so a claim carrying
# these is capped at REQUIRES_HUMAN_REVIEW (never auto-promoted to supported).
_CAUSAL_MARKERS = (
    "because", "due to", "driven by", "as a result", "resulted in", "resulting in",
    "caused by", "caused", "led to", "attributable to", "thanks to", "owing to",
    "on account of", "reflecting", "reflects",
)
_LEGAL_MARKERS = (
    "enforceable", "legally binding", "guarantees the", "guaranteed", "covenant",
    "indenture", "obligation to pay", "cross-default", "security interest",
    "perfected", "ranking", "subordination",
)


def _is_causal_or_legal_claim(*texts: str) -> bool:
    """Whether any supplied claim text makes a causal or legally significant
    assertion that deterministic token matching cannot adjudicate."""
    blob = " ".join(t or "" for t in texts).casefold()
    return any(m in blob for m in _CAUSAL_MARKERS) or any(
        m in blob for m in _LEGAL_MARKERS)


def _percentage_point_confusion(claim_text: str) -> bool:
    """Detect a percentage-vs-percentage-point confusion in the claim.

    A claim that mixes a percentage change and a percentage-point change for the
    same quantity (e.g. 'margin improved 2%' meaning 2 pp) is not machine-
    reconcilable without the canonical datum; the ambiguity is flagged so the
    claim is not treated as entailed. Conservative: only fires when BOTH a bare
    percentage and an explicit 'percentage point'/'pp'/'bps' phrase appear, as
    that is where the two readings genuinely diverge.
    """
    blob = claim_text.casefold()
    has_pct = "%" in claim_text or "percent" in blob
    has_pp = ("percentage point" in blob or "pp" in blob.split()
              or " bps" in blob or "basis point" in blob)
    return has_pct and has_pp


# -- canonical financial verification ----------------------------------------
#
# Reuses the repo's resolved canonical metric values (ParameterResult) and their
# versioned ResolvedMetricDefinition (units / period type / included-excluded
# components). It is a VERIFICATION layer only: it never recomputes metrics or
# builds a competing financial engine. Gated entirely on availability — with no
# resolved canonical datum for a claim it returns no checks and the caller falls
# back to the corrected textual gate (never fabricating a result).

# A small, explicit alias map from metric vocabulary a claim may use to the
# canonical metric_definition_id. Deliberately conservative: an unmapped metric
# phrase yields no canonical check (human review), never a wrong match.
_METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "revenue_growth": ("revenue growth", "revenue increased", "revenue grew",
                       "revenue rose", "sales growth", "sales grew"),
    "operating_margin": ("operating margin",),
    "net_debt": ("net debt",),
    "net_debt_to_ebitda": ("net debt to ebitda", "net leverage", "leverage ratio"),
    "interest_coverage": ("interest coverage",),
    "free_cash_flow": ("free cash flow", "fcf"),
    "liquidity": ("liquidity",),
}


def _identify_canonical_metric(claim_text: str) -> str | None:
    """Return the canonical metric_definition_id a claim refers to, if any.

    Conservative substring match against :data:`_METRIC_ALIASES`. A claim that
    names no recognised metric (or names more than one) returns ``None`` so the
    richer check is skipped rather than guessing the wrong metric identity.
    """
    blob = claim_text.casefold()
    hits = {metric_id for metric_id, aliases in _METRIC_ALIASES.items()
            if any(alias in blob for alias in aliases)}
    return next(iter(hits)) if len(hits) == 1 else None


def _identified_metrics(text: str) -> set[str]:
    """Every canonical metric a passage names (not just the unambiguous one).

    Unlike :func:`_identify_canonical_metric`, which returns a single metric only
    when exactly one is named, this reports the full set. It lets the
    deterministic gate establish whether a claim and its cited passage talk about
    the SAME metric before a difference in numbers is treated as a conflict.
    """
    blob = text.casefold()
    return {metric_id for metric_id, aliases in _METRIC_ALIASES.items()
            if any(alias in blob for alias in aliases)}


# A number is "period-like" when it is matched by :data:`_PERIOD_RE` (a fiscal
# year, quarter or reporting year), i.e. it plays the ROLE of a reporting
# period, not a financial value. Problem B: such a token must never enter the
# value-comparison set. We do NOT drop every four-digit number — only the ones
# contextually matched as a period — so a legitimate four-digit amount (e.g. a
# $3000 figure) is still compared.
def _period_value_tokens(text: str) -> set[str]:
    """Normalised numeric forms that are actually reporting-period tokens.

    Derived from the fiscal-period matches in ``text`` (e.g. ``FY2024`` ->
    ``2024``, ``Q3 2022`` -> ``2022``) so they can be excluded from the financial
    value set. A bare four-digit financial amount that is NOT part of a period
    phrase is not captured here and remains a comparable value.
    """
    out: set[str] = set()
    for token in _PERIOD_RE.findall(text):
        for num in _NUMBER_RE.findall(token):
            norm = _normalise_number(num)
            if norm:
                out.add(norm)
    return out


def _metric_bound_values(text: str, metric_id: str) -> set[str]:
    """The numeric values in ``text`` that belong to ``metric_id`` specifically.

    Problem A: a number appearing anywhere in a passage is not evidence for a
    claim unless it is the value OF THE CLAIM'S METRIC. This isolates the
    relevant metric-value relationship by scanning each metric-alias occurrence
    and collecting the numeric tokens in its local window, stopping at the next
    (different) metric mention so an unrelated metric's figure (e.g. ``net debt
    was 12``) is not attributed to the metric under test (e.g. ``revenue grew
    8%``). Period-role tokens (years) are excluded.

    Returns an empty set when the metric is not named, or is named but carries no
    adjacent value — in which case the claim stays UNSUPPORTED rather than being
    matched to an arbitrary number elsewhere in the passage.
    """
    aliases = _METRIC_ALIASES.get(metric_id, ())
    if not aliases:
        return set()
    blob = text.casefold()
    period_tokens = _period_value_tokens(text)
    # Boundaries of every recognised metric mention, so a window for one metric
    # never bleeds into another metric's clause.
    all_alias_spans: list[tuple[int, int, str]] = []
    for mid, mid_aliases in _METRIC_ALIASES.items():
        for alias in mid_aliases:
            start = 0
            while True:
                idx = blob.find(alias, start)
                if idx == -1:
                    break
                all_alias_spans.append((idx, idx + len(alias), mid))
                start = idx + 1
    all_alias_spans.sort()

    values: set[str] = set()
    for idx, end, mid in all_alias_spans:
        if mid != metric_id:
            continue
        # The clause for this mention runs until the next DIFFERENT-metric
        # mention (or a hard clause break), so adjacent unrelated figures are
        # not captured. A generous window still bounds run-on sentences.
        next_boundary = len(text)
        for n_idx, _n_end, n_mid in all_alias_spans:
            if n_idx > idx and n_mid != metric_id:
                next_boundary = min(next_boundary, n_idx)
                break
        window = text[idx:min(next_boundary, idx + 80)]
        # Stop the window at an explicit clause separator to avoid pulling in a
        # figure from a following clause of the same sentence.
        for sep in (";", ",", " while ", " whereas ", " and net ", " and "):
            cut = window.casefold().find(sep, len(_nearest_alias(window, aliases)))
            if cut != -1:
                window = window[:cut]
                break
        for num in _NUMBER_RE.findall(window):
            norm = _normalise_number(num)
            if norm and norm not in period_tokens:
                values.add(norm)
    return values


def _nearest_alias(window: str, aliases: tuple[str, ...]) -> str:
    """The metric alias at the start of ``window`` (to anchor separator search
    past the alias phrase itself). Returns the matched alias or an empty string.
    """
    low = window.casefold()
    for alias in aliases:
        if low.startswith(alias):
            return alias
    return ""


# Scale/unit markers that make two bare numbers NON-comparable unless the scale
# is the same. A claim in "$3 billion" and a passage reading "3000 (millions)"
# describe the same magnitude but as different tokens; a blanket token-mismatch
# rule would wrongly flag them. We therefore require a compatible scale before a
# numeric difference may be read as a conflict, and never convert across scales
# ourselves (no fabricated reconciliation).
_SCALE_MARKERS = ("trillion", "billion", "million", "thousand", "bn", "mm")
# Currency markers. USD vs SGD (etc.) are different measurement bases; a numeric
# difference across currencies is not, by itself, a contradiction.
_CURRENCY_MARKERS = ("usd", "sgd", "eur", "gbp", "jpy", "cny", "hkd", "aud", "€", "£", "¥")


def _scales(text: str) -> set[str]:
    blob = text.casefold()
    return {m for m in _SCALE_MARKERS if m in blob}


def _currencies(text: str) -> set[str]:
    blob = text.casefold()
    return {m for m in _CURRENCY_MARKERS if m in blob}


# Reporting-scope vocabulary. A claim about a SUBSIDIARY, segment or standalone
# entity is NOT on the same reporting scope as a CONSOLIDATED/group figure; a
# value difference across scopes is not a contradiction (the subsidiary claim
# simply stays unverified by the consolidated datum).
_CONSOLIDATED_MARKERS = ("consolidated", "group ", "groupwide", "group-wide",
                         "at the group")
_SUBSIDIARY_MARKERS = ("subsidiary", "standalone", "stand-alone", "segment",
                       "division", "operating company", "opco", "at the subsidiary",
                       "entity level", "entity-level", "unconsolidated")


def _claim_reporting_scope(claim_text: str) -> str | None:
    """The reporting scope a claim asserts, if any: ``'consolidated'`` /
    ``'subsidiary'`` / ``None`` (unstated). Conservative: a claim that names both
    or neither returns ``None`` so scope is treated as UNKNOWN, never assumed.
    """
    blob = claim_text.casefold()
    is_consol = any(m in blob for m in _CONSOLIDATED_MARKERS)
    is_sub = any(m in blob for m in _SUBSIDIARY_MARKERS)
    if is_consol and not is_sub:
        return "consolidated"
    if is_sub and not is_consol:
        return "subsidiary"
    return None


def _claim_mentions_entity(claim_text: str, entity_id: str | None,
                           entity_name: str | None) -> bool:
    """Whether the claim text explicitly identifies the canonical datum's entity.

    Problem D: a subsidiary-level figure may only be compared to a claim about
    the SAME legal entity. Identity must be established from a canonical
    legal-entity identifier or a verified entity name recorded on the datum — NOT
    from loose string similarity or the generic word "subsidiary". A whole-token
    match of the identifier/name in the claim is required; otherwise identity is
    treated as NOT established.
    """
    blob = claim_text.casefold()
    for candidate in (entity_id, entity_name):
        if not candidate:
            continue
        cand = str(candidate).casefold().strip()
        if cand and re.search(rf"(?<!\w){re.escape(cand)}(?!\w)", blob):
            return True
    return False


def _scope_comparable(claim_text: str, datum_scope: str | None, *,
                      datum_entity_id: str | None = None,
                      datum_entity_name: str | None = None) -> tuple[bool, str]:
    """Whether a claim and a canonical datum are on a comparable reporting scope.

    Returns ``(comparable, reason_when_not)``. A subsidiary/standalone claim is
    NOT comparable to a consolidated/group canonical datum. When the claim leaves
    its scope unstated, scope is UNKNOWN: we do not assume equivalence, so a value
    difference cannot be read as a contradiction (uncertainty is preserved).

    Problem D: for a SUBSIDIARY-level comparison, matching reporting scope is not
    enough — two different named subsidiaries share the subsidiary scope yet are
    different legal persons. A subsidiary datum is therefore comparable only when
    the datum carries a canonical legal-entity identifier (or verified entity
    name) that the claim explicitly identifies. Without an established entity
    identity, scope is NOT comparable (so a value difference stays UNSUPPORTED).
    """
    datum_scope_norm = (datum_scope or "").casefold().strip()
    claim_scope = _claim_reporting_scope(claim_text)
    if claim_scope is None:
        return (False, "the claim does not state its reporting scope, so it cannot "
                       "be established to share the canonical metric's scope "
                       f"({datum_scope_norm or 'unstated'}); equivalence is not assumed")
    if not datum_scope_norm:
        return (False, "the canonical metric's reporting scope is not recorded, so "
                       "scope equivalence with the claim cannot be established")
    # Treat 'group' as consolidated for comparison purposes.
    datum_is_consol = datum_scope_norm in {"consolidated", "group"}
    if claim_scope == "consolidated" and datum_is_consol:
        return (True, "")
    if claim_scope == "subsidiary" and not datum_is_consol:
        # Require an established legal-entity identity, not merely a shared
        # subsidiary classification.
        if not (datum_entity_id or datum_entity_name):
            return (False, "the claim and the canonical metric are both at the "
                           "subsidiary level, but the canonical datum carries no "
                           "legal-entity identifier, so the two cannot be "
                           "established as the same legal entity")
        if not _claim_mentions_entity(claim_text, datum_entity_id, datum_entity_name):
            return (False, "the claim does not identify the same legal entity as the "
                           f"canonical subsidiary datum ({datum_entity_id or datum_entity_name!r}); "
                           "two different subsidiaries are not comparable merely "
                           "because both are subsidiaries")
        return (True, "")
    return (False, f"the claim is at the {claim_scope} level but the canonical "
                   f"metric is reported on a {datum_scope_norm} basis; the two "
                   "reporting scopes are not equivalent")


def _comparable_basis(claim_text: str, passage_text: str) -> tuple[bool, str]:
    """Whether a claim and a cited passage are on a comparable-enough basis for a
    numeric difference between them to be read as a genuine contradiction.

    The central rule: a different number is a contradiction ONLY when the two
    statements are about the SAME metric, the SAME reporting period and a
    compatible unit/currency/scale. This surface-level textual gate establishes
    those preconditions conservatively; when it cannot, it returns ``False`` and
    the caller downgrades to UNSUPPORTED rather than CONTRADICTED.

    Returns ``(comparable, reason_when_not_comparable)``.
    """
    claim_metrics = _identified_metrics(claim_text)
    passage_metrics = _identified_metrics(passage_text)
    # Metric identity: both must name a metric and share at least one. If either
    # side names no recognised metric, or they share none, we cannot establish
    # that the numbers describe the same quantity.
    if not claim_metrics or not passage_metrics:
        return (False, "the metric of the claim and/or the cited passage could "
                       "not be identified, so a numeric difference cannot be "
                       "attributed to the same quantity")
    if not (claim_metrics & passage_metrics):
        return (False, f"the claim concerns {sorted(claim_metrics)} but the cited "
                       f"passage concerns {sorted(passage_metrics)}; a different "
                       "number describes a different metric, not a conflict")
    # Period identity: if the claim names a period, the passage must assert the
    # same one. A different fiscal period does not, by itself, disprove the claim.
    claim_periods = {p.upper().replace(" ", "") for p in _PERIOD_RE.findall(claim_text)}
    passage_periods = {p.upper().replace(" ", "") for p in _PERIOD_RE.findall(passage_text)}
    if claim_periods and passage_periods and not (claim_periods & passage_periods):
        return (False, f"the claim concerns period(s) {sorted(claim_periods)} but "
                       f"the cited passage concerns {sorted(passage_periods)}; a "
                       "different period is not a conflicting assertion")
    # Scale/unit compatibility: a difference in stated scale (millions vs
    # billions) or currency is not reconciled here, so a numeric difference
    # across them is not treated as a conflict.
    claim_scales, passage_scales = _scales(claim_text), _scales(passage_text)
    if claim_scales and passage_scales and claim_scales != passage_scales:
        return (False, f"the claim is stated in {sorted(claim_scales)} but the "
                       f"cited passage in {sorted(passage_scales)}; the magnitudes "
                       "are not on a comparable scale and are not converted here")
    claim_ccy, passage_ccy = _currencies(claim_text), _currencies(passage_text)
    if claim_ccy and passage_ccy and claim_ccy != passage_ccy:
        return (False, f"the claim is in {sorted(claim_ccy)} but the cited passage "
                       f"in {sorted(passage_ccy)}; a cross-currency difference is "
                       "not a conflict without an established conversion")
    # Reporting-scope identity: a subsidiary/standalone claim and a consolidated/
    # group passage are different scopes; a value difference across them is not a
    # conflict. When either side leaves scope unstated we do not assume equality.
    claim_scope = _claim_reporting_scope(claim_text)
    passage_scope = _claim_reporting_scope(passage_text)
    if claim_scope and passage_scope and claim_scope != passage_scope:
        return (False, f"the claim is at the {claim_scope} level but the cited "
                       f"passage at the {passage_scope} level; a value difference "
                       "across reporting scopes is not a conflict")
    return (True, "")


def _canonical_financial_checks(
    item: dict[str, Any], claim_text: str, *,
    canonical_metrics: dict[str, dict[str, Any]],
) -> tuple[DeterministicCheck, ...]:
    """Verify a numeric claim against a resolved canonical metric when available.

    ``canonical_metrics`` maps canonical ``metric_definition_id`` -> a resolved
    datum dict: ``value`` (float|None), ``units``, ``period``, ``accounting_basis``,
    ``currency``, ``reporting_scope`` and the metric ``definition`` view. These are
    read from ``prep.indicators`` / :class:`ParameterResult` and their
    :class:`ResolvedMetricDefinition`; nothing is recomputed here.

    Checks: metric identity, fiscal period, accounting basis, currency+units,
    numerical value (within tolerance), and pp-vs-% confusion. A conflicting
    canonical value yields ``contradiction=True`` (CONTRADICTED on a comparable
    basis); an ABSENT-but-non-conflicting datum yields ``contradiction=False``
    (UNSUPPORTED). Returns ``()`` when no canonical datum resolves for the claim.
    """
    metric_id = _identify_canonical_metric(claim_text)
    if metric_id is None or metric_id not in canonical_metrics:
        return ()
    datum = canonical_metrics[metric_id]
    checks: list[DeterministicCheck] = []

    # Reporting-scope comparability (Problem D). A value difference may be read as
    # a CONTRADICTION only when the claim and the canonical datum share the same
    # reporting scope AND, for subsidiary-level data, the same established legal
    # entity. A subsidiary/standalone claim is NOT contradicted by a consolidated
    # group figure, and a claim about one named subsidiary is NOT contradicted by
    # a different subsidiary's figure; an unstated claim scope is UNKNOWN (never
    # assumed equivalent). When scope is not comparable the value/period mismatch
    # is downgraded to UNSUPPORTED (contradiction=False), preserving uncertainty.
    scope_comparable, scope_reason = _scope_comparable(
        claim_text, datum.get("reporting_scope"),
        datum_entity_id=datum.get("entity_id"),
        datum_entity_name=datum.get("entity_name"))
    # Currency comparability. A claim in a currency other than the canonical
    # datum's cannot be contradicted without an established conversion.
    datum_ccy = {str(datum.get("currency")).casefold()} if datum.get("currency") else set()
    claim_ccy = _currencies(claim_text)
    ccy_comparable = not (claim_ccy and datum_ccy and not (claim_ccy & datum_ccy))
    ccy_reason = (f"the claim is in {sorted(claim_ccy)} but the canonical metric "
                  f"in {sorted(datum_ccy)}; a cross-currency difference is not a "
                  "conflict without an established conversion")

    # Fiscal-period comparability (Problem C). A canonical datum for a DIFFERENT
    # reporting period than the claim cannot verify or contradict the claim: both
    # values may be correct for their respective periods. When the claim names a
    # period and the canonical datum carries a DIFFERENT one, period is NOT
    # comparable and a value difference stays UNSUPPORTED (never CONTRADICTED).
    # The latest canonical metric does not supersede a historical-period claim.
    claimed_periods = {p.upper().replace(" ", "") for p in _PERIOD_RE.findall(claim_text)}
    datum_period = (datum.get("period") or "")
    datum_period_norm = datum_period.upper().replace(" ", "")
    period_comparable = not (
        claimed_periods and datum_period_norm
        and datum_period_norm not in claimed_periods)
    period_reason = (f"the claim concerns period(s) {sorted(claimed_periods)} but the "
                     f"canonical metric is for {datum_period!r}; a figure for a "
                     "different reporting period does not verify or contradict the "
                     "claim, and a later period does not supersede a historical one")

    # A difference may be a contradiction only on a FULLY comparable basis:
    # scope + entity, currency AND reporting period must all line up.
    basis_comparable = scope_comparable and ccy_comparable and period_comparable
    if not scope_comparable:
        basis_reason = scope_reason
    elif not ccy_comparable:
        basis_reason = ccy_reason
    elif not period_comparable:
        basis_reason = period_reason
    else:
        basis_reason = ""

    # pp vs % confusion — not machine-reconcilable; flag for review (no auto-pass).
    if _percentage_point_confusion(claim_text):
        checks.append(DeterministicCheck(
            name="percentage_point_vs_percentage_change", passed=False,
            detail=("the claim mixes a percentage and a percentage-point/bps change "
                    "for the same quantity; the two are not interchangeable and "
                    "cannot be reconciled deterministically"),
            contradiction=False))

    # Fiscal period: if the claim names a period that differs from the canonical
    # one, record a non-match. This is UNSUPPORTED (a different-period datum does
    # not disprove the claim's period), never CONTRADICTED — so the check always
    # carries contradiction=False.
    if claimed_periods and datum_period_norm and not period_comparable:
        checks.append(DeterministicCheck(
            name="canonical_fiscal_period_match", passed=False,
            detail=(f"claim period {sorted(claimed_periods)} does not match the "
                    f"canonical metric period {datum_period!r}; a figure for a "
                    "different reporting period does not verify or contradict the "
                    "claim"),
            contradiction=False))

    # Numerical value within tolerance (only when the claim states a number and
    # the canonical datum carries one). Compared ONLY on a comparable period —
    # a cross-period datum is not compared at all (Problem C). Percentages are
    # compared as given.
    datum_value = datum.get("value")
    claimed_numbers = sorted({
        _normalise_number(t) for t in _NUMBER_RE.findall(claim_text)
        if _normalise_number(t)
        and _normalise_number(t) not in _period_value_tokens(claim_text)})
    if claimed_numbers and isinstance(datum_value, (int, float)) and period_comparable:
        # Compare each claimed number to the canonical value within a relative
        # tolerance that also tolerates ordinary rounding of a percentage.
        def _matches(n: str) -> bool:
            try:
                claimed = float(n)
            except ValueError:
                return False
            tol = max(abs(datum_value) * 0.02, 0.5)
            return abs(claimed - float(datum_value)) <= tol
        if not any(_matches(n) for n in claimed_numbers):
            # A value difference is a CONTRADICTION only on a demonstrably
            # equivalent basis (same reporting scope/entity, compatible currency
            # and the same reporting period); otherwise it is UNSUPPORTED.
            checks.append(DeterministicCheck(
                name="canonical_numerical_value_match", passed=False,
                detail=(f"claimed value(s) {claimed_numbers} do not match the "
                        f"canonical metric value {datum_value} (unit="
                        f"{datum.get('units')!r}, period={datum_period!r}, scope="
                        f"{datum.get('reporting_scope')!r})"
                        + (" within tolerance on a comparable basis"
                           if basis_comparable
                           else f"; not treated as a conflict because {basis_reason}")),
                contradiction=basis_comparable))
    elif claimed_numbers and isinstance(datum_value, (int, float)) and not period_comparable:
        # The claim states a value but the only canonical datum is for a
        # different period; it neither verifies nor contradicts the claim.
        checks.append(DeterministicCheck(
            name="canonical_numerical_value_match", passed=False,
            detail=(f"claimed value(s) {claimed_numbers} are not substantiated by the "
                    f"canonical metric value {datum_value} for period {datum_period!r}; "
                    f"{period_reason}"),
            contradiction=False))

    return tuple(checks)


def _deterministic_claim_checks(
    claim_text: str, evidence_ids: list[str], evidence_texts: dict[str, str],
) -> tuple[DeterministicCheck, ...]:
    """Deterministic numeric/period checks of a claim against its cited evidence.

    Returns an empty tuple when nothing machine-checkable is asserted or no
    cited evidence text is available (then the caller keeps its conservative
    disposition — citation-id existence is NOT entailment). No live model is
    called.

    CRITICAL distinction (Priority 2): absence of support is NOT contradiction,
    and a different number is NOT a contradiction unless the two statements are
    on a comparable basis (same metric, period and compatible unit/currency/
    scale). The central rule governs when CONTRADICTED may be used.

    * A claimed value/period simply ABSENT from the cited evidence fails with
      ``contradiction=False`` -> the caller downgrades to ``unverified``
      (UNSUPPORTED). The cited passage does not substantiate the claim; it does
      not establish a conflicting fact either.
    * A claimed value that is ABSENT while the cited evidence asserts a DIFFERENT
      value FOR THE SAME METRIC, PERIOD and compatible unit/currency/scale fails
      with ``contradiction=True`` -> the caller rejects it (CONTRADICTED). Only a
      directly conflicting assertion on a comparable basis qualifies. A different
      number for a different metric/period/scale is UNSUPPORTED, not a conflict.
    * A claimed fiscal period that is absent is UNSUPPORTED (``contradiction=
      False``): a citation covering a different period does not disprove a claim
      about another period.

    This is a conservative, surface-level textual gate. Comparability is
    established only from metric/period/unit vocabulary present in the texts; it
    does not establish entity, accounting basis or units with certainty, nor does
    it prove semantic entailment or causation. The richer canonical checks and
    human review carry anything this gate cannot settle.
    """
    corpus = " ".join(
        evidence_texts.get(eid, "") for eid in evidence_ids if eid in evidence_texts)
    if not corpus.strip():
        return ()
    checks: list[DeterministicCheck] = []

    # Only a directly comparable claim/passage may yield a numeric CONTRADICTED.
    comparable, incomparable_reason = _comparable_basis(claim_text, corpus)

    claimed_periods = {p.upper().replace(" ", "") for p in _PERIOD_RE.findall(claim_text)}
    corpus_periods = {p.upper().replace(" ", "") for p in _PERIOD_RE.findall(corpus)}

    # Problem B: a reporting-year token (e.g. the 2024 inside FY2024) plays the
    # role of a period, not a financial value, and must never count as a
    # conflicting figure. Exclude the period-role tokens from BOTH the claim and
    # corpus value sets (but keep genuine four-digit amounts that are not part of
    # a period phrase).
    claim_period_tokens = _period_value_tokens(claim_text)
    claimed_numbers = {
        _normalise_number(t) for t in _NUMBER_RE.findall(claim_text)
        if _normalise_number(t) and _normalise_number(t) not in claim_period_tokens
    }

    # Problem A: the numbers that can SUPPORT or CONTRADICT the claim are the
    # value(s) bound to the SAME metric in the passage — not every number in it.
    # We bind by metric when the claim names exactly one recognised metric and
    # the passage also names it; otherwise we fall back to the whole (period-
    # stripped) corpus number set, which is still gated by ``comparable`` (so an
    # unmatched-metric passage can never establish support/contradiction).
    claim_metric = _identify_canonical_metric(claim_text)
    corpus_period_tokens = _period_value_tokens(corpus)
    corpus_numbers = {
        _normalise_number(t) for t in _NUMBER_RE.findall(corpus)
        if _normalise_number(t) and _normalise_number(t) not in corpus_period_tokens
    }
    metric_bound = (
        _metric_bound_values(corpus, claim_metric) if claim_metric else set())
    # Use the metric-bound subset when we could identify the claim's metric and
    # that metric is actually named in the passage; this isolates the relevant
    # metric-value relationship and discards figures belonging to other metrics.
    use_bound = bool(claim_metric) and claim_metric in _identified_metrics(corpus)
    relevant_numbers = metric_bound if use_bound else corpus_numbers

    for number in sorted(claimed_numbers):
        # A claimed number counts as SUPPORTED only when it is present AMONG THE
        # VALUES OF THE CLAIM'S METRIC and the passage is on a comparable basis.
        # A coincidental token match for a DIFFERENT metric is NOT support.
        token_present = number in relevant_numbers
        present = token_present and comparable
        # A conflicting number is recorded ONLY when the passage asserts a
        # DIFFERENT value FOR THE SAME METRIC on a comparable basis. Absence of
        # any metric-bound value, or a difference across metric/period/scale, is
        # UNSUPPORTED — never CONTRADICTED. When binding is unavailable we fall
        # back to the whole corpus but keep the same comparability gate.
        conflicting = (not token_present) and bool(relevant_numbers) and comparable
        if present:
            detail = ""
        elif number in corpus_numbers and not token_present and use_bound:
            # The number appears in the passage but bound to a DIFFERENT metric
            # than the claim's (e.g. the net-debt 12 for a revenue claim). That
            # coincidental token must not establish support, and because the
            # claim's own metric carries a different value it does not silently
            # pass either.
            detail = (f"claimed value {number} appears in the cited evidence but "
                      f"only as the value of a different metric, not of "
                      f"{claim_metric!r}; a coincidental token match does not "
                      "establish support for this metric")
        elif token_present and not comparable:
            detail = (f"claimed value {number} appears in the cited evidence but "
                      f"that figure is not on a comparable basis ({incomparable_reason}), "
                      "so a coincidental token match does not establish support")
        elif conflicting:
            bound_note = (f" (bound to {claim_metric})" if use_bound else "")
            detail = (f"claimed value {number} conflicts with the value(s) "
                      f"{sorted(relevant_numbers)}{bound_note} asserted for the "
                      "same metric and period on a comparable basis in the cited "
                      "evidence")
        elif relevant_numbers and not comparable:
            detail = (f"claimed value {number} is not substantiated by the cited "
                      f"evidence; the other value(s) {sorted(relevant_numbers)} are "
                      f"not a conflicting assertion because {incomparable_reason}")
        else:
            detail = (f"claimed value {number} is not substantiated by the cited "
                      "evidence (absent; no conflicting value present for this "
                      "metric)")
        checks.append(DeterministicCheck(
            name="numeric_value_present_in_cited_evidence", passed=present,
            detail=detail, contradiction=conflicting))
    for period in sorted(claimed_periods):
        present = period in corpus_periods
        # A different fiscal period in a citation does not, by itself, disprove a
        # claim about another period: an absent claimed period is UNSUPPORTED,
        # never CONTRADICTED.
        if present:
            detail = ""
        else:
            detail = (f"claimed period {period} is not substantiated by the cited "
                      "evidence; a citation covering a different period does not "
                      "disprove a claim about this period")
        checks.append(DeterministicCheck(
            name="fiscal_period_present_in_cited_evidence", passed=present,
            detail=detail, contradiction=False))
    return tuple(checks)


class M3AssessmentError(ValueError):
    pass


def _date_ok(value: str | None) -> bool:
    if value is None:
        return True
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _issue(issues, rule_id, item_id, action, reason):
    issues.append({"rule_id": rule_id, "item_id": item_id,
                   "action": action, "reason": reason})


def _evidence_ok(ids: list[str], allowed: set[str]) -> bool:
    return bool(ids) and set(ids) <= allowed


def _structure_index(structure_facts: dict[str, Any] | None) -> dict[str, Any]:
    """Build the lookup sets Validator 7 uses to cross-check structural findings.

    Returns empty sets when no structure was supplied (the structural checks
    then no-op and existing callers are unaffected).
    """
    facts = structure_facts or {}
    nodes = facts.get("nodes") or []
    links = facts.get("links") or []
    source = facts.get("source") or {}
    borrower = facts.get("borrower") or {}
    pct_by_link = {
        l.get("link_id"): l.get("ownership_percent") for l in links
        if l.get("link_id")}
    return {
        "present": bool(structure_facts),
        "known_node_ids": {n.get("node_id") for n in nodes if n.get("node_id")},
        "known_link_ids": set(pct_by_link),
        "node_pct_by_link": pct_by_link,
        "guarantor_node_ids": set(facts.get("guarantor_node_ids") or []),
        "grouping_node_ids": set(facts.get("grouping_node_ids") or []),
        "borrower_node_id": borrower.get("node_id"),
        "is_simulated": bool(source.get("is_simulated")),
    }


def _govern_structural_finding(
    item: dict[str, Any], *, item_id: str, disposition: str | None,
    reasons: list[str], issues: list[dict], struct: dict[str, Any],
) -> str | None:
    """Cross-check a structural credit finding against the admitted structure.

    Applies the factual / guarantor / cash-flow / simulation-isolation /
    minority-JV / complexity rules from the Layer-2 spec. Returns the (possibly
    downgraded) disposition. No-op when no structure was supplied.
    """
    if not struct["present"]:
        return disposition

    node_refs = item.get("referenced_node_ids") or []
    link_refs = item.get("referenced_link_ids") or []
    supporting = item.get("supporting_evidence_ids") or []
    dimension = item.get("analytical_dimension")
    direction = item.get("credit_direction")
    mechanism = (item.get("causal_mechanism") or "").strip()
    text = " ".join((
        item.get("credit_hypothesis") or "", item.get("structural_observation") or "",
        item.get("causal_mechanism") or "")).casefold()

    # (a) FACTUAL referential integrity.
    unknown_nodes = [n for n in node_refs if n not in struct["known_node_ids"]]
    unknown_links = [l for l in link_refs if l not in struct["known_link_ids"]]
    if unknown_nodes or unknown_links:
        if disposition in {"supported", "qualified"}:
            disposition = "unverified"
        reason = ("Referenced corporate facts are not in the admitted structure: "
                  f"nodes={unknown_nodes} links={unknown_links}. A finding may only "
                  "reference validated node/link ids.")
        reasons.append(reason)
        _issue(issues, "m3.structural_reference_integrity", item_id, "downgraded", reason)
    # A referenced grouping node is not a verified legal person.
    grouping_refs = [n for n in node_refs if n in struct["grouping_node_ids"]]
    if grouping_refs and disposition in {"supported", "qualified"}:
        disposition = "qualified"
        reason = ("Referenced aggregate-grouping node(s) are drawn for context and "
                  f"are not independently established legal persons: {grouping_refs}.")
        reasons.append(reason)
        _issue(issues, "m3.grouping_node_not_legal_entity", item_id, "downgraded", reason)
    # A borrower-role assertion must match the admitted borrower.
    if item.get("borrower_specific") and struct["borrower_node_id"] and node_refs:
        if struct["borrower_node_id"] not in node_refs and (
                "borrower" in text) and disposition in {"supported", "qualified"}:
            disposition = "unverified"
            reason = ("A borrower-specific finding does not reference the admitted "
                      f"borrower node ({struct['borrower_node_id']}); a borrower role "
                      "may not be invented.")
            reasons.append(reason)
            _issue(issues, "m3.structural_borrower_invented", item_id, "downgraded", reason)
    # An ownership-percentage assertion must match the admitted link value.
    claimed_numbers = {_normalise_number(t) for t in _NUMBER_RE.findall(text)}
    for link_id in link_refs:
        pct = struct["node_pct_by_link"].get(link_id)
        if pct is None:
            continue
        pct_token = _normalise_number(f"{pct:g}")
        pct_pct_token = _normalise_number(f"{pct:g}%")
        # Only act when the finding asserts SOME percentage that is not the
        # admitted one (a bare percent like "35%" that disagrees).
        asserted_pcts = {n for n in claimed_numbers
                         if n and n not in {pct_token, pct_pct_token}
                         and f"{n}%" in text.replace(" ", "")}
        if asserted_pcts and disposition in {"supported", "qualified"}:
            disposition = "rejected"
            reason = (f"Ownership percentage asserted ({sorted(asserted_pcts)}) does "
                      f"not match the admitted link {link_id} value ({pct}).")
            reasons.append(reason)
            _issue(issues, "m3.structural_percentage_mismatch", item_id, "rejected", reason)

    has_admitted_support = bool(supporting)

    # (b) GUARANTOR coverage.
    if dimension == "guarantor_coverage" and direction == "positive":
        if not has_admitted_support and disposition in {"supported", "qualified"}:
            # Support rests only on a guarantor flag / ownership, no legal/debt doc.
            enforceable = any(w in text for w in (
                "enforceable", "legally binding", "guarantees the", "guaranteed"))
            disposition = "rejected" if enforceable and disposition == "supported" else "qualified"
            reason = ("A guarantor flag or ownership does not prove the entity "
                      "guarantees the proposed borrowing or that any guarantee is "
                      "enforceable; admitted legal/debt evidence is required.")
            reasons.append(reason)
            _issue(issues, "m3.guarantee_not_proven_by_flag", item_id, "downgraded", reason)
    if dimension == "guarantor_coverage" and struct["is_simulated"]:
        qualified_sim = any("simul" in q.casefold()
                            for q in (item.get("qualifications") or []))
        if not qualified_sim:
            if disposition in {"supported"}:
                disposition = "qualified"
            reason = ("The structure is simulated; a simulated guarantor marking is "
                      "not verified legal protection and must be qualified as such.")
            reasons.append(reason)
            _issue(issues, "m3.simulated_guarantor_not_verified", item_id, "downgraded", reason)

    # (c) CASH-FLOW access.
    if dimension == "cash_flow_access":
        unrestricted = any(w in text for w in (
            "unrestricted", "full access", "freely", "unimpeded"))
        if unrestricted and not has_admitted_support and disposition in {"supported", "qualified"}:
            disposition = "unverified"
            reason = ("Ownership percentage alone does not establish unrestricted "
                      "upstream cash access; dividend/covenant/statutory restriction "
                      "evidence is required.")
            reasons.append(reason)
            _issue(issues, "m3.cash_access_not_proven_by_ownership", item_id, "downgraded", reason)
        restriction_unknown = (
            item.get("assessment_availability") == "insufficient_evidence"
            or item.get("missing_information"))
        if not has_admitted_support and not restriction_unknown and disposition in {"supported", "qualified"}:
            reason = ("Distribution-restriction status is not established by admitted "
                      "evidence; the limitation must be reported (insufficient "
                      "evidence or missing_information).")
            reasons.append(reason)
            _issue(issues, "m3.cash_restriction_unknown", item_id, "recorded_gap", reason)

    # (d) SIMULATION ISOLATION.
    if struct["is_simulated"] and has_admitted_support and direction in {"positive", "negative"}:
        qualified_sim = any(("simul" in q.casefold() or "hypothetical" in q.casefold())
                            for q in (item.get("qualifications") or []))
        asserts_real = any(w in text for w in (
            "actual", "real", "confirmed", "established", "enforceable")) or (
            disposition == "supported")
        if asserts_real and not qualified_sim:
            if disposition in {"supported"}:
                disposition = "qualified"
            reason = ("The structure is simulated; combining the fictional hierarchy "
                      "with external (e.g. 10-K) evidence to assert real "
                      "debts/guarantees/restrictions must stay hypothetical and "
                      "qualified unless entity identity and legal applicability are "
                      "explicitly established.")
            reasons.append(reason)
            _issue(issues, "m3.simulation_isolation", item_id, "downgraded", reason)

    # (e) MINORITY / JV + COMPLEXITY must not be auto-negative.
    if direction == "negative" and not has_admitted_support and len(mechanism) < 15:
        if dimension == "minority_or_jv":
            if disposition in {"supported", "qualified"}:
                disposition = "unverified"
            reason = ("Minority ownership or a joint venture is not credit-negative "
                      "in itself; a defensible credit mechanism and supporting "
                      "evidence are required.")
            reasons.append(reason)
            _issue(issues, "m3.minority_not_auto_negative", item_id, "downgraded", reason)
        elif dimension == "structural_complexity":
            if disposition in {"supported", "qualified"}:
                disposition = "unverified"
            reason = ("Entity count or holding-company depth is not credit-negative "
                      "in itself; a defensible credit mechanism and supporting "
                      "evidence are required.")
            reasons.append(reason)
            _issue(issues, "m3.complexity_not_auto_negative", item_id, "downgraded", reason)

    return disposition


def _govern_finding(raw: dict[str, Any], *, allowed: set[str], borrower_known: bool,
                     issues: list[dict], bucket: str,
                     evidence_texts: dict[str, str] | None = None,
                     structure_facts: dict[str, Any] | None = None,
                     canonical_metrics: dict[str, dict[str, Any]] | None = None,
                     ) -> dict[str, Any]:
    item = deepcopy(raw)
    item_id = str(item.get("finding_id") or item.get("observation_id") or "unknown")
    supporting = item.get("supporting_evidence_ids") or []
    contradicting = item.get("contradicting_evidence_ids") or []
    reasons = list(item.get("validation_reasons") or [])
    disposition = item.get("disposition")
    if disposition in {"supported", "qualified"} and (
        not item.get("supporting_evidence_investigated")
        or not item.get("contradictory_evidence_investigated")
        or len((item.get("causal_mechanism") or item.get("credit_mechanism") or "").strip()) < 15
    ):
        disposition = "unverified"
        reasons.append(
            "A citation alone is insufficient: supporting and contradictory evidence searches and a credit mechanism must be recorded."
        )
        _issue(issues, "m3.evidence_sufficiency", item_id, "downgraded", reasons[-1])
    if not _evidence_ok(supporting, allowed) and disposition in {"supported", "qualified"}:
        disposition = "unverified"
        reasons.append("Supporting evidence is absent or does not resolve in the sealed M3 packet.")
        _issue(issues, "m3.evidence_integrity", item_id, "downgraded", reasons[-1])
    if contradicting and not set(contradicting) <= allowed:
        disposition = "unverified"
        reasons.append("Contradictory evidence contains an unknown reference.")
        _issue(issues, "m3.contradictory_evidence_integrity", item_id, "downgraded", reasons[-1])
    if item.get("borrower_specific") and not borrower_known:
        disposition = "unverified"
        reasons.append("Borrower-specific conclusion is unavailable because no borrower is identified.")
        _issue(issues, "m3.borrower_unavailable", item_id, "downgraded", reasons[-1])
    if bucket == "management_assessments":
        # Credit direction is governed SEPARATELY from disposition (credibility).
        # A credible explanation of an adverse development must never launder a
        # negative observation into a positive/neutral one.
        direction = item.get("credit_direction")
        if direction in {"negative", "mixed"} and disposition in {"supported", "qualified"}:
            note = ("A credible management explanation does not change the adverse "
                    "credit direction of the underlying development; credibility "
                    "and credit direction are assessed separately.")
            if not item.get("credibility_vs_direction_note"):
                item["credibility_vs_direction_note"] = note
            if note not in reasons:
                reasons.append(note)
                _issue(issues, "m3.credibility_vs_direction", item_id, "recorded_gap", note)
        # Reported background is useful, but is not outcome evidence of competence.
        text = " ".join((item.get("hypothesis") or "", item.get("credit_mechanism") or "")).lower()
        weak_basis = item.get("evidence_basis") in {
            "reported_experience", "credentials_or_tenure", "unclassified",
        }
        if disposition == "supported" and (weak_basis or (
            ("tenure" in text or "qualification" in text) and not contradicting
            or "no misconduct" in text or "absence of misconduct" in text
        )):
            disposition = "qualified"
            reasons.append("Reported experience, tenure, qualifications, or absence of misconduct alone cannot establish management competence or integrity; the observation remains attributed background unless outcome evidence is supplied.")
            _issue(issues, "m3.management_proxy", item_id, "downgraded", reasons[-1])
        if item.get("evidence_basis") == "control_assurance":
            scope = (item.get("assurance_scope") or "").casefold()
            if "financial reporting" not in scope or not item.get("assurance_date"):
                disposition = "unverified"
                reasons.append("Control assurance must identify its financial-reporting scope and assurance date; it cannot establish operational, clinical or strategic governance.")
                _issue(issues, "m3.icfr_scope", item_id, "downgraded", reasons[-1])
    elif bucket == "governance_screening":
        text = " ".join((item.get("credit_hypothesis") or "",
                         item.get("causal_mechanism") or "")).casefold()
        if disposition == "supported" and "internal control" in text and any(
                word in text for word in ("governance", "operational", "clinical", "strategic")):
            disposition = "qualified"
            reasons.append("Auditor assurance over internal control over financial reporting is date- and scope-specific and does not establish broader operational, clinical or strategic governance effectiveness.")
            _issue(issues, "m3.icfr_scope", item_id, "downgraded", reasons[-1])
    # Layer 2: structural credit findings are cross-checked against the admitted
    # corporate structure graph (referential integrity, guarantor/cash-flow/
    # simulation isolation, minority-JV and complexity safeguards).
    if bucket == "structural_credit_findings" and structure_facts is not None:
        disposition = _govern_structural_finding(
            item, item_id=item_id, disposition=disposition, reasons=reasons,
            issues=issues, struct=_structure_index(structure_facts))

    # Citation-id existence is NOT entailment: for a still-admitted claim that
    # asserts a specific value/period, run deterministic checks against the cited
    # evidence text and (where available) the resolved canonical financial datum
    # (no live model). The four-state outcome reuses existing dispositions:
    #   * CONTRADICTED (a conflicting value/period on a comparable basis) ->
    #     'rejected';
    #   * UNSUPPORTED  (absent, with no conflicting value present)         ->
    #     'unverified';
    #   * REQUIRES_HUMAN_REVIEW (causal/legal reasoning numbers cannot settle) ->
    #     'unverified' + a recorded human-review reason;
    #   * VERIFIED (all checks pass / nothing machine-checkable) -> the
    #     conservative disposition and its limitation stand.
    # Absence of support is never treated as contradiction.
    if evidence_texts and disposition in {"supported", "qualified"}:
        claim_text = " ".join((
            item.get("credit_hypothesis") or item.get("hypothesis") or "",
            item.get("causal_mechanism") or item.get("credit_mechanism") or ""))
        checks = list(_deterministic_claim_checks(claim_text, supporting, evidence_texts))
        if canonical_metrics:
            checks += list(_canonical_financial_checks(
                item, claim_text, canonical_metrics=canonical_metrics))
        failed = [c for c in checks if not c.passed]
        if failed:
            contradicted = any(c.contradiction for c in failed)
            disposition = "rejected" if contradicted else "unverified"
            for check in failed:
                classification = ("contradicted" if check.contradiction
                                   else "unsupported")
                reason = (f"Deterministic verification failed ({check.name}; "
                          f"{classification}): {check.detail or 'mismatch'}. A "
                          "matching citation id does not establish the asserted "
                          "value/period, and absence of support is not contradiction.")
                reasons.append(reason)
                _issue(issues, "m3.deterministic_entailment", item_id,
                       "rejected" if check.contradiction else "downgraded", reason)

    # A CAUSAL or LEGALLY significant claim cannot be settled by deterministic
    # numeric/period agreement: financial agreement does not establish a causal
    # explanation or a legal conclusion. Such a still-supported claim is capped
    # at REQUIRES_HUMAN_REVIEW (recorded as 'qualified' with a human-review
    # reason — never auto-promoted to 'supported'). The credibility-vs-direction
    # separation is untouched.
    if disposition == "supported" and _is_causal_or_legal_claim(
            item.get("credit_hypothesis"), item.get("hypothesis"),
            item.get("causal_mechanism"), item.get("credit_mechanism")):
        disposition = "qualified"
        reason = ("The claim rests on causal or legally significant reasoning that "
                  "deterministic checks cannot adjudicate; numeric/period agreement "
                  "does not prove the attribution or legal conclusion. Requires "
                  "human review before it may be treated as established.")
        reasons.append(reason)
        _issue(issues, "m3.requires_human_review", item_id, "recorded_gap", reason)

    item["disposition"] = disposition
    claim_type = ClaimType.INFERENCE
    item["admissibility"] = derive_admissibility(claim_type, Disposition(disposition)).value
    item["validation_reasons"] = reasons
    return item


def validate_m3_assessment(
    proposal: dict[str, Any], *, allowed_evidence_ids: set[str],
    validated_borrower_entity_ids: set[str] | None = None,
    evidence_texts: dict[str, str] | None = None,
    structure_facts: dict[str, Any] | None = None,
    canonical_metrics: dict[str, dict[str, Any]] | None = None,
) -> M3CorporateAssessment:
    """Validate, filter and conservatively downgrade an M3 model proposal.

    Structurally malformed contracts fail the specialist. Invalid graph edges are
    retained only in ``rejected_items``; dependent findings are downgraded. The
    original proposal is embedded verbatim for audit.
    """
    original = deepcopy(proposal)
    proposal = deepcopy(proposal)
    # Additive compatibility for 1.0 proposals created before identity_type was
    # explicit. Parent classifications denote an individual corporate node;
    # operating-subsidiary descriptions stay unresolved because they are often
    # collective groups or segments rather than named legal persons.
    for entity in proposal.get("legal_entities") or []:
        if "identity_type" not in entity:
            entity["identity_type"] = (
                "legal_entity" if entity.get("classification") in {
                    "ultimate_parent", "intermediate_holding_company",
                    "reporting_parent", "proposed_borrower",
                } else "unresolved")
    try:
        draft = M3CorporateAssessment.model_validate(proposal)
    except ValidationError as exc:
        raise M3AssessmentError(f"invalid m3_assessment contract: {exc}") from exc
    data = draft.model_dump(mode="json")
    issues: list[dict] = []
    rejected: list[dict] = []
    validated_borrowers = set(validated_borrower_entity_ids or ())
    evidence_texts = evidence_texts or {}

    entities: dict[str, dict] = {}
    clean_entities = []
    for entity in data["legal_entities"]:
        eid = entity["entity_id"]
        if eid in entities:
            _issue(issues, "m3.entity_id_unique", eid, "rejected", "Duplicate entity identifier.")
            rejected.append({"kind": "legal_entity", "item": entity,
                             "reason": "duplicate_entity_id"})
            continue
        if entity.get("identity_type") != "legal_entity":
            reason = f"{entity.get('identity_type') or 'unresolved'}_is_not_a_legal_person"
            _issue(issues, "m3.legal_entity_identity", eid, "rejected",
                   "Only an individually identified legal person may enter the legal-entity graph.")
            rejected.append({"kind": "legal_entity", "item": entity, "reason": reason})
            continue
        if entity["verification_status"] == "verified" and not _evidence_ok(
                entity["evidence_ids"], allowed_evidence_ids):
            entity["verification_status"] = "unverified"
            _issue(issues, "m3.entity_evidence", eid, "downgraded",
                   "Verified entity lacks a resolving sealed-packet evidence reference.")
        entities[eid] = entity
        clean_entities.append(entity)
    data["legal_entities"] = clean_entities

    borrower = data["borrower_identification"]
    if borrower["status"] == "identified" and (
        borrower["entity_id"] not in entities
        or not _evidence_ok(borrower["evidence_ids"], allowed_evidence_ids)
        or borrower["entity_id"] not in validated_borrowers
    ):
        _issue(issues, "m3.proposed_borrower_transaction_evidence",
               borrower.get("entity_id"), "downgraded",
               "Reporting identity, parent status, or historical debt issuance does not identify the obligor for the proposed facility; transaction evidence or a validated case specification is required.")
        borrower.update(status="unavailable", entity_id=None, evidence_ids=[],
                        limitation="Proposed borrower is unavailable; SO must confirm the facility obligor from transaction evidence.")
        if not any(d.get("module") == "SO" and "obligor" in d.get("question", "").casefold()
                   for d in data["downstream_dependencies"]):
            data["downstream_dependencies"].append({
                "module": "SO",
                "question": "Confirm the proposed facility obligor and legal borrower from transaction-specific evidence.",
            })
    borrower_known = borrower["status"] == "identified"

    seen_relationships: dict[tuple[str, str], dict] = {}
    clean_relationships = []
    for rel in data["ownership_relationships"]:
        rid = rel["relationship_id"]
        reason = None
        pct = rel["ownership_percentage"]
        if rel["parent_entity_id"] not in entities or rel["subsidiary_entity_id"] not in entities:
            reason = "relationship_references_unknown_entity"
        elif rel["parent_entity_id"] == rel["subsidiary_entity_id"]:
            reason = "self_relationship"
        elif pct is not None and not 0 <= pct <= 100:
            reason = "ownership_percentage_out_of_bounds"
        elif not _date_ok(rel["effective_from"]) or not _date_ok(rel["effective_to"]):
            reason = "invalid_effective_date"
        elif rel["confirmation_status"] == "verified" and not _evidence_ok(
                rel["evidence_ids"], allowed_evidence_ids):
            reason = "verified_relationship_lacks_evidence"
        key = (rel["parent_entity_id"], rel["subsidiary_entity_id"])
        prior = seen_relationships.get(key)
        if prior and (prior.get("ownership_percentage") != pct
                      or prior.get("control_basis") != rel.get("control_basis")):
            reason = "contradictory_ownership_or_control"
        if reason:
            _issue(issues, "m3.ownership_integrity", rid, "rejected", reason)
            rejected.append({"kind": "ownership_relationship", "item": rel,
                             "reason": reason})
            continue
        seen_relationships[key] = rel
        clean_relationships.append(rel)
    data["ownership_relationships"] = clean_relationships

    seen_transfer_ids: set[str] = set()
    for bucket, source_key, destination_key in (
        ("cash_transfer_relationships", "source_entity_id", "destination_entity_id"),
    ):
        clean = []
        for rel in data[bucket]:
            item_id = rel["transfer_id"]
            if item_id in seen_transfer_ids:
                _issue(issues, "m3.transfer_id_unique", item_id, "rejected",
                       "Duplicate cash-transfer identifier.")
                rejected.append({"kind": bucket, "item": rel,
                                 "reason": "duplicate_transfer_id"})
                continue
            seen_transfer_ids.add(item_id)
            if rel[source_key] not in entities or rel[destination_key] not in entities:
                _issue(issues, "m3.transfer_referential_integrity", item_id, "rejected",
                       "Cash-transfer relationship references an unknown entity.")
                rejected.append({"kind": bucket, "item": rel,
                                 "reason": "unknown_entity"})
                continue
            if rel["confirmation_status"] == "verified" and not _evidence_ok(
                    rel["evidence_ids"], allowed_evidence_ids):
                rel["confirmation_status"] = "unverified"
                _issue(issues, "m3.transfer_evidence", item_id, "downgraded",
                       "Verified cash-transfer relationship lacks resolving evidence.")
            clean.append(rel)
        data[bucket] = clean

    clean_cash = []
    seen_cash_ids: set[str] = set()
    for cash in data["cash_flow_locations"]:
        cid = cash["cash_flow_id"]
        reason = None
        if cid in seen_cash_ids:
            reason = "duplicate_cash_flow_id"
        seen_cash_ids.add(cid)
        entity = entities.get(cash["entity_id"])
        measure = cash.get("measure_type", "unclassified")
        roles = set(cash.get("cash_flow_roles") or [])
        if entity is None:
            reason = "unknown_entity"
        elif cash["amount"] is not None and not _evidence_ok(
                cash["evidence_ids"], allowed_evidence_ids):
            reason = "amount_lacks_resolving_evidence"
        elif (cash["amount"] is not None and cash["reporting_scope"] == "consolidated"
              and entity["classification"] in {
                  "operating_subsidiary", "joint_venture", "partially_owned_subsidiary"
              }):
            reason = "unsupported_subsidiary_allocation_of_consolidated_cash_flow"
        elif measure in {"revenue", "ebitda", "operating_income", "unclassified"} and roles:
            reason = "financial_measure_is_not_cash_flow"
        elif measure == "cash_balance" and "operating_cash_generator" in roles:
            reason = "cash_balance_is_not_cash_generation"
        elif measure == "distributable_cash" and cash["reporting_scope"] != "entity":
            reason = "distributable_cash_requires_entity_scope"
        if reason:
            _issue(issues, "m3.cash_flow_semantics", cid, "rejected", reason)
            rejected.append({"kind": "cash_flow_location", "item": cash, "reason": reason})
            continue
        clean_cash.append(cash)
    data["cash_flow_locations"] = clean_cash

    for bucket in ("structural_credit_findings", "governance_screening",
                   "management_assessments"):
        seen_ids: set[str] = set()
        clean = []
        id_field = "observation_id" if bucket == "management_assessments" else "finding_id"
        for item in data[bucket]:
            item_id = item[id_field]
            if item_id in seen_ids:
                _issue(issues, "m3.finding_id_unique", item_id, "rejected",
                       f"Duplicate identifier in {bucket}.")
                rejected.append({"kind": bucket, "item": item,
                                 "reason": "duplicate_finding_id"})
                continue
            seen_ids.add(item_id)
            clean.append(_govern_finding(
                item, allowed=allowed_evidence_ids, borrower_known=borrower_known,
                issues=issues, bucket=bucket, evidence_texts=evidence_texts,
                structure_facts=(structure_facts
                                 if bucket == "structural_credit_findings" else None),
                canonical_metrics=canonical_metrics))
        data[bucket] = clean

    identities: dict[str, tuple[str, str]] = {}
    for item in data["management_assessments"]:
        key = item["management_id"]
        identity = (item["legal_name"].casefold().strip(), item["role"].casefold().strip())
        if key in identities and identities[key] != identity:
            item["disposition"] = "unverified"
            item["admissibility"] = "audit_only"
            item["validation_reasons"].append("Management identity or role is inconsistent.")
            _issue(issues, "m3.management_identity", item["observation_id"],
                   "downgraded", item["validation_reasons"][-1])
        if not _date_ok(item["effective_from"]) or not _date_ok(item["effective_to"]):
            item["disposition"] = "unverified"
            item["admissibility"] = "audit_only"
            item["validation_reasons"].append("Management effective date is invalid.")
            _issue(issues, "m3.management_effective_date", item["observation_id"],
                   "downgraded", item["validation_reasons"][-1])
        identities.setdefault(key, identity)

    if not data["information_gaps"]:
        data["information_gaps"] = [
            "Completeness of corporate structure and management evidence was not established."
        ]
        _issue(issues, "m3.required_information_gaps", None, "recorded_gap",
               data["information_gaps"][0])
    if not data["governance_screening"] and not any(
            "governance" in gap.casefold() or "related-part" in gap.casefold()
            for gap in data["information_gaps"]):
        data["information_gaps"].append(
            "Governance and related-party screening found no conclusive evidence; risk absence is not established."
        )
        _issue(issues, "m3.governance_screen_limitation", None, "recorded_gap",
               data["information_gaps"][-1])
    data["validation_issues"] = issues
    data["rejected_items"] = rejected
    data["model_proposal"] = original
    return M3CorporateAssessment.model_validate(data)
