# Module 3 Layer 2 — scripted/offline structural-credit fixtures

These files are **scripted, offline** Module 3 specialist responses and a
fictional corporate-structure onboarding document. They are consumed by
`tests/integration/test_bo_m3_structural_credit.py` with the offline
`FakeLLMBackend` (no model is ever called).

Every response file carries a top-level `"_scripted_offline": true` and a
`"_label"` describing the developer-authored expected outcome. Evidence
references use the `{"$evidence": "phrase"}` anchor form so they resolve against
each test's evidence snapshot (see `app.services.bo.comparison.resolve_anchors`).

Contents:

- `hca_simulated_single_pass.json` — the **simulated** HCA structure: grounded
  deterministic observations plus clearly **qualified** structural credit
  findings that reference the real HCA node/link ids (borrower position, a
  guarantor finding marked simulated / not legally enforceable, and a
  cash-flow-access finding reported as insufficient evidence), plus a
  senior-management observation.
- `fictional_synthetic_structure.json` — a **fictional** simplified v1.0
  corporate-structure onboarding document (NOT HCA) with its own stable ids and
  `data_status: simulated_educational_example`. Used by the positive / negative
  / mixed / unsupported-guarantee cases.
- `fictional_positive_single_pass.json` — a QUALIFIED positive guarantor-support
  finding supported by synthetic admitted legal evidence.
- `fictional_negative_single_pass.json` — a defensible negative finding on
  subsidiary-level creditor priority / cash-transfer restriction, supported by
  synthetic admitted evidence.
- `fictional_mixed_single_pass.json` — a mixed case (one positive and one
  negative finding coexist; none force-balanced).
- `fictional_unsupported_guarantee_single_pass.json` — an enforceable-guarantee
  claim asserted solely from a guarantor flag (no admitted legal evidence), so
  Validator 7 rejects or qualifies it.

IMPORTANT: these are developer-authored scripted inputs with controlled expected
outcomes. A passing scripted set is NOT proof of real-world analytical
reliability. Live analytical capability is UNVERIFIED — no live model is run.
Deterministic validation does not guarantee economic or legal correctness, and
the HCA structure is simulated.

## Regenerating the committed demo outputs (OFFLINE, no model call)

The six inspectable files per case under `demo_output/<case>/` are produced by
the scripted generator. Run it from the worktree root (it needs the repo on the
import path):

```
DEBUG=false PYTHONPATH=. .venv/bin/python examples/bo_m3/structural_credit/generate_demo.py
```

Output is byte-stable (`app.core.hashing.canonical_json`): re-running over the
same inputs leaves `git diff` empty. The five scenarios now produce **materially
different** consolidated conclusions (positive → guarantor recourse; negative →
cash-distribution restriction; mixed → both preserved, not netted; HCA → what
can/cannot be established; unsupported-guarantee → excluded), and each
conclusion statement carries the validated finding id it rests on
(`consolidated_conclusion.conclusion_trace`).

## Offline standalone CLI reproduce command

Each `run_manifest.json` embeds the exact offline reproduce command. The scripted
(offline, no model) invocation of the standalone Module 3 path is:

```
DEBUG=false python -m app.cli --output <OUTPUT_DIR> run-m3 <CASE_ID> \
  --corporate-structure <path/to/simplified_structure.json> \
  --responses <path/to/single_pass.json>
```

`--responses` is required for the offline path; there are no undocumented
fixtures. (The `run-m3` CLI reads a source `--package`/`--bundle` to build the
evidence snapshot; the `generate_demo.py` script above uses the in-repo
fictional evidence snapshot directly, which is the reproducible offline fixture.)

## Opt-in real-model invocation (DOCUMENTED, NOT RUN here)

A real-model pathway for the SAME standalone Module 3 specialist is implemented
but **disabled by default** and **not executed** in this repository (no paid
call is made). To run it yourself you must configure a provider through the
existing abstraction and pass `--live` INSTEAD of `--responses`:

```
export LLM_PROVIDER=openai          # or openai_compatible
export LLM_MODEL=<your-model-id>
export LLM_API_KEY=<your-key>        # read via SecretStr; never logged/committed
# optional bounds (defaults shown): LLM_TIMEOUT_SECONDS=120,
#   LLM_MAX_OUTPUT_TOKENS=12000, LLM_MAX_INPUT_BYTES=1500000

DEBUG=false python -m app.cli --output <OUTPUT_DIR> run-m3 <CASE_ID> \
  --corporate-structure <path/to/simplified_structure.json> \
  --live
```

Safeguards: only `m3_corporate_management` is invoked (no Modules 1/2/4,
synthesizer, challenger, scoring or renderer); exactly one specialist execution
(retries only if explicitly bounded); timeout + token limits come from
`Settings`; the model response is schema-validated before use; an invalid
response or provider error fails safely with NO fabricated output and NO silent
fallback to the scripted path; and `run_manifest.json`'s
`execution.execution_mode` / `execution.real_model_invoked` state whether a real
model actually answered. If no provider/credentials are configured, `--live`
fails with a clear, credential-free blocker rather than inventing a run.

Real-LLM analytical consistency is therefore reported as **NOT YET EVALUATED**;
offline byte-stability is NOT a substitute for real-model consistency.
