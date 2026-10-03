"""End-to-end reproducibility runner (task 9.8, Req 23.13).

Given the SAME source snapshot and the SAME pinned versions (configuration,
metric-definition, rule, prompt/model), the deterministic parts of the pipeline
MUST reproduce EXACTLY:

* the source set
* deterministic facts
* deterministic metrics
* rule triggers
* canonical schema structure
* ``FinalCaseSnapshot`` linkage

Reproducibility is asserted via canonical SHA-256 hashing (``app.core.hashing``)
over each component, so two runs under identical pinned versions produce
identical digests.

For the LLM part the runner does NOT assume byte-identical regeneration: it
STORES the original LLM outputs and compares against the stored run (the
deterministic FakeLLMBackend used in tests makes this reproducible, but the
contract is "store, do not assume regeneration").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.core.hashing import content_hash


@dataclass
class PinnedVersions:
    """The versions that must be held constant for a reproducible run."""

    config_versions: dict[str, int] = field(default_factory=dict)
    metric_def_versions: dict[str, int] = field(default_factory=dict)
    rule_versions: dict[str, int] = field(default_factory=dict)
    prompt_model_versions: dict[str, str] = field(default_factory=dict)

    def fingerprint(self) -> str:
        return content_hash(
            {
                "config": self.config_versions,
                "metric_defs": self.metric_def_versions,
                "rules": self.rule_versions,
                "prompt_model": self.prompt_model_versions,
            }
        )


@dataclass
class RunArtifacts:
    """The reproducible artifacts a single pipeline run emits.

    ``stored_llm_outputs`` holds the ORIGINAL LLM run output (stored, not
    assumed to regenerate byte-for-byte).
    """

    source_set: list[str]
    facts: list[Mapping[str, Any]]
    metrics: list[Mapping[str, Any]]
    rule_triggers: list[str]
    schema_structure: Mapping[str, Any]
    final_snapshot_linkage: Mapping[str, Any]
    stored_llm_outputs: list[Mapping[str, Any]] = field(default_factory=list)

    def component_hashes(self) -> dict[str, str]:
        """Canonical hashes for each DETERMINISTIC component (Req 23.13)."""
        return {
            "source_set": content_hash(sorted(self.source_set)),
            "facts": content_hash(self.facts),
            "metrics": content_hash(self.metrics),
            "rule_triggers": content_hash(sorted(self.rule_triggers)),
            "schema_structure": content_hash(self.schema_structure),
            "final_snapshot_linkage": content_hash(self.final_snapshot_linkage),
        }


@dataclass
class ReproducibilityResult:
    pinned_fingerprint: str
    first_hashes: dict[str, str]
    second_hashes: dict[str, str]
    stored_llm_outputs: list[Mapping[str, Any]]
    llm_outputs_match: bool

    @property
    def reproducible(self) -> bool:
        """True when every deterministic component hash matches across runs."""
        return self.first_hashes == self.second_hashes

    def mismatches(self) -> list[str]:
        return [
            k
            for k in self.first_hashes
            if self.first_hashes[k] != self.second_hashes.get(k)
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "pinned_fingerprint": self.pinned_fingerprint,
            "reproducible": self.reproducible,
            "mismatches": self.mismatches(),
            "component_hashes": dict(self.first_hashes),
            "llm_outputs_match": self.llm_outputs_match,
            "stored_llm_output_count": len(self.stored_llm_outputs),
        }


def assert_reproducible(
    first: RunArtifacts,
    second: RunArtifacts,
    *,
    pinned: PinnedVersions,
) -> ReproducibilityResult:
    """Compare two runs produced under the SAME pinned versions (Req 23.13).

    The deterministic components must hash identically. The LLM outputs are
    compared against the STORED original run (``first.stored_llm_outputs``)
    rather than assuming byte-exact regeneration.
    """
    first_hashes = first.component_hashes()
    second_hashes = second.component_hashes()
    llm_match = first.stored_llm_outputs == second.stored_llm_outputs
    return ReproducibilityResult(
        pinned_fingerprint=pinned.fingerprint(),
        first_hashes=first_hashes,
        second_hashes=second_hashes,
        stored_llm_outputs=list(first.stored_llm_outputs),
        llm_outputs_match=llm_match,
    )
