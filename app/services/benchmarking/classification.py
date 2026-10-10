"""Medical-device sector classification (deterministic; never calls an LLM).

A SIC code alone never decides a company's sector. The classifier combines:

* a company identity record (legal name, CIK, verification status);
* the current reported SIC code, with its source and retrieval date;
* the workbook's SIC table (segment + mapping confidence) as a PROVISIONAL map;
* corroborating signals: the workbook company list's industry group and
  device / pharmaceutical / diversified terms in admitted business or segment
  description evidence.

It returns ``classified`` only when the provisional mapping is corroborated and
nothing conflicts; otherwise ``requires_review`` with no industry assigned, so
benchmarking does not run on a forced mapping. Diversified signals are flagged
for review; no revenue-weighted allocation is ever invented. A human may
override with a rationale; the override is appended (the prior record is
superseded, never edited) and audited.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.hashing import content_hash
from app.models.orm import SectorClassificationRow
from app.services.audit.log import ActorType, AuditLog, EventType
from app.services.benchmarking.sector_config import SectorConfig

CLASSIFIER_VERSION = "sector-classifier-1.0.0"
CONFIDENCE_ORDER = ("low", "medium", "high")


class ClassificationError(ValueError):
    """Raised for an invalid override or inconsistent classification input."""


@dataclass(frozen=True)
class CompanyIdentity:
    """What is known about the company being classified.

    ``identity_verification`` is ``sec_verified`` only when the CIK and SIC were
    read from SEC EDGAR for this case; a value typed into a package is
    ``declared_unverified`` and caps classification confidence.
    """

    entity_id: str
    legal_name: str
    cik: str | None = None
    ticker: str | None = None
    reported_sic: int | None = None
    sic_source: str | None = None
    sic_retrieved_at: str | None = None
    identity_verification: str = "declared_unverified"
    # Admitted narrative/segment evidence: [{"evidence_id": ..., "text": ...}].
    descriptions: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "legal_name": self.legal_name,
            "cik": self.cik,
            "ticker": self.ticker,
            "reported_sic": self.reported_sic,
            "sic_source": self.sic_source,
            "sic_retrieved_at": self.sic_retrieved_at,
            "identity_verification": self.identity_verification,
            "description_evidence_ids": sorted(
                d["evidence_id"] for d in self.descriptions if d.get("evidence_id")
            ),
        }


def _keyword_hits(texts: list[dict[str, Any]], keywords: list[str]) -> dict[str, list[str]]:
    """``{keyword: [evidence_id, ...]}`` for whole-phrase matches."""
    hits: dict[str, list[str]] = {}
    for keyword in keywords:
        pattern = re.compile(r"(?<![A-Za-z])" + re.escape(keyword) + r"(?![A-Za-z])", re.IGNORECASE)
        for item in texts:
            if pattern.search(item.get("text") or ""):
                hits.setdefault(keyword, []).append(item.get("evidence_id") or "")
    return {k: sorted(set(v)) for k, v in hits.items()}


def _find_company(dataset: dict[str, Any], identity: CompanyIdentity) -> dict[str, Any] | None:
    ticker = (identity.ticker or "").upper()
    name = identity.legal_name.casefold()
    for record in dataset["companies"]["records"]:
        listed = str(record.get("ticker") or "")
        if ticker and listed.rsplit(":", 1)[-1].upper() == ticker:
            return record
    for record in dataset["companies"]["records"]:
        listed_name = str(record.get("name") or "").casefold()
        if listed_name.startswith(name + " (") or listed_name == name:
            return record
    return None


class SectorClassifier:
    """Classify a company into the sector's benchmark industry, or defer."""

    def __init__(
        self,
        config: SectorConfig,
        dataset: dict[str, Any],
        *,
        session: Session | None = None,
        audit: AuditLog | None = None,
    ) -> None:
        self._config = config
        self._rules = config["classification"]
        self._dataset = dataset
        self._session = session
        self._audit = audit

    # ----------------------------------------------------------- classify

    def classify(self, identity: CompanyIdentity) -> dict[str, Any]:
        rules = self._rules
        provisional = rules["provisional_industry"]
        signals: list[dict[str, Any]] = []
        blocking: list[dict[str, Any]] = []
        notes: list[dict[str, Any]] = []

        if not identity.cik:
            blocking.append(_flag("missing_cik", "No CIK on the company identity record."))
        if identity.identity_verification != "sec_verified":
            notes.append(_flag(
                "identity_not_sec_verified",
                "CIK and SIC were declared in the package, not read from SEC EDGAR "
                "for this case; confidence is capped.",
            ))

        sic = identity.reported_sic
        sic_row = None
        if sic is None:
            blocking.append(_flag("missing_sic", "No current reported SIC code."))
        else:
            if not identity.sic_source or not identity.sic_retrieved_at:
                blocking.append(_flag(
                    "sic_source_undocumented",
                    "The reported SIC code has no source or retrieval date.",
                ))
            sic_row = next(
                (r for r in self._dataset["sic_codes"]["records"]
                 if r.get("sic_code") == sic and r.get("status") == "observed"),
                None,
            )
            for span in rules.get("conflicting_sic_ranges", []):
                if span["min"] <= sic <= span["max"]:
                    blocking.append(_flag(
                        "conflicting_sic_category",
                        f"SIC {sic} falls in the {span['category']} range.",
                    ))
            if sic in rules["device_sic_codes"] and sic_row is not None:
                mapped = rules["segment_to_industry"].get(sic_row.get("segment"))
                confidence = sic_row.get("mapping_confidence")
                if mapped == provisional and confidence in rules["sic_mapping_confidence_required"]:
                    signals.append({
                        "signal": "sic_device_code",
                        "kind": "provisional_mapping",
                        "detail": (
                            f"SIC {sic} ({sic_row.get('title')}) is in segment "
                            f"{sic_row.get('segment')!r}, mapped to {provisional!r} "
                            f"with {confidence} mapping confidence."
                        ),
                        "source": {"sheet": sic_row["sheet"], "cell": sic_row["cell"]},
                    })
                else:
                    blocking.append(_flag(
                        "sic_mapping_not_reliable",
                        f"SIC {sic} maps to {mapped!r} with {confidence!r} confidence.",
                    ))
            elif sic not in rules["device_sic_codes"]:
                blocking.append(_flag(
                    "sic_not_a_device_code",
                    f"SIC {sic} is not one of the device codes {rules['device_sic_codes']}.",
                ))
            else:
                blocking.append(_flag(
                    "sic_absent_from_reference_table",
                    f"SIC {sic} is not in the workbook SIC table.",
                ))

        corroborating = 0
        listed = _find_company(self._dataset, identity)
        if listed is not None:
            source = {"sheet": self._config["workbook_mapping"]["sheets"]["companies"],
                      "row": listed["row"]}
            if listed["industry_group"] == provisional:
                corroborating += 1
                signals.append({
                    "signal": "workbook_company_list_industry_group",
                    "kind": "corroborating",
                    "detail": f"Company list records {listed['name']!r} under {provisional!r}.",
                    "source": source,
                })
            else:
                blocking.append(_flag(
                    "company_list_industry_conflict",
                    f"Company list records industry group {listed['industry_group']!r}.",
                ))
            if sic is not None and listed.get("sic_code") not in (None, sic):
                blocking.append(_flag(
                    "sic_mismatch_between_sources",
                    f"Reported SIC {sic} differs from the company list's "
                    f"{listed.get('sic_code')}.",
                ))

        device = _keyword_hits(identity.descriptions, rules["device_keywords"])
        pharma = _keyword_hits(identity.descriptions, rules["pharma_keywords"])
        diversified = _keyword_hits(identity.descriptions, rules["diversified_keywords"])
        if device:
            corroborating += 1
            signals.append({
                "signal": "business_description_device_terms",
                "kind": "corroborating",
                "detail": f"Device terms in admitted description evidence: {sorted(device)}.",
                "evidence_ids": sorted({e for ids in device.values() for e in ids if e}),
            })
        if not identity.descriptions:
            notes.append(_flag(
                "no_segment_description_evidence",
                "No admitted business or product-segment description evidence was "
                "available to corroborate the classification.",
            ))
        if pharma or diversified:
            terms = sorted({*pharma, *diversified})
            blocking.append(_flag(
                "multi_category_activity",
                "Description evidence also references other healthcare categories "
                f"({terms}); a diversified business needs human review. No "
                "revenue-weighted allocation is inferred.",
                evidence_ids=sorted({e for hits in (pharma, diversified)
                                     for ids in hits.values() for e in ids if e}),
            ))

        if corroborating < rules["min_corroborating_signals"]:
            blocking.append(_flag(
                "insufficient_corroboration",
                f"{corroborating} corroborating signal(s); "
                f"{rules['min_corroborating_signals']} required beyond the SIC code.",
            ))

        classified = not blocking
        confidence = "low"
        if classified:
            confidence = "high" if corroborating >= 2 else "medium"
            if identity.identity_verification != "sec_verified":
                cap = rules["unverified_identity_confidence_cap"]
                if CONFIDENCE_ORDER.index(confidence) > CONFIDENCE_ORDER.index(cap):
                    confidence = cap

        result: dict[str, Any] = {
            "classifier_version": CLASSIFIER_VERSION,
            "sector_id": self._config.sector_id,
            "entity_id": identity.entity_id,
            "status": "classified" if classified else "requires_review",
            "industry_label": provisional if classified else None,
            "provisional_industry": provisional,
            "confidence": confidence,
            "identity": identity.as_dict(),
            "signals": signals,
            "blocking_flags": blocking,
            "review_notes": notes,
            "evidence_requirements": list(rules["evidence_requirements"]),
            "classification_source": {
                "sic_source": identity.sic_source,
                "sic_retrieved_at": identity.sic_retrieved_at,
                "sic_table_last_updated": self._dataset["vintage"].get("sic_list_last_updated"),
                "reference_workbook_sha256": self._dataset["source"]["sha256"],
                "reference_dataset_hash": self._dataset["dataset_hash"],
                "sector_config_version": self._config.version,
                "sector_config_hash": self._config.content_hash,
            },
            "human_override": None,
        }
        result["classification_hash"] = content_hash(result)
        return result

    # ------------------------------------------------------------ override

    def override(
        self,
        classification: dict[str, Any],
        *,
        industry_label: str,
        reviewer: str,
        rationale: str,
    ) -> dict[str, Any]:
        """Return a NEW classification recording a human override."""
        if not reviewer or not reviewer.strip():
            raise ClassificationError("A human override needs a named reviewer.")
        if not rationale or not rationale.strip():
            raise ClassificationError("A human override needs a rationale.")
        if industry_label not in self._dataset["industries"]:
            raise ClassificationError(
                f"{industry_label!r} is not a benchmark industry "
                f"({self._dataset['industries']})."
            )
        updated = {k: v for k, v in classification.items() if k != "classification_hash"}
        updated.update({
            "status": "human_override",
            "industry_label": industry_label,
            "human_override": {
                "reviewer": reviewer.strip(),
                "rationale": rationale.strip(),
                "previous_status": classification["status"],
                "previous_industry_label": classification["industry_label"],
                "previous_classification_hash": classification["classification_hash"],
            },
        })
        updated["classification_hash"] = content_hash(updated)
        return updated

    # --------------------------------------------------------- persistence

    def persist(
        self, classification: dict[str, Any], *, case_id: str,
        supersedes_id: str | None = None,
    ) -> SectorClassificationRow:
        if self._session is None:
            raise ValueError("SectorClassifier.persist requires a session.")
        if supersedes_id is not None:
            prior = self._session.get(SectorClassificationRow, supersedes_id)
            if prior is not None and prior.acceptance_state == "accepted":
                prior.acceptance_state = "superseded"
        row = SectorClassificationRow(
            id=f"sc_{uuid.uuid4().hex[:16]}",
            case_id=case_id,
            entity_id=classification["entity_id"],
            sector_id=classification["sector_id"],
            industry_label=classification["industry_label"],
            status=classification["status"],
            confidence=classification["confidence"],
            payload=classification,
            classification_hash=classification["classification_hash"],
            supersedes_id=supersedes_id,
            acceptance_state="accepted",
        )
        self._session.add(row)
        self._session.flush()
        if self._audit is not None:
            override = classification.get("human_override")
            self._audit.record(
                EventType.SECTOR_CLASSIFICATION_OVERRIDDEN if override
                else EventType.SECTOR_CLASSIFIED,
                case_id=case_id,
                actor_type=ActorType.HUMAN if override else ActorType.SYSTEM,
                actor_id=override["reviewer"] if override else None,
                before=(
                    {"status": override["previous_status"],
                     "industry_label": override["previous_industry_label"]}
                    if override else None
                ),
                after={
                    "classification_id": row.id,
                    "status": classification["status"],
                    "industry_label": classification["industry_label"],
                    "confidence": classification["confidence"],
                    "classification_hash": classification["classification_hash"],
                },
                reason=(override["rationale"] if override else
                        f"Sector classification: {classification['status']}."),
                linked_objects=[row.id],
            )
        return row

    def accepted(self, case_id: str, entity_id: str) -> SectorClassificationRow | None:
        if self._session is None:
            return None
        return self._session.scalars(
            select(SectorClassificationRow)
            .where(
                SectorClassificationRow.case_id == case_id,
                SectorClassificationRow.entity_id == entity_id,
                SectorClassificationRow.sector_id == self._config.sector_id,
                SectorClassificationRow.acceptance_state == "accepted",
            )
            .order_by(SectorClassificationRow.created_at.desc(), SectorClassificationRow.id.desc())
        ).first()


def _flag(code: str, detail: str, **extra: Any) -> dict[str, Any]:
    return {"code": code, "detail": detail, **extra}


def benchmarking_permitted(classification: dict[str, Any]) -> bool:
    """Benchmarks run only on a classified or human-overridden mapping."""
    return (
        classification["status"] in {"classified", "human_override"}
        and classification["industry_label"] is not None
    )
