# Offline Vertex M3 schema diagnostics

These public assets contain schemas, hashes and aggregate request sizes only. Private filing text, full requests and service messages remain in ignored local SQLite recordings.

- `phase2_accepted_provider_schema.json`: saved successful Phase 2 schema, with original property order.
- `vertex_m3_1_3_provider_schema.json`: historical v1.3 reconstruction, not a failed HTTP wire capture.
- `manifest.json`: provenance and hashes.
- `compare_offline.py`: actual adapter variants A–E and installed SDK serialization through mandatory in-memory mock transport. No ADC refresh, live option, retry loop or remote provider request.
- Run `compare_offline.py` to generate comparison results, exact structural/property-order differences and mock observations locally.
- `minimal_probe_plan.json`: one proposed schema-only request, awaiting scope/count approval. This file does not execute anything.

Run from the repository root:

```bash
PYTHONPATH="$PWD" .venv/bin/python examples/bo_m3/vertex400_diagnostics/compare_offline.py
```

This prints schema-only diagnostic JSON. Offline SDK success does not prove remote Vertex compatibility. See `MODULE_3_README.md` at the repository root for current live-compatibility evidence and analytical limitations. Historical machine-specific reports remain local.
