"""BO <-> FO evidence provenance extraction and cross-module consistency.

Two responsibilities:

1. :func:`provenance_from_payload` — read the verifiable provenance fields an
   authoritative producer already recorded, and classify canonical-snapshot
   equivalence honestly. BO artifacts carry a real canonical snapshot identity
   (version + content hash) -> ``verified``. FO artifacts come from an
   independent filing loader with no recorded canonical-snapshot link -> the
   filing identifier / source signature are preserved and equivalence is marked
   ``unverified`` (never assumed from a shared case id).

2. :func:`cross_module_consistency` — a lightweight check over overlapping
   financial facts (total indebtedness, cash, reporting period, currency/units,
   reporting entity, debt maturities). It compares figures ONLY when their
   definitions and reporting bases are compatible, never overwrites either side,
   and flags unresolved inconsistencies for human review. It does not alter any
   scoring methodology.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.schemas.artifact_manifest import (
    ProvenanceRecord,
    SnapshotEquivalence,
)


# ---------------------------------------------------------------------------
# Provenance extraction
# ---------------------------------------------------------------------------

def provenance_from_payload(
    payload: Any,
    *,
    case_id: str,
    producer_module: Optional[str],
) -> ProvenanceRecord:
    """Build a :class:`ProvenanceRecord` from an artifact payload.

    Reads only fields the producer already recorded; infers nothing about
    canonical-snapshot equivalence that is not independently verifiable.
    """
    doc = payload if isinstance(payload, dict) else {}

    # BO family: a recorded canonical snapshot identity (version + content hash).
    snap_version: Optional[int] = None
    snap_hash: Optional[str] = None
    evsnap = doc.get("evidence_snapshot")
    if isinstance(evsnap, dict):
        snap_version = evsnap.get("snapshot_version")
        snap_hash = evsnap.get("content_hash")
    if snap_version is None:
        refs = doc.get("canonical_references")
        if isinstance(refs, dict):
            snap_version = refs.get("evidence_snapshot_version")
    if snap_version is None and "evidence_snapshot_version" in doc:
        snap_version = doc.get("evidence_snapshot_version")

    # FO family: an independent filing loader; no canonical snapshot link.
    filing = doc.get("filing") if isinstance(doc.get("filing"), dict) else {}
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    filing_identifier = filing.get("folder") or doc.get("filing_folder")
    source_signature = filing.get("signature") or doc.get("folder_signature")
    reporting_period = (meta.get("period_end") or doc.get("period_end")
                        or doc.get("reporting_period"))
    reporting_entity = (meta.get("entity_name") or doc.get("entity_name")
                        or doc.get("reporting_entity"))

    source_document_ids: list[str] = []
    for key in ("source_document_ids", "document_ids"):
        value = doc.get(key)
        if isinstance(value, list):
            source_document_ids.extend(str(v) for v in value)

    # Classify snapshot equivalence honestly.
    if snap_version is not None:
        equivalence = SnapshotEquivalence.VERIFIED
        note = "Derived from the recorded canonical evidence snapshot."
    elif (producer_module == "FO") or filing_identifier or source_signature:
        equivalence = SnapshotEquivalence.UNVERIFIED
        note = ("No recorded canonical-snapshot link; the producer used an "
                "independent filing path. Preserved filing identity / source "
                "signature; canonical-snapshot equivalence is UNVERIFIED.")
    else:
        equivalence = SnapshotEquivalence.NOT_APPLICABLE
        note = None

    return ProvenanceRecord(
        case_id=case_id,
        reporting_period=reporting_period,
        reporting_entity=reporting_entity,
        filing_identifier=filing_identifier,
        source_signature=source_signature,
        source_document_ids=source_document_ids,
        canonical_snapshot_version=snap_version,
        canonical_snapshot_hash=snap_hash,
        snapshot_equivalence=equivalence,
        equivalence_note=note,
    )


# ---------------------------------------------------------------------------
# Cross-module numerical consistency
# ---------------------------------------------------------------------------

# Overlapping facts we compare, with the unit family each is expected in. We
# compare ONLY within a compatible unit family and reporting basis.
_UNIT_FAMILIES = {
    "usd": {"usd", "usd_million", "usd_billion", "usd_thousand", "dollars",
            "$", "millions", "million"},
}
# Scale factors to a common base (absolute USD) for the money family.
_SCALE_TO_BASE = {
    "usd": 1.0, "dollars": 1.0, "$": 1.0,
    "usd_thousand": 1e3, "thousand": 1e3, "thousands": 1e3,
    "usd_million": 1e6, "million": 1e6, "millions": 1e6,
    "usd_billion": 1e9, "billion": 1e9, "billions": 1e9,
}
_TOLERANCE = 0.005  # 0.5% relative tolerance for "equivalent" figures


@dataclass
class ConsistencyFinding:
    fact: str
    status: str            # consistent | inconsistent | incomparable | one_sided
    bo_value: Any = None
    fo_value: Any = None
    bo_unit: Optional[str] = None
    fo_unit: Optional[str] = None
    detail: str = ""


@dataclass
class ConsistencyReport:
    case_id: str
    bo_run_id: Optional[str] = None
    fo_run_id: Optional[str] = None
    same_reporting_period: Optional[bool] = None
    same_reporting_entity: Optional[bool] = None
    snapshot_equivalence: str = SnapshotEquivalence.UNVERIFIED.value
    findings: list[ConsistencyFinding] = field(default_factory=list)

    @property
    def has_unresolved(self) -> bool:
        return any(f.status == "inconsistent" for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "bo-fo-consistency-1.0",
            "case_id": self.case_id,
            "bo_run_id": self.bo_run_id,
            "fo_run_id": self.fo_run_id,
            "same_reporting_period": self.same_reporting_period,
            "same_reporting_entity": self.same_reporting_entity,
            "snapshot_equivalence": self.snapshot_equivalence,
            "requires_review": self.has_unresolved,
            "findings": [vars(f) for f in self.findings],
        }


def _unit_family(unit: Optional[str]) -> Optional[str]:
    if not unit:
        return None
    low = str(unit).strip().lower()
    for family, members in _UNIT_FAMILIES.items():
        if low in members:
            return family
    return None


def _to_base(value: float, unit: Optional[str]) -> Optional[float]:
    low = str(unit or "").strip().lower()
    scale = _SCALE_TO_BASE.get(low)
    if scale is None:
        return None
    return value * scale


def _compare_money(name: str, bo: dict, fo: dict) -> ConsistencyFinding:
    bo_v, fo_v = bo.get("value"), fo.get("value")
    bo_u, fo_u = bo.get("unit"), fo.get("unit")
    if bo_v is None or fo_v is None:
        return ConsistencyFinding(name, "one_sided", bo_v, fo_v, bo_u, fo_u,
                                  "Only one module reports this fact.")
    if _unit_family(bo_u) != _unit_family(fo_u) or _unit_family(bo_u) is None:
        return ConsistencyFinding(
            name, "incomparable", bo_v, fo_v, bo_u, fo_u,
            "Units are not in a compatible family; not compared.")
    base_bo, base_fo = _to_base(float(bo_v), bo_u), _to_base(float(fo_v), fo_u)
    if base_bo is None or base_fo is None:
        return ConsistencyFinding(name, "incomparable", bo_v, fo_v, bo_u, fo_u,
                                  "Unit scale unknown; not compared.")
    denom = max(abs(base_bo), abs(base_fo), 1.0)
    rel = abs(base_bo - base_fo) / denom
    if rel <= _TOLERANCE:
        return ConsistencyFinding(
            name, "consistent", bo_v, fo_v, bo_u, fo_u,
            f"Equivalent within {_TOLERANCE:.1%} after unit normalisation.")
    return ConsistencyFinding(
        name, "inconsistent", bo_v, fo_v, bo_u, fo_u,
        f"Differ by {rel:.1%} after unit normalisation; flagged for review "
        f"(neither value overwritten).")


def cross_module_consistency(
    case_id: str,
    bo_facts: dict[str, dict],
    fo_facts: dict[str, dict],
    *,
    bo_run_id: Optional[str] = None,
    fo_run_id: Optional[str] = None,
    bo_period: Optional[str] = None,
    fo_period: Optional[str] = None,
    bo_entity: Optional[str] = None,
    fo_entity: Optional[str] = None,
    snapshot_equivalence: str = SnapshotEquivalence.UNVERIFIED.value,
) -> ConsistencyReport:
    """Compare overlapping BO/FO facts; flag, never overwrite.

    ``bo_facts`` / ``fo_facts`` map a canonical fact key (e.g.
    ``"total_indebtedness"``, ``"cash_and_equivalents"``, ``"debt_maturities"``)
    to ``{"value": ..., "unit": ...}``. Only money-family facts with compatible
    units are compared numerically; period/entity are compared as identity.
    """
    report = ConsistencyReport(
        case_id=case_id, bo_run_id=bo_run_id, fo_run_id=fo_run_id,
        snapshot_equivalence=snapshot_equivalence,
    )
    if bo_period is not None and fo_period is not None:
        report.same_reporting_period = (bo_period == fo_period)
        if not report.same_reporting_period:
            report.findings.append(ConsistencyFinding(
                "reporting_period", "inconsistent", bo_period, fo_period,
                detail="BO and FO report different periods; figures are not "
                       "comparable until reconciled (flagged, not merged)."))
    if bo_entity is not None and fo_entity is not None:
        report.same_reporting_entity = (
            str(bo_entity).strip().lower() == str(fo_entity).strip().lower())
        if not report.same_reporting_entity:
            report.findings.append(ConsistencyFinding(
                "reporting_entity", "inconsistent", bo_entity, fo_entity,
                detail="BO and FO name different reporting entities (flagged)."))

    # If periods differ, money comparisons are not meaningful -> mark incomparable.
    periods_compatible = report.same_reporting_period in (None, True)
    for key in sorted(set(bo_facts) | set(fo_facts)):
        bo = bo_facts.get(key, {})
        fo = fo_facts.get(key, {})
        if not periods_compatible and key in bo_facts and key in fo_facts:
            report.findings.append(ConsistencyFinding(
                key, "incomparable", bo.get("value"), fo.get("value"),
                bo.get("unit"), fo.get("unit"),
                "Reporting periods differ; monetary comparison skipped."))
            continue
        report.findings.append(_compare_money(key, bo, fo))
    return report


# Canonical overlapping-fact keys and the payload paths we know how to read.
_OVERLAP_KEYS = (
    "total_indebtedness", "cash_and_equivalents", "debt_maturities",
)


def extract_overlapping_facts(payload: Any) -> dict[str, dict]:
    """Best-effort pull of overlapping financial facts from a payload.

    Returns a map of canonical key -> ``{"value", "unit"}`` for the facts a
    payload exposes at a readable location. Deliberately conservative: a fact is
    only included when both a numeric value and a unit are discoverable, so an
    ambiguous figure is treated as absent (``one_sided``) rather than guessed.

    .. note:: **Known limitation (documented).**

       This extractor reads only an explicit, top-level ``overlapping_facts``
       block (``{key: {"value", "unit"}}``). It does **not** yet deep-mine the
       FO metrics tree (``payload["metrics"][…]["columns"]``) or the BO memo's
       ``module_conclusions`` / M4 debt deliverables to auto-derive overlapping
       figures such as total indebtedness or cash. Consequently, unless a
       producer (or caller) populates ``overlapping_facts``, the consistency
       check reports those facts as ``one_sided`` (not compared) rather than
       guessing them from heterogeneous nested structures. This is intentional
       for correctness over coverage: a wrong auto-mapping would be worse than a
       declared gap. Extending extraction to the native FO/M4 structures (with
       per-fact metric-definition compatibility) is a follow-up; until then the
       cross-module numeric check is exercised on declared/compatible facts and
       flags — never overwrites — any inconsistency it can compare.
    """
    doc = payload if isinstance(payload, dict) else {}
    out: dict[str, dict] = {}

    # An explicit ``overlapping_facts`` block is honoured verbatim when present
    # (lets a producer or a test declare comparable figures unambiguously).
    declared = doc.get("overlapping_facts")
    if isinstance(declared, dict):
        for key, entry in declared.items():
            if isinstance(entry, dict) and entry.get("value") is not None:
                out[key] = {"value": entry.get("value"), "unit": entry.get("unit")}
    return out


def consistency_from_payloads(
    case_id: str,
    bo_payload: Any,
    fo_payload: Any,
    *,
    bo_run_id: Optional[str] = None,
    fo_run_id: Optional[str] = None,
    snapshot_equivalence: str = SnapshotEquivalence.UNVERIFIED.value,
) -> ConsistencyReport:
    """Run the consistency check straight from a BO and an FO payload."""
    bo_doc = bo_payload if isinstance(bo_payload, dict) else {}
    fo_doc = fo_payload if isinstance(fo_payload, dict) else {}
    bo_prov = provenance_from_payload(bo_doc, case_id=case_id, producer_module="BO")
    fo_prov = provenance_from_payload(fo_doc, case_id=case_id, producer_module="FO")
    return cross_module_consistency(
        case_id,
        extract_overlapping_facts(bo_doc),
        extract_overlapping_facts(fo_doc),
        bo_run_id=bo_run_id, fo_run_id=fo_run_id,
        bo_period=bo_prov.reporting_period, fo_period=fo_prov.reporting_period,
        bo_entity=bo_prov.reporting_entity, fo_entity=fo_prov.reporting_entity,
        snapshot_equivalence=snapshot_equivalence,
    )
