"""Human-readable + machine-readable Module 1 review package (Task 12).

:func:`build_report_model` turns a :class:`DemoScenario` and the real
:class:`BusinessOverviewModule1Run` into a plain, JSON-serialisable dict covering
sections A-H plus an evidence viewer. :func:`render_markdown` and
:func:`render_html` render that same model for a human reviewer.

Rules enforced here (presentation-level governance):

* Every scripted item is explicitly labelled as a scripted test scenario; no
  scripted hypothesis is presented as an independently discovered insight.
* An unverified / partially-supported conclusion is shown with its EFFECTIVE
  governed status and its uncorroborated premises -- never as proven.
* A citation is only shown as SUPPORTING a finding when its grounding verdict is
  actually supportive; inconclusive citations are shown as "cited (grounding
  inconclusive)" rather than as support.
* HTML / inline-XBRL source locations are shown as section / element locations;
  no PDF page numbers are invented.
* The mandatory offline-evaluation warning appears at the top of every rendering.
"""

from __future__ import annotations

import html
from typing import Any

from app.services.analysis.module1_demo import (
    SCRIPTED_SCENARIO_PROVENANCE,
    DemoScenario,
)
from app.services.analysis.module1_orchestrator import (
    BusinessOverviewModule1Run,
    Stage,
)

WARNING_BANNER = (
    "OFFLINE EVALUATION — SCRIPTED MODEL RESPONSES. "
    "NOT AN AUTONOMOUS CREDIT ASSESSMENT OR LENDING RECOMMENDATION."
)

REAL_VS_SCRIPTED = {
    "real": [
        "Source documents, SHA-256 hashes, reporting/evidence cutoff.",
        "Admitted vs rejected source set (post-cutoff proxy excluded).",
        "Narrative passages: verbatim text, HTML-native section locations, stable ids.",
        "Deterministic retrieval, per-premise grounding, materiality rubric.",
        "Stage 12 completeness gate, RED reference governance, escalation.",
    ],
    "scripted": [
        "Hypothesis statements and credit dimensions (FakeLLMBackend).",
        "Enquiry search queries and intents.",
        "Per-premise hypothesis-testing verdicts proposed by the model.",
        "RED challenge objections proposed by the model.",
    ],
    "note": (
        "Scripted items are an OFFLINE TEST SCENARIO anchored to real NVIDIA "
        "10-K passages. They do NOT demonstrate autonomous AI credit analysis "
        "and are NOT independently discovered company insights."
    ),
}


# ---------------------------------------------------------------------------
# Model construction (machine-readable; the single source for both renderers)
# ---------------------------------------------------------------------------


def build_report_model(
    scenario: DemoScenario, run: BusinessOverviewModule1Run
) -> dict[str, Any]:
    """Assemble the full, JSON-serialisable review model from the REAL run."""
    passages_by_id = {p.passage_id: p for p in scenario.corpus.passages}

    model: dict[str, Any] = {
        "warning": WARNING_BANNER,
        "generated_by": "app.demo (Business Overview Module 1 offline demo)",
        "fixture": scenario.fixture_key,
        "run_id": run.run_id,
        "disposition": run.disposition.value,
        "real_vs_scripted": REAL_VS_SCRIPTED,
        "stage_statuses": _stage_statuses(run),
        "A_source_information": _section_source(scenario),
        "B_hypothesis_generation": _section_hypotheses(scenario, run),
        "C_evidence_investigation": _section_investigation(run, passages_by_id),
        "D_hypothesis_testing": _section_testing(run),
        "E_credit_findings": _section_findings(run),
        "F_evidence_completeness": _section_completeness(run),
        "G_red_challenge": _section_red(run),
        "H_overall_summary": _section_summary(scenario, run),
        "evidence_viewer": _evidence_viewer(scenario, run, passages_by_id),
    }
    return model


