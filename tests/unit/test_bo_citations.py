"""Evidence aliases and per-citation repair (app.services.bo.citations).

The two live rejections these guard against: M1S2 retyped a 64-character
evidence id with one wrong character, and M4 cited computed-indicator ids
(``pr_...``) as a parameter's evidence. Each used to reject the whole agent.
"""

from __future__ import annotations

import json

from app.services.bo.citations import (
    alias_inputs,
    evidence_aliases,
    repair_citations,
    unalias_answer,
)
from app.services.bo.comparison import run_single_pass
from app.services.llm.client import LLMBackend, LLMRawResult, LLMRequest
from tests.integration.test_bo_m3_live_readiness import _live_valid_response
from tests.unit.test_bo_comparison_failure_detail import m3_prep  # noqa: F401  (fixture)

REAL = "28bfd61854487ea96e0b110a7458dd90ced656cd047d6f875675ae53f0979738"
OTHER = "019827f37cce4816b8cae0e6a19f1b0a040a5d310ccef7ab5419e127cee981b9"


def test_aliases_round_trip_only_under_evidence_keys():
    aliases = evidence_aliases([REAL, OTHER, REAL])
    assert aliases == {REAL: "E01", OTHER: "E02"}
    sent = alias_inputs({"evidence": [{"evidence_id": REAL, "text": "t"}],
                         "source_metadata": {OTHER: {"date": "x"}}}, aliases)
    assert sent == {"evidence": [{"evidence_id": "E01", "text": "t"}],
                    "source_metadata": {"E02": {"date": "x"}}}
    answer = {"hypotheses": [{"hypothesis_id": "M1S2-H001", "claim": "E01 shows",
                              "supporting_evidence_ids": ["E01", "E02"],
                              "evidence_quotes": [{"evidence_id": "E02", "quote": "q"}]}],
              "executives": [{"executive_id": "E01"}]}
    back = unalias_answer(answer, aliases)
    hypothesis = back["hypotheses"][0]
    assert hypothesis["supporting_evidence_ids"] == [REAL, OTHER]
    assert hypothesis["evidence_quotes"][0]["evidence_id"] == OTHER
    # Prose and other kinds of id are never rewritten.
    assert hypothesis["claim"] == "E01 shows"
    assert back["executives"][0]["executive_id"] == "E01"


def test_one_character_typo_is_corrected_not_rejected():
    typo = REAL.replace("7458dd90", "7458gd90")
    parsed = {"hypotheses": [{"hypothesis_id": "M1S2-H004",
                              "supporting_evidence_ids": [typo]}]}
    fixed, notes = repair_citations(parsed, {REAL, OTHER})
    assert fixed["hypotheses"][0]["supporting_evidence_ids"] == [REAL]
    assert notes[0]["hypothesis_id"] == "M1S2-H004" and "Corrected" in notes[0]["note"]


def test_indicator_ids_in_parameter_evidence_are_removed_and_backfilled():
    parsed = {
        "hypotheses": [{"hypothesis_id": "M4-H001", "supporting_evidence_ids": [OTHER]}],
        "parameters": [
            {"parameter_id": "capex_strategy_assessment", "status": "ok",
             "evidence_ids": ["pr_4ae524395572477f", "pr_6575f6a46b714919"],
             "hypothesis_ids": ["M4-H001"]},
            {"parameter_id": "unsupported", "status": "provisional",
             "evidence_ids": ["pr_4ae524395572477f"], "hypothesis_ids": []},
        ]}
    fixed, notes = repair_citations(parsed, {REAL, OTHER})
    supported, unsupported = fixed["parameters"]
    assert supported["evidence_ids"] == [OTHER] and supported["status"] == "ok"
    assert unsupported["evidence_ids"] == [] and unsupported["status"] == "unavailable"
    assert sum("Removed" in n["note"] for n in notes) == 3


def test_quote_with_unknown_id_is_reattributed_to_the_passage_holding_it():
    parsed = {"hypotheses": [{"hypothesis_id": "H1", "evidence_quotes": [
        {"evidence_id": "invented", "quote": "Revenue grew in Florida."},
        {"evidence_id": "invented", "quote": "Not in any passage."}]}]}
    passages = {REAL: "In 2025, Revenue grew in Florida. Other text.", OTHER: "Unrelated."}
    fixed, _ = repair_citations(parsed, {REAL, OTHER}, passages=passages)
    assert fixed["hypotheses"][0]["evidence_quotes"] == [
        {"evidence_id": REAL, "quote": "Revenue grew in Florida."}]


def test_unrelated_short_ids_are_never_guessed():
    parsed = {"hypotheses": [{"hypothesis_id": "H1", "supporting_evidence_ids": ["E1"]}]}
    fixed, _ = repair_citations(parsed, {"E2", "E3"})
    assert fixed["hypotheses"][0]["supporting_evidence_ids"] == []


class _AliasEcho(LLMBackend):
    """Answers with the aliases it was shown, as a live model would."""

    model_id = "fake-vertex-under-test"

    def __init__(self) -> None:
        self.seen: list[dict] = []

    def generate(self, request: LLMRequest) -> LLMRawResult:
        self.seen.append(request.inputs)
        shown = [p["evidence_id"] for p in request.inputs["evidence"]]
        response = _live_valid_response()
        # Cite the first passage exactly as shown to the model.
        response["hypotheses"][0]["supporting_evidence_ids"] = [shown[0]]
        return LLMRawResult(raw_response=json.dumps(response), model_id=self.model_id)


def test_model_sees_aliases_and_validation_sees_real_ids(db_session, m3_prep):
    backend = _AliasEcho()
    arm = run_single_pass(db_session, m3_prep, backend=backend)
    sent = json.dumps(backend.seen[0])
    assert not any(e in sent for e in m3_prep.packet.evidence_ids)
    assert backend.seen[0]["evidence"][0]["evidence_id"] == "E01"
    first = list(dict.fromkeys(m3_prep.packet.evidence_ids))[0]
    assert arm.parsed["hypotheses"][0]["supporting_evidence_ids"] == [first]
