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


def _deterministic_claim_checks(
    claim_text: str, evidence_ids: list[str], evidence_texts: dict[str, str],
) -> tuple[DeterministicCheck, ...]:
    """Deterministic numeric/period checks of a claim against its cited evidence.

    Returns an empty tuple when nothing machine-checkable is asserted or no
    cited evidence text is available (then the caller keeps its conservative
    disposition — citation-id existence is NOT entailment). A check fails with
    ``contradiction=True`` when a specific value/period asserted in the claim is
    absent from every cited evidence passage. No live model is called.
    """
    corpus = " ".join(
        evidence_texts.get(eid, "") for eid in evidence_ids if eid in evidence_texts)
    if not corpus.strip():
        return ()
    corpus_numbers = {_normalise_number(t) for t in _NUMBER_RE.findall(corpus)}
    corpus_periods = {p.upper().replace(" ", "") for p in _PERIOD_RE.findall(corpus)}
    checks: list[DeterministicCheck] = []

    claimed_periods = {p.upper().replace(" ", "") for p in _PERIOD_RE.findall(claim_text)}
    # Numbers in the claim that are not themselves a period token.
    claimed_numbers = {
        _normalise_number(t) for t in _NUMBER_RE.findall(claim_text)
        if _normalise_number(t) and _normalise_number(t) not in {
            p.lstrip("FYQ") for p in claimed_periods}
    }
    for number in sorted(claimed_numbers):
        present = number in corpus_numbers
        checks.append(DeterministicCheck(
            name="numeric_value_present_in_cited_evidence", passed=present,
            detail=("" if present else
                    f"claimed value {number} is absent from the cited evidence"),
            contradiction=not present))
    for period in sorted(claimed_periods):
        present = period in corpus_periods
        checks.append(DeterministicCheck(
            name="fiscal_period_present_in_cited_evidence", passed=present,
            detail=("" if present else
                    f"claimed period {period} is absent from the cited evidence"),
            contradiction=not present))
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
                     structure_facts: dict[str, Any] | None = None) -> dict[str, Any]:
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
    # asserts a specific value/period, run deterministic numeric/period checks
    # against the cited evidence text (no live model). A mismatch downgrades the
    # claim; where nothing is machine-checkable the conservative disposition and
    # its limitation stand (human review still required).
    if evidence_texts and disposition in {"supported", "qualified"}:
        claim_text = " ".join((
            item.get("credit_hypothesis") or item.get("hypothesis") or "",
            item.get("causal_mechanism") or item.get("credit_mechanism") or ""))
        checks = _deterministic_claim_checks(claim_text, supporting, evidence_texts)
        failed = [c for c in checks if not c.passed]
        if failed:
            disposition = "rejected" if any(c.contradiction for c in failed) else "unverified"
            for check in failed:
                reason = (f"Deterministic entailment check failed ({check.name}): "
                          f"{check.detail or 'mismatch'}. A matching citation id does "
                          f"not establish the asserted value/period.")
                reasons.append(reason)
                _issue(issues, "m3.deterministic_entailment", item_id,
                       "rejected" if disposition == "rejected" else "downgraded", reason)
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
                                 if bucket == "structural_credit_findings" else None)))
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
