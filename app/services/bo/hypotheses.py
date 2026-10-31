"""Deterministic validation and storage of BO hypothesis records.

A specialist's hypotheses pass through this layer before any of them can inform
the orchestrator. Two kinds of outcome, mirroring the existing validation split:

* HARD issues reject the agent's whole output (nothing is promoted): an unknown
  or unadmitted evidence id, an invented or mismatched number, a quoted
  calculation bound to the wrong unit / period / denominator, a score-like
  field, malformed or duplicate ids, an interaction built on hypotheses that do
  not exist.
* SOFT adjustments keep the output but stop an individual claim from becoming
  an established finding: no verbatim quote, a number with no binding, a "fact"
  that is really management belief, a "supported" claim with counter-evidence,
  a mitigant called effective without an evidenced outcome. Every adjustment is
  written to ``validation_notes``.

Validity, direction and confidence are never derived from one another here.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.orm import HypothesisRecordRow
from app.schemas.agentic import AcceptanceState, Materiality
from app.schemas.bo import (
    Admissibility,
    ClaimType,
    Confidence,
    Direction,
    DisplayPriority,
    Disposition,
    HypothesisRecord,
    MitigantAssessment,
    MitigantEffectiveness,
)
from app.services.bo import inference_rules
from app.services.bo.indicators import IndicatorSet
from app.services.bo.plans import (
    LABEL_PARAMETERS,
    REQUIRES_COMPANY_EVIDENCE,
    BoAgentSpec,
)
from app.services.bo.presentation import classify_presentation
from app.services.bo.reconcile import tokens
from app.services.bo.recovery import recover_quote
from app.services.orchestration.benchmark_context import (
    _METRIC_NUMBER,
    _SCORE_KEYS,
    _matches,
    metric_value,
)

MIN_QUOTE_CHARS = 12

# Belief / intent language: a quote carrying it supports a management claim,
# not an established fact.
_BELIEF = re.compile(
    r"\b(?:we|management|the company)\s+(?:\w+\s+)?(?:believes?|expects?|intends?|"
    r"anticipates?|plans?|seeks?|aims?|estimates?|hopes?)\b|"
    r"\bour (?:strategy|goal|objective|belief|mission|vision)\b|"
    r"\b(?:is|are) (?:designed|intended) to\b|"
    r"\bwe (?:are focused on|strive|endeavou?r|are committed to|continue to seek)\b|"
    r"\bshould (?:enable|allow|help)\b",
    re.I,
)

# Risk-factor language about what COULD happen, as opposed to what has.
_HYPOTHETICAL = re.compile(
    r"\b(?:could|may|might|would)\b|\bif (?:we|any|our|the)\b|\bin the event\b|"
    r"no assurance|cannot (?:assure|predict|guarantee|be certain)|\bpotential(?:ly)?\b|"
    r"\bwe (?:are|may be) subject to\b|\buncertain\b",
    re.I,
)
# Language that reports something specific that exists or happened.
_REALIZED = re.compile(
    r"\d|\b(?:was|were|has been|have been|had been|represented|accounted for|totaled|"
    r"increased|decreased|declined|grew|completed|acquired|recorded|derived|"
    r"received|operated?|owns?|owned|are currently|is currently|currently)\b|"
    r"for the years? ended|as of (?:December|January|the)",
    re.I,
)
COMPANY_SPECIFIC = ("company_specific_disclosure", "deterministic_calculation")
# Share of a fact's content words that must also occur in its verified quotes.
MIN_QUOTE_COVERAGE = 0.15

INTERPRETATION_FIELDS = ("implication", "magnitude", "trend", "watch_trigger")
# Words that describe a size or a change without stating it.
_MAGNITUDE_WORDS = re.compile(
    r"\b(?:significant(?:ly)?|substantial(?:ly)?|large|majority|concentrat\w*|"
    r"share|portion|proportion|intensi\w*|margins?|grow(?:th|s|n)?|grew|"
    r"increas\w*|decreas\w*|declin\w*|rose|fell)\b",
    re.I,
)


def derive_evidence_basis(*, quotes: list[str], has_calculation: bool,
                          has_other_support: bool) -> str:
    """Classify what a hypothesis rests on, from its VERIFIED quotes only.

    A heuristic reading of the quoted wording: it separates reported specifics
    from belief, from hypothetical risk-factor language and from general
    statements. It does not judge whether the quote proves the claim.
    """
    if has_calculation:
        return "deterministic_calculation"
    if not quotes:
        return "inference" if has_other_support else "none"
    if all(_BELIEF.search(q) for q in quotes):
        return "management_statement"
    if any(_REALIZED.search(q) and not _HYPOTHETICAL.search(q) for q in quotes):
        return "company_specific_disclosure"
    if all(_HYPOTHETICAL.search(q) for q in quotes):
        return "generic_risk_disclosure"
    if any(_REALIZED.search(q) for q in quotes):
        return "company_specific_disclosure"
    return "general_statement"

_TRANSLATE = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ",
})


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").translate(_TRANSLATE)).strip()


def quote_in_passage(quote: str, passage_text: str) -> bool:
    needle = normalise(quote)
    return len(needle) >= MIN_QUOTE_CHARS and needle in normalise(passage_text)


_CHANGE_WORD = re.compile(
    r"\b(?:reduc|decreas|increas|improv|declin|lower|higher|grew|grow|fell|rose|"
    r"rise|risen|drop|cut|saved|saving)\w*", re.I)


def shows_measured_outcome(text: str) -> bool:
    """Heuristic: the passage reports a measured change (a figure + a change word)."""
    return bool(re.search(r"\d", text or "")) and bool(_CHANGE_WORD.search(text or ""))


def derive_admissibility(claim_type: ClaimType, disposition: Disposition) -> Admissibility:
    if disposition in (Disposition.REJECTED, Disposition.UNVERIFIED):
        return Admissibility.AUDIT_ONLY
    if claim_type is ClaimType.ASSUMPTION:
        return Admissibility.AUDIT_ONLY
    if disposition is Disposition.QUALIFIED or claim_type is ClaimType.INFERENCE:
        return Admissibility.QUALIFIED_NARRATIVE
    if claim_type is ClaimType.MANAGEMENT_CLAIM:
        return Admissibility.ATTRIBUTED
    return Admissibility.ESTABLISHED


def score_key_paths(node: Any, path: str = "") -> list[str]:
    """Paths of score/band/rating-like keys anywhere in a model answer."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() in _SCORE_KEYS:
                found.append(f"{path}{key}")
            found.extend(score_key_paths(value, f"{path}{key}."))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(score_key_paths(value, f"{path}{index}."))
    return found


