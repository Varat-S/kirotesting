# Agentic Credit Analysis — Implementation Report

This report summarizes the agentic credit-analysis architecture added on top of
the baseline credit-memo pipeline. The full specification is in
[`.kiro/specs/credit-memo-agentic-analysis/`](.kiro/specs/credit-memo-agentic-analysis/)
(`requirements.md`, `design.md`, `tasks.md`).

## What it is

A bounded, concurrent multi-agent credit-analysis layer that runs **after** the
human-reviewed `CanonicalEvidenceSnapshot` boundary and replaces only the
downstream analytical portion of the pipeline. The guiding division is
unchanged:

> Code is the calculator, rule engine, scenario engine, scoring engine and
> policy gate. LLMs are interpreters, classifiers, challengers and synthesizers.

## Architecture at a glance

```
Validated CanonicalEvidenceSnapshot
  → AgenticAnalysisRun (one per execution; owns every artifact)
  → Evidence Router (narrow, hashed per-agent packets)
  → deterministic Parameter Engine + concurrent narrow LLM agents
  → deterministic validation (gate) → ParameterResult registry
  → deterministic Scoring (Business / Financial / Obligor / StructureProtection / Facility)
  → topic orchestrators → topic challenge → bounded targeted rerun
  → Structuring feasibility engine → cross-topic orchestration/challenge
  → human review → immutable FinalCaseSnapshot → memo
```

### Non-negotiable guarantees enforced in code

- **No LLM arithmetic / scoring.** Deterministic parameters and all five scores
  are produced by versioned engines; the registry rejects any LLM agent that
  tries to own a deterministic parameter, and validation rejects an `llm`/
  `hybrid` result whose id is deterministic-only.
- **Validation gates promotion.** A `ParameterResult` can only be produced from a
  `ValidatedAgentOutput`, which exists only when deterministic validation passed
  in full — invalid output cannot promote and no partial subset is retained.
- **Evidence provenance.** Substantive semantic parameters must cite admitted
  evidence; material conclusion claims must carry parameter/evidence provenance;
  numeric quotes bind an exact accepted `parameter_result_id`.
- **DSCR = CFADS / debt service** (never EBITDA); candidate economics are derived
  from the `CandidateStructure` terms; missing structuring inputs stay missing,
  never coerced to zero.
- **Append-only lineage.** A targeted rerun supersedes affected artifacts and
  appends new accepted versions; descendants are invalidated by dependency
  traversal and recomputed; unaffected branches are untouched; one automatic
  rerun round, then human escalation.
- **Run isolation + reproducibility.** Every artifact carries its owning
  `analysis_run_id`; two runs over the same snapshot never co-mingle; the final
  snapshot freezes exactly one accepted run and its artifact ids/hashes.

## Deterministic vs agentic

| Deterministic (never calls an LLM) | Agentic (may call an LLM) |
|---|---|
| Evidence Router, Parameter Engine, validation, Scoring Engine + rubric, structuring feasibility/stress, cache, rerun controller | 15 narrow/early-extraction agents, 4 risk-to-mitigant agents, 4 orchestrators, 4 challengers (27 logical jobs) |

## Model provider / Vertex

The single `LLMClient` abstraction is provider-neutral. Backends: `FakeLLMBackend`
(all tests, offline), `RealProviderBackend` (OpenAI / compatible), and
`VertexProviderBackend` (Google Vertex AI Gemini). `google-genai` is an optional
dependency (`pip install -e ".[vertex]"`), imported lazily; **no live provider
calls occur in CI**. Model-tier routing maps narrow agents to a cheap/fast model
and orchestrators/challengers to a stronger model; token usage is captured per
agent run and aggregated per analysis run.

### Running the agentic path offline (no credentials)

```python
CreditMemoPipeline(session, analysis_mode="agentic").run_case(case_id, package=pkg)
```

### Running live against Vertex (opt-in; local only)

1. `pip install -e ".[vertex]"`
2. Authenticate with Application Default Credentials:
   `gcloud auth application-default login` (or set
   `GOOGLE_APPLICATION_CREDENTIALS` to a service-account key file).
3. In your local `.env` (never committed):
   ```
   LLM_PROVIDER=vertex
   GOOGLE_CLOUD_PROJECT=your-project
   GOOGLE_CLOUD_LOCATION=us-central1
   VERTEX_MODEL_NARROW=...
   VERTEX_MODEL_ORCHESTRATOR=...
   VERTEX_MODEL_CHALLENGE=...
   ```
4. Construct the pipeline with `analysis_mode="agentic"` and a
   `VertexProviderBackend`.

Credentials are read from the environment / ADC only, never hardcoded or logged.

## Migration / backward compatibility

`analysis_mode` defaults to `legacy`; the baseline single-pass analysis path is
unchanged and all baseline tests continue to pass. The agentic path is additive
(new tables via the additive `app/migrate.py`), begins after the same evidence
boundary, and keeps human sign-off mandatory for finalization. Legacy removal is
intentionally out of scope.

## Testing

The whole graph is driven offline by `FakeLLMBackend`; Vertex has mocked
request/response tests only. CI runs the full suite on Python 3.11, 3.12 and
3.13 with no network. OCR-dependent and PDF-export tests skip gracefully where
those system dependencies are absent.
