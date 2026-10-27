"""Observable leadership history and qualified continuity-risk verification.

This is a bounded necessary-support verifier, not a competence rating or a
general semantic entailment engine. It neither invents executive facts nor
promotes a finding withheld by Validator 7. Legacy proposals use the old path.
"""

from __future__ import annotations

import calendar
import re
from datetime import date

from app.schemas.bo_m3_management import CONTINUITY_DIMENSIONS


MANAGEMENT_POLICY_VERSION = "bo-m3-management-stability-1.0"


def normal(text):
    return re.sub(r"\s+", " ", (text or "").replace("’", "'").replace("—", "-").replace("–", "-")).strip().casefold()


def date_bounds(value):
    """Intervals for calculation only; never render a fabricated exact date."""
    if not value or not re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value):
        return None
    try:
        parts = [int(p) for p in value.split("-")]
        if len(parts) == 1:
            return date(parts[0], 1, 1), date(parts[0], 12, 31)
        if len(parts) == 2:
            return date(*parts, 1), date(*parts, calendar.monthrange(*parts)[1])
        return date(*parts), date(*parts)
    except ValueError:
        return None


def date_in_quote(value, quote):
    bounds = date_bounds(value)
    if bounds is None:
        return False
    if re.search(r"(?<!\d)" + re.escape(value) + r"(?![-\d])", quote):
        return True
    parts = value.split("-")
    if len(parts) == 1:
        return bool(re.search(r"\b" + parts[0] + r"\b", quote))
    month = calendar.month_name[int(parts[1])]
    if len(parts) == 2:
        pattern = month + r"\s+" + parts[0]
    else:
        pattern = month + r"\s+0?" + str(int(parts[2])) + r",?\s+" + parts[0]
    return bool(re.search(r"\b" + pattern + r"\b", quote, re.I))


def role_key(role):
    role = normal(role)
    for short, title in (("CEO", "chief executive officer"), ("CFO", "chief financial officer"),
                         ("COO", "chief operating officer")):
        if title in role or re.search(r"\b" + short.casefold() + r"\b", role):
            return short
    return None


def source_ok(source, texts):
    return bool(source and source["evidence_id"] in texts and source["quote"].strip()
                and normal(source["quote"]) in normal(texts[source["evidence_id"]]))


def _company_context(quote, aliases):
    return bool(re.search(r"\b(?:the company|our|this company)\b", quote) or any(
        re.search(r"\b" + re.escape(normal(alias)) + r"\b", quote) for alias in aliases if alias))


def _role_in(value, quote):
    return normal(value) in quote or bool(role_key(value) and role_key(value) == role_key(quote))


