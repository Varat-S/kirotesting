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
from app.services.bo.indicators import IndicatorSet
from app.services.bo.plans import BoAgentSpec
from app.services.orchestration.benchmark_context import (
    _METRIC_NUMBER,
    _SCORE_KEYS,
    _matches,
)

MIN_QUOTE_CHARS = 12

# Belief / intent language: a quote carrying it supports a management claim,
# not an established fact.
_BELIEF = re.compile(
    r"\b(?:we|management|the company)\s+(?:\w+\s+)?(?:believes?|expects?|intends?|"
    r"anticipates?|plans?|seeks?|aims?|estimates?|hopes?)\b|"
    r"\bour (?:strategy|goal|objective|belief)\b|\b(?:is|are) (?:designed|intended) to\b|"
    r"\bwe are focused on\b|\bshould (?:enable|allow|help)\b",
    re.I,
)

_TRANSLATE = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ",
})


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").translate(_TRANSLATE)).strip()


def quote_in_passage(quote: str, passage_text: str) -> bool:
    needle = normalise(quote)
    return len(needle) >= MIN_QUOTE_CHARS and needle in normalise(passage_text)


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
        if _matches(abs(float(token)), [abs(v) for v in quoted_values]):
            continue
        if re.search(rf"(?<![\d.]){re.escape(token.lstrip('-'))}(?!\d)", haystack):
            continue
        loose.append(token)
    return loose


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
    ) -> None:
        self._spec = spec
        self._passages = passages  # admitted evidence_id -> passage text
        self._indicators = indicators
        self._known = indicators.known_values()
        self._primary = primary_hypotheses

    def validate(self, parsed: dict[str, Any], *, analysis_run_id: str,
                 agent_run_id: str) -> HypothesisValidation:
        out = HypothesisValidation()
        for path in score_key_paths(parsed):
            out.issues.append(
                f"official_score_assignment: {path!r} is not permitted; scores "
                "are deterministic.")
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

        for parameter in parsed.get("parameters") or []:
            if isinstance(parameter, dict):
                for hid in parameter.get("hypothesis_ids") or []:
                    if hid not in dispositions:
                        out.issues.append(
                            f"Parameter {parameter.get('parameter_id')!r} cites "
                            f"unknown hypothesis {hid!r}.")

        for item in items:
            record = self._one(item, dispositions, out, analysis_run_id, agent_run_id)
            if record is not None:
                out.records.append(record)
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
            out.issues.append(f"Hypothesis {hid!r} has an invalid enum value: {exc}.")
            return None

        supporting = list(dict.fromkeys(item.get("supporting_evidence_ids") or []))
        contradicting = list(dict.fromkeys(item.get("contradicting_evidence_ids") or []))
        quotes = [q for q in item.get("evidence_quotes") or [] if isinstance(q, dict)]
        mitigant_raw = item.get("mitigant") if isinstance(item.get("mitigant"), dict) else None
        outcome_ids = list((mitigant_raw or {}).get("outcome_evidence_ids") or [])

        for eid in {*supporting, *contradicting, *outcome_ids,
                    *(q.get("evidence_id") for q in quotes)}:
            if eid not in self._passages:
                out.issues.append(
                    f"Hypothesis {hid!r} cites unknown/unadmitted evidence {eid!r}.")

        # --- quoted calculations: exact value, unit, period, denominator -----
        quoted_numbers: list[float] = []
        parameter_result_ids: list[str] = []
        for entry in item.get("quoted_values") or []:
            if not isinstance(entry, dict):
                continue
            prid, value = entry.get("parameter_result_id"), entry.get("value")
            trace = self._indicators.trace_for(prid) if prid else None
            if prid not in self._known or trace is None:
                out.issues.append(
                    f"Hypothesis {hid!r} quotes {value!r} for {prid!r}, which is "
                    "not an accepted deterministic result (invented number).")
                continue
            if abs(float(value) - float(self._known[prid])) > 1e-9:
                out.issues.append(
                    f"Hypothesis {hid!r} quotes {value!r} for {prid!r} but the "
                    f"calculated value is {self._known[prid]!r} (no LLM arithmetic).")
                continue
            expected = {
                "unit": trace.units, "period": trace.period,
                "denominator": (trace.denominator or {}).get("name"),
            }
            for key, want in expected.items():
                got = entry.get(key)
                if got is not None and str(got) != str(want):
                    out.issues.append(
                        f"Hypothesis {hid!r} quotes {prid!r} with {key} {got!r} "
                        f"but the calculation's {key} is {want!r}.")
            quoted_numbers.append(float(value))
            parameter_result_ids.append(prid)

        # --- verbatim quotes --------------------------------------------------
        verified_quotes = []
        for quote in quotes:
            eid, text = quote.get("evidence_id"), quote.get("quote") or ""
            if eid in self._passages and quote_in_passage(text, self._passages[eid]):
                verified_quotes.append({"evidence_id": eid, "quote": normalise(text)})
            elif eid in self._passages:
                adjust(f"A quote attributed to {eid[:12]} was not found verbatim in "
                       "that passage and was discarded.")
        quote_texts = [q["quote"] for q in verified_quotes]

        corrects = item.get("corrects_hypothesis_id")
        if corrects is not None:
            if corrects not in dispositions:
                out.issues.append(
                    f"Hypothesis {hid!r} corrects unknown hypothesis {corrects!r}.")
            elif dispositions[corrects] != Disposition.REJECTED.value:
                adjust(f"corrects_hypothesis_id {corrects} is not a rejected "
                       "hypothesis; the link was dropped.")
                corrects = None

        components = list(item.get("component_hypothesis_ids") or [])
        kind = item.get("interaction_kind")
        if self._primary is not None:  # BO6
            for cid in components:
                primary = self._primary.get(cid)
                if primary is None or not primary.usable:
                    out.issues.append(
                        f"Hypothesis {hid!r} builds on {cid!r}, which is not a "
                        "validated primary hypothesis.")
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

        loose = unbound_numbers(item.get("claim") or "", quoted_numbers, quote_texts)
        if live and loose:
            adjust(f"Number(s) {', '.join(sorted(set(loose)))} are bound to neither a "
                   "deterministic result nor a verbatim quote; marked unverified.")
            disposition, live = Disposition.UNVERIFIED, False

        if disposition is Disposition.SUPPORTED and contradicting:
            adjust("Counter-evidence is cited, so the hypothesis is qualified "
                   "rather than supported.")
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
            if effectiveness is MitigantEffectiveness.DEMONSTRATED and not valid_outcomes:
                adjust("The mitigant is called demonstrated without evidence of a "
                       "measured outcome; set to unproven.")
                effectiveness = MitigantEffectiveness.UNPROVEN
            mitigant = MitigantAssessment(
                capability=str(mitigant_raw.get("capability") or ""),
                existence=existence, effectiveness=effectiveness,
                outcome_evidence_ids=valid_outcomes)

        admissibility = derive_admissibility(claim_type, disposition)
        if admissibility is Admissibility.AUDIT_ONLY and display is DisplayPriority.DISPLAY:
            display = DisplayPriority.ON_DEMAND
        review = bool(item.get("review_required")) or (
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
            missing_information=list(item.get("missing_information") or []),
            display_priority=display,
            review_required=review,
            corrects_hypothesis_id=corrects,
            parameter_id=item.get("parameter_id"),
            mitigant=mitigant,
            component_hypothesis_ids=components,
            interaction_kind=kind,
            validation_notes=notes,
        )


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