def _stage_statuses(run: BusinessOverviewModule1Run) -> list[dict]:
    rows = []
    for stage in Stage:
        st = run.stage(stage)
        rows.append({
            "stage": stage.value,
            "state": st.state.value if st is not None else "not_run",
            "detail": st.detail if st is not None else "stage did not run",
            "run_id": getattr(st, "run_id", None) if st is not None else None,
        })
    return rows


# -- A. Source information ---------------------------------------------------


def _section_source(scenario: DemoScenario) -> dict:
    return {
        "company": scenario.company,
        "reporting_entity": scenario.reporting_entity,
        "reporting_period_as_of": scenario.as_of_date,
        "evidence_cutoff": scenario.evidence_cutoff,
        "primary_document_id": scenario.primary_document_id,
        "admitted_sources": scenario.admitted_sources,
        "rejected_sources": scenario.rejected_sources,
        "source_extraction_limitations": scenario.source_extraction_limitations,
    }


# -- B. Hypothesis generation ------------------------------------------------


def _section_hypotheses(
    scenario: DemoScenario, run: BusinessOverviewModule1Run
) -> dict:
    anchor_by_id = {a.hypothesis_id: a for a in scenario.anchors}
    hyps = []
    if run.hypotheses is not None:
        for h in run.hypotheses.hypotheses:
            anchor = anchor_by_id.get(h.hypothesis_id)
            hyps.append({
                "hypothesis_id": h.hypothesis_id,
                "dimension": h.dimension.value,
                "business_characteristic": h.business_characteristic,
                "statement": h.statement,
                "credit_direction": h.credit_direction.value,
                "initial_verification_status": h.verification_status.value,
                "provenance": SCRIPTED_SCENARIO_PROVENANCE,
                "anchored_real_passage_ids": (
                    anchor.anchor_passage_ids if anchor else []
                ),
            })
    return {
        "provenance_note": SCRIPTED_SCENARIO_PROVENANCE,
        "hypotheses": hyps,
    }


# -- C. Evidence investigation -----------------------------------------------


def _section_investigation(run, passages_by_id) -> dict:
    bundle = run.candidate_evidence
    enquiries = []
    if bundle is not None:
        for ex in bundle.executed:
            enq = ex.enquiry
            res = ex.result
            passages = [
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
            ]
            enquiries.append({
                "enquiry_id": enq.enquiry_id,
                "hypothesis_id": enq.hypothesis_id,
                "requirement_kind": enq.requirement_kind.value,
                "search_intent": enq.search_intent.value,
                "search_query": enq.search_query,
                "outcome": res.outcome.value,
                "retrieved_passages": passages,
            })
    unresolved = list(getattr(bundle, "unresolved_enquiries", []) or [])
    return {
        "note": (
            "Search queries/intents are SCRIPTED; the retrieval and the returned "
            "passages are REAL. A no-match outcome is not proof a capability is "
            "absent."
        ),
        "enquiries": enquiries,
        "unresolved_enquiries": unresolved,
    }


# -- D. Hypothesis testing ---------------------------------------------------


