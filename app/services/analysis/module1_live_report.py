"""Live-evaluation review package for Module 1 Stages 1-3 (Task 13 §12).

Builds a machine-readable model (sections A-G) from a
:class:`LiveEvaluationResult` and renders Markdown + standalone HTML. Every
rendering is clearly marked LIVE and explicitly distinguishes REAL LLM output
(hypotheses, enquiry plan) from DETERMINISTIC retrieval results, and states that
stages after enquiry/retrieval were not run live.

Governance in the renderer: API credentials are never exposed; the analytical
review (section F) surfaces observations WITHOUT assigning an automated overall
model-quality score (that requires a human-approved rubric, §12.F).
"""

from __future__ import annotations

import html
from typing import Any

from app.schemas.llm import CreditDimension
from app.services.analysis.module1_live import (
    LIVE_WARNING_BANNER,
    LiveEvaluationResult,
)

REAL_VS_DETERMINISTIC = {
    "live_model_output": [
        "Stage 1 — credit hypotheses (dimension, direction, rationale, citations).",
        "Stage 2 — evidence-enquiry plan (questions, intents, queries).",
    ],
    "deterministic_not_model": [
        "Source admission, hashes, cutoff and the admitted/rejected set.",
        "Stage 3 — evidence retrieval (lexical search over the real corpus).",
        "All schema / Pydantic / governance validation of the model output.",
    ],
    "not_run_live": [
        "Hypothesis testing, credit signals, materiality, completeness gate and "
        "RED challenge were NOT run in this live evaluation.",
    ],
}


def build_live_report_model(result: LiveEvaluationResult) -> dict[str, Any]:
    passages_by_id = {p.passage_id: p for p in result.corpus.passages}
    return {
        "warning": LIVE_WARNING_BANNER,
        "evaluation_kind": "live_stage_1_to_3",
        "generated_by": "app.demo --mode live (Module 1 live evaluation)",
        "fixture": result.fixture_key,
        "real_vs_deterministic": REAL_VS_DETERMINISTIC,
        "A_source_scope": _section_source(result),
        "B_model_configuration": _section_model(result),
        "C_autonomous_hypotheses": _section_hypotheses(result),
        "D_autonomous_enquiry_plan": _section_plan(result),
        "E_actual_retrieval": _section_retrieval(result),
        "F_analytical_review": _section_review(result, passages_by_id),
        "G_limitations": _section_limitations(result),
    }


# -- A. Source scope ---------------------------------------------------------


def _section_source(r: LiveEvaluationResult) -> dict:
    sc = r.source_context
    return {
        "company": r.company,
        "reporting_entity": r.reporting_entity,
        "reporting_period_as_of": r.as_of_date,
        "evidence_cutoff": r.evidence_cutoff,
        "primary_document_id": r.primary_document_id,
        "admitted_sources": r.admitted_sources,
        "rejected_sources": r.rejected_sources,
        "source_extraction_limitations": r.source_extraction_limitations,
        "model_context": {
            "selection_process": sc.selection_process,
            "supplied_passage_count": len(sc.supplied),
            "omitted_passage_count": sc.omitted_passage_count,
            "total_passage_count": sc.total_passage_count,
            "sections_supplied": sc.sections_supplied,
            "sections_in_corpus": sc.sections_in_corpus,
            "max_excerpt_chars": sc.max_excerpt_chars,
            "supplied_passage_ids": sc.supplied_passage_ids,
            "coverage_limitations": sc.coverage_limitations,
        },
    }


# -- B. Model configuration --------------------------------------------------


def _section_model(r: LiveEvaluationResult) -> dict:
    cost = (
        r.estimated_cost_usd
        if r.estimated_cost_usd is not None
        else "not estimated (no configured pricing)"
    )
    return {
        "provider": r.provider,
        "model_id": r.model_id,
        "credentials": "never exposed (read from environment; masked by SecretStr)",
        "prompts": {
            "hypothesis_generation_prompt_id": r.hypothesis_prompt_id,
            "evidence_enquiry_planning_prompt_id": r.enquiry_prompt_id,
        },
        "run_ids": {
            "hypothesis_generation_run_id": r.hypothesis_run_id,
            "evidence_enquiry_planning_run_id": r.enquiry_run_id,
        },
        "model_calls": r.model_calls,
        "token_usage": r.usage_totals,
        "per_call_usage": r.usage_log,
        "estimated_cost_usd": cost,
    }


# -- C. Autonomous hypotheses ------------------------------------------------


