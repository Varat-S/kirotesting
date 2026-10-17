"""TEMPORARY: live accuracy test of the premise entailment judge (fix R5).

    python -m app.services.bo.judge_eval --case-dir <case folder> --case-id <ID> \\
        --cases <cases.json> --runs 3 --out output/judge_eval

Each case names a passage by anchor phrase (resolved against the admitted
narrative evidence), a premise and the expected answer written before any run.
Reports the judge's raw verdict and the guarded result (what the pipeline would
accept). Paid calls; one batched call per run.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path


def run(case_dir: Path, case_id: str, cases_path: Path, runs: int, out: Path,
        model: str | None = None) -> dict:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.cli import _live_backend
    from app.models.orm import Base
    from app.prompts.bo_prompts import BO_PREMISE_JUDGE_PROMPT
    from app.services.analysis.grounding import judged_support_guard
    from app.services.bo.comparison import _client, borrower_aliases
    from app.services.bo.recovery import recover_quote
    from app.services.bo.transcript import RecordingBackend
    from app.services.pipeline.package import SourcePackage
    from app.services.pipeline.runner import CreditMemoPipeline

    spec = json.loads(cases_path.read_text(encoding="utf-8"))
    tmp = Path(tempfile.mkdtemp(prefix="judge_eval_"))
    engine = create_engine(f"sqlite:///{tmp}/case.db")
    Base.metadata.create_all(engine)
    session = Session(engine)
    prepared = CreditMemoPipeline(session, data_root=tmp / "data",
                                  output_root=tmp / "out").prepare_case(
        case_id, package=SourcePackage.load(case_dir / "package.json"))
    evidence = prepared.evidence_snapshot
    aliases = borrower_aliases(evidence)
    passages = [p for p in evidence.narrative_evidence if p.get("evidence_id")]

    def passage_for(anchor: str) -> dict:
        hit = next((p for p in passages if anchor.lower() in (p.get("text") or "").lower()),
                   None)
        if hit is None:
            raise ValueError(f"anchor not found: {anchor!r}")
        return hit

    items = []
    for c in spec["cases"]:
        p = passage_for(c["passage_anchor"])
        items.append({"key": c["id"], "premise": c["premise"],
                      "passages": [{"passage_id": p["evidence_id"], "text": p["text"]}]})
    texts = {i["key"]: i["passages"][0]["text"] for i in items}

    backend = _live_backend()
    if model:
        from app.core.config import get_settings
        from app.services.llm.providers_vertex import VertexProviderBackend

        backend = VertexProviderBackend(get_settings().model_copy(
            update={"vertex_model_narrow": model, "llm_max_output_tokens": 32768}))
    results = []
    for n in range(1, runs + 1):
        recorder = RecordingBackend(backend)
        client, _ = _client(session, recorder)
        response = client.analyze({"items": items}, case_id=case_id,
                                  prompt_name=BO_PREMISE_JUDGE_PROMPT)
        verdicts = {v["key"]: v for v in ((response.parsed or {}).get("verdicts") or [])}
        rows = []
        for c in spec["cases"]:
            v = verdicts.get(c["id"], {})
            raw = "states" if v.get("verdict") == "states" else "not_states"
            if raw == "states":
                pid = items[[i["key"] for i in items].index(c["id"])]["passages"][0]["passage_id"]

                def repair(q, _pid=pid):
                    rec = recover_quote(quote=q, cited_evidence_id=_pid, corpus=passages)
                    return rec.get("replacement_quote") if rec.get("outcome") == "layout_gap" else None
                ok, reason = judged_support_guard(
                    c["premise"], v.get("supporting_sentence"), texts[c["id"]],
                    aliases=aliases, repair=repair)
            else:
                ok, reason = False, f"verdict {v.get('verdict')!r}"
            rows.append({**c, "verdict": v.get("verdict"), "raw": raw,
                         "guarded": "states" if ok else "not_states", "reason": reason,
                         "supporting_sentence": v.get("supporting_sentence")})
        mc = (recorder.calls[0].get("model_config") or {}) if recorder.calls else {}
        results.append({"run": n, "rows": rows, "usage": {
            k: mc.get(k) for k in ("input_tokens", "output_tokens", "thinking_tokens")}})
        print(f"run {n}: " + summarise(rows), flush=True)
    out.mkdir(parents=True, exist_ok=True)
    report = {"cases": str(cases_path), "model": model or "default narrow tier",
              "runs": results}
    (out / "judge_eval.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return report


def summarise(rows: list[dict]) -> str:
    def counts(field: str) -> str:
        tp = sum(r[field] == "states" and r["expected"] == "states" for r in rows)
        fp = sum(r[field] == "states" and r["expected"] == "not_states" for r in rows)
        fn = sum(r[field] == "not_states" and r["expected"] == "states" for r in rows)
        tn = len(rows) - tp - fp - fn
        return f"{field}: correct {tp + tn}/{len(rows)} (false 'states' {fp}, missed {fn})"
    return counts("raw") + "; " + counts("guarded")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--model", default=None)
    parser.add_argument("--out", type=Path, default=Path("output/judge_eval"))
    args = parser.parse_args(argv)
    run(args.case_dir, args.case_id, args.cases, args.runs, args.out, args.model)


if __name__ == "__main__":
    main()