def _fact_reason(fact, profile, texts, aliases):
    if not source_ok(fact["source"], texts):
        return "Fact source is missing or its quote is not a contiguous admitted excerpt."
    quote = normal(fact["source"]["quote"])
    surname = normal(profile["name"]).split()[-1]
    if not re.search(r"\b" + re.escape(surname) + r"\b", quote):
        return "The quoted fact does not identify the executive; no subject was inferred."
    if not fact["value"].strip() or not _role_in(fact["value"], quote):
        return "The fact value is not expressed in its quoted evidence."
    for key in ("date", "end_date"):
        if fact[key] and not date_in_quote(fact[key], fact["source"]["quote"]):
            return "The stated date or its precision is not supported by the quoted source."
    if fact["date"] and fact["end_date"] and date_bounds(fact["date"])[0] > date_bounds(fact["end_date"])[1]:
        return "The stated date interval is reversed."
    kind = fact["kind"]
    company = _company_context(quote, aliases)
    if kind in {"current_role", "appointment"} and not (
            _role_in(profile["current_role"], quote) and
            re.search(r"appointed|has served|serves as|is (?:our |the )?|executive officers|became", quote)):
        return "The current appointment/role is not established by the quotation."
    if kind == "appointment" and (not fact["date"] or not re.search(r"appointed|since|became|effective", quote)):
        return "An appointment needs an explicit appointment date, with its original precision."
    if kind == "appointment":
        # A date elsewhere in a long biography may belong to a different role
        # (e.g. a board appointment). Bind it to the actual quoted role clause.
        contexts = [re.split(r"[.;]|\band (?:was |is )?appointed\b|\bprior to\b", quote[m.end():])[0]
                    for m in re.finditer(re.escape(normal(fact["value"])), quote)]
        if not any(date_in_quote(fact["date"], context) for context in contexts):
            return "The date is not tied to this appointment role; another biography date cannot substitute."
    if kind == "company_join" and not (company and re.search(r"\bjoined\b", quote) and fact["date"]):
        return "Company entry needs an explicitly dated company-join disclosure."
    if kind == "company_join" and not any(date_in_quote(fact["date"], re.split(r"[.;]", tail)[0])
                                           for tail in re.split(r"\bjoined\b", quote)[1:]):
        return "The stated company-entry date is not tied to the company-join clause."
    if kind == "prior_internal_role":
        prefix = quote[:quote.find(normal(fact["value"]))] if normal(fact["value"]) in quote else quote
        if (not company or not re.search(r"previously|prior|served|from", quote) or
                re.search(r"(?:prior to|before) joining[^.]*$", prefix)):
            return "Prior internal service is not established; external roles or biography silence cannot substitute."
    if kind == "external_role" and not re.search(r"prior to joining|before joining|externally|industry|at |with ", quote):
        return "The quotation does not identify relevant external experience."
    if kind == "external_appointment" and not re.search(r"externally recruit|recruited externally|external (?:hire|appointment)|joined .{0,80} from ", quote):
        return "External appointment must be explicit, not inferred from an incomplete biography."
    if kind in {"external_appointment", "no_prior_company_experience"} and re.search(
            r"\bif\b|\b(?:could|might|may|would)\b|not (?:true|the case|established)|incorrect|untrue|"
            r"(?:not|never) (?:recruited externally|externally recruited|an external)", quote):
        return "A hypothetical or negated disclosure cannot establish external recruitment or absence of prior service."
    if kind == "no_prior_company_experience" and not (company and re.search(
            r"(?:no|never|neither|not) (?:previous |previously |prior |had |held |has |worked |a |any |at |for |roles? |positions? |served )*"
            r"(?:experience|roles?|positions?|worked|served|employment)", quote)):
        return "Absence of prior company service must be stated explicitly, not inferred from missing history."
    patterns = {"departure": r"resign|depart|retir|replaced|replacement",
                "planned_succession": r"planned succession|succession plan|orderly transition",
                "internal_promotion": r"promoted internally|internal promotion|promot.{0,50}(?:the company|our)",
                "role_expansion": r"expanded role|role expansion|additional responsibilities",
                "transition_risk": r"transition|integration|coordination|disrupt"}
    if kind in patterns and not re.search(patterns[kind], quote):
        return "The quoted text does not establish this event or transition context."
    return None


def _months(start, end):
    return max(0, (end.year - start.year) * 12 + end.month - start.month - (end.day < start.day))