def _section_testing(run) -> dict:
    results = []
    for g in run.governed_tests or []:
        corroborated, uncorroborated = [], []
        for pc in g.premise_corroboration:
            entry = {
                "premise_id": pc.premise_id,
                "premise": pc.premise,
                "material": pc.material,
                "is_outcome_premise": pc.is_outcome_premise,
                "model_status": pc.model_status.value,
                "corroborated": pc.corroborated,
                "corroboration_basis": list(pc.corroboration_basis),
            }
            (corroborated if pc.corroborated else uncorroborated).append(entry)
        supporting = [
            {"passage_id": a.passage_id, "rationale": a.rationale}
            for a in g.evidence_assessments
            if a.assessment.value == "supports_premise"
        ]
        contradicting = [
            {"passage_id": a.passage_id, "rationale": a.rationale}
            for a in g.evidence_assessments
            if a.assessment.value == "contradicts_premise"
        ]
        results.append({
            "hypothesis_id": g.hypothesis_id,
            "dimension": g.dimension.value,
            "proposed_status": g.proposed_status.value,
            "effective_status": g.effective_status.value,
            "governance_downgrades": list(g.downgrade_reasons),
            "independently_corroborated": g.independently_corroborated,
            "corroboration_basis": list(g.corroboration_basis),
            "corroborated_premises": corroborated,
            "uncorroborated_material_premises": uncorroborated,
            "supporting_evidence": supporting,
            "contradicting_evidence": contradicting,
            "unresolved_evidence_requirements": list(
                g.unresolved_evidence_requirements),
            "human_review_required": g.human_review_required,
            "proposed_status_provenance": SCRIPTED_SCENARIO_PROVENANCE,
        })
    return {
        "note": (
            "Proposed statuses are SCRIPTED; the EFFECTIVE status, premise "
            "corroboration and downgrades are computed by the real governance "
            "layer. A conclusion is never presented as proven on model say-so."
        ),
        "results": results,
    }


# -- E. Credit findings ------------------------------------------------------


def _section_findings(run) -> dict:
    findings = []
    if run.findings is not None:
        for f in run.findings.findings:
            sig = f.signal
            findings.append({
                "signal_id": sig.signal_id,
                "hypothesis_id": sig.hypothesis_id,
                "dimension": sig.dimension.value,
                "signal_statement": sig.signal_statement,
                "assessed_direction": sig.assessed_direction.value,
                "verification_status": sig.verification_status.value,
                "is_investigation_candidate": sig.is_investigation_candidate,
                "impact_channel": f.impact.impact_channel.value,
                "transmission_mechanism": f.impact.transmission_mechanism,
                "quantification": f.impact.quantification.value,
                "quantified_metric_refs": list(f.impact.quantified_metric_refs),
                "unproven_inferences": list(f.impact.unproven_inferences),
                "material_assumptions": list(f.impact.material_assumptions),
                "provisional_materiality": f.materiality.materiality.value,
                "materiality_rationale": f.materiality.rationale,
                "materiality_rubric_version": f.materiality.rubric_version,
                "uncorroborated_material_premises": list(
                    f.uncorroborated_material_premises),
                "required_human_review": f.required_human_review,
            })
    candidates = []
    if run.findings is not None:
        candidates = [
            {"signal_id": c.signal_id, "hypothesis_id": c.hypothesis_id,
             "signal_statement": c.signal_statement}
            for c in run.findings.investigation_candidates
        ]
    return {
        "note": (
            "Materiality is a PROVISIONAL analytical triage (versioned rubric), "
            "not a bank rating. Unquantified impacts remain unquantified; no "
            "numbers are invented."
        ),
        "findings": findings,
        "investigation_candidates": candidates,
    }


# -- F. Evidence completeness ------------------------------------------------


def _section_completeness(run) -> dict:
    c = run.completeness
    if c is None:
        return {"available": False}
    return {
        "available": True,
        "overall_investigation_status": c.overall_investigation_status.value,
        "overall_evidence_sufficiency": c.overall_evidence_sufficiency.value,
        "progression_recommendation": c.progression.value,
        "dimension_coverage": [
            {
                "dimension": d.dimension.value,
                "applicability": d.applicability.value,
                "coverage_status": d.coverage_status.value,
                "evaluated_hypotheses": list(d.evaluated_hypotheses),
                "reason": d.reason,
            }
            for d in c.dimension_coverage
        ],
        "material_gaps": [
            {
                "gap_id": g.gap_id,
                "category": g.category.value,
                "description": g.description,
                "materiality": g.materiality.value,
                "hypothesis_id": g.hypothesis_id,
                "finding_id": g.finding_id,
                "blocks_progression": g.blocks_progression,
                "requires_human_review": g.requires_human_review,
                "evidence_required": list(g.evidence_required),
            }
            for g in c.material_gaps
        ],
        "human_review_requirements": list(c.human_review_requirements),
        "progression_note": (
            "READY_FOR_RED_CHALLENGE is NOT lending approval. BLOCKED means "
            "material evidence is missing and the module must not progress."
        ),
    }


