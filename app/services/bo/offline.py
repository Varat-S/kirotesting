"""Scripted offline backend for BO infrastructure tests and offline demonstrations.

``ScriptedBoBackend`` is a ``FakeLLMBackend``: it calls no model. It replays a
reviewer-authored script, so a run that uses it is always labelled an OFFLINE
SIMULATION and proves pipeline behaviour, never analytical correctness of a
model.

Two things the plain fake backend cannot do are added:

* SEQUENCES — a list of responses per agent is replayed call by call (the last
  one repeats), so a rerun can answer differently from the first attempt;
* ANCHORS — evidence ids are content hashes that depend on the case, so a script
  refers to evidence by what it says and the backend resolves it against the
  request the agent actually received:

  - ``{"$evidence": "phrase"}``  -> id of the first routed passage containing it
  - ``{"$indicator": "parameter_id"}`` -> that indicator's parameter_result_id
  - ``{"$indicator_value": "parameter_id"}`` -> its exact calculated value
  - ``{"$hypothesis_evidence": "BO1-H001"}`` -> that hypothesis's evidence ids
    (spliced into the surrounding list)

An anchor that matches nothing resolves to an ``UNRESOLVED:...`` id, which the
deterministic validators then reject — a wrong script fails loudly.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.services.bo.hypotheses import normalise
from app.services.llm.client import FakeLLMBackend, LLMRawResult, LLMRequest

SIMULATION_MODEL_ID = "scripted-offline-simulation-v1"


class ScriptedBoBackend(FakeLLMBackend):
    def __init__(self, script: dict[str, Any], *, model_id: str = SIMULATION_MODEL_ID):
        super().__init__(model_id)
        self._script = {
            key: value if isinstance(value, list) else [value]
            for key, value in script.items() if not key.startswith("_")
        }
        self._calls: dict[str, int] = {}
        self._lock = threading.Lock()
        self.requests: list[tuple[str, dict[str, Any]]] = []

    @classmethod
    def from_file(cls, path: str | Path) -> "ScriptedBoBackend":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def calls(self, method: str) -> int:
        return self._calls.get(method, 0)

    def generate(self, request: LLMRequest) -> LLMRawResult:
        with self._lock:
            index = self._calls.get(request.method, 0)
            self._calls[request.method] = index + 1
            self.requests.append((request.method, request.inputs))
        responses = self._script.get(request.method)
        if not responses:
            raise KeyError(f"No scripted response for {request.method!r}.")
        response = responses[min(index, len(responses) - 1)]
        raw = (response if isinstance(response, str)
               else json.dumps(_resolve(response, request.inputs), sort_keys=True))
        return LLMRawResult(
            raw_response=raw, model_id=self._model_id,
            model_config={"temperature": request.temperature, "backend": "fake",
                          "simulation": True, "script_call_index": index})


def _lookup(inputs: dict[str, Any]) -> dict[str, Any]:
    hypotheses = {}
    for key in ("hypotheses", "primary_hypotheses", "hypotheses_relied_on",
                "hypotheses_not_used", "audit_only_hypotheses", "resilience_findings"):
        for item in inputs.get(key) or []:
            hypotheses.setdefault(item.get("hypothesis_id"), item)
    return {
        "passages": [(p["evidence_id"], normalise(p.get("text") or ""))
                     for p in inputs.get("evidence") or []],
        "indicators": {i["parameter_id"]: i for i in inputs.get("indicators") or []},
        "hypotheses": hypotheses,
    }


def _resolve(node: Any, inputs: dict[str, Any], lookup=None) -> Any:
    lookup = lookup or _lookup(inputs)
    if isinstance(node, dict):
        if set(node) == {"$evidence"}:
            phrase = normalise(node["$evidence"])
            return next((eid for eid, text in lookup["passages"] if phrase in text),
                        f"UNRESOLVED:{node['$evidence']}")
        if set(node) == {"$indicator"}:
            found = lookup["indicators"].get(node["$indicator"])
            return found["parameter_result_id"] if found else (
                f"UNRESOLVED:{node['$indicator']}")
        if set(node) == {"$indicator_value"}:
            found = lookup["indicators"].get(node["$indicator_value"])
            return found["value"] if found else None
        return {k: _resolve(v, inputs, lookup) for k, v in node.items()}
    if isinstance(node, list):
        out = []
        for item in node:
            if isinstance(item, dict) and set(item) == {"$hypothesis_evidence"}:
                found = lookup["hypotheses"].get(item["$hypothesis_evidence"], {})
                out.extend([*(found.get("supporting_evidence_ids") or []),
                            *(found.get("contradicting_evidence_ids") or [])])
            else:
                out.append(_resolve(item, inputs, lookup))
        return out
    return node