def govern_management_stability(data, *, evidence_texts, company_aliases=(), window_years=3):
    """Mutate only the new governed management section and its observations."""
    stability = data.get("management_stability")
    if stability is None:
        return
    issues = data["validation_issues"]

    def gap(item_id, reason):
        issues.append({"rule_id": "m3.management_stability", "item_id": item_id,
                       "action": "downgraded", "reason": reason})

    stability["validation_reasons"] = []
    from app.services.bo.m3_roster import disclosed_rosters, roster_coverage
    rosters = disclosed_rosters(evidence_texts)
    stability["source_metadata"] = {eid: {"roster_observation": roster} for eid, roster in rosters.items()}
    if stability["recent_change_window_years"] != window_years:
        stability["validation_reasons"].append("Model window differs from the configured review window; recentness cannot be asserted.")
    as_of = None
    if stability["as_of_date"]:
        anchor = stability["as_of_date"]
        if (len(anchor) == 10 and source_ok(stability["as_of_source"], evidence_texts)
                and date_in_quote(anchor, stability["as_of_source"]["quote"])):
            as_of = date.fromisoformat(anchor)
        else:
            stability["validation_reasons"].append("Review date is not established at day precision by the admitted source.")
    roster_dates = {r["as_of_date"] for r in rosters.values()}
    if as_of and roster_dates and roster_dates != {as_of.isoformat()}:
        stability["validation_reasons"].append("The review date conflicts with the disclosed executive-roster date; a filing date cannot replace it.")
        as_of = None
    try:
        start = as_of.replace(year=as_of.year - window_years, day=min(as_of.day, calendar.monthrange(as_of.year - window_years, as_of.month)[1])) if as_of else None
    except ValueError:
        stability["validation_reasons"].append("Configured review window is outside the supported calendar range.")
        as_of, start = None, None
    facts, profiles = {}, {}
    all_ids = [f["fact_id"] for p in stability["executives"] for f in p["facts"]]
    duplicates = {fid for fid in all_ids if all_ids.count(fid) > 1}
    profile_ids = [p["executive_id"] for p in stability["executives"]]
    for profile in stability["executives"]:
        pid = profile["executive_id"]
        profile["validation_reasons"] = []
        valid = []
        for fact in profile["facts"]:
            reason = _fact_reason(fact, profile, evidence_texts, company_aliases)
            if fact["fact_id"] in duplicates or profile_ids.count(pid) != 1:
                reason = "Executive and fact IDs must be unique."
            fact["verification_status"] = "unverified" if reason else "verified"
            fact["validation_reasons"] = [reason] if reason else []
            if reason:
                gap(fact["fact_id"], reason)
            else:
                valid.append(fact)
                facts[fact["fact_id"]] = (pid, fact)
        if not any(f["kind"] in {"current_role", "appointment"} and
                   normal(profile["name"]) in normal(f["source"]["quote"]) for f in valid):
            profile["validation_reasons"].append("Current named executive/role is not established.")
        appointment = [f for f in valid if f["kind"] == "appointment"]
        appointment = appointment[0] if len(appointment) == 1 else None
        internal = any(f["kind"] == "prior_internal_role" for f in valid)
        if appointment:
            internal |= any(f["kind"] == "company_join" and date_bounds(f["date"])[1] <
                            date_bounds(appointment["date"])[0] for f in valid)
        absent = any(f["kind"] == "no_prior_company_experience" for f in valid)
        profile["prior_company_experience"] = ("disputed" if internal and absent else
            "established" if internal else "explicitly_absent" if absent else "unknown")
        external = any(f["kind"] == "external_appointment" for f in valid)
        profile["transition_type"] = ("unknown" if internal and absent else
            "external_appointment" if external else
            "role_expansion" if any(f["kind"] == "role_expansion" for f in valid) else
            "internal_promotion" if any(f["kind"] == "internal_promotion" for f in valid) else
            "internal_succession" if internal and appointment else "unknown")
        profile["tenure"] = {}
        profile["company_tenure"] = {}
        profile["recent_appointment"] = "unknown"
        if appointment and as_of and not stability["validation_reasons"]:
            low, high = date_bounds(appointment["date"])
            if low > as_of:
                profile["validation_reasons"].append("Appointment is after the review date.")
            elif high <= as_of:
                profile["tenure"] = {"min_completed_months": _months(high, as_of),
                    "max_completed_months": _months(low, as_of), "as_of_date": as_of.isoformat(),
                    "appointment_date_precision": {4: "year", 7: "month", 10: "day"}[len(appointment["date"])]}
                profile["recent_appointment"] = "yes" if low >= start else "no" if high < start else "unknown"
        joined = [f for f in valid if f["kind"] == "company_join"]
        if len(joined) == 1 and as_of and not stability["validation_reasons"]:
            low, high = date_bounds(joined[0]["date"])
            if high <= as_of:
                profile["company_tenure"] = {"min_completed_months": _months(high, as_of),
                    "max_completed_months": _months(low, as_of), "as_of_date": as_of.isoformat(),
                    "join_date_precision": {4: "year", 7: "month", 10: "day"}[len(joined[0]["date"])]}
        profiles[pid] = profile
    coverage = roster_coverage(rosters, list(profiles.values()))
    if coverage["status"] == "partial":
        reason = ("Disclosed executive-roster coverage is incomplete: omitted=" +
                  ", ".join(coverage["omitted_names"]) + "; unverified=" +
                  ", ".join(coverage["unverified_names"]) + ". No profiles were fabricated.")
        stability["limitations"].append(reason)
        gap("management_roster", reason)
        if re.search(r"\bcomprehensive\b|\bcomplete review\b|\ball executive", normal(stability["review_scope"])):
            stability["validation_reasons"].append("The model's comprehensive-review claim is unsupported by actual roster coverage.")
    elif coverage["status"] == "unavailable":
        stability["limitations"].append("A dated executive roster could not be extracted; full roster coverage is unavailable.")
    history = stability["history_source"]
    complete = bool(as_of and stability["history_start"] and stability["history_end"] and
                    source_ok(history, evidence_texts) and
                    all(date_bounds(stability[k]) and len(stability[k]) == 10 and
                        date_in_quote(stability[k], history["quote"]) for k in ("history_start", "history_end")))
    if complete:
        complete = (date.fromisoformat(stability["history_start"]) <= start and
                    date.fromisoformat(stability["history_end"]) >= as_of and
                    bool(re.search(r"complete (?:leadership|management|executive).{0,30}history|comprehensive.{0,30}review", normal(history["quote"]))))
    if coverage["status"] == "partial":
        complete = False
    no_changes = bool(complete and re.search(r"no (?:senior |key |executive |leadership |management )*changes", normal(history["quote"]))
                      and not re.search(r"\b(?:if|could|might|would|may)\b|not (?:true|the case)", normal(history["quote"]))
                      and all(key in normal(history["quote"]) for key in ("ceo", "cfo", "coo"))
                      and not any(p["recent_appointment"] == "yes" for p in profiles.values()))
    stability["history_coverage"] = "complete" if complete else "partial"
    stability["change_history_status"] = ("changes_established" if any(
        p["recent_appointment"] == "yes" and not p["validation_reasons"] for p in profiles.values()) else
        "no_changes_established" if no_changes else "insufficient_information")
    if not complete:
        limitation = "A single filing's selected biographies do not establish a complete change history or turnover frequency for the review window."
        if limitation not in stability["limitations"]:
            stability["limitations"].append(limitation)
    for item in data["management_assessments"]:
        if item["dimension"] not in CONTINUITY_DIMENSIONS:
            # Legacy findings remain auditable; new-mode conclusions must not
            # silently fall back to competence or integrity assessments.
            reason = "The stability contract does not admit legacy competence/integrity judgments."
        else:
            reason = None
        linked_profiles = [profiles[e] for e in item["executive_ids"] if e in profiles]
        linked_facts = [facts[f] for f in item["fact_ids"] if f in facts]
        if (not linked_profiles or len(linked_profiles) != len(set(item["executive_ids"])) or
                not linked_facts or len(linked_facts) != len(set(item["fact_ids"])) or
                any(p["validation_reasons"] for p in linked_profiles) or
                any(pid not in item["executive_ids"] for pid, _ in linked_facts)):
            reason = "Finding needs consistent named executives and verified, claim-linked fact IDs."
        elif not {f["source"]["evidence_id"] for _, f in linked_facts} <= set(item["supporting_evidence_ids"] + item["contradicting_evidence_ids"]):
            reason = "Finding citations must include the sources of its linked executive facts."
        text = normal(item["hypothesis"] + " " + item["credit_mechanism"])
        if re.search(r"(?:superior|proven|demonstrated|high|strong) (?:management )?(?:competence|integrity|credibility|decision.making)|\bincompetent\b", text):
            reason = "Continuity and experience do not establish superior competence, integrity or decision-making."
        if unsupported_performance_claim(text):
            reason = "Leadership history does not establish predictable strategic execution or realised financial improvement."
        if re.search(r"no (?:recent |leadership |management |executive )*changes|no turnover", text) and not no_changes:
            reason = "Absence of changes requires an explicitly evidenced complete-period review."
        elif re.search(r"no (?:recent |leadership |management |executive )*changes|no turnover", text) and (
                history["evidence_id"] not in item["supporting_evidence_ids"] + item["contradicting_evidence_ids"]):
            reason = "The no-change finding must cite the actual complete-period review source."
        if re.search(r"(?:has|have|had) disrupted|disruption (?:has |had )?occurred|caused (?:operational |financial )?disruption", normal(item["hypothesis"])):
            if not any(f["kind"] == "transition_risk" and re.search(r"\bdisrupted\b|disruption occurred|caused disruption", normal(f["source"]["quote"]))
                       and not re.search(r"\b(?:may|could|might|not|no)\b", normal(f["source"]["quote"])) for _, f in linked_facts):
                reason = "A transition inference does not establish that disruption has actually occurred."
        if item["credit_direction"] == "negative" and not reason:
            changed = [p for p in linked_profiles if p["recent_appointment"] == "yes" and
                       p["transition_type"] == "external_appointment" and p["prior_company_experience"] == "explicitly_absent"]
            if not changed:
                reason = "Negative transition inference needs a recent external appointment and explicit lack of prior company service; unknown is not absent."
            elif any(not {"appointment", "external_appointment", "no_prior_company_experience"} <=
                     {f["kind"] for pid, f in linked_facts if pid == p["executive_id"]} for p in changed):
                reason = "The negative finding must link the actual appointment, external-hire and no-prior-service facts."
        if item["credit_direction"] == "positive" and not reason:
            if not any(p["prior_company_experience"] == "established" and p["tenure"] for p in linked_profiles):
                reason = "Positive continuity inference requires established company familiarity and evidenced current-role history."
            elif not ({"appointment"} <= {f["kind"] for _, f in linked_facts} and
                      {"company_join", "prior_internal_role"} & {f["kind"] for _, f in linked_facts}):
                reason = "The continuity finding must cite its appointment and company-familiarity facts."
        mitigation = [f for p in linked_profiles for f in p["facts"] if
                      f["verification_status"] == "verified" and f["kind"] in {"external_role", "planned_succession"}]
        if item["credit_direction"] == "negative" and not reason and mitigation and (
                not item["mitigating_considerations"] or
                not {f["fact_id"] for f in mitigation} <= set(item["fact_ids"])):
            reason = "A negative transition finding must acknowledge disclosed external experience and succession mitigation."
        if not reason and not re.search(r"\bmay\b|\bcould\b|\bsuggest|\bpotential|\buncertain|does not establish|not proof", text):
            reason = "Credit implications of disclosed leadership history must remain explicitly qualified."
        if reason:
            if item["disposition"] != "rejected":
                item["disposition"] = "unverified"
            item["admissibility"] = "audit_only"
            item["validation_reasons"].append(reason)
            gap(item["observation_id"], reason)
        elif item["disposition"] == "supported":
            item.update(disposition="qualified", admissibility="qualified_narrative")
    return data


