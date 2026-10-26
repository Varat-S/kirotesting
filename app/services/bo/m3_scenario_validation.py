"""Necessary provenance and reference checks for hypothetical case findings."""
import re


def structural_reference_reasons(item, facts, scenario_id, hypotheses):
    reasons = []
    nodes = {n["node_id"]: n for n in facts.get("nodes", [])}
    links = {l["link_id"]: l for l in facts.get("links", [])}
    refs = set(item.get("referenced_node_ids") or [])
    link_refs = set(item.get("referenced_link_ids") or [])
    if scenario_id in (item.get("supporting_evidence_ids") or []):
        if item.get("onboarding_source_reference") not in {
                (facts.get("source") or {}).get("source_document_id"),
                (facts.get("source") or {}).get("document_hash")}:
            reasons.append("Scenario findings must identify the actual onboarding source; none was repaired.")
        if not refs or not refs <= set(nodes) or not link_refs <= set(links):
            reasons.append("Scenario evidence does not authorize unknown or absent graph references.")
        if item.get("analytical_dimension") == "guarantor_coverage" and item.get("credit_direction") == "positive" and not any(
                nodes[n]["is_guarantor"] and not nodes[n]["is_grouping_node"] for n in refs & set(nodes)):
            reasons.append("An assumed-guarantor coverage benefit needs an actual guarantor node reference.")
        if item.get("analytical_dimension") in {"borrower_position", "cash_flow_access", "operating_debt_structure", "minority_or_jv"} and not link_refs:
            reasons.append("An ownership or hierarchy mechanism requires the actual relevant ownership links.")
        if any({links[l]["parent_id"], links[l]["child_id"]} - refs for l in link_refs if l in links):
            reasons.append("Referenced ownership links must connect the referenced case nodes.")
        observation = item.get("structural_observation") or ""
        for nid in refs & set(nodes):
            label = "(?:" + re.escape(nid) + "|" + re.escape(nodes[nid]["display_name"]) + ")"
            match = re.search(label + r"\s+(?:is|is marked as|is assumed to be)\s+(?:an? |assumed )*"
                r"(?P<negative>non[- ]|not (?:an? )?)?guarantor\b", observation, re.I)
            if match and (not bool(match["negative"])) != nodes[nid]["is_guarantor"]:
                reasons.append("The claimed guarantor marking conflicts with the referenced case node.")
        if not any(re.search(r"hypothetical|scenario|simulat", q, re.I) for q in item.get("qualifications", [])):
            reasons.append("A scenario finding must explicitly retain its hypothetical scope.")
        text = " ".join(str(item.get(k) or "") for k in ("structural_observation", "credit_hypothesis", "causal_mechanism"))
        for clause in re.split(r"[.;]", text.casefold()):
            if re.search(r"\b(?:not|no|unproven|unverified|unknown|may|could|might|if|would|requires?)\b", clause):
                continue
            if re.search(r"enforceable|legally binding|contractually restrict|dividends? (?:are|is) restricted|"
                         r"creditors? (?:have|has) priority|debt (?:ranks|is) senior|unrestricted cash|full cash access|"
                         r"cash is freely|freely (?:available|distribut|upstream)|unrestricted (?:access|distribution)", clause):
                reasons.append("Case ownership/guarantor assumptions cannot prove real enforceability, creditor ranking or cash restrictions.")
                break
    hid = item.get("hypothesis_id")
    if hypotheses is not None:
        hypothesis = hypotheses.get(hid)
        if not hypothesis:
            reasons.append("A structural finding needs a resolving structural hypothesis reference.")
        else:
            claim = hypothesis.get("claim", "").casefold()
            dimension = item.get("analytical_dimension")
            topics = {"guarantor_coverage": r"guarant|recourse", "cash_flow_access": r"cash|upstream|distribution|dividend",
                "borrower_position": r"borrower|holding|subsidiar|subordinat", "operating_debt_structure": r"creditor|debt|subordinat|liabilit",
                "minority_or_jv": r"minority|joint venture|ownership|economic interest", "structural_complexity": r"hierarchy|structure|holding"}
            if dimension and not re.search(topics.get(dimension, r"(?!)"), claim):
                reasons.append("The linked hypothesis addresses another topic, not this structural dimension.")
            citations = set(hypothesis.get("supporting_evidence_ids", [])) | {
                q["evidence_id"] for q in hypothesis.get("evidence_quotes", [])}
            if not citations.intersection(item.get("supporting_evidence_ids") or []):
                reasons.append("The structural finding and its hypothesis have no common cited support.")
    return reasons