def unbound_numbers(text: str, quoted_values: list[float], quotes: list[str]) -> list[str]:
    """Metric-like numbers in ``text`` bound to neither a calculation nor a quote."""
    haystack = " ".join(normalise(q) for q in quotes)
    loose = []
    for match in _METRIC_NUMBER.finditer(text or ""):
        token = match.group(0)
        if _matches(abs(metric_value(token)), [abs(v) for v in quoted_values]):
            continue
        if re.search(rf"(?<![\d.]){re.escape(token.lstrip('-'))}(?!\d)", haystack):
            continue
        loose.append(token)
    return loose


# Identifies the validation logic on every record it produces. Bump when a
# check changes what is accepted, held back or rejected.
HYPOTHESIS_VALIDATOR_VERSION = "bo-hypothesis-validator-2.2.1"
_V2_VALIDATOR_IDS = {"BO1": "validator_1", "BO2": "validator_2", "BO3": "validator_3",
                     "BO4": "validator_4", "BO5": "validator_5", "BO6": "agent6_governance"}


def validator_identity(spec: BoAgentSpec) -> str:
    """The logically separate validator that governs ``spec``'s output.

    Validation code is shared; the identity (and the sealed packet, owned
    parameters and id prefix it is bound to) is per agent.
    """
    return spec.validator_id or _V2_VALIDATOR_IDS.get(spec.code) or f"validator_{spec.code}"


@dataclass
class HypothesisValidation:
    records: list[HypothesisRecord] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    adjustments: list[dict[str, str]] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.issues


