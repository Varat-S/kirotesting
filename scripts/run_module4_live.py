"""Run the Module 4 specialist end-to-end against a real SEC 10-K bundle.

Module 4 (Debt Structure, Refinancing History & Financial Stewardship) has no
CLI command of its own. This script wires it to the REAL deterministic pipeline
so the test is honest:

    package.json  --SourcePackage.load-->  (loads filing/bundle.json)
                  --CreditMemoPipeline.prepare_case-->  admitted evidence
                  --bounded debt-evidence selection-->  Module4Inputs
                  --Module4Specialist.run (live Vertex/Gemini)-->  4 deliverables
                  --Validator8-->  deterministic re-check + escalation on failure

Evidence-first, no fabrication: the inputs handed to the model are only facts and
verbatim passages that the pipeline ADMITTED from the filing (after the evidence
cutoff). Nothing is invented. Debt-relevant passages are selected with the
project's own deterministic ranker (app.services.bo.retrieval._search) and
bounded so the live input stays well under LLM_MAX_INPUT_BYTES.

By default this runs a DRY RUN: it assembles and reports the evidence WITHOUT
calling the model (no cost). Pass --live to make the paid Gemini call, which
requires a git-ignored .env with LLM_PROVIDER=vertex, VERTEX_API_KEY and a
VERTEX_MODEL_* (see app/services/bo/README.md).

Usage (from the repo root):
    .venv\\Scripts\\python -m scripts.run_module4_live            # dry run
    .venv\\Scripts\\python -m scripts.run_module4_live --live     # paid call
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from app.core.config import get_settings
from app.models.base import create_engine_and_session, init_db
from app.prompts.registry import PromptRegistry
from app.services.audit.log import AuditLog
from app.services.bo.retrieval import _search
from app.services.escalation.engine import EscalationEngine
from app.services.llm.client import LLMClient
from app.services.modules.module4.specialist import (
    Module4Inputs,
    Module4Specialist,
)
from app.services.pipeline.package import SourcePackage
from app.services.pipeline.runner import CreditMemoPipeline

# Debt / refinancing / stewardship vocabulary for selecting passages. These are
# ranking terms, not answers: the ranker only surfaces admitted passages that
# already contain this language; it never asserts a passage supports a claim.
DEBT_TERMS = [
    "long-term debt",
    "senior notes",
    "senior unsecured",
    "revolving credit",
    "credit facility",
    "commercial paper",
    "indebtedness",
    "maturit",          # maturity / maturities
    "refinanc",         # refinance / refinancing
    "redemption",
    "guarantee",
    "guarantor",
    "interest expense",
    "liquidity",
    "capital resources",
    "debentures",
]

# Concept fragments that mark a fact as debt / financing related.
DEBT_FACT_FRAGMENTS = (
    "debt",
    "note",
    "borrow",
    "maturit",
    "interest",
    "financ",
    "repurchase",
    "dividend",
    "capital expenditure",
    "acquisition",
)

MAX_DEBT_PASSAGES = 16


def _is_debt_fact(fact: dict) -> bool:
    name = (fact.get("name") or "").lower()
    concept = (fact.get("taxonomy_concept") or "").lower()
    haystack = name + " " + concept
    return any(fragment in haystack for fragment in DEBT_FACT_FRAGMENTS)


def build_inputs(prepared) -> tuple[Module4Inputs, dict]:
    """Assemble Module4Inputs from admitted evidence only, with a provenance report."""
    snap = prepared.evidence_snapshot

    # Narrative passages, debt-filtered and bounded by the deterministic ranker.
    ranked = _search(snap.narrative_evidence, DEBT_TERMS, MAX_DEBT_PASSAGES)
    # Expose the passage's native id under `id` so evidence_ids() counts it.
    snippets = [
        {**passage, "id": passage.get("evidence_id")}
        for _score, passage, _matched in ranked
    ]

    # Structured financial facts the pipeline mapped to canonical concepts.
    mapped_facts = [f for f in snap.facts if f.get("mapping_status") == "mapped"]
    debt_facts = [f for f in mapped_facts if _is_debt_fact(f)]

    # Surface missing-critical signals (never silently fill gaps).
    missing = [
        e.get("reason", "")
        for e in prepared.escalations
        if "missing" in e.get("reason", "").lower()
    ]

    inputs = Module4Inputs(
        sec_filing_snippets=snippets,
        structured_financials=mapped_facts,
        debt_schedule_facts=debt_facts,
        missing_critical_evidence=missing,
    )

    provenance = {
        "admitted_facts_total": len(snap.facts),
        "mapped_facts": len(mapped_facts),
        "debt_facts_selected": len(debt_facts),
        "narrative_passages_total": len(snap.narrative_evidence),
        "debt_passages_selected": len(snippets),
        "debt_passage_sections": sorted(
            {s.get("section") for s in snippets if s.get("section")}
        ),
        "missing_critical_signals": missing,
        "input_payload_bytes": len(
            json.dumps(inputs.to_payload(), default=str).encode("utf-8")
        ),
        "evidence_ids_reported": len(inputs.evidence_ids()),
    }
    return inputs, provenance


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package",
        default="examples/healthcare/hca-2025/package.json",
        help="Path to the source package manifest.",
    )
    parser.add_argument("--case-id", default="HCA_FY2025_M4_LIVE")
    parser.add_argument(
        "--database", default="data/module4_live.db", help="SQLite DB path."
    )
    parser.add_argument(
        "--output",
        default="output/module4_live",
        help="Directory for the deliverables + provenance.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Make the paid Gemini call. Omit for a free dry run (evidence only).",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    stage_log = Path("output/m4_stage.log")
    stage_log.write_text("", encoding="utf-8")

    def stage(msg: str) -> None:
        with open(stage_log, "a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
        print(msg)

    stage(f"START live={args.live} db={args.database} case={args.case_id}")

    # --- deterministic pipeline: package.json -> admitted evidence -----------
    db_path = Path(args.database)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine, factory = create_engine_and_session(f"sqlite:///{db_path.as_posix()}")
    init_db(engine)
    session = factory()

    stage("loading package")
    package = SourcePackage.load(Path(args.package))
    pipeline = CreditMemoPipeline(
        session, data_root=db_path.parent, output_root=out_dir
    )
    stage("prepare_case: ingesting 10-K (this takes a bit)")
    prepared = pipeline.prepare_case(args.case_id, package=package)
    session.commit()
    stage("prepare_case: done")

    inputs, provenance = build_inputs(prepared)
    stage("inputs assembled")

    # Always write what evidence WOULD be sent, so the input is auditable.
    (out_dir / "module4_inputs.json").write_text(
        json.dumps(inputs.to_payload(), indent=2, default=str), encoding="utf-8"
    )
    (out_dir / "evidence_provenance.json").write_text(
        json.dumps(provenance, indent=2, default=str), encoding="utf-8"
    )

    print("== Module 4 evidence assembly (from admitted HCA 10-K evidence) ==")
    for key, value in provenance.items():
        print(f"  {key}: {value}")

    if not args.live:
        print(
            "\nDRY RUN complete. No model was called and no cost was incurred.\n"
            f"Assembled evidence written to {out_dir}/.\n"
            "Re-run with --live to make the paid Gemini call."
        )
        return 0

    # --- live Gemini call ----------------------------------------------------
    # The google-genai SDK reads GOOGLE_APPLICATION_CREDENTIALS from the OS
    # process env for the service-account (ADC) path. pydantic-settings loads
    # .env into Settings but does not export it, so surface it here from .env if
    # it is not already in the environment.
    import os

    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        for line in Path(".env").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("GOOGLE_APPLICATION_CREDENTIALS="):
                os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = line.split("=", 1)[1]
                break

    settings = get_settings()
    if settings.llm_provider != "vertex":
        print(
            "ERROR: LLM_PROVIDER is not 'vertex'. Set it (and VERTEX_API_KEY + a "
            "VERTEX_MODEL_*) in .env before using --live.",
            file=sys.stderr,
        )
        return 2

    # Reuse the project's backend factory so provider selection + configuration
    # checks match the BO --live path exactly.
    from app.cli import _live_backend

    stage("building live backend")
    backend = _live_backend()  # VertexProviderBackend; .check_configuration() runs

    prompts = PromptRegistry(session)
    prompts.register_catalogue()  # includes module4_debt_structure_specialist
    audit = AuditLog(session)
    client = LLMClient(backend, prompts=prompts, session=session, audit=audit)
    escalation = EscalationEngine(session, audit=audit, case_id=args.case_id)

    specialist = Module4Specialist(
        client, escalation_engine=escalation, case_id=args.case_id
    )

    stage(f"calling model {backend.model_id}")

    # Make the model call directly first so the raw response + validation detail
    # are ALWAYS persisted and saved, even when the output is rejected. A
    # rejected model output must remain inspectable (auditability), not vanish.
    from app.prompts.registry import PromptRegistry as _PR  # noqa: F401
    from app.services.modules.module4.prompt import MODULE4_AGENT_NAME

    run_result = client.analyze(
        inputs.to_payload(),
        evidence_ids=inputs.evidence_ids(),
        case_id=args.case_id,
        prompt_name=MODULE4_AGENT_NAME,
    )
    session.commit()  # persist the ModelRun row regardless of outcome
    stage(f"model returned: outcome={run_result.validation_outcome} "
          f"raw_len={len(run_result.raw_response or '')}")

    (out_dir / "raw_model_response.json").write_text(
        run_result.raw_response or "", encoding="utf-8"
    )

    if not run_result.is_valid or run_result.parsed is None:
        # Read the persisted validation detail so the schema mismatch is visible.
        from app.models.orm import ModelRun
        from sqlalchemy import select as _select

        row = session.get(ModelRun, run_result.run_id)
        detail = row.validation_detail if row is not None else "(detail unavailable)"
        print("\n== Module 4 LIVE result: OUTPUT REJECTED ==")
        print(f"  model: {run_result.model_id}")
        print(f"  run_id: {run_result.run_id}")
        print(f"  validation_outcome: {run_result.validation_outcome}")
        print(f"  validation_detail: {detail}")
        print(
            f"  raw response ({len(run_result.raw_response or '')} chars) saved to "
            f"{out_dir}/raw_model_response.json"
        )
        print(
            "\nThe specialist correctly refused to use output that does not match "
            "MODULE4_JSON_SCHEMA. This is the schema guard working, not a crash."
        )
        return 3

    # Output passed schema validation; run the full specialist (Validator 8 etc.).
    result = specialist.run(inputs)
    session.commit()

    deliverables = result.output.model_dump(mode="json")
    (out_dir / "module4_deliverables.json").write_text(
        json.dumps(deliverables, indent=2, default=str), encoding="utf-8"
    )

    v8 = {
        "passed": result.validator8.passed,
        "checks": [asdict(c) for c in result.validator8.checks],
    }
    (out_dir / "validator8_report.json").write_text(
        json.dumps(v8, indent=2, default=str), encoding="utf-8"
    )

    print("\n== Module 4 LIVE result ==")
    print(f"  model: {result.run.model_id}")
    print(f"  run_id: {result.run.run_id}")
    print(f"  schema-valid output: {result.run.is_valid}")
    print(f"  Validator 8 passed: {result.validator8.passed}")
    for c in result.validator8.checks:
        mark = "PASS" if c.passed else "FAIL"
        print(f"    [{mark}] {c.check_id} {c.name}: {c.detail}")
    if result.escalation_id:
        print(f"  MANDATORY escalation raised: {result.escalation_id}")

    box = result.output.deliverable_4.executive_credit_summary_box
    print("\n  Executive credit summary:")
    print(f"    entity/period: {box.target_entity_and_period}")
    print(f"    verdict: {box.overall_credit_rating_verdict}")
    print(f"    recommended placement: {box.recommended_loan_placement}")

    print(f"\nDeliverables + Validator 8 report written to {out_dir}/.")
    print(
        "This is an UNAPPROVED draft for analyst review. A Validator-8 failure is "
        "a real signal, not prose to ignore."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
