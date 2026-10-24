"""Provenance and label contract for the senior-management benchmark.

Mirrors :mod:`tests.unit.test_bo_m3_independent_evaluation`: it proves the
benchmark's source hashes resolve against the tracked HCA 10-K files, that every
non-SCRIPTED passage is a verbatim excerpt of its cited filing (after the same
HTML->text normalisation the manifest documents), that each case carries BOTH an
independent credibility label and a SEPARATE credit_direction label, and that
the required credible-adverse and unsupported-causal-attribution cases exist.

The benchmark labels are developer-authored and await external review; a passing
scripted/mocked run is not proof of real-world model performance.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path


ROOT = Path(__file__).parents[2]
BENCHMARK = ROOT / "examples/bo_m3/management_benchmark"


def _normalise_html_to_text(raw: str) -> str:
    """Strip tags, unescape entities, collapse whitespace (manifest contract)."""
    without_tags = re.sub(r"<[^>]+>", " ", raw)
    unescaped = html.unescape(without_tags)
    return re.sub(r"\s+", " ", unescaped).strip()


def _normalise_passage(passage: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(passage)).strip()


def test_manifest_source_hashes_resolve_and_review_status_is_explicit():
    manifest = json.loads((BENCHMARK / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["label_status"] == "developer_authored_awaiting_external_review"
    assert manifest["review"]["status"] == "awaiting_external_review"
    assert manifest["sources"]
    for source in manifest["sources"]:
        content = (ROOT / source["path"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == source["sha256"]


def test_every_case_has_separate_credibility_and_credit_direction_labels():
    cases = json.loads((BENCHMARK / "cases.json").read_text(encoding="utf-8"))["cases"]
    assert 5 <= len(cases) <= 10
    for case in cases:
        assert case["review_status"] == "awaiting_external_review"
        credibility = case["credibility"]
        direction = case["credit_direction"]
        # Two INDEPENDENT labels: credibility (entailment) vs credit sign.
        assert credibility["expected"] in {
            "supported", "contradicted", "insufficient", "uncertain"}
        assert direction["label"] in {"positive", "negative", "neutral", "mixed"}
        assert credibility.get("reason") and direction.get("reason")


def test_non_scripted_passages_are_verbatim_substrings_of_cited_filing():
    cases = json.loads((BENCHMARK / "cases.json").read_text(encoding="utf-8"))["cases"]
    cache: dict[str, str] = {}
    checked = 0
    for case in cases:
        if str(case.get("grounding", "")).upper() in {"SCRIPTED", "MOCK"}:
            continue
        source_file = case["source_file"]
        path = next(
            ROOT / s["path"] for s in json.loads(
                (BENCHMARK / "manifest.json").read_text())["sources"]
            if Path(s["path"]).name == source_file)
        if source_file not in cache:
            cache[source_file] = _normalise_html_to_text(
                path.read_text(encoding="utf-8", errors="ignore"))
        assert _normalise_passage(case["passage"]) in cache[source_file], (
            f"{case['id']} passage is not verbatim in {source_file}")
        checked += 1
    assert checked >= 1  # at least one real admitted excerpt was verified


def test_required_special_cases_are_present():
    cases = json.loads((BENCHMARK / "cases.json").read_text(encoding="utf-8"))["cases"]
    credible_adverse = [
        c for c in cases
        if c.get("credible_adverse")
        and c["credibility"]["expected"] in {"supported", "qualified"}
        and c["credit_direction"]["label"] in {"negative", "mixed"}]
    unsupported_causal = [
        c for c in cases
        if c.get("unsupported_causal_attribution")
        and c["credibility"]["expected"] in {"insufficient", "uncertain", "contradicted"}]
    assert credible_adverse, "a credible-adverse case is required"
    assert unsupported_causal, "an unsupported-causal-attribution case is required"
