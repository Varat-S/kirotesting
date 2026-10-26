"""Reproduce schema metrics/conversion without ADC, sockets or live requests."""
import json
from importlib.metadata import version
from pathlib import Path

import jsonschema

from app.prompts.bo_prompts import BO_M3_JSON_SCHEMA
from app.services.llm.providers import strict_schema
from app.services.llm.providers_vertex_m3 import vertex_m3_response_schema
from examples.bo_m3.vertex400_diagnostics.compare_offline import schema_stats, sdk_wire_check, structural_diff

HERE = Path(__file__).resolve().parent
BASELINE_HASH = "6238ae584c5931311dc231c568ef1d676d2f6b0d4cc4f1523d8a6e0970398320"


def variants():
    return {"v1_4_live_accepted": vertex_m3_response_schema(BO_M3_JSON_SCHEMA, variant="compact"),
            "fact_core_isolation_only": vertex_m3_response_schema(BO_M3_JSON_SCHEMA, variant="fact_core"),
            "v1_5_typed_core_candidate": vertex_m3_response_schema(BO_M3_JSON_SCHEMA),
            "full_strict_comparison_only": strict_schema(BO_M3_JSON_SCHEMA)}


def compare():
    choices = variants()
    assert schema_stats(choices["v1_4_live_accepted"])["sha256"] == BASELINE_HASH
    for schema in choices.values():
        jsonschema.Draft202012Validator.check_schema(schema)
    return {"scope": "offline only; SDK responses are mocked; zero remote requests",
            "google_genai_version": version("google-genai"),
            "variants": {name: {**schema_stats(schema), **sdk_wire_check(schema)} for name, schema in choices.items()},
            "v1_4_to_v1_5_changes": structural_diff(choices["v1_4_live_accepted"], choices["v1_5_typed_core_candidate"])}


if __name__ == "__main__":
    print(json.dumps(compare(), indent=2))
