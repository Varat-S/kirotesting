"""Compare actual schema variants via a mandatory in-memory mock transport.

No live client, ADC or provider call is used. Schema-only fixture A is from the
successful recording; fixture B is the pre-correction reconstruction. C and D
are actual adapter variants over today's unchanged canonical schema.
"""
import json
from importlib.metadata import version
from collections import Counter
from pathlib import Path

from app.core.hashing import content_hash
from app.prompts.bo_prompts import BO_M3_JSON_SCHEMA
from app.services.llm.providers_vertex_m3 import vertex_m3_response_schema


HERE = Path(__file__).resolve().parent
JSON_SCHEMA_FIELDS = {"$id", "$defs", "$ref", "$anchor", "type", "format", "title", "description", "enum",
    "items", "prefixItems", "minItems", "maxItems", "minimum", "maximum", "anyOf", "oneOf",
    "properties", "additionalProperties", "required", "propertyOrdering"}


def variants():
    return {
        "A_phase2_accepted": json.loads((HERE / "phase2_accepted_provider_schema.json").read_text()),
        "B_v1_3_failed_reconstructed": vertex_m3_response_schema(BO_M3_JSON_SCHEMA, variant="v1_3"),
        "C_new_requirements_removed": vertex_m3_response_schema(BO_M3_JSON_SCHEMA, variant="requirements_removed"),
        "D_compact_v1_4": vertex_m3_response_schema(BO_M3_JSON_SCHEMA, variant="compact"),
        "E_management_object_removed_isolation_only": vertex_m3_response_schema(BO_M3_JSON_SCHEMA, variant="without_management"),
    }


def structural_diff(before, after, path=""):
    if type(before) is not type(after) or not isinstance(before, dict):
        return [] if before == after else [{"path": path, "before": before, "after": after}]
    changes = []
    for key in sorted(set(before) | set(after)):
        child = path + "/" + key
        if key not in before:
            changes.append({"path": child, "added": after[key]})
        elif key not in after:
            changes.append({"path": child, "removed": before[key]})
        else:
            changes.extend(structural_diff(before[key], after[key], child))
    return changes


def schema_stats(schema):
    counts = Counter(properties=0, required=0, arrays=0, enums=0, nullable_unions=0, anyOf=0, oneOf=0)
    unsupported, null_enums, required_mismatches = [], [], []
    max_depth = 0

    def visit(node, path="", depth=0):
        nonlocal max_depth
        if not isinstance(node, dict):
            return
        max_depth = max(max_depth, depth)
        unsupported.extend(path + "/" + k for k in node if k not in JSON_SCHEMA_FIELDS)
        counts["properties"] += len(node.get("properties", {}))
        counts["required"] += len(node.get("required", []))
        counts["arrays"] += node.get("type") == "array"
        counts["enums"] += "enum" in node
        counts["nullable_unions"] += isinstance(node.get("type"), list) and "null" in node["type"]
        for key in ("anyOf", "oneOf"):
            counts[key] += len(node.get(key, []))
            for i, value in enumerate(node.get(key, [])):
                visit(value, path + f"/{key}/{i}", depth + 1)
        for value in node.get("enum", []):
            if value is None:
                null_enums.append(path + "/enum")
        counts["additionalProperties_true"] += node.get("additionalProperties") is True
        counts["additionalProperties_false"] += node.get("additionalProperties") is False
        if set(node.get("required", [])) - set(node.get("properties", {})):
            required_mismatches.append(path)
        for key, value in node.get("properties", {}).items():
            visit(value, path + "/properties/" + key, depth + 1)
        if isinstance(node.get("items"), dict):
            visit(node["items"], path + "/items", depth + 1)
        if isinstance(node.get("additionalProperties"), dict):
            visit(node["additionalProperties"], path + "/additionalProperties", depth + 1)
    visit(schema)
    return {**counts, "max_schema_node_depth": max_depth, "unsupported_keyword_paths": unsupported,
        "null_enum_paths": null_enums, "required_not_in_properties": required_mismatches,
        "serialized_bytes": len(json.dumps(schema, separators=(",", ":")).encode()), "sha256": content_hash(schema)}


def property_order_diff(before, after, path=""):
    changes = []
    if not isinstance(before, dict) or not isinstance(after, dict):
        return changes
    if "properties" in before and "properties" in after and list(before["properties"]) != list(after["properties"]):
        changes.append({"path": path + "/properties", "before": list(before["properties"]), "after": list(after["properties"])})
    for key in set(before) & set(after):
        if isinstance(before[key], dict) and isinstance(after[key], dict):
            changes.extend(property_order_diff(before[key], after[key], path + "/" + key))
    return changes


def sdk_wire_check(schema):
    import httpx
    from google import genai
    from google.oauth2.credentials import Credentials
    sent = []

    def handle(request):
        sent.append({"body": json.loads(request.content), "path": request.url.path})
        return httpx.Response(200, json={"candidates": [{"content": {"role": "model", "parts": [{"text": "{}"}]}, "finishReason": "STOP"}]})

    # A synthetic pre-filled token + mock transport preserve the project/global
    # Vertex request shape without ADC lookup, token refresh or network sockets.
    client = genai.Client(vertexai=True, project="offline-diagnostic-project", location="global",
        credentials=Credentials(token="offline-synthetic-token"), http_options={"client_args": {"transport": httpx.MockTransport(handle)}})
    try:
        client.models.generate_content(model="gemini-3.1-flash-lite", contents="Offline SDK serialisation only.", config={
            "response_mime_type": "application/json", "response_json_schema": schema,
            "max_output_tokens": 512, "temperature": 1.0,
            "http_options": {"retry_options": {"attempts": 1}},
        })
    finally:
        client.close()
    config = sent[0]["body"]["generationConfig"]
    assert len(sent) == 1 and "responseSchema" not in config
    assert config["responseJsonSchema"] == schema
    return {"mock_http_requests": len(sent), "raw_json_schema_preserved": True, "remote_acceptance_tested": False,
            "sdk_api_path": sent[0]["path"]}


def compare():
    choices = variants()
    historical_b = json.loads((HERE / "vertex_m3_1_3_provider_schema.json").read_text())
    assert choices["B_v1_3_failed_reconstructed"] == historical_b
    return {"scope": "offline SDK/mock only; zero remote provider requests", "sdk_version": version("google-genai"), "variants": {
        name: {**schema_stats(schema), **sdk_wire_check(schema)} for name, schema in choices.items()},
        "exact_A_to_B_diff": structural_diff(choices["A_phase2_accepted"], choices["B_v1_3_failed_reconstructed"]),
        "A_to_B_property_order_diff": property_order_diff(choices["A_phase2_accepted"], choices["B_v1_3_failed_reconstructed"]),
        "exact_B_to_D_diff": structural_diff(choices["B_v1_3_failed_reconstructed"], choices["D_compact_v1_4"])}


if __name__ == "__main__":
    print(json.dumps(compare(), indent=2))
