"""M3 answers are repaired item by item, and re-asked once, before failing.

Regression for the live run whose compact draft wrote the parameter value
``not_established`` into ``parameters[].status``: one misplaced token used to
reject the whole response (``rule=enum; schema_path=properties/a/items/
properties/d/enum``). Everything here is offline.
"""

from __future__ import annotations

import json

from app.services.bo.comparison import run_single_pass
from app.services.llm.client import LLMBackend, LLMRawResult, LLMRequest
from tests.integration.test_bo_m3_live_readiness import _live_valid_response
from tests.unit.test_bo_comparison_failure_detail import m3_prep  # noqa: F401  (fixture)


def test_live_rejected_answer_shape_is_repaired(db_session, m3_prep):
    response = _live_valid_response()
    response["parameters"] = [{
        "parameter_id": "governance_concern_assessment", "value": None,
        "value_type": "categorical", "status": "not_established",
        "evidence_ids": [], "hypothesis_ids": []}]
    arm = run_single_pass(db_session, m3_prep, backend=_Sequence(json.dumps(response)))
    assert arm.failure is None or "schema validation" not in arm.failure
    parameter = arm.parsed["parameters"][0]
    assert (parameter["value"], parameter["status"]) == ("not_established", "unavailable")
    assert arm.metadata["draft_repairs"] == [
        {"action": "moved_value_out_of_status", "path": "/parameters/[]/status"}]


def test_enum_spelling_variant_is_normalised(db_session, m3_prep):
    response = _live_valid_response()
    observation = response["m3_assessment"]["management_assessments"][0]
    observation["dimension"] = observation["dimension"].upper()
    arm = run_single_pass(db_session, m3_prep, backend=_Sequence(json.dumps(response)))
    assert arm.parsed["m3_assessment"]["management_assessments"][0]["dimension"] \
        == _live_valid_response()["m3_assessment"]["management_assessments"][0]["dimension"]
    assert arm.metadata["draft_repairs"][0]["action"] == "normalised_enum_spelling"


class _Sequence(LLMBackend):
    """Returns each scripted raw answer in turn and records the requests."""

    model_id = "fake-vertex-under-test"

    def __init__(self, *answers: str) -> None:
        self._answers = list(answers)
        self.requests: list[LLMRequest] = []

    def generate(self, request: LLMRequest) -> LLMRawResult:
        self.requests.append(request)
        return LLMRawResult(raw_response=self._answers[min(len(self.requests), len(self._answers)) - 1],
                            model_id=self.model_id)


def test_unrepairable_answer_is_reasked_once_with_the_rule(db_session, m3_prep):
    bad = _live_valid_response()
    bad["m3_assessment"]["borrower_identification"]["status"] = "invented"
    backend = _Sequence(json.dumps(bad), json.dumps(_live_valid_response()))
    arm = run_single_pass(db_session, m3_prep, backend=backend, repair_attempts=1)
    assert len(backend.requests) == 2
    feedback = backend.requests[1].inputs["previous_answer_rejected"]["reason"]
    assert "schema_path=" in feedback and "invented" not in feedback
    assert arm.parsed is not None


def test_repair_calls_are_bounded_and_off_by_default(db_session, m3_prep):
    bad = _live_valid_response()
    bad["m3_assessment"]["borrower_identification"]["status"] = "invented"
    backend = _Sequence(json.dumps(bad))
    arm = run_single_pass(db_session, m3_prep, backend=backend)
    assert len(backend.requests) == 1 and arm.parsed is None
    backend = _Sequence(json.dumps(bad))
    arm = run_single_pass(db_session, m3_prep, backend=backend, repair_attempts=1)
    assert len(backend.requests) == 2 and "schema validation" in arm.failure