def _section_hypotheses(r: LiveEvaluationResult) -> dict:
    hyps = []
    for h in r.hypotheses.hypotheses:
        hyps.append({
            "hypothesis_id": h.hypothesis_id,
            "dimension": h.dimension.value,
            "business_characteristic": h.business_characteristic,
            "statement": h.statement,
            "credit_direction": h.credit_direction.value,
            "economic_reasoning": h.economic_reasoning,
            "potential_credit_impact": h.potential_credit_impact,
            "offsetting_characteristics": list(h.offsetting_characteristics),
            "classification": h.classification.value,
            "verification_status": h.verification_status.value,
            "supporting_evidence_ids": list(h.supporting_evidence_ids),
            "contradictory_evidence_ids": list(h.contradictory_evidence_ids),
            "additional_evidence_required": list(h.additional_evidence_required),
            "missing_information": list(h.missing_information),
            "interpretation_confidence": h.interpretation_confidence,
            "provenance": "LIVE MODEL OUTPUT",
        })
    return {
        "note": (
            "Every hypothesis below is REAL live-model output, generated from "
            "the supplied real 10-K passages only. All are initial status "
            "'unverified' (generation never verifies)."
        ),
        "hypotheses": hyps,
        "dimensions_covered": sorted({h["dimension"] for h in hyps}),
        "dimensions_unassessed": r.unassessed_dimensions,
        "unassessed_note": (
            "A dimension with no generated hypothesis is UNASSESSED / requires "
            "further investigation. It is NOT marked NOT_APPLICABLE."
        ),
    }


# -- D. Autonomous enquiry plan ----------------------------------------------


def _section_plan(r: LiveEvaluationResult) -> dict:
    plan = r.enquiry_plan
    enquiries = []
    perspectives_by_hyp: dict[str, set] = {}
    for e in plan.enquiries:
        enquiries.append({
            "enquiry_id": e.enquiry_id,
            "hypothesis_id": e.hypothesis_id,
            "dimension": e.dimension.value,
            "requirement_kind": e.requirement_kind.value,
            "search_intent": e.search_intent.value,
            "investigative_question": e.investigative_question,
            "search_query": e.search_query,
            "provenance": "LIVE MODEL OUTPUT",
        })
        perspectives_by_hyp.setdefault(e.hypothesis_id, set()).add(
            e.requirement_kind.value)
    coverage = {
        hid: sorted(kinds) for hid, kinds in perspectives_by_hyp.items()
    }
    return {
        "note": (
            "The questions, intents and queries are REAL live-model output. The "
            "Task 5A coverage gate was enforced (not weakened)."
        ),
        "planner_mode": getattr(plan, "planner_mode", None),
        "enquiries": enquiries,
        "perspective_coverage_by_hypothesis": coverage,
        "incomplete_coverage": list(getattr(plan, "incomplete_coverage", []) or []),
    }


# -- E. Actual retrieval -----------------------------------------------------


def _section_retrieval(r: LiveEvaluationResult) -> dict:
    bundle = r.candidate_evidence
    executed = []
    for ex in bundle.executed:
        res = ex.result
        executed.append({
            "enquiry_id": ex.enquiry.enquiry_id,
            "hypothesis_id": ex.enquiry.hypothesis_id,
            "requirement_kind": ex.enquiry.requirement_kind.value,
            "search_intent": ex.enquiry.search_intent.value,
            "search_query": ex.enquiry.search_query,
            "outcome": res.outcome.value,
            "retrieved_passages": [
                {
                    "rank": rp.rank,
                    "score": rp.score,
                    "passage_id": rp.passage.passage_id,
                    "document_id": rp.passage.document_id,
                    "section": rp.passage.section,
                    "source_location": rp.passage.source_location,
                    "excerpt": _excerpt(rp.passage.text),
                }
                for rp in res.ranked_passages
            ],
        })
    return {
        "note": (
            "Retrieval is DETERMINISTIC lexical search over the real corpus (no "
            "model call). A retrieved passage is a candidate; retrieval does NOT "
            "establish that the passage verifies any premise."
        ),
        "executed": executed,
        "unresolved_enquiries": list(
            getattr(bundle, "unresolved_enquiries", []) or []),
    }


# -- F. Analytical review (observations only; NO automated score) ------------


