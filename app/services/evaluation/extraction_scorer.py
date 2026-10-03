"""Golden extraction dataset scorer (task 9.2, Req 23.2, 23.5).

Scores a set of extracted facts against a declared
:class:`~app.services.evaluation.manifest.GroundTruthManifest`. Per Req 23.2 the
scorer measures, reported BY FIELD TYPE:

* **field accuracy** -- did the extracted field match on entity, period,
  currency, scale, source location, and expected status;
* **numeric accuracy** -- did the normalized value match within the field's
  declared tolerance;
* **citation accuracy** -- did the extracted field carry the expected source
  location / document reference;
* **required-field recall** -- of the required fields declared in the manifest,
  how many were actually extracted.

Headline constraint (Req 23.5): the scorer REQUIRES a declared manifest. Calling
it with ``manifest=None`` raises :class:`NoDeclaredManifestError`; no accuracy or
recall number is ever produced without declared ground truth. Equally, the
scorer only ever reads contemporaneous expectations
(``manifest.verified_facts``); it never consults ``future_outcome`` (Req 23.6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.services.evaluation.manifest import ExpectedField, GroundTruthManifest


class NoDeclaredManifestError(ValueError):
    """Raised when a scorer is asked to score without a declared manifest.

    Enforces Req 23.5: no scored metric without a declared GroundTruthManifest.
    """


@dataclass
class FieldTypeScore:
    """Accuracy/recall rates for one field type."""

    field_type: str
    total_expected: int = 0
    extracted: int = 0  # how many expected fields were produced at all
    field_accurate: int = 0
    numeric_total: int = 0  # expected fields that carry a numeric value
    numeric_accurate: int = 0
    citation_accurate: int = 0
    required_total: int = 0
    required_recalled: int = 0

    def _ratio(self, num: int, den: int) -> float | None:
        return (num / den) if den else None

    @property
    def field_accuracy(self) -> float | None:
        return self._ratio(self.field_accurate, self.total_expected)

    @property
    def numeric_accuracy(self) -> float | None:
        return self._ratio(self.numeric_accurate, self.numeric_total)

    @property
    def citation_accuracy(self) -> float | None:
        # Citation accuracy is measured over fields that were actually extracted
        # (an un-extracted field has no citation to assess). Required-field
        # recall separately captures the fields that were not produced at all.
        return self._ratio(self.citation_accurate, self.extracted)

    @property
    def required_recall(self) -> float | None:
        return self._ratio(self.required_recalled, self.required_total)

    def as_dict(self) -> dict[str, Any]:
        return {
            "field_type": self.field_type,
            "total_expected": self.total_expected,
            "extracted": self.extracted,
            "field_accuracy": self.field_accuracy,
            "numeric_accuracy": self.numeric_accuracy,
            "citation_accuracy": self.citation_accuracy,
            "required_recall": self.required_recall,
        }


@dataclass
class ExtractionScore:
    """Aggregate extraction score across all field types (Req 23.2)."""

    case_id: str
    manifest_version: str
    by_type: dict[str, FieldTypeScore] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)

    def overall_field_accuracy(self) -> float | None:
        num = sum(s.field_accurate for s in self.by_type.values())
        den = sum(s.total_expected for s in self.by_type.values())
        return (num / den) if den else None

    def overall_required_recall(self) -> float | None:
        num = sum(s.required_recalled for s in self.by_type.values())
        den = sum(s.required_total for s in self.by_type.values())
        return (num / den) if den else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "manifest_version": self.manifest_version,
            "overall_field_accuracy": self.overall_field_accuracy(),
            "overall_required_recall": self.overall_required_recall(),
            "by_field_type": {k: v.as_dict() for k, v in self.by_type.items()},
            "missing_fields": sorted(self.missing_fields),
        }


# An "extracted field" is a plain mapping so the scorer is decoupled from any
# particular fact object. The pipeline adapter below converts CanonicalFacts.
ExtractedField = Mapping[str, Any]


def _values_match(expected: ExpectedField, extracted: ExtractedField) -> bool:
    """Numeric-or-string value equality within tolerance for numerics."""
    exp = expected.expected_value
    got = extracted.get("value")
    if exp is None:
        return got is None
    if isinstance(exp, (int, float)) and isinstance(got, (int, float)):
        return abs(float(got) - float(exp)) <= expected.tolerance
    return str(got) == str(exp)


def _citation_match(expected: ExpectedField, extracted: ExtractedField) -> bool:
    """A citation is correct only when it points at the expected source.

    Citation presence alone is NOT sufficient (mirrors Req 13.8): the extracted
    source document must match the declared one when the manifest declares it.
    """
    cites = extracted.get("source_document_ids") or []
    if expected.source_document_id is None:
        return bool(cites)
    return expected.source_document_id in cites


def _field_attributes_match(expected: ExpectedField, extracted: ExtractedField) -> bool:
    """Entity/period/currency/scale/status comparability (Req 23.2)."""
    checks = [
        expected.entity_id is None or extracted.get("entity_id") == expected.entity_id,
        expected.currency is None or extracted.get("currency") == expected.currency,
        expected.scale is None or extracted.get("scale") == expected.scale,
        expected.period_end is None
        or str(extracted.get("period_end")) == str(expected.period_end),
        extracted.get("status") == expected.expected_status,
    ]
    return all(checks)


def score_extraction(
    extracted: Mapping[str, ExtractedField] | None,
    *,
    manifest: GroundTruthManifest | None,
) -> ExtractionScore:
    """Score extracted fields against the declared manifest, by field type.

    ``extracted`` maps field name -> extracted-field mapping with keys
    ``value, entity_id, currency, scale, period_end, status,
    source_document_ids``.

    Raises :class:`NoDeclaredManifestError` when ``manifest`` is ``None``
    (Req 23.5).
    """
    if manifest is None:
        raise NoDeclaredManifestError(
            "score_extraction requires a declared GroundTruthManifest; no scored "
            "metric may be reported without declared ground truth (Req 23.5)."
        )
    extracted = extracted or {}

    score = ExtractionScore(
        case_id=manifest.case_id, manifest_version=manifest.manifest_version
    )
    for field_type, fields in manifest.fields_by_type().items():
        ts = FieldTypeScore(field_type=field_type)
        for ef in fields:
            ts.total_expected += 1
            if ef.required:
                ts.required_total += 1

            got = extracted.get(ef.name)
            if got is None:
                score.missing_fields.append(ef.name)
                continue

            ts.extracted += 1
            if ef.required:
                ts.required_recalled += 1

            attrs_ok = _field_attributes_match(ef, got)
            value_ok = _values_match(ef, got)
            cite_ok = _citation_match(ef, got)

            if attrs_ok and value_ok and cite_ok:
                ts.field_accurate += 1
            if cite_ok:
                ts.citation_accurate += 1
            if isinstance(ef.expected_value, (int, float)):
                ts.numeric_total += 1
                if value_ok:
                    ts.numeric_accurate += 1
        score.by_type[field_type] = ts
    return score