# -- G. RED challenge --------------------------------------------------------


def _section_red(run) -> dict:
    red = run.red_report
    stage = run.stage(Stage.RED_CHALLENGE)
    if red is None:
        return {
            "performed": False,
            "withheld_by_gate": stage is not None and stage.state.value == "blocked",
            "reason": (stage.detail if stage is not None else "RED stage did not run"),
            "note": (
                "A withheld RED pass is a correct governance outcome, not a "
                "failure: the completeness gate blocked progression."
            ),
        }
    challenges = [
        {
            "challenge_id": c.challenge_id,
            "target_kind": c.target_kind.value,
            "target_id": c.target_id,
            "category": c.category.value,
            "severity": c.severity.value,
            "challenged_statement": c.challenged_statement,
            "reason": c.reason,
            "evidence_refs": list(c.evidence_refs),
            "reference_resolved": c.reference_resolved,
            "evidence_backed": c.evidence_backed,
            "disposition": c.reconciliation.disposition.value,
            "escalation_id": c.escalation_id,
            "requires_human_review": c.requires_human_review,
            "provenance": SCRIPTED_SCENARIO_PROVENANCE,
        }
        for c in red.challenges
    ]
    return {
        "performed": True,
        "pass_kind": red.pass_kind,
        "progression_gate": red.progression_gate,
        "challenges": challenges,
        "unresolved_objections": [
            c["challenge_id"] for c in challenges
            if c["disposition"] != "resolved"
        ],
        "escalations": list(red.escalation_ids),
        "open_escalations": list(run.open_escalations),
        "note": (
            "RED objections are SCRIPTED; reference resolution, evidence backing "
            "and escalation are enforced by the real governance layer."
        ),
    }


# -- H. Overall summary ------------------------------------------------------


def _section_summary(scenario, run) -> dict:
    completed = [
        run.stage(s).stage.value for s in Stage
        if run.stage(s) is not None and run.stage(s).state.value == "completed"
    ]
    corroborated = []
    unverified = []
    for g in run.governed_tests or []:
        if g.independently_corroborated:
            corroborated.append(g.hypothesis_id)
        if g.uncorroborated_material_premises:
            unverified.append(g.hypothesis_id)
    further = []
    if run.completeness is not None:
        further = [
            g.description for g in run.completeness.material_gaps
            if g.blocks_progression or g.requires_human_review
        ]
    progression = (
        run.completeness.progression.value if run.completeness is not None else None
    )
    can_progress = progression in {"ready_for_red_challenge", "ready_with_escalation"}
    blockers = []
    if run.completeness is not None:
        blockers = [g.description for g in run.completeness.blocking_gaps()]
    return {
        "what_was_executed": completed,
        "disposition": run.disposition.value,
        "adequately_corroborated_hypotheses": corroborated,
        "unverified_hypotheses_with_material_gaps": unverified,
        "requires_further_investigation": further,
        "can_progress_to_red_challenge": can_progress,
        "progression_recommendation": progression,
        "prevents_lender_ready_conclusion": blockers or [
            "This is an OFFLINE, SCRIPTED demonstration; no live-model reasoning, "
            "no numeric corroboration from approved metrics, and no human "
            "adjudication were performed."
        ],
    }


# -- Evidence viewer ---------------------------------------------------------