def _section_review(r: LiveEvaluationResult, passages_by_id) -> dict:
    hyps = r.hypotheses.hypotheses
    supplied_ids = set(r.source_context.supplied_passage_ids)

    # Evidence-reference integrity: every cited id must be a supplied passage
    # (governance already rejects out-of-scope citations, so this should hold;
    # we surface it explicitly for the reviewer).
    bad_refs = []
    for h in hyps:
        for rid in list(h.supporting_evidence_ids) + list(h.contradictory_evidence_ids):
            if rid not in supplied_ids:
                bad_refs.append({"hypothesis_id": h.hypothesis_id, "ref": rid})

    directions = {}
    for h in hyps:
        directions.setdefault(h.credit_direction.value, 0)
        directions[h.credit_direction.value] += 1

    named_offsets = [
        {"hypothesis_id": h.hypothesis_id,
         "offsets": list(h.offsetting_characteristics)}
        for h in hyps if h.offsetting_characteristics
    ]

    # Perspective coverage of the plan, per hypothesis (for "missing enquiries").
    plan = r.enquiry_plan
    kinds_by_hyp: dict[str, set] = {}
    for e in plan.enquiries:
        kinds_by_hyp.setdefault(e.hypothesis_id, set()).add(e.requirement_kind.value)
    missing_enquiries = []
    for h in hyps:
        kinds = kinds_by_hyp.get(h.hypothesis_id, set())
        want = {"supporting", "contradictory"}
        missing = sorted(want - kinds)
        if missing:
            missing_enquiries.append(
                {"hypothesis_id": h.hypothesis_id, "missing_perspectives": missing})

    return {
        "note": (
            "Observations to support HUMAN review. No automated overall "
            "model-quality score is assigned (that requires a human-approved "
            "evaluation rubric)."
        ),
        "hypothesis_count": len(hyps),
        "direction_mix": directions,
        "both_upside_and_downside_present": len(
            {h.credit_direction.value for h in hyps} & {"positive", "negative"}) == 2,
        "dimensions_covered": sorted({h.dimension.value for h in hyps}),
        "dimensions_unassessed": r.unassessed_dimensions,
        "company_specific_offsets_named": named_offsets,
        "hypotheses_citing_evidence": sum(
            1 for h in hyps if h.supporting_evidence_ids),
        "hypotheses_without_any_citation": [
            h.hypothesis_id for h in hyps if not h.supporting_evidence_ids],
        "out_of_scope_citations": bad_refs,
        "hypotheses_missing_core_enquiries": missing_enquiries,
        "self_certified_verification": [
            h.hypothesis_id for h in hyps
            if h.verification_status.value != "unverified"
        ],  # must be empty (governance forbids it)
        "review_checklist": [
            "Company-specificity: are hypotheses about NVIDIA's actual disclosed "
            "characteristics, not generic boilerplate?",
            "Upside and downside: did the model consider both directions where a "
            "real economic mechanism exists?",
            "Overlooked issues: are there material characteristics in the "
            "supplied passages the model did not raise?",
            "Plausible offsets: are named offsets real company-specific "
            "capabilities, kept distinct from demonstrated effectiveness?",
            "Unsupported claims / weak or generic hypotheses: flag any.",
            "Missing enquiries and evidence-reference failures: see fields above.",
        ],
    }


# -- G. Limitations ----------------------------------------------------------


def _section_limitations(r: LiveEvaluationResult) -> dict:
    return {
        "stages_not_run_live": [
            "hypothesis_testing", "credit_signals", "materiality",
            "evidence_completeness", "red_challenge",
        ],
        "source_coverage": (
            f"The model saw {len(r.source_context.supplied)} of "
            f"{r.source_context.total_passage_count} admitted narrative passages; "
            "it did NOT review the entire filing."),
        "unverified_evidence": (
            "Retrieved passages are candidates only; none is a verified fact. "
            "Numeric SEC facts remain 'unverified' and were not supplied as "
            "approved corroboration."),
        "approval_authentication": (
            "DimensionNonApplicability approval (approved_by / approval_ref) is "
            "NOT authenticated against a trusted human-review record in this "
            "task; unsupported non-applicability claims are kept unassessed and "
            "visible. Approval-record integration remains a dependency."),
        "cost": (
            "Token usage is reported as provided by the API; a USD figure is "
            "shown only if provider pricing was configured, never invented."),
    }


# -- helpers / renderers -----------------------------------------------------


def _excerpt(text: str, limit: int = 600) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[: limit - 3].rstrip() + "..."


