"""Conservative M3-only support-form and counterexample checks.

These are necessary support conditions, not a general entailment engine. No
rating thresholds are inferred. Unsupported categories become unavailable;
findings are only downgraded. Original model proposals and investigation flags
are never repaired. All searches are deterministic over supplied admitted text.
"""

from __future__ import annotations

import re
from copy import deepcopy

from app.schemas.bo_m3 import M3CorporateAssessment


M3_GROUNDING_VERSION = "bo-m3-grounding-1.1"
_SUBSTANTIVE = {"ok", "provisional", "requires_review"}
_KEY_PERSON = r"key[- ]person(?:nel)? (?:dependency|dependence|risk)"
_PERIOD = r"\b(?:(?:fy\s*)?20\d{2}|fiscal year|reporting period|year ended)\b"
_REVIEW = r"\b(?:review|assessment|evaluation|screening)\b"


def _normal(text):
    return re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-")).strip().lower()


def _sentences(text):
    return [_normal(s) for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


def _has(pattern, text):
    return bool(re.search(pattern, text, re.I))


def received_inquiry(text):
    """An affirmative received/existing matter, not 'may receive' or 'not received'."""
    for sentence in _sentences(text):
        match = re.search(
            r"\b(?:we|our|the company)\b(?P<context>[^.;!?]{0,110}?)"
            r"(?P<verb>\b(?:have|has) (?:already )?received|\b(?:is|are) (?:currently )?"
            r"(?:a defendant|defendants|subject to an? (?:ongoing|pending)))"
            r"[^.;!?]{0,150}\b(?:inquir\w*|investigat\w*|proceedings?|litigation|claims?)\b",
            sentence)
        if match and not _has(r"\b(?:if|may|might|could|would|not|never)\b",
                              sentence[:match.start("verb")]):
            # A clearly resolved historical matter does not prove a live one.
            if not _has(r"\ball (?:prior |such )?(?:inquiries|investigations|proceedings) "
                        r"(?:have been|were) (?:closed|resolved)|\bno outstanding matters\b", sentence):
                return True
    return False


def _scoped_absence(text, kind):
    """Require an explicit dated, broad review result, not investigation silence."""
    absence = (r"\bno (?:material )?governance concerns? (?:were |was )?(?:identified|found)\b"
               if kind == "governance" else
               r"\bno material events? (?:were |was )?(?:identified|found)\b")
    if _has(r"\b(?:except|excluding|excluded|did not cover|does not cover)\b", text):
        return False
    if not (any(_has(absence, s) and not _has(r"\b(?:if|would|could|might|may|not)\b", s)
                for s in _sentences(text)) and all(_has(p, text) for p in (_REVIEW, _PERIOD))):
        return False
    domains = ((r"board|oversight", r"related[- ]part|conflicts? of interest",
                r"controls|financial reporting", r"misconduct|enforcement|regulatory")
               if kind == "governance" else
               (r"acquisitions?|disposals?|corporate actions", r"leadership|executive|succession",
                r"litigation|regulatory|enforcement", r"restatements?|financial reporting|controls"))
    return all(_has(pattern, text) for pattern in domains)


def _explicit_dependency_grades(text):
    """Bind a grade to the assessment verb, not another rating word nearby."""
    grades = set()
    for sentence in _sentences(text):
        if _has(r"\b(?:may|might|could|would|not|never|cannot)\b", sentence):
            continue
        pattern = (r"\b(?:assessed|rated|classified|evaluated) " + _KEY_PERSON +
                   r" (?:as )?(low|moderate|high)\b|" + _KEY_PERSON +
                   r" (?:is |was )?(?:assessed|rated|classified|evaluated) "
                   r"(?:as )?(low|moderate|high)\b")
        for match in re.finditer(pattern, sentence):
            grades.add(next(g for g in match.groups() if g))
    return grades


def _current_matter_screen(text):
    # An inquiry-only screen cannot establish absence of legal proceedings.
    for sentence in _sentences(text):
        match = re.search(r"\bno (?:currently )?(?:pending|outstanding)\b", sentence)
        if not match or _has(r"\b(?:if|would|could|might|may|not)\b", sentence):
            continue
        scope = re.split(r"\b(?:but|however|although|while|except)\b|;", sentence[match.end():])[0]
        if _has(r"\b(?:inquiries|investigations)\b", scope) and _has(r"\b(?:proceedings|litigation)\b", scope):
            return True
    return False


def _affirmative_counterexample(pattern, text):
    for sentence in _sentences(text):
        for match in re.finditer(pattern, sentence):
            prefix = sentence[max(0, match.start() - 40):match.start()]
            if not _has(r"\b(?:not|never|no|if|may|could|might|would)\b", prefix):
                return True
    return False


def _event_counterexample(text):
    return _affirmative_counterexample(
        r"\b(?:we|the company) (?:completed|announced|disclosed) "
        r"(?:a |an )?(?:material|significant) (?:acquisition|disposal|restatement)\b|"
        r"\b(?:identified|reported) a material weakness\b", text)


def gate_m3_parameters(gated, records, *, evidence_texts):
    """Strengthen the existing parameter gate; never restore a held-back value."""
    result = deepcopy(gated)
    by_id = {r.hypothesis_id: r for r in records if r.usable}
    changes = []
    stability_mode = (result.get("m3_assessment") or {}).get("management_stability") is not None
    for item in result.get("parameters") or []:
        if item.get("status") not in _SUBSTANTIVE:
            continue
        pid, value = item.get("parameter_id"), item.get("value")
        if stability_mode and pid in {"management_quality_assessment", "key_person_dependency_assessment"}:
            changes.append({"parameter_id": pid, "original_value": value,
                            "original_status": item["status"], "reason": "m3.legacy_management_rating_retired",
                            "policy_version": "bo-m3-management-stability-1.0"})
            item.update(status="unavailable", value=None,
                        notes="The versioned management-stability remit does not assign this legacy quality/dependency rating.")
            continue
        linked = [by_id[h] for h in item.get("hypothesis_ids", []) if h in by_id]
        cited = {e: evidence_texts[e] for e in item.get("evidence_ids", []) if e in evidence_texts}
        text = _normal(" ".join(cited.values()))
        linked_ids = {e for r in linked for e in r.supporting_evidence_ids}
        linked_ids.update(q["evidence_id"] for r in linked for q in r.evidence_quotes)
        reason = None
        counter_ids = []
        if not set(cited) & linked_ids:
            reason = "parameter_citation_not_linked_to_usable_hypothesis"
        elif pid == "key_person_dependency_assessment" and value in {"low", "moderate", "high"}:
            grades = {m.group(1) for r in linked for m in re.finditer(
                _KEY_PERSON + r" (?:is |was |remains )?(low|moderate|high)\b", _normal(r.claim))}
            source_grades = _explicit_dependency_grades(" ".join(cited.values()))
            if grades and grades != {value}:
                reason = "dependency_grade_conflicts_with_linked_hypothesis"
            elif source_grades != {value}:
                reason = "dependency_grade_lacks_explicit_calibration_basis"
        elif pid == "governance_concern_assessment" and value == "none":
            if not _scoped_absence(text, "governance"):
                reason = "governance_absence_lacks_broad_dated_review"
            counter_ids = [eid for eid, passage in evidence_texts.items()
                           if _affirmative_counterexample(
                               r"\b(?:identified|reported) a material weakness\b|"
                               r"\b(?:established|confirmed) (?:a )?(?:conflict of interest|misconduct)\b", passage)]
            if counter_ids:
                reason = "governance_absence_has_unresolved_counterexample"
        elif pid == "material_event_assessment" and value == "none_identified":
            if not _scoped_absence(text, "events"):
                reason = "event_absence_lacks_broad_dated_review"
            counter_ids = [eid for eid, passage in evidence_texts.items() if _event_counterexample(passage)]
            if counter_ids:
                reason = "event_absence_has_unresolved_counterexample"
        elif pid == "litigation_risk_assessment" and value == "contingent_exposure_only":
            counter_ids = [eid for eid, passage in evidence_texts.items() if received_inquiry(passage)]
            if counter_ids:
                reason = "contingent_only_has_received_or_existing_inquiry"
            elif not _current_matter_screen(text):
                reason = "contingent_only_lacks_explicit_current_matter_screen"
        if reason:
            changes.append({
                "parameter_id": pid, "original_value": value, "original_status": item["status"],
                "reason": "m3." + reason, "policy_version": M3_GROUNDING_VERSION,
                "evidence_ids": list(cited), "counterevidence_ids": counter_ids,
                "hypothesis_ids": list(item.get("hypothesis_ids") or []),
                "detail": "Necessary M3 support condition not met; no replacement rating was inferred.",
            })
            item.update(status="unavailable", value=None,
                        notes=f"Held back by M3 support verification ({reason}); human evidence review required.")
    return result, changes


def _caveats(claim, evidence_texts):
    """Find specific adverse/limiting statements relevant to a claim's topic."""
    patterns = []
    if _has(r"succession|transition|continuity|institutional knowledge", claim):
        patterns.append(r"succession plans? may not|institutional knowledge may be lost|"
                        r"failure to .{0,70}succession plan|difficult to replace")
    if _has(r"compliance|governance|investigat|litigation", claim):
        patterns.append(r"designed to|inconsistent with our practices|could result in significant penalties|"
                        r"have received.{0,160}government inquir|material,? adverse effect")
    if _has(r"cash|distribution|dividend|guarantee|subordination", claim):
        patterns.append(r"may not be (?:able|permitted)|not be permitted|restrictions contained")
    return {eid: [s for s in _sentences(text) if any(_has(p, s) for p in patterns)]
            for eid, text in evidence_texts.items()
            if any(any(_has(p, s) for p in patterns) for s in _sentences(text))}


def strengthen_m3_assessment(governed, *, evidence_texts, structure_facts):
    """Add conservative checks AFTER unchanged Validator 7; no promotions."""
    data = governed.model_dump(mode="json")
    reviews = []
    issues = data["validation_issues"]

    def issue(rule, item_id, reason):
        issues.append({"rule_id": "m3." + rule, "item_id": item_id,
                       "action": "downgraded", "reason": reason})

    def hold(item, rule, reason):
        if item["disposition"] != "rejected":
            item["disposition"] = "unverified"
        item["admissibility"] = "audit_only"
        if reason not in item["validation_reasons"]:
            item["validation_reasons"].append(reason)
        issue(rule, item.get("finding_id") or item.get("observation_id"), reason)

    facts = structure_facts or {}
    simulated_ids = ({n["node_id"] for n in facts.get("nodes", [])}
                     if (facts.get("source") or {}).get("is_simulated") else set())
    for entity in data["legal_entities"]:
        citations = [evidence_texts[e] for e in entity["evidence_ids"] if e in evidence_texts]
        if entity["entity_id"] in simulated_ids and not any(
                _normal(entity["legal_name"]) in _normal(t) for t in citations):
            if entity["verification_status"] != "unverified":
                entity["verification_status"] = "unverified"
                issue("simulated_identity_not_legal_evidence", entity["entity_id"],
                      "An illustrative node alone does not verify an actual legal person or transaction role.")
    entities = {e["entity_id"]: e for e in data["legal_entities"]}
    for bucket, endpoints in (("ownership_relationships", ("parent_entity_id", "subsidiary_entity_id")),
                              ("cash_transfer_relationships", ("source_entity_id", "destination_entity_id"))):
        for rel in data[bucket]:
            if rel["confirmation_status"] == "verified" and any(
                    entities[eid]["verification_status"] == "unverified"
                    for field in endpoints if (eid := rel[field]) in entities):
                rel["confirmation_status"] = "unverified"
                issue("relationship_identity_unverified", rel.get("relationship_id") or rel.get("transfer_id"),
                      "A verified relationship requires verified legal endpoints; no unknown entity was repaired.")
    for cash in data["cash_flow_locations"]:
        entity = entities.get(cash["entity_id"])
        if cash["provenance_status"] == "verified" and entity and entity["verification_status"] == "unverified":
            cash["provenance_status"] = "unverified"
            issue("cash_role_identity_unverified", cash["cash_flow_id"],
                  "Verified entity-level cash roles cannot rest on an unverified legal identity; no role or amount was inferred.")
    for bucket in ("structural_credit_findings", "management_assessments", "governance_screening"):
        for item in data[bucket]:
            claim = _normal(" ".join(str(item.get(k) or "") for k in
                                     ("credit_hypothesis", "hypothesis", "causal_mechanism", "credit_mechanism")))
            hits = _caveats(claim, evidence_texts)
            ack_ids = set(item.get("contradicting_evidence_ids") or [])
            review = {"item_id": item.get("finding_id") or item.get("observation_id"),
                      "policy_version": M3_GROUNDING_VERSION,
                      "application_search_performed": True,
                      "scope": "bounded admitted M3 packet; topic-specific counterexample patterns, not semantic entailment",
                      "passages_searched": len(evidence_texts), "matched_caveats": hits,
                      "model_counterevidence_review_claim": item["contradictory_evidence_investigated"],
                      "acknowledged_counterevidence_ids": sorted(ack_ids)}
            reviews.append(review)
            if bucket == "structural_credit_findings" and facts and (
                not item.get("analytical_dimension") or not
                (item.get("referenced_node_ids") or item.get("referenced_link_ids"))
            ):
                hold(item, "structural_support_contract",
                     "A structural finding needs its analytical dimension and applicable validated graph references; none were inferred.")
            cited = " ".join(evidence_texts[e] for e in item.get("supporting_evidence_ids", []) if e in evidence_texts)
            if _has(r"\bno (?:material )?governance (?:concerns?|risks?)\b|governance (?:concerns?|risks?) (?:are )?absent", claim) and not _scoped_absence(cited, "governance"):
                hold(item, "finding_governance_absence_scope", "A narrow disclosure or ICFR opinion does not establish absence of broad governance concerns.")
            if _has(r"\bno material events?\b|\bnone identified\b", claim) and not _scoped_absence(cited, "events"):
                hold(item, "finding_event_absence_scope", "No comprehensive dated material-event review supports this absence conclusion.")
            if _has(r"contingent[- ]exposure[- ]only|merely contingent|only contingent", claim) and (
                    any(received_inquiry(t) for t in evidence_texts.values()) or not _current_matter_screen(cited)):
                hold(item, "finding_litigation_scope", "A contingent-only conclusion requires a current-matter screen and reconciliation of received inquiries and source caveats.")
            if _has(r"internal control|icfr", cited) and _has(
                    r"(?:proves?|establishes?|demonstrates?) (?:sound |strong |effective |overall )?(?:governance|management integrity|operational competence)", claim):
                hold(item, "finding_icfr_overreach", "Financial-reporting assurance does not establish broad governance, integrity or operational competence.")
            if hits and (not set(hits) <= ack_ids or item["disposition"] == "supported"):
                hold(item, "claim_specific_counterevidence",
                     "Relevant admitted caveats were not explicitly reconciled with a qualified finding and cited counter-evidence.")
            if (bucket == "management_assessments" and item["evidence_basis"] in
                    {"reported_experience", "credentials_or_tenure"} and
                    _has(r"\b(?:reduce|reduces|mitigate|mitigates|eliminate|eliminates|ensure|ensures)\b", claim)
                    and not (item["disposition"] == "qualified" and
                             _has(r"\b(?:may|could|potential|unproven|not demonstrated)\b", claim))):
                hold(item, "experience_not_demonstrated_risk_reduction",
                     "Reported experience alone does not demonstrate reduced execution or transition risk; outcome evidence is required.")
    return M3CorporateAssessment.model_validate(data), reviews
