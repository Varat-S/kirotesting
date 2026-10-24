"""Artifact registry — a read-only index over governed pipeline outputs.

Public surface:

* :func:`build_manifest` — scan an ``output_root`` for a case and build an
  :class:`~app.schemas.artifact_manifest.ArtifactManifest` indexing the existing
  BO / M3 / FO JSON artifacts (never rewriting them).
* :func:`build_pipeline_graph` — derive the left-to-right dataflow graph (nodes
  + directed edges) for the Pipeline Explorer, from the agent registry's
  *declared* dependencies and the manifest's recorded artifacts.
"""

from __future__ import annotations

from app.services.registry.indexer import build_manifest, index_case_from_dir
from app.services.registry.graph import build_pipeline_graph

__all__ = [
    "build_manifest",
    "index_case_from_dir",
    "build_pipeline_graph",
]
