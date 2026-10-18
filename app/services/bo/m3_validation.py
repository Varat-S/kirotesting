"""Validator 7 checks for the versioned Module 3 structured extension."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from typing import Any

from pydantic import ValidationError

from app.schemas.bo import Admissibility, ClaimType, Disposition
from app.schemas.bo_m3 import M3CorporateAssessment
from app.services.bo.hypotheses import derive_admissibility


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


def _govern_finding(raw: dict[str, Any], *, allowed: set[str], borrower_known: bool,
                     issues: list[dict], bucket: str) -> dict[str, Any]:
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
        # Tenure/credentials or absence of misconduct alone never establishes a judgement.
        text = " ".join((item.get("hypothesis") or "", item.get("credit_mechanism") or "")).lower()
        if disposition == "supported" and (
            ("tenure" in text or "qualification" in text) and not contradicting
            or "no misconduct" in text or "absence of misconduct" in text
        ):
            disposition = "qualified"
            reasons.append("Tenure, qualifications, or absence of misconduct alone cannot establish management competence or integrity.")
            _issue(issues, "m3.management_proxy", item_id, "downgraded", reasons[-1])
    item["disposition"] = disposition
    claim_type = ClaimType.INFERENCE
    item["admissibility"] = derive_admissibility(claim_type, Disposition(disposition)).value
    item["validation_reasons"] = reasons
    return item


def validate_m3_assessment(
    proposal: dict[str, Any], *, allowed_evidence_ids: set[str],
) -> M3CorporateAssessment:
    """Validate, filter and conservatively downgrade an M3 model proposal.

    Structurally malformed contracts fail the specialist. Invalid graph edges are
    retained only in ``rejected_items``; dependent findings are downgraded. The
    original proposal is embedded verbatim for audit.
    """
    original = deepcopy(proposal)
    try:
        draft = M3CorporateAssessment.model_validate(proposal)
    except ValidationError as exc:
        raise M3AssessmentError(f"invalid m3_assessment contract: {exc}") from exc
    data = draft.model_dump(mode="json")
    issues: list[dict] = []
    rejected: list[dict] = []

    entities: dict[str, dict] = {}
    clean_entities = []
    for entity in data["legal_entities"]:
        eid = entity["entity_id"]
        if eid in entities:
            _issue(issues, "m3.entity_id_unique", eid, "rejected", "Duplicate entity identifier.")
            rejected.append({"kind": "legal_entity", "item": entity,
                             "reason": "duplicate_entity_id"})
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
    ):
        _issue(issues, "m3.borrower_evidence", borrower.get("entity_id"), "downgraded",
               "Borrower identification does not resolve to an evidenced graph entity.")
        borrower.update(status="disputed", entity_id=None, evidence_ids=[],
                        limitation="Proposed borrower identity was not validated by Validator 7.")
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
        if reason:
            _issue(issues, "m3.cash_flow_provenance", cid, "rejected", reason)
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
                issues=issues, bucket=bucket))
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
