"""Coverage diagnostics separate absent observations from mapping/eligibility."""

import pytest

from app.services.pipeline.inspection_views import metric_review


@pytest.mark.parametrize(
    "facts, supplied, expected",
    [
        ([], {}, "Not extracted"),
        (
            [{"fact_id": "quarterly", "name": "rpm", "mapping_status": "mapped"}],
            {},
            "Check its period",
        ),
        (
            [
                {
                    "fact_id": "ambiguous",
                    "name": "Passenger miles",
                    "mapping_status": "ambiguous",
                    "mapping_candidates": ["rpm"],
                }
            ],
            {},
            "mapping requires review",
        ),
        (
            [],
            {"rpm": {"value": None, "status": "conflicting"}},
            "review reconciliation",
        ),
    ],
)
def test_missing_inputs_distinguish_observation_mapping_and_reconciliation(
    facts, supplied, expected
):
    preview = {
        "evidence": {"facts": facts},
        "metrics": {
            "load_factor": {
                "metric_definition_id": "load_factor",
                "metric_definition_version": 1,
                "inputs": supplied,
                "result": None,
            }
        },
    }
    view = metric_review(preview, {("load_factor", 1): {"inputs": ["rpm", "asm"]}})
    missing = view["unavailable"][0]["missing_inputs"]
    assert [item["name"] for item in missing] == ["rpm", "asm"]
    assert expected in missing[0]["reason"]