def _evidence_viewer(scenario, run, passages_by_id) -> list[dict]:
    """Map each real retrieved passage to the hypothesis/finding that used it.

    Only passages a hypothesis-testing assessment actually cited as SUPPORTING a
    premise are shown as support; a passage with an inconclusive assessment is
    labelled as such rather than presented as support (Task 12 §5)."""
    rows: dict[str, dict] = {}

    def ensure(pid: str) -> dict | None:
        p = passages_by_id.get(pid)
        if p is None:
            return None
        if pid not in rows:
            rows[pid] = {
                "passage_id": pid,
                "document_name": _document_name(scenario, p.document_id),
                "document_id": p.document_id,
                "source_location": _location_label(p),
                "excerpt": _excerpt(p.text),
                "used_by": [],
            }
        return rows[pid]

    for g in run.governed_tests or []:
        for a in g.evidence_assessments:
            row = ensure(a.passage_id)
            if row is None:
                continue
            kind = a.assessment.value
            if kind == "supports_premise":
                relation = "supports a premise of"
            elif kind == "contradicts_premise":
                relation = "contradicts a premise of"
            else:
                relation = f"cited for ({kind}; grounding inconclusive) in"
            row["used_by"].append({
                "hypothesis_id": g.hypothesis_id,
                "relation": relation,
                "assessment": kind,
            })
    return list(rows.values())


# -- helpers -----------------------------------------------------------------


def _document_name(scenario, document_id: str) -> str:
    for d in scenario.admitted_sources:
        if d.get("document_id") == document_id:
            return d.get("filename") or document_id
    return document_id


def _location_label(passage) -> str:
    bits = []
    if passage.section:
        bits.append(str(passage.section))
    if passage.source_location:
        bits.append(str(passage.source_location))
    if passage.char_offset is not None:
        bits.append(f"char {passage.char_offset}")
    return " · ".join(bits) if bits else "(HTML filing; section location only)"


def _excerpt(text: str, limit: int = 600) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[: limit - 3].rstrip() + "..."


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def render_markdown(model: dict) -> str:
    L: list[str] = []
    L.append(f"> **{model['warning']}**")
    L.append("")
    L.append("# Business Overview Module 1 — Analytical Review Package")
    L.append("")
    L.append(f"- Fixture: `{model['fixture']}`")
    L.append(f"- Run id: `{model['run_id']}`")
    L.append(f"- Overall disposition: **{model['disposition']}**")
    L.append("")

    rvs = model["real_vs_scripted"]
    L.append("## Real versus scripted")
    L.append("")
    L.append("**Real in this run:**")
    for item in rvs["real"]:
        L.append(f"- {item}")
    L.append("")
    L.append("**Scripted in this run:**")
    for item in rvs["scripted"]:
        L.append(f"- {item}")
    L.append("")
    L.append(f"_{rvs['note']}_")
    L.append("")

    L.append("## Stage statuses")
    L.append("")
    L.append("| Stage | State | Detail |")
    L.append("| --- | --- | --- |")
    for s in model["stage_statuses"]:
        L.append(f"| {s['stage']} | {s['state']} | {_md(s['detail'])} |")
    L.append("")

    _md_section_a(L, model["A_source_information"])
    _md_section_b(L, model["B_hypothesis_generation"])
    _md_section_c(L, model["C_evidence_investigation"])
    _md_section_d(L, model["D_hypothesis_testing"])
    _md_section_e(L, model["E_credit_findings"])
    _md_section_f(L, model["F_evidence_completeness"])
    _md_section_g(L, model["G_red_challenge"])
    _md_section_h(L, model["H_overall_summary"])
    _md_evidence_viewer(L, model["evidence_viewer"])

    L.append("")
    L.append(f"> **{model['warning']}**")
    L.append("")
    return "\n".join(L)