class HypothesisValidator:
    """Validate one specialist's hypotheses against its admitted material."""

    def __init__(
        self,
        spec: BoAgentSpec,
        *,
        passages: dict[str, str],
        indicators: IndicatorSet,
        primary_hypotheses: dict[str, HypothesisRecord] | None = None,
        corpus: list[dict[str, Any]] | None = None,
        apply_presentation_policy: bool = False,
        materiality_basis: str | None = None,
        enforce: bool = True,
    ) -> None:
        self._spec = spec
        # Advisory (``enforce=False``): every check still runs and is noted, but
        # the model's own claim type, disposition and materiality stand and its
        # quotes are kept. Only what a record cannot be built without stays hard.
        self._enforce = enforce
        # v3: step 11 class comes from the deterministic policy, not the model.
        # v2 keeps its recorded behaviour (model label, demoted if audit-only).
        self._presentation_policy = apply_presentation_policy
        # Declared by the caller, never by the model: "rubric" when materiality
        # comes from the deterministic rubric (investigation path).
        self._materiality_basis = materiality_basis
        self.validator_id = validator_identity(spec)
        self._passages = dict(passages)  # admitted evidence_id -> passage text
        self._indicators = indicators
        self._known = indicators.known_values()
        # The indicators this specialist was given (its groups, usable values).
        self._agent_indicator_ids = sorted(
            t.parameter_result_id for t in indicators.traces
            if t.group in spec.indicator_groups and t.parameter_result_id in self._known)
        self._primary = primary_hypotheses
        # The admitted filing in document order; enables quote recovery.
        self._corpus = corpus or []
        self._corpus_text = {p["evidence_id"]: p.get("text") or ""
                             for p in self._corpus}

    def validate(self, parsed: dict[str, Any], *, analysis_run_id: str,
                 agent_run_id: str) -> HypothesisValidation:
        out = HypothesisValidation()
        for path in score_key_paths(parsed):
            message = (f"official_score_assignment: {path!r} is not permitted; scores "
                       "are deterministic.")
            if self._enforce:
                out.issues.append(message)
            else:
                out.adjustments.append({"hypothesis_id": "(output)",
                                        "note": "Advisory: " + message + " Ignored."})
        items = [h for h in parsed.get("hypotheses") or [] if isinstance(h, dict)]
        ids = [h.get("hypothesis_id") for h in items]
        pattern = re.compile(rf"^{self._spec.code}-H\d{{1,4}}$")
        for hid in ids:
            if not isinstance(hid, str) or not pattern.match(hid):
                out.issues.append(
                    f"hypothesis_id {hid!r} does not match '{self._spec.code}-Hnnn'.")
        for hid in sorted({h for h in ids if ids.count(h) > 1}, key=str):
            out.issues.append(f"hypothesis_id {hid!r} is used more than once.")
        dispositions = {h.get("hypothesis_id"): h.get("disposition") for h in items}

        for item in items:
            record = self._one(item, dispositions, out, analysis_run_id, agent_run_id)
            if record is not None:
                out.records.append(record)
        bound = {p for r in out.records for p in r.parameter_result_ids}
        unused = [i for i in self._agent_indicator_ids if i not in bound]
        if out.records and unused:
            out.adjustments.append({
                "hypothesis_id": "(output)",
                "note": ("Indicators supplied but bound by no hypothesis: "
                         + ", ".join(unused) + ".")})
        if out.issues:
            out.records = []  # no partial retention
        return out

    # -- one hypothesis -------------------------------------------------------

    def _one(self, item, dispositions, out, analysis_run_id, agent_run_id):
        hid = item.get("hypothesis_id")
        notes: list[str] = []

        def adjust(message: str) -> None:
            notes.append(message)
            out.adjustments.append({"hypothesis_id": str(hid), "note": message})

        try:
            claim_type = ClaimType(item["claim_type"])
            direction = Direction(item["direction"])
            disposition = Disposition(item["disposition"])
            confidence = Confidence(item.get("confidence") or "medium")
            materiality = Materiality(item.get("materiality") or "medium")
            display = DisplayPriority(item.get("display_priority") or "on_demand")
        except (KeyError, ValueError) as exc:
            message = f"Hypothesis {hid!r} has an invalid enum value: {exc}."
            if self._enforce:
                out.issues.append(message)
            else:
                adjust(f"Advisory: {message} The hypothesis cannot be recorded and was skipped.")
            return None
        stated = (claim_type, disposition, materiality)

        supporting = list(dict.fromkeys(item.get("supporting_evidence_ids") or []))
        contradicting = list(dict.fromkeys(item.get("contradicting_evidence_ids") or []))
        quotes = [q for q in item.get("evidence_quotes") or [] if isinstance(q, dict)]
        mitigant_raw = item.get("mitigant") if isinstance(item.get("mitigant"), dict) else None
        outcome_ids = list((mitigant_raw or {}).get("outcome_evidence_ids") or [])

        unknown = sorted({str(e) for e in (*supporting, *contradicting, *outcome_ids,
                                           *(q.get("evidence_id") for q in quotes))
                          if e not in self._passages})
        if unknown and self._enforce:
            out.issues.extend(f"Hypothesis {hid!r} cites unknown/unadmitted evidence {eid!r}."
                              for eid in unknown)
        elif unknown:
            # A citation must resolve to an admitted passage to be shown at all.
            adjust("Advisory: dropped citations of unknown/unadmitted evidence "
                   + ", ".join(unknown) + ".")
            supporting = [e for e in supporting if e in self._passages]
            contradicting = [e for e in contradicting if e in self._passages]
            outcome_ids = [e for e in outcome_ids if e in self._passages]
            quotes = [q for q in quotes if q.get("evidence_id") in self._passages]

        # --- quoted calculations: exact value, unit, period, denominator -----
        # A defective binding (invented id, different value, wrong unit, period
        # or denominator) makes THIS hypothesis unusable; it never passes, and it
        # does not discard the specialist's other findings.
        quoted_numbers: list[float] = []
        parameter_result_ids: list[str] = []
        binding_defects: list[str] = []
        for entry in item.get("quoted_values") or []:
            if not isinstance(entry, dict):
                continue
            prid, value = entry.get("parameter_result_id"), entry.get("value")
            trace = self._indicators.trace_for(prid) if prid else None
            if prid not in self._known or trace is None:
                binding_defects.append(
                    f"quotes {value!r} for {prid!r}, which is not an accepted "
                    "deterministic result (invented number)")
                continue
            try:
                same = abs(float(value) - float(self._known[prid])) <= 1e-9
            except (TypeError, ValueError):
                same = False
            if not same:
                binding_defects.append(
                    f"quotes {value!r} for {prid!r} but the calculated value is "
                    f"{self._known[prid]!r} (no LLM arithmetic)")
                continue
            expected = {
                "unit": trace.units, "period": trace.period,
                "denominator": (trace.denominator or {}).get("name"),
            }
            mismatched = [
                f"quotes {prid!r} with {key} {entry.get(key)!r} but the "
                f"calculation's {key} is {want!r}"
                for key, want in expected.items()
                if entry.get(key) is not None and str(entry.get(key)) != str(want)]
            if mismatched:
                binding_defects.extend(mismatched)
                continue
            quoted_numbers.append(float(value))
            parameter_result_ids.append(prid)

        # --- verbatim quotes, with one bounded recovery attempt each ----------
        verified_quotes = []
        discarded_quotes = []
        recovery_records = []
        for quote in quotes:
            eid, text = quote.get("evidence_id"), quote.get("quote") or ""
            if eid in self._passages and quote_in_passage(text, self._passages[eid]):
                verified_quotes.append({"evidence_id": eid, "quote": normalise(text)})
                continue
            if eid not in self._passages:
                continue  # already a hard issue above
            attempt = (recover_quote(quote=text, cited_evidence_id=eid,
                                     corpus=self._corpus) if self._corpus else None)
            if attempt is not None:
                attempt["hypothesis_id"] = hid
                recovery_records.append(attempt)
            if attempt is not None and attempt["outcome"] in ("layout_gap", "relocated"):
                new_id = attempt["replacement_evidence_ids"][0]
                verified_quotes.append({
                    "evidence_id": new_id, "quote": attempt["replacement_quote"],
                    "recovered": attempt["outcome"]})
                for rid in attempt["replacement_evidence_ids"]:
                    # A recovered passage is admitted filing text.
                    self._passages.setdefault(rid, self._corpus_text.get(rid, ""))
                    if rid not in supporting and eid in supporting:
                        supporting.append(rid)
                adjust(
                    "Quote was not verbatim in the cited passage; recovered from "
                    f"the admitted filing ({attempt['outcome']}): {attempt['detail']}. "
                    "The source wording replaces the model's quote; the claim is "
                    "unchanged.")
            else:
                discarded_quotes.append({"evidence_id": eid, "quote": normalise(text)})
                adjust(f"A quote attributed to {eid[:12]} was not found verbatim in "
                       "that passage and was discarded"
                       + ("; one recovery attempt over the admitted filing found "
                          "no match." if attempt is not None else "."))
        quote_texts = [q["quote"] for q in verified_quotes]

        corrects = item.get("corrects_hypothesis_id")
        if corrects is not None:
            if corrects not in dispositions:
                adjust(f"corrects_hypothesis_id {corrects} does not exist; the "
                       "link was dropped.")
                corrects = None
            elif dispositions[corrects] != Disposition.REJECTED.value:
                adjust(f"corrects_hypothesis_id {corrects} is not a rejected "
                       "hypothesis; the link was dropped.")
                corrects = None

        components = list(item.get("component_hypothesis_ids") or [])
        kind = item.get("interaction_kind")
        mechanism = (item.get("transmission_mechanism") or "").strip() or None
        component_records: list[HypothesisRecord] = []
        if self._primary is not None:  # BO6
            invalid_components = []
            for cid in components:
                primary = self._primary.get(cid)
                if primary is None or not primary.usable:
                    invalid_components.append(cid)
                else:
                    component_records.append(primary)
            if invalid_components and disposition in (
                    Disposition.SUPPORTED, Disposition.QUALIFIED):
                # A rejected, unverified or non-existent finding cannot be a
                # premise: the interaction built on it is not usable.
                adjust("Builds on " + ", ".join(invalid_components) + ", which "
                       "is not a validated primary hypothesis; marked unverified.")
                disposition = Disposition.UNVERIFIED
            components = [c for c in components if c not in invalid_components]
            needed = 2 if kind == "compound_risk" else 1
            if len(set(components)) < needed and disposition in (
                    Disposition.SUPPORTED, Disposition.QUALIFIED):
                adjust("An interaction finding needs validated primary hypotheses "
                       f"as components (at least {needed}); marked unverified.")
                disposition = Disposition.UNVERIFIED
        elif components:
            adjust("component_hypothesis_ids is only meaningful for BO6; ignored.")
            components, kind = [], None

        live = disposition in (Disposition.SUPPORTED, Disposition.QUALIFIED)
        binding_flags: list[dict[str, Any]] = []
        if binding_defects:
            for defect in binding_defects:
                adjust("Numeric binding rejected: " + defect + ".")
            binding_flags.append({
                "rule_id": "numeric_binding_invalid", "layer": "B_numerical_integrity",
                "effect": "audit_only", "heuristic": False,
                "description": "A number was bound to a deterministic result that "
                               "does not exist or does not match in value, unit, "
                               "period or denominator.",
                "correction": "Copy text figures in a verbatim quote; bind only "
                              "listed indicators with their exact value.",
                "fo_handoff_variable": None})
            if live:
                disposition, live = Disposition.UNVERIFIED, False

        # --- attribution: belief language supports a claim, not a fact --------
        if (live and claim_type is ClaimType.FACT and quote_texts
                and all(_BELIEF.search(q) for q in quote_texts)):
            adjust("The only supporting quotes express management belief or "
                   "intent; reclassified from fact to management_claim.")
            claim_type = ClaimType.MANAGEMENT_CLAIM

        if live and claim_type in (ClaimType.FACT, ClaimType.MANAGEMENT_CLAIM) \
                and not verified_quotes:
            adjust(f"A {claim_type.value} needs a verbatim quote from a cited "
                   "admitted passage; none verified, so it is unverified.")
            disposition, live = Disposition.UNVERIFIED, False
        if live and claim_type is ClaimType.CALCULATED_FACT and not parameter_result_ids:
            adjust("A calculated_fact must be bound to a deterministic result in "
                   "quoted_values; marked unverified.")
            disposition, live = Disposition.UNVERIFIED, False
        if live and claim_type is ClaimType.INFERENCE and not (
                supporting or parameter_result_ids or components):
            adjust("An inference with no supporting evidence is unverified.")
            disposition, live = Disposition.UNVERIFIED, False

        # BO6 may restate numbers its components already carry.
        inherited_quotes = [q["quote"] for c in component_records
                            for q in c.evidence_quotes]
        inherited_numbers = [float(q["value"]) for c in component_records
                             for q in c.quoted_values
                             if isinstance(q.get("value"), (int, float))]
        loose = unbound_numbers(item.get("claim") or "",
                                [*quoted_numbers, *inherited_numbers],
                                [*quote_texts, *inherited_quotes])
        if live and loose:
            adjust(f"Number(s) {', '.join(sorted(set(loose)))} are bound to neither a "
                   "deterministic result nor a verbatim quote; marked unverified.")
            disposition, live = Disposition.UNVERIFIED, False

        # The interpretation is commentary on the finding, so an unbound number
        # there removes that field only; the finding itself is unaffected.
        interpretation: dict[str, str] = {}
        raw_interpretation = item.get("interpretation")
        for key in INTERPRETATION_FIELDS:
            text = ((raw_interpretation or {}).get(key) if isinstance(
                raw_interpretation, dict) else None)
            text = text.strip() if isinstance(text, str) else ""
            if not text:
                continue
            loose = unbound_numbers(text, [*quoted_numbers, *inherited_numbers],
                                    [*quote_texts, *inherited_quotes])
            if loose:
                unbound = (f"number(s) {', '.join(sorted(set(loose)))} are bound to "
                           "neither a deterministic result nor a verbatim quote")
                if self._enforce:
                    adjust(f"Interpretation {key} dropped: {unbound}.")
                    continue
                adjust(f"Advisory: interpretation {key} kept although {unbound}.")
            interpretation[key] = text

        # A size or change described in words while a measuring indicator was
        # supplied: noted for review, never a downgrade (lexical heuristic).
        # Interactions inherit their components' figures, and a "could" risk
        # statement describes no actual size, so neither is flagged.
        claim = item.get("claim") or ""
        if (live and self._primary is None and not parameter_result_ids
                and self._agent_indicator_ids and _MAGNITUDE_WORDS.search(claim)
                and not _HYPOTHETICAL.search(claim)
                and not _METRIC_NUMBER.search(claim)):
            flags_pending = [{
                "rule_id": "unquantified_magnitude",
                "layer": "B_numerical_integrity", "effect": "note",
                "description": "The claim describes a size or change in words, "
                               "binds no indicator and states no figure, although "
                               "indicators were supplied to this specialist.",
                "heuristic": True, "fo_handoff_variable": None,
                "correction": "Bind the indicator that measures it, or quote the "
                              "figure the filing gives."}]
        else:
            flags_pending = []

        if disposition is Disposition.SUPPORTED and contradicting:
            adjust("Counter-evidence is cited, so the hypothesis is qualified "
                   "rather than supported.")
            disposition = Disposition.QUALIFIED

        # --- Layer D: what kind of support is this? ---------------------------
        basis = derive_evidence_basis(
            quotes=quote_texts, has_calculation=bool(parameter_result_ids),
            has_other_support=bool(supporting or components))
        flags: list[dict[str, Any]] = [*binding_flags, *flags_pending]
        if (live and basis == "generic_risk_disclosure"
                and claim_type is not ClaimType.MANAGEMENT_CLAIM):
            flags.append({
                "rule_id": "generic_risk_disclosure_only",
                "layer": "D_evidence_sufficiency", "effect": "qualify",
                "description": "Supported only by risk-factor language about what "
                               "could happen; it may be reported as a disclosed "
                               "risk, not as an event that has occurred.",
                "heuristic": True, "fo_handoff_variable": None, "correction": None})

        # A genuine quote that shares almost no wording with the claim does not
        # show that the filing STATES the claim (a lexical check, not entailment).
        if (live and claim_type is ClaimType.FACT and quote_texts
                and not parameter_result_ids):
            claim_terms = tokens(item.get("claim") or "")
            quote_terms = tokens(" ".join(quote_texts))
            if len(claim_terms) >= 3 and (
                    len(claim_terms & quote_terms) / len(claim_terms) < MIN_QUOTE_COVERAGE):
                flags.append({
                    "rule_id": "quote_does_not_cover_claim",
                    "layer": "D_evidence_sufficiency", "effect": "qualify",
                    "description": "The quote is verified, but it shares almost no "
                                   "wording with the claim, so it does not show "
                                   "that the filing states the claim; treated as "
                                   "the specialist's inference.",
                    "heuristic": True, "fo_handoff_variable": None,
                    "correction": "Quote the sentence that states the claim, or "
                                  "present the claim as an inference."})

        # --- Layer C: known unsupported inferential steps ---------------------
        if live:
            flags += inference_rules.evaluate(
                claim=item.get("claim") or "", mechanism=mechanism,
                claim_type=claim_type.value, evidence_basis=basis)

        # --- BO6: an interaction cannot be stronger than what it is built on --
        if self._primary is not None and live:
            if not mechanism or len(mechanism) < 20:
                adjust("An interaction needs a stated transmission mechanism; "
                       "marked unverified.")
                disposition, live = Disposition.UNVERIFIED, False
            weaker = [c.hypothesis_id for c in component_records
                      if c.admissibility is not Admissibility.ESTABLISHED]
            if live and disposition is Disposition.SUPPORTED and weaker:
                adjust("Built on qualified or management-attributed inputs "
                       f"({', '.join(weaker)}); it cannot be more certain than "
                       "they are, so it is qualified.")
                disposition = Disposition.QUALIFIED
            order = {Materiality.LOW: 0, Materiality.MEDIUM: 1, Materiality.HIGH: 2}
            if component_records:
                ceiling = max((c.materiality for c in component_records),
                              key=lambda m: order[m])
                if order[materiality] > order[ceiling]:
                    adjust(f"Materiality {materiality.value} exceeds that of every "
                           f"component; capped at {ceiling.value}.")
                    materiality = ceiling
            attributed = [c.hypothesis_id for c in component_records
                          if c.admissibility is Admissibility.ATTRIBUTED]
            if live and attributed:
                flags.append({
                    "rule_id": "rests_on_management_claim",
                    "layer": "D_evidence_sufficiency", "effect": "note",
                    "description": "A component is a management claim "
                                   f"({', '.join(attributed)}); the attribution "
                                   "carries through.",
                    "heuristic": False, "fo_handoff_variable": None,
                    "correction": None})

        fo_handoffs = [
            {"variable": str(h.get("variable")), "question": str(h.get("question")),
             "origin": "agent"}
            for h in item.get("fo_handoff") or [] if isinstance(h, dict)]
        for flag in flags:
            if flag["rule_id"] == "numeric_binding_invalid":
                continue  # already explained defect by defect
            adjust(f"{flag['rule_id']}: {flag['description']}")
            if flag.get("fo_handoff_variable"):
                fo_handoffs.append({
                    "variable": flag["fo_handoff_variable"],
                    "question": "Quantify the financial effect asserted in the "
                                "held-back Business Overview claim: "
                                + (item.get("claim") or ""),
                    "origin": "validator"})
        if live and any(f["effect"] == "audit_only" for f in flags):
            disposition, live = Disposition.UNVERIFIED, False
        elif live and any(f["effect"] == "qualify" for f in flags) \
                and disposition is Disposition.SUPPORTED:
            disposition = Disposition.QUALIFIED

        mitigant = None
        if mitigant_raw is not None:
            try:
                effectiveness = MitigantEffectiveness(
                    mitigant_raw.get("effectiveness") or "unproven")
            except ValueError:
                effectiveness = MitigantEffectiveness.UNPROVEN
            existence = mitigant_raw.get("existence") or "unverified"
            if existence == "verified" and not verified_quotes:
                adjust("Mitigant existence has no verified quote; set to unverified.")
                existence = "unverified"
            valid_outcomes = [e for e in outcome_ids if e in self._passages]
            # Fix R4: an admitted passage is not outcome evidence unless it
            # reports a measured change (lexical heuristic, flagged as such).
            measured = [e for e in valid_outcomes
                        if shows_measured_outcome(self._passages[e])]
            if effectiveness is MitigantEffectiveness.DEMONSTRATED and not valid_outcomes:
                adjust("The mitigant is called demonstrated without evidence of a "
                       "measured outcome; set to unproven.")
                effectiveness = MitigantEffectiveness.UNPROVEN
            elif effectiveness is MitigantEffectiveness.DEMONSTRATED and not measured:
                adjust("The passages cited as outcome evidence report no measured "
                       "change; mitigant set to unproven.")
                effectiveness = MitigantEffectiveness.UNPROVEN
                flags.append({
                    "rule_id": "mitigant_outcome_not_measured",
                    "layer": "D_evidence_sufficiency", "effect": "qualify",
                    "description": "The cited outcome passages contain no measured "
                                   "change (a figure with an increase, decrease or "
                                   "similar), so effectiveness is not shown.",
                    "heuristic": True, "fo_handoff_variable": None,
                    "correction": "Cite a passage that reports the measured result "
                                  "of the mitigant, or call it unproven."})
            valid_outcomes = measured
            if not self._enforce:
                try:
                    effectiveness = MitigantEffectiveness(
                        mitigant_raw.get("effectiveness") or "unproven")
                except ValueError:
                    pass
                existence = mitigant_raw.get("existence") or "unverified"
                valid_outcomes = outcome_ids
            mitigant = MitigantAssessment(
                capability=str(mitigant_raw.get("capability") or ""),
                existence=existence, effectiveness=effectiveness,
                outcome_evidence_ids=valid_outcomes)

        if not self._enforce:
            if (claim_type, disposition, materiality) != stated:
                adjust("Advisory: validators are off, so the model's claim type "
                       f"({stated[0].value}), disposition ({stated[1].value}) and "
                       f"materiality ({stated[2].value}) stand.")
            claim_type, disposition, materiality = stated
            # Quotes not found verbatim stay with the claim (each was noted above).
            verified_quotes += [q for q in discarded_quotes if q["quote"]]
        admissibility = derive_admissibility(claim_type, disposition)
        presentation_basis: dict[str, Any] = {}
        if self._presentation_policy:
            decision = classify_presentation(
                disposition=disposition, admissibility=admissibility,
                materiality=materiality,
                materiality_basis=self._materiality_basis or (
                    "model_proposed_capped"
                    if materiality.value != item.get("materiality")
                    else "model_proposed"),
                model_proposed=item.get("display_priority"))
            display = decision.presentation
            presentation_basis = decision.as_record()
        elif admissibility is Admissibility.AUDIT_ONLY and display is DisplayPriority.DISPLAY:
            display = DisplayPriority.ON_DEMAND
        review = bool(item.get("review_required")) or bool(flags) or (
            materiality is Materiality.HIGH
            and disposition in (Disposition.QUALIFIED, Disposition.UNVERIFIED))

        return HypothesisRecord(
            hypothesis_id=hid,
            analysis_run_id=analysis_run_id,
            agent_id=self._spec.agent_id,
            agent_run_id=agent_run_id,
            section=self._spec.code,
            claim=item.get("claim") or "",
            claim_type=claim_type,
            direction=direction,
            supporting_evidence_ids=supporting,
            contradicting_evidence_ids=contradicting,
            evidence_quotes=verified_quotes,
            parameter_result_ids=parameter_result_ids,
            quoted_values=[q for q in item.get("quoted_values") or []
                           if isinstance(q, dict)
                           and q.get("parameter_result_id") in parameter_result_ids],
            disposition=disposition,
            admissibility=admissibility,
            confidence=confidence,
            materiality=materiality,
            credit_mechanism=item.get("credit_mechanism"),
            interpretation=interpretation,
            missing_information=list(item.get("missing_information") or []),
            display_priority=display,
            review_required=review,
            corrects_hypothesis_id=corrects,
            parameter_id=item.get("parameter_id"),
            mitigant=mitigant,
            component_hypothesis_ids=components,
            interaction_kind=kind,
            validation_notes=notes,
            evidence_basis=basis,
            model_stated={
                "claim_type": item.get("claim_type"),
                "disposition": item.get("disposition"),
                "evidence_basis": item.get("evidence_basis"),
                "materiality": item.get("materiality"),
                "quotes": [{"evidence_id": q.get("evidence_id"),
                            "quote": q.get("quote")} for q in quotes],
            },
            review_flags=flags,
            transmission_mechanism=mechanism,
            fo_handoffs=fo_handoffs,
            validator_id=self.validator_id,
            validator_version=HYPOTHESIS_VALIDATOR_VERSION + ("" if self._enforce else "+advisory"),
            presentation_basis=presentation_basis,
            recovery=recovery_records,
        )


