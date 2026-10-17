# Multi-borrower semantic evaluation

This folder is frozen developer-authored held-out material awaiting external
review. It is not independently validated ground truth. The primary documents
are already committed in the repository; `manifest.json` records their hashes.

Run the deterministic contract and scripted semantic evaluation with:

```bash
DEBUG=false /Users/pearn/NTU/BU5203/kirotesting/.venv/bin/python \
  -m app.services.bo.m3_semantic_eval \
  examples/bo_m3/independent_evaluation/cases.json
```

The scripted judge represents the frozen expected label. This validates service
governance and metric calculation only. It does not measure an LLM's semantic
accuracy. Record any external review in a separate adjudication artifact; do not
rewrite the frozen expectations silently.