def _md(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(model: dict) -> str:
    L: list[str] = []
    L.append(f"> **{model['warning']}**")
    L.append("")
    L.append("# Business Overview Module 1 — LIVE Evaluation (Stages 1–3)")
    L.append("")
    L.append(f"- Fixture: `{model['fixture']}`")
    L.append(f"- Evaluation kind: {model['evaluation_kind']}")
    L.append("")
    rvd = model["real_vs_deterministic"]
    L.append("## Real model output vs deterministic results")
    L.append("")
    L.append("**Live model output:**")
    for i in rvd["live_model_output"]:
        L.append(f"- {i}")
    L.append("")
    L.append("**Deterministic (not model):**")
    for i in rvd["deterministic_not_model"]:
        L.append(f"- {i}")
    L.append("")
    L.append("**Not run live:**")
    for i in rvd["not_run_live"]:
        L.append(f"- {i}")
    L.append("")

    _md_source(L, model["A_source_scope"])
    _md_model(L, model["B_model_configuration"])
    _md_hypotheses(L, model["C_autonomous_hypotheses"])
    _md_plan(L, model["D_autonomous_enquiry_plan"])
    _md_retrieval(L, model["E_actual_retrieval"])
    _md_review(L, model["F_analytical_review"])
    _md_limitations(L, model["G_limitations"])

    L.append("")
    L.append(f"> **{model['warning']}**")
    L.append("")
    return "\n".join(L)


def _md_source(L, a):
    L.append("## A. Source scope")
    L.append("")
    L.append(f"- Company: **{a['company']}**")
    L.append(f"- Reporting period (as-of): {a['reporting_period_as_of']}")
    L.append(f"- Evidence cutoff: {a['evidence_cutoff']}")
    L.append(f"- Primary document id: `{a['primary_document_id']}`")
    L.append(f"- Admitted sources: {len(a['admitted_sources'])}; "
             f"rejected: {len(a['rejected_sources'])}")
    mc = a["model_context"]
    L.append("")
    L.append("Model context supplied:")
    L.append(f"- Selection: {mc['selection_process']}")
    L.append(f"- Supplied {mc['supplied_passage_count']} of "
             f"{mc['total_passage_count']} passages "
             f"({mc['omitted_passage_count']} omitted).")
    L.append(f"- Sections supplied: {', '.join(mc['sections_supplied'])}")
    L.append("- Coverage limitations:")
    for lim in mc["coverage_limitations"]:
        L.append(f"  - {_md(lim)}")
    L.append("")


def _md_model(L, b):
    L.append("## B. Model configuration")
    L.append("")
    L.append(f"- Provider: {b['provider']}")
    L.append(f"- Model: `{b['model_id']}`")
    L.append(f"- Credentials: {b['credentials']}")
    L.append(f"- Hypothesis prompt: `{b['prompts']['hypothesis_generation_prompt_id']}`")
    L.append(f"- Enquiry prompt: `{b['prompts']['evidence_enquiry_planning_prompt_id']}`")
    L.append(f"- Hypothesis run id: `{b['run_ids']['hypothesis_generation_run_id']}`")
    L.append(f"- Enquiry run id: `{b['run_ids']['evidence_enquiry_planning_run_id']}`")
    L.append(f"- Model calls: {b['model_calls']}")
    L.append(f"- Token usage: {b['token_usage']}")
    L.append(f"- Estimated cost (USD): {b['estimated_cost_usd']}")
    L.append("")


def _md_hypotheses(L, c):
    L.append("## C. Autonomous hypotheses (LIVE model output)")
    L.append("")
    L.append(f"_{c['note']}_")
    L.append("")
    for h in c["hypotheses"]:
        L.append(f"### {h['hypothesis_id']} — {h['dimension']} ({h['credit_direction']})")
        L.append(f"- Characteristic: {_md(h['business_characteristic'])}")
        L.append(f"- Statement: {_md(h['statement'])}")
        L.append(f"- Economic reasoning: {_md(h['economic_reasoning'])}")
        L.append(f"- Potential credit impact: {_md(h['potential_credit_impact'])}")
        L.append(f"- Classification: {h['classification']}; status: "
                 f"**{h['verification_status']}**")
        if h["offsetting_characteristics"]:
            L.append(f"- Offsets named: {_md('; '.join(h['offsetting_characteristics']))}")
        if h["supporting_evidence_ids"]:
            L.append(f"- Supporting evidence ids: "
                     f"{', '.join('`'+x+'`' for x in h['supporting_evidence_ids'])}")
        if h["contradictory_evidence_ids"]:
            L.append(f"- Contradictory evidence ids: "
                     f"{', '.join('`'+x+'`' for x in h['contradictory_evidence_ids'])}")
        if h["missing_information"]:
            L.append(f"- Missing information: {_md('; '.join(h['missing_information']))}")
        L.append("")
    L.append(f"- Dimensions covered: {', '.join(c['dimensions_covered']) or 'none'}")
    L.append(f"- Dimensions UNASSESSED: {', '.join(c['dimensions_unassessed']) or 'none'}")
    L.append(f"- _{c['unassessed_note']}_")
    L.append("")


def _md_plan(L, d):
    L.append("## D. Autonomous enquiry plan (LIVE model output)")
    L.append("")
    L.append(f"_{d['note']}_")
    L.append("")
    for e in d["enquiries"]:
        L.append(f"- `{e['enquiry_id']}` [{e['requirement_kind']}/{e['search_intent']}] "
                 f"hyp `{e['hypothesis_id']}`: {_md(e['search_query'])}")
    L.append("")
    L.append("Perspective coverage by hypothesis:")
    for hid, kinds in d["perspective_coverage_by_hypothesis"].items():
        L.append(f"- `{hid}`: {', '.join(kinds)}")
    L.append("")


def _md_retrieval(L, e):
    L.append("## E. Actual retrieval (DETERMINISTIC)")
    L.append("")
    L.append(f"_{e['note']}_")
    L.append("")
    for ex in e["executed"]:
        L.append(f"### Enquiry `{ex['enquiry_id']}` ({ex['requirement_kind']}) "
                 f"— {ex['outcome']}")
        L.append(f"- Query: {_md(ex['search_query'])}")
        for p in ex["retrieved_passages"]:
            L.append(f"  - [{p['rank']}] `{p['passage_id']}` — "
                     f"{p['section'] or '(section)'}")
            L.append(f"    > {_md(p['excerpt'])}")
        L.append("")


def _md_review(L, f):
    L.append("## F. Analytical review (for human inspection)")
    L.append("")
    L.append(f"_{f['note']}_")
    L.append("")
    L.append(f"- Hypotheses: {f['hypothesis_count']}; direction mix: {f['direction_mix']}")
    L.append(f"- Both upside and downside present: {f['both_upside_and_downside_present']}")
    L.append(f"- Dimensions covered: {', '.join(f['dimensions_covered']) or 'none'}")
    L.append(f"- Dimensions unassessed: {', '.join(f['dimensions_unassessed']) or 'none'}")
    L.append(f"- Hypotheses citing evidence: {f['hypotheses_citing_evidence']}")
    if f["hypotheses_without_any_citation"]:
        L.append(f"- Hypotheses with NO citation: "
                 f"{', '.join(f['hypotheses_without_any_citation'])}")
    L.append(f"- Out-of-scope citations (should be empty): {f['out_of_scope_citations']}")
    L.append(f"- Self-certified verification (must be empty): "
             f"{f['self_certified_verification']}")
    if f["company_specific_offsets_named"]:
        L.append("- Company-specific offsets named:")
        for o in f["company_specific_offsets_named"]:
            L.append(f"  - `{o['hypothesis_id']}`: {_md('; '.join(o['offsets']))}")
    if f["hypotheses_missing_core_enquiries"]:
        L.append("- Hypotheses missing core enquiries:")
        for m in f["hypotheses_missing_core_enquiries"]:
            L.append(f"  - `{m['hypothesis_id']}`: {', '.join(m['missing_perspectives'])}")
    L.append("")
    L.append("Reviewer checklist:")
    for item in f["review_checklist"]:
        L.append(f"- {_md(item)}")
    L.append("")


def _md_limitations(L, g):
    L.append("## G. Limitations")
    L.append("")
    L.append(f"- Stages not run live: {', '.join(g['stages_not_run_live'])}")
    L.append(f"- Source coverage: {_md(g['source_coverage'])}")
    L.append(f"- Unverified evidence: {_md(g['unverified_evidence'])}")
    L.append(f"- Approval authentication: {_md(g['approval_authentication'])}")
    L.append(f"- Cost: {_md(g['cost'])}")
    L.append("")


def render_html(model: dict) -> str:
    md = render_markdown(model)
    banner = html.escape(model["warning"])
    body = html.escape(md)
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>Module 1 — LIVE Evaluation (Stages 1–3)</title>\n"
        "<style>\n"
        "body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;"
        "padding:0;background:#f5f5f5;color:#1a1a1a}\n"
        ".banner{background:#1f5a8a;color:#fff;padding:16px 24px;font-weight:700;"
        "font-size:15px}\n"
        ".wrap{max-width:980px;margin:24px auto;padding:0 16px}\n"
        "pre{white-space:pre-wrap;word-wrap:break-word;background:#fff;border:1px "
        "solid #ddd;border-radius:8px;padding:24px;font-family:ui-monospace,"
        "SFMono-Regular,Menlo,monospace;font-size:13px;line-height:1.5}\n"
        "</style>\n</head>\n<body>\n"
        f"<div class=\"banner\">{banner}</div>\n"
        "<div class=\"wrap\">\n"
        f"<pre>{body}</pre>\n"
        "</div>\n</body>\n</html>\n"
    )