def unsupported_performance_claim(text):
    """Necessary check on asserted outcomes, not a general semantic judgement."""
    for clause in re.split(r"[.;]|,\s*(?:and|thereby|thus)|\bwhich\b", normal(text)):
        if re.search(r"\b(?:not|never|no evidence|does not|cannot)\b", clause):
            continue
        if re.search(r"\b(?:ensures?|guarantees?|proves?)\b.{0,60}(?:execut|performance|cash.flow|predictab|competence)", clause):
            return True
        if (re.search(r"(?:predictable strategic execution|predictable financial outcomes|"
                      r"improves? cash.flow|superior competence|better financial performance)", clause)
                and not re.search(r"\b(?:may|could|potential|unproven|uncertain)\b", clause)):
            return True
    return False


def stability_view(stability, observations):
    """Four reviewable sections; group direction never follows a CEO alone."""
    from app.services.bo.m3_artifacts import _is_admitted

    sources = stability.get("source_metadata") or {}
    from app.services.bo.m3_roster import roster_coverage
    coverage = roster_coverage({eid: meta["roster_observation"] for eid, meta in sources.items()
                               if "roster_observation" in meta}, stability["executives"])

    def citation(fact):
        return {**fact["source"], **sources.get(fact["source"]["evidence_id"], {})}

    details = []
    for profile in stability["executives"]:
        valid = [f for f in profile["facts"] if f["verification_status"] == "verified"]
        appointments = [f for f in valid if f["kind"] == "appointment"]
        details.append({
            "executive_id": profile["executive_id"], "name": profile["name"], "current_role": profile["current_role"],
            "profile_status": "verified" if not profile["validation_reasons"] else "unverified",
            "appointment_date": appointments[0]["date"] if len(appointments) == 1 else None,
            "current_role_tenure": profile["tenure"], "company_tenure": profile["company_tenure"],
            "prior_company_experience": profile["prior_company_experience"],
            "previous_company_roles": [f["value"] for f in valid if f["kind"] == "prior_internal_role"],
            "external_roles": [f["value"] for f in valid if f["kind"] == "external_role"],
            "transition_type": profile["transition_type"], "recent_appointment": profile["recent_appointment"],
            "source_references": [citation(f) for f in valid],
            "limitations": list(profile["validation_reasons"]),
        })
    admitted = [o for o in observations if o["dimension"] in CONTINUITY_DIMENSIONS and _is_admitted(o)]
    fact_index = {f["fact_id"]: f for p in stability["executives"] for f in p["facts"]}
    findings = [{
        "finding_id": o["observation_id"], "executive_ids": o["executive_ids"],
        "evidence": [{"fact_id": fid, "kind": fact_index[fid]["kind"], "value": fact_index[fid]["value"],
                      "date": fact_index[fid]["date"], "source": citation(fact_index[fid])}
                     for fid in o["fact_ids"] if fid in fact_index and fact_index[fid]["verification_status"] == "verified"],
        "qualified_inference": o["hypothesis"], "credit_implication": o["credit_mechanism"],
        "credit_direction": o["credit_direction"], "materiality": o["materiality"], "confidence": o["confidence"],
        "disposition": o["disposition"], "uncertainties": o["uncertainties"] + o["missing_information"],
        "mitigating_considerations": o["mitigating_considerations"],
        "source_references": list(dict.fromkeys(o["supporting_evidence_ids"] + o["contradicting_evidence_ids"])),
    } for o in admitted]
    valid_profiles = [p for p in stability["executives"] if not p["validation_reasons"]]
    covered = {role_key(p["current_role"]) for p in valid_profiles} - {None}
    negative_ids = {eid for o in admitted if o["credit_direction"] == "negative" for eid in o["executive_ids"]}
    external_changes = {role_key(p["current_role"]) for p in valid_profiles if p["executive_id"] in negative_ids
                        and p["recent_appointment"] == "yes" and p["prior_company_experience"] == "explicitly_absent"} - {None}
    directions = {o["credit_direction"] for o in admitted}
    if coverage["status"] == "partial":
        classification, direction = "insufficient_information", None
        summary = "Individual findings may be useful, but incomplete coverage of the disclosed executive roster prevents a group-wide conclusion."
    elif len(external_changes) >= 2:
        classification, direction = "increased_transition_uncertainty", "negative"
        summary = "Multiple disclosed key-role changes with explicitly absent prior company service may increase coordination and transition uncertainty; realised disruption is not established."
    elif "negative" in directions or "mixed" in directions:
        classification, direction = "mixed_indicators", "mixed"
        summary = "The disclosed group has transition considerations requiring qualification; a single external appointment does not establish an overall adverse management assessment."
    elif {"CEO", "CFO", "COO"} <= covered and "positive" in directions and all(
            p["prior_company_experience"] == "established" for p in valid_profiles if role_key(p["current_role"])):
        classification, direction = "organisational_continuity_supported", "positive"
        summary = "Disclosed key executives' role histories and internal experience support institutional familiarity and potential continuity benefits, including where a current appointment is recent."
    elif admitted and directions == {"neutral"}:
        classification, direction = "mixed_indicators", "neutral"
        summary = "The admitted leadership observations provide context without establishing an overall positive or negative direction."
    else:
        classification, direction = "insufficient_information", None
        summary = "Specific executive findings may be useful, but the disclosed history does not support a group-wide direction."
    limits = list(dict.fromkeys(stability["limitations"] + stability["validation_reasons"] + [
        "Continuity and company familiarity do not prove management competence, integrity or superior decision-making."]))
    return {
        "version": MANAGEMENT_POLICY_VERSION,
        "overall_management_stability_assessment": {
            "credit_direction": direction, "summary": summary, "review_scope": stability["review_scope"],
            "as_of_date": stability["as_of_date"], "recent_change_window_years": stability["recent_change_window_years"],
            "history_coverage": stability["history_coverage"], "change_history_status": stability["change_history_status"],
            "established_recent_appointments": [p["executive_id"] for p in valid_profiles if p["recent_appointment"] == "yes"],
            "internal_experience_established": [p["executive_id"] for p in valid_profiles if p["prior_company_experience"] == "established"],
            "external_appointments_established": [p["executive_id"] for p in valid_profiles if p["transition_type"] == "external_appointment"],
            "limitations": limits,
            "executive_roster_coverage": coverage,
        },
        "key_executive_details": details,
        "credit_findings": findings,
        "consolidated_management_stability_conclusion": {
            "classification": classification, "credit_direction": direction, "summary": summary,
            "admitted_finding_ids": [f["finding_id"] for f in findings], "limitations": limits,
            "requires_human_review": True,
        },
    }