_SUBSTANTIVE_STATUSES = ("ok", "provisional", "requires_review")


def gate_parameters(parsed: dict[str, Any], records: list[HypothesisRecord], *,
                    value_sets: dict[str, Any],
                    primary: dict[str, HypothesisRecord] | None = None,
                    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Hold back parameter values the validated hypotheses do not carry.

    Returns a copy of ``parsed`` in which an unsupported or non-permitted
    parameter is recorded as ``unavailable`` (never silently accepted, never
    rewritten to a different value), plus the list of adjustments made:

    * the value is not one of the permitted categorical values (or is a
      sentence where a short label is required);
    * no linked hypothesis survived validation;
    * the value asserts something specific about this company but every linked
      hypothesis rests on management statements, generic risk language or
      inference.
    """
    # BO6 may support a parameter with the validated BO1-BO5 hypotheses it
    # received, as well as with its own.
    by_id = {**(primary or {}), **{r.hypothesis_id: r for r in records}}
    adjusted = dict(parsed)
    adjusted["parameters"] = []
    changes: list[dict[str, Any]] = []
    for item in parsed.get("parameters") or []:
        if not isinstance(item, dict):
            continue
        item = dict(item)
        pid, value = item.get("parameter_id"), item.get("value")
        status = item.get("status", "ok")
        reason = detail = None
        if status in _SUBSTANTIVE_STATUSES:
            allowed = value_sets.get(pid)
            linked = [by_id[h] for h in item.get("hypothesis_ids") or []
                      if h in by_id and by_id[h].usable]
            text = "" if value is None else str(value)
            if text == "not_established":
                reason = "not_established"
                detail = ("The agent reported that the evidence does not support "
                          "a classification.")
            elif allowed and text not in allowed:
                reason = "value_not_permitted"
                detail = f"{text[:80]!r} is not one of {sorted(allowed)}."
            elif pid in LABEL_PARAMETERS and (
                    not text or len(text) > LABEL_PARAMETERS[pid]
                    or len(text.split()) > 6 or text.rstrip().endswith(".")):
                reason = "label_not_concise"
                detail = "A short label is required, not a sentence."
            elif not linked:
                reason = "no_validated_supporting_hypothesis"
                detail = ("None of the hypotheses cited for this value survived "
                          "validation as a usable finding.")
            elif text in REQUIRES_COMPANY_EVIDENCE.get(pid, ()) and not any(
                    h.evidence_basis in COMPANY_SPECIFIC
                    and h.admissibility is Admissibility.ESTABLISHED
                    for h in linked):
                reason = "needs_company_specific_evidence"
                detail = (f"{text!r} asserts something specific about this company, "
                          "but no linked hypothesis is an established, "
                          "company-specific finding (they are: " + ", ".join(sorted(
                              {f"{h.admissibility.value}/{h.evidence_basis}"
                               for h in linked})) + ").")
        if reason:
            changes.append({"parameter_id": pid, "original_value": value,
                            "original_status": status, "reason": reason,
                            "detail": detail,
                            "hypothesis_ids": list(item.get("hypothesis_ids") or [])})
            item.update(status="unavailable", value=None,
                        notes=f"Held back by validation ({reason}): {detail}")
        adjusted["parameters"].append(item)
    return adjusted, changes


class HypothesisStore:
    """Append-only persistence of hypothesis records."""

    def __init__(self, session: Session, *, case_id: str, snapshot_version: int) -> None:
        self._session = session
        self._case_id = case_id
        self._version = snapshot_version

    def persist(self, record: HypothesisRecord) -> HypothesisRecordRow:
        row = HypothesisRecordRow(
            id=f"hyp_{uuid.uuid4().hex[:16]}",
            hypothesis_id=record.hypothesis_id,
            analysis_run_id=record.analysis_run_id,
            case_id=self._case_id,
            snapshot_version=self._version,
            agent_id=record.agent_id,
            agent_run_id=record.agent_run_id,
            section=record.section,
            claim_type=record.claim_type.value,
            direction=record.direction.value,
            disposition=record.disposition.value,
            admissibility=record.admissibility.value,
            payload=record.model_dump(mode="json"),
            supersedes_id=record.supersedes_id,
            acceptance_state=record.acceptance_state.value,
        )
        self._session.add(row)
        self._session.flush()
        return row

    def supersede_agent(self, analysis_run_id: str, agent_id: str) -> dict[str, str]:
        """Mark an agent's accepted hypotheses superseded; ``{hypothesis_id: row id}``."""
        rows = self._session.execute(
            select(HypothesisRecordRow)
            .where(HypothesisRecordRow.analysis_run_id == analysis_run_id)
            .where(HypothesisRecordRow.agent_id == agent_id)
            .where(HypothesisRecordRow.acceptance_state == "accepted")
        ).scalars().all()
        prior = {}
        for row in rows:
            row.acceptance_state = "superseded"
            row.payload = {**row.payload, "acceptance_state": "superseded"}
            prior[row.hypothesis_id] = row.id
        self._session.flush()
        return prior

    def latest_ids(self, analysis_run_id: str, agent_id: str) -> dict[str, str]:
        """``{hypothesis_id: newest row id}`` for an agent, whatever its state."""
        return {
            row.hypothesis_id: row.id
            for row in self.rows(analysis_run_id) if row.agent_id == agent_id
        }

    def rows(self, analysis_run_id: str, *, accepted_only: bool = False):
        stmt = (
            select(HypothesisRecordRow)
            .where(HypothesisRecordRow.analysis_run_id == analysis_run_id)
            .order_by(HypothesisRecordRow.created_at, HypothesisRecordRow.hypothesis_id)
        )
        if accepted_only:
            stmt = stmt.where(HypothesisRecordRow.acceptance_state == "accepted")
        return self._session.execute(stmt).scalars().all()

    def accepted(self, analysis_run_id: str) -> list[HypothesisRecord]:
        return [
            HypothesisRecord.model_validate(
                {**r.payload, "acceptance_state": AcceptanceState.ACCEPTED.value})
            for r in self.rows(analysis_run_id, accepted_only=True)
        ]
