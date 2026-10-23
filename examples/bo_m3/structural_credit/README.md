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
