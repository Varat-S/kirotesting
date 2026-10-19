"""Complete BO synthesis/challenge from a recorded specialists-only run.

Examples:
  python -m scripts.complete_bo_recorded --source <specialists_run.json> --mode offline
  python -m scripts.complete_bo_recorded --source <specialists_run.json> --mode replay \
      --replay <bo_completion_packet.json>
  python -m scripts.complete_bo_recorded --source <specialists_run.json> --mode live
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.config import get_settings
from app.services.bo.completion import (
    ConservativeCompletionBackend,
    complete_recorded_specialists,
)
from app.services.llm.client import FakeLLMBackend


def _live_backend():
    settings = get_settings()
    if settings.llm_provider == "vertex":
        from app.services.llm.providers_vertex import VertexProviderBackend
        backend = VertexProviderBackend(settings)
    else:
        from app.services.llm.providers import RealProviderBackend
        backend = RealProviderBackend(settings)
    backend.check_configuration()
    return backend


def _replay_backend(path: Path):
    packet = json.loads(path.read_text(encoding="utf-8"))
    calls = packet.get("model_calls") or []
    by_stage = {call.get("stage"): call.get("output") for call in calls}
    if not isinstance(by_stage.get("orchestrate"), dict) \
            or not isinstance(by_stage.get("challenge"), dict):
        raise ValueError("Replay packet must contain orchestrate and challenge outputs.")
    backend = FakeLLMBackend(model_id="bo-completion-recorded-replay-v1")
    backend.register("analyze", by_stage["orchestrate"])
    backend.register("challenge", by_stage["challenge"])
    backend.recorded_execution_mode = (packet.get("handoff") or {}).get(
        "execution_mode", "offline_simulation")
    return backend


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--mode", choices=("offline", "replay", "live"), required=True)
    parser.add_argument("--replay", type=Path,
                        help="Prior completion packet; required for replay mode")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    if args.mode == "replay" and args.replay is None:
        parser.error("--mode replay requires --replay")
    if args.mode != "replay" and args.replay is not None:
        parser.error("--replay is only valid with --mode replay")
    backend = (ConservativeCompletionBackend() if args.mode == "offline"
               else _replay_backend(args.replay) if args.mode == "replay"
               else _live_backend())
    output = args.output_dir or args.source.parent / "completion"
    packet = complete_recorded_specialists(
        args.source, backend=backend, execution_mode=args.mode, output_dir=output)
    print(json.dumps({
        "analysis_run_id": packet["analysis_run_id"],
        "source_sha256": packet["source"]["sha256"],
        "conclusion_status": packet["handoff"]["conclusion_status"],
        "human_review_status": packet["handoff"]["human_review_status"],
        "business_risk_score_status": packet["handoff"]["business_risk_score_status"],
        "incremental_usage": packet["incremental_usage"],
        "packet_hash": packet["packet_hash"],
        "handoff_content_hash": packet["handoff"]["content_hash"],
        "output_dir": str(output.resolve()),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