def _md(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _md_section_a(L, a):
    L.append("## A. Source information")
    L.append("")
    L.append(f"- Company: **{a['company']}**")
    L.append(f"- Reporting entity: {a['reporting_entity']}")
    L.append(f"- Reporting period (as-of): {a['reporting_period_as_of']}")
    L.append(f"- Evidence cutoff: {a['evidence_cutoff']}")
    L.append(f"- Primary document id: `{a['primary_document_id']}`")
    L.append("")
    L.append("Admitted source documents:")
    L.append("")
    L.append("| Filename | Role | SHA-256 (id) |")
    L.append("| --- | --- | --- |")
    for d in a["admitted_sources"]:
        L.append(f"| {_md(d.get('filename'))} | {_md(d.get('role'))} | "
                 f"`{_md(d.get('sha256'))}` |")
    L.append("")
    if a["rejected_sources"]:
        L.append("Rejected sources (out of scope / after cutoff):")
        L.append("")
        L.append("| Filename | Form | Reason |")
        L.append("| --- | --- | --- |")
        for r in a["rejected_sources"]:
            L.append(f"| {_md(r.get('filename'))} | {_md(r.get('form'))} | "
                     f"{_md(r.get('reason'))} |")
        L.append("")
    L.append("Source-extraction limitations:")
    for lim in a["source_extraction_limitations"]:
        L.append(f"- {lim}")
    L.append("")


def _md_section_b(L, b):
    L.append("## B. Hypothesis generation")
    L.append("")
    L.append(f"_Provenance: {b['provenance_note']}_")
    L.append("")
    for h in b["hypotheses"]:
        L.append(f"### {h['hypothesis_id']} — {h['dimension']}")
        L.append(f"- Business characteristic: {h['business_characteristic']}")
        L.append(f"- Statement (SCRIPTED): {h['statement']}")
        L.append(f"- Credit direction: {h['credit_direction']}")
        L.append(f"- Initial verification status: **{h['initial_verification_status']}**")
        if h["anchored_real_passage_ids"]:
            ids = ", ".join(f"`{p}`" for p in h["anchored_real_passage_ids"])
            L.append(f"- Anchored to real passage(s): {ids}")
        L.append("")


def _md_section_c(L, c):
    L.append("## C. Evidence investigation")
    L.append("")
    L.append(f"_{c['note']}_")
    L.append("")
    for e in c["enquiries"]:
        L.append(f"### Enquiry {e['enquiry_id']} ({e['requirement_kind']})")
        L.append(f"- Hypothesis: `{e['hypothesis_id']}`")
        L.append(f"- Search intent: {e['search_intent']}")
        L.append(f"- Query (SCRIPTED): {e['search_query']}")
        L.append(f"- Outcome: **{e['outcome']}**")
        for p in e["retrieved_passages"]:
            loc = p["section"] or "(section)"
            L.append(f"  - [{p['rank']}] `{p['passage_id']}` — {loc}")
            L.append(f"    > {_md(p['excerpt'])}")
        L.append("")
    if c["unresolved_enquiries"]:
        L.append("Unresolved enquiries:")
        for u in c["unresolved_enquiries"]:
            L.append(f"- {_md(u)}")
        L.append("")


def _md_section_d(L, d):
    L.append("## D. Hypothesis testing")
    L.append("")
    L.append(f"_{d['note']}_")
    L.append("")
    for r in d["results"]:
        L.append(f"### {r['hypothesis_id']} — {r['dimension']}")
        L.append(f"- Proposed status (SCRIPTED): {r['proposed_status']}")
        L.append(f"- **Effective governed status: {r['effective_status']}**")
        L.append(f"- Independently corroborated: {r['independently_corroborated']} "
                 f"({', '.join(r['corroboration_basis']) or 'none'})")
        if r["governance_downgrades"]:
            L.append(f"- Governance downgrades: {_md('; '.join(r['governance_downgrades']))}")
        if r["corroborated_premises"]:
            L.append("- Corroborated premises:")
            for p in r["corroborated_premises"]:
                L.append(f"  - {_md(p['premise'])} "
                         f"(basis: {', '.join(p['corroboration_basis']) or 'n/a'})")
        if r["uncorroborated_material_premises"]:
            L.append("- Uncorroborated material premises:")
            for p in r["uncorroborated_material_premises"]:
                flag = " [outcome]" if p["is_outcome_premise"] else ""
                L.append(f"  - {_md(p['premise'])}{flag}")
        if r["supporting_evidence"]:
            L.append("- Supporting evidence:")
            for s in r["supporting_evidence"]:
                L.append(f"  - `{s['passage_id']}` — {_md(s['rationale'])}")
        if r["contradicting_evidence"]:
            L.append("- Contradicting evidence:")
            for s in r["contradicting_evidence"]:
                L.append(f"  - `{s['passage_id']}` — {_md(s['rationale'])}")
        if r["unresolved_evidence_requirements"]:
            L.append("- Unresolved evidence requirements:")
            for u in r["unresolved_evidence_requirements"]:
                L.append(f"  - {_md(u)}")
        L.append(f"- Human review required: {r['human_review_required']}")
        L.append("")


def _md_section_e(L, e):
    L.append("## E. Credit findings")
    L.append("")
    L.append(f"_{e['note']}_")
    L.append("")
    for f in e["findings"]:
        L.append(f"### {f['signal_id'][:12]} — {f['dimension']} "
                 f"({f['assessed_direction']})")
        L.append(f"- Signal: {_md(f['signal_statement'])}")
        L.append(f"- Verification status: **{f['verification_status']}**")
        L.append(f"- Impact channel: {f['impact_channel']}")
        L.append(f"- Transmission: {_md(f['transmission_mechanism'])}")
        L.append(f"- Quantification: {f['quantification']} "
                 f"(metric refs: {f['quantified_metric_refs'] or 'none'})")
        L.append(f"- Provisional materiality: **{f['provisional_materiality']}** "
                 f"(rubric `{f['materiality_rubric_version']}`)")
        if f["unproven_inferences"]:
            L.append(f"- Unproven inferences: {_md('; '.join(f['unproven_inferences']))}")
        if f["uncorroborated_material_premises"]:
            L.append("- Uncorroborated material premises: "
                     f"{_md('; '.join(f['uncorroborated_material_premises']))}")
        L.append(f"- Human review flag: {f['required_human_review']}")
        L.append("")
    if e["investigation_candidates"]:
        L.append("Investigation candidates (rejected hypotheses, not findings):")
        for c in e["investigation_candidates"]:
            L.append(f"- `{c['signal_id'][:12]}` {_md(c['signal_statement'])}")
        L.append("")


def _md_section_f(L, f):
    L.append("## F. Evidence completeness")
    L.append("")
    if not f.get("available"):
        L.append("_Completeness stage did not run._")
        L.append("")
        return
    L.append(f"- Overall investigation status: **{f['overall_investigation_status']}**")
    L.append(f"- Overall evidence sufficiency: **{f['overall_evidence_sufficiency']}**")
    L.append(f"- Progression recommendation: **{f['progression_recommendation']}**")
    L.append(f"- _{f['progression_note']}_")
    L.append("")
    L.append("Six-dimension coverage:")
    L.append("")
    L.append("| Dimension | Applicability | Coverage | Hypotheses |")
    L.append("| --- | --- | --- | --- |")
    for d in f["dimension_coverage"]:
        L.append(f"| {d['dimension']} | {d['applicability']} | "
                 f"{d['coverage_status']} | {', '.join(d['evaluated_hypotheses']) or '—'} |")
    L.append("")
    if f["material_gaps"]:
        L.append("Material gaps:")
        for g in f["material_gaps"]:
            flags = []
            if g["blocks_progression"]:
                flags.append("BLOCKS")
            if g["requires_human_review"]:
                flags.append("review")
            tag = f" _({', '.join(flags)})_" if flags else ""
            L.append(f"- [{g['category']}] {_md(g['description'])}{tag}")
        L.append("")
    if f["human_review_requirements"]:
        L.append("Human-review requirements:")
        for h in f["human_review_requirements"]:
            L.append(f"- {_md(h)}")
        L.append("")


def _md_section_g(L, g):
    L.append("## G. RED challenge")
    L.append("")
    if not g["performed"]:
        L.append(f"- RED pass performed: **no**")
        L.append(f"- Withheld by completeness gate: {g.get('withheld_by_gate')}")
        L.append(f"- Reason: {_md(g.get('reason'))}")
        L.append(f"- _{g['note']}_")
        L.append("")
        return
    L.append(f"- Pass kind: {g['pass_kind']} (gate: {g['progression_gate']})")
    L.append(f"- _{g['note']}_")
    L.append("")
    if g["challenges"]:
        for c in g["challenges"]:
            L.append(f"### Challenge {c['challenge_id']} — {c['severity']} "
                     f"({c['category']})")
            L.append(f"- Target: {c['target_kind']} `{c['target_id']}` "
                     f"(reference resolved: {c['reference_resolved']})")
            L.append(f"- Statement (SCRIPTED): {_md(c['challenged_statement'])}")
            L.append(f"- Reason: {_md(c['reason'])}")
            L.append(f"- Evidence backed (service-determined): {c['evidence_backed']}")
            L.append(f"- Disposition: **{c['disposition']}**")
            if c["escalation_id"]:
                L.append(f"- Escalation: `{c['escalation_id']}`")
            L.append("")
    else:
        L.append("- No challenges generated.")
        L.append("")
    if g["escalations"]:
        L.append(f"Escalations raised: {', '.join(g['escalations'])}")
        L.append("")


def _md_section_h(L, h):
    L.append("## H. Overall review summary")
    L.append("")
    L.append(f"- What was executed: {', '.join(h['what_was_executed']) or 'none'}")
    L.append(f"- Disposition: **{h['disposition']}**")
    L.append(f"- Adequately corroborated hypotheses: "
             f"{', '.join(h['adequately_corroborated_hypotheses']) or 'none'}")
    L.append(f"- Unverified (material gaps): "
             f"{', '.join(h['unverified_hypotheses_with_material_gaps']) or 'none'}")
    L.append(f"- Can progress to RED challenge: **{h['can_progress_to_red_challenge']}** "
             f"(recommendation: {h['progression_recommendation']})")
    L.append("")
    L.append("Requires further investigation:")
    for item in h["requires_further_investigation"] or ["(none recorded)"]:
        L.append(f"- {_md(item)}")
    L.append("")
    L.append("What prevents a lender-ready conclusion:")
    for item in h["prevents_lender_ready_conclusion"]:
        L.append(f"- {_md(item)}")
    L.append("")


def _md_evidence_viewer(L, viewer):
    L.append("## Evidence viewer")
    L.append("")
    if not viewer:
        L.append("_No cited passages._")
        L.append("")
        return
    for row in viewer:
        L.append(f"### `{row['passage_id']}` — {row['document_name']}")
        L.append(f"- Source location: {row['source_location']}")
        for u in row["used_by"]:
            L.append(f"- {u['relation']} `{u['hypothesis_id']}` "
                     f"(assessment: {u['assessment']})")
        L.append(f"  > {_md(row['excerpt'])}")
        L.append("")


# ---------------------------------------------------------------------------
# HTML rendering (standalone, no scripts)
# ---------------------------------------------------------------------------


def render_html(model: dict) -> str:
    md = render_markdown(model)
    # A deliberately simple, dependency-free standalone page: the full Markdown
    # is shown in a <pre> block for faithful offline viewing, with the mandatory
    # banner rendered prominently above it. No scripts, no external resources.
    banner = html.escape(model["warning"])
    body = html.escape(md)
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        "<title>Business Overview Module 1 — Review Package</title>\n"
        "<style>\n"
        "body{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;"
        "padding:0;background:#f5f5f5;color:#1a1a1a}\n"
        ".banner{background:#8a1f1f;color:#fff;padding:16px 24px;font-weight:700;"
        "font-size:15px;letter-spacing:.3px}\n"
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
