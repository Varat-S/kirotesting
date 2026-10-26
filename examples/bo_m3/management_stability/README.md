# Observable senior-management stability examples

These are **developer-authored offline regression inputs**, not Gemini responses or measured model-performance results.

- `hca_fy2025_fixture.json`: grounded in the actual HCA FY2025 filing and its admitted canonical evidence IDs. Covers all seven disclosed executives, partial-date precision, relevant internal/external experience, recent internal succession and explicit succession-risk qualifications.
- `fictional_negative_fixture.json`: clearly fictional ABC CEO/CFO external replacements with explicitly absent prior company service, abrupt departures and relevant industry experience as mitigation. Its qualified credit-negative finding remains admissible.
- `run_examples.py`: runs both through standalone M3 with the fake/scripted backend, preserves private response/validation recording, and writes inspectable artifacts. It does not create a live provider.

Run from the repository root with a fresh destination:

```bash
BO_LIVE_TEST=0 PYTHONPATH="$PWD" .venv/bin/python \
  examples/bo_m3/management_stability/run_examples.py \
  --output /absolute/path/to/an-unused-directory
```

HCA's preserved Phase 2 canonical database must be present. It is opened read-only and hash checked. A missing fixture never triggers a paid call. Existing output directories are refused.

The leadership-only responses do not perform independent structural-credit, governance or litigation analysis. Existing structural tests remain separate. No management-quality score is produced. See `MODULE_3_README.md` at the repository root for the current work-in-progress status and review map. Historical machine-specific reports remain local.
