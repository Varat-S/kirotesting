"""TEMPORARY: live M2 comparison for one v3 specialist with readable transcripts.

    python -m app.services.bo.m2_live --case-dir <case folder> --case-id <ID> \
        --runs 3 --out output/m2_live

The case folder holds ``package.json`` and, optionally, ``coverage_checks.json``.

Each run executes both arms against the configured provider (``.env``; paid
calls) over the same sealed packet, then writes per run:

* ``transcript.html`` - every model call: stage, prompt, inputs, raw output,
  tokens, latency; then what the deterministic validators did with each finding;
* ``transcript.pdf``  - the same, with very long input strings shortened (the
  HTML keeps everything);
* ``run.json``        - structured results.

and ``summary.json`` / ``summary.md`` across runs. ``output/`` is git-ignored.
Nothing here changes validation or scoring; it only runs and reports.
"""

from __future__ import annotations

import argparse
import html
import json
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any

PRICE_IN_PER_M = 0.25   # USD, Gemini 3.1 Flash-Lite on Vertex (third-party trackers)
PRICE_OUT_PER_M = 1.50
_PDF_FIELD_LIMIT = 1200


def _stage(call: dict[str, Any], arm: str) -> str:
    name = call.get("prompt_name") or call.get("task_type") or "?"
    if arm == "single_pass":
        return f"single pass ({name})"
    return {"hypothesis_generation": "step 6 - hypothesis generation",
            "evidence_enquiry_planning": "step 7 - enquiry planning",
            "hypothesis_testing": "step 8 - hypothesis testing"}.get(
        name, "parameter classification" if name.endswith("__parameters") else name)


def _cost(tokens_in: int, tokens_out: int) -> float:
    return tokens_in / 1e6 * PRICE_IN_PER_M + tokens_out / 1e6 * PRICE_OUT_PER_M


def describe_arm(arm) -> dict[str, Any]:
    """Descriptive outcome of one live arm (there is no answer key for live runs)."""
    v = arm.validation
    records = v.records if v else []
    usage = arm.usage()
    out = {
        "arm": arm.arm, "failure": arm.failure,
        "valid": bool(v and v.valid), "issues": list(v.issues) if v else [],
        "hypotheses": len(records),
        "dispositions": dict(Counter(r.disposition.value for r in records)),
        "usable": sum(1 for r in records if r.usable),
        "presentation": dict(Counter(r.display_priority.value for r in records)),
        "review_flags": dict(Counter(f.get("rule_id") for r in records
                                     for f in r.review_flags)),
        "with_contradicting_evidence": sum(1 for r in records
                                           if r.contradicting_evidence_ids),
        "mitigants": dict(Counter(r.mitigant.effectiveness.value for r in records
                                  if r.mitigant)),
        "parameters": {p.parameter_id: {"status": p.status.value, "value": p.value}
                       for p in (v.parameters if v else [])},
        "usage": usage,
        "cost_usd": round(_cost(usage["input_tokens"], usage["output_tokens"]), 5),
        "findings": [
            {"id": r.hypothesis_id, "claim": r.claim, "claim_type": r.claim_type.value,
             "direction": r.direction.value, "disposition": r.disposition.value,
             "admissibility": r.admissibility.value,
             "presentation": r.display_priority.value,
             "flags": [f.get("rule_id") for f in r.review_flags],
             "notes": list(r.validation_notes),
             "governed": {k: r.investigation.get(k) for k in (
                 "proposed_status", "effective_status", "downgrade_reasons",
                 "adapter_notes")} if r.investigation else None}
            for r in records],
    }
    if arm.investigation is not None:
        inv = arm.investigation
        out["stages"] = [vars(s) for s in inv.stages]
        out["out_of_scope"] = inv.out_of_scope
        out["progression"] = inv.progression()
    return out


# -- rendering ------------------------------------------------------------------


def _pretty(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return value
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _shorten(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _shorten(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shorten(v) for v in value]
    if isinstance(value, str) and len(value) > _PDF_FIELD_LIMIT:
        return value[:_PDF_FIELD_LIMIT] + f" ... [{len(value) - _PDF_FIELD_LIMIT} more characters in the HTML]"
    return value


def render_html(run: dict[str, Any], arms: list, path: Path) -> None:
    e = html.escape
    parts = [
        "<!doctype html><meta charset='utf-8'><title>M2 live transcript</title>",
        "<style>body{font:14px/1.45 system-ui,sans-serif;margin:24px;max-width:1100px}"
        "pre{white-space:pre-wrap;background:#f6f6f6;padding:8px;border-radius:4px;"
        "font-size:12px}details{margin:6px 0}summary{cursor:pointer;font-weight:600}"
        "table{border-collapse:collapse;font-size:12px}td,th{border:1px solid #ccc;"
        "padding:4px;vertical-align:top}h2{border-top:2px solid #333;padding-top:8px}"
        ".bad{color:#a40000}.ok{color:#006400}</style>",
        f"<h1>M2 live run {e(str(run['run']))} - {e(run['case'])}, {e(run['specialist'])}</h1>",
        f"<p>Model: {e(run['model'])}. Same sealed packet for both arms "
        f"(fingerprint {e(run['fingerprint'][:16])}). Live output varies between runs; "
        "do not conclude anything from one run.</p>",
    ]
    for arm, d in zip(arms, run["arms"].values()):
        parts.append(f"<h2>{e(arm.arm.replace('_', ' '))}</h2>")
        u = d["usage"]
        parts.append(f"<p>{u['model_calls']} call(s); {u['input_tokens']:,} input / "
                     f"{u['output_tokens']:,} output tokens; about ${d['cost_usd']:.4f}."
                     + (f" <b class='bad'>Failed: {e(d['failure'])}</b>" if d["failure"]
                        else "") + "</p>")
        if d.get("stages"):
            parts.append("<p>Stages: " + "; ".join(
                f"{e(s['stage'])} {e(s['state'])} ({e(s['detail'])})" for s in d["stages"])
                + "</p>")
        parts.append("<h3>Model calls</h3>")
        for i, c in enumerate(arm.calls, 1):
            mc = c.get("model_config") or {}
            parts.append(
                f"<details open><summary>Call {i}: {e(_stage(c, arm.arm))} - "
                f"{mc.get('input_tokens', '?')} in / {mc.get('output_tokens', '?')} out, "
                f"{mc.get('latency_ms', '?')} ms</summary>"
                f"<details><summary>Instructions (prompt {e(str(c.get('prompt_name')))} "
                f"v{e(str(c.get('prompt_version')))})</summary><pre>"
                f"{e(c.get('instructions') or '')}</pre></details>"
                f"<details><summary>Inputs sent</summary><pre>{e(_pretty(c.get('inputs')))}"
                f"</pre></details><details open><summary>Raw output</summary><pre>"
                f"{e(_pretty(c.get('raw_response') or c.get('error') or ''))}</pre>"
                "</details></details>")
        parts.append("<h3>What the validators did with each finding</h3><table><tr>"
                     "<th>id</th><th>claim</th><th>type / direction</th><th>result</th>"
                     "<th>governed (steps 8-12)</th><th>flags and notes</th></tr>")
        for f in d["findings"]:
            g = f["governed"] or {}
            cls = "ok" if f["admissibility"] != "audit_only" else "bad"
            parts.append(
                f"<tr><td>{e(f['id'])}</td><td>{e(f['claim'])}</td>"
                f"<td>{e(f['claim_type'])} / {e(f['direction'])}</td>"
                f"<td class='{cls}'>{e(f['disposition'])}<br>{e(f['admissibility'])}<br>"
                f"{e(f['presentation'])}</td>"
                f"<td>{e(str(g.get('proposed_status', '')))} &rarr; "
                f"{e(str(g.get('effective_status', '')))}<br>"
                f"{'<br>'.join(e(x) for x in (g.get('downgrade_reasons') or []))}"
                f"{'<br>' + e(', '.join(g.get('adapter_notes') or [])) if g.get('adapter_notes') else ''}</td>"
                f"<td>{e(', '.join(x for x in f['flags'] if x))}<br>"
                f"{'<br>'.join(e(n) for n in f['notes'])}</td></tr>")
        parts.append("</table><h3>Parameters</h3><ul>" + "".join(
            f"<li>{e(k)}: {e(str(v['value']))} ({e(v['status'])})</li>"
            for k, v in d["parameters"].items()) + "</ul>")
        if d.get("out_of_scope"):
            parts.append("<p>Out of scope (not investigated): " + "; ".join(
                e(o["dimension"] + ": " + o["statement"]) for o in d["out_of_scope"]) + "</p>")
    path.write_text("\n".join(parts), encoding="utf-8")


def render_pdf(run: dict[str, Any], arms: list, path: Path) -> bool:
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import (PageBreak, Paragraph, Preformatted,
                                        SimpleDocTemplate, Spacer)
    except ImportError:
        return False
    tr = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-",
                        "—": "-", "…": "...", "→": "->", " ": " ", "•": "*"})

    def clean(text: Any) -> str:
        return str(text or "").translate(tr).encode("latin-1", "replace").decode("latin-1")

    h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=14, leading=18)
    h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12, leading=15,
                        spaceBefore=8)
    h3 = ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=9.5, leading=12,
                        spaceBefore=6)
    body = ParagraphStyle("b", fontName="Helvetica", fontSize=8.5, leading=11)
    mono = ParagraphStyle("m", fontName="Courier", fontSize=6.6, leading=7.8)

    def p(text, style=body):
        return Paragraph(html.escape(clean(text)).replace("\n", "<br/>"), style)

    def pre(text):
        lines = []
        for line in clean(text).splitlines():
            while len(line) > 150:
                lines.append(line[:150])
                line = "    " + line[150:]
            lines.append(line)
        return Preformatted("\n".join(lines), mono)

    story = [p(f"M2 live run {run['run']} - {run['case']}, {run['specialist']}", h1),
             p(f"Model: {run['model']}. Same sealed packet for both arms. Long input "
               "strings are shortened here; transcript.html has them in full."),
             Spacer(1, 4 * mm)]
    for arm, d in zip(arms, run["arms"].values()):
        u = d["usage"]
        story += [p(arm.arm.replace("_", " ").upper(), h2),
                  p(f"{u['model_calls']} call(s); {u['input_tokens']:,} in / "
                    f"{u['output_tokens']:,} out tokens; about ${d['cost_usd']:.4f}."
                    + (f" FAILED: {d['failure']}" if d["failure"] else ""))]
        if d.get("stages"):
            story.append(p("Stages: " + "; ".join(
                f"{s['stage']} {s['state']} ({s['detail']})" for s in d["stages"])))
        story.append(p("What the validators did with each finding", h3))
        for f in d["findings"]:
            g = f["governed"] or {}
            line = (f"{f['id']} [{f['claim_type']}, {f['direction']}] -> "
                    f"{f['disposition']} / {f['admissibility']} / {f['presentation']}")
            if g:
                line += (f"; governed {g.get('proposed_status')} -> "
                         f"{g.get('effective_status')}")
            story += [p(line, ParagraphStyle("f", parent=body, fontName="Helvetica-Bold")),
                      p(f["claim"])]
            for x in [*(g.get("downgrade_reasons") or []), *(g.get("adapter_notes") or []),
                      *[fl for fl in f["flags"] if fl], *f["notes"]]:
                story.append(p("- " + str(x)))
        story.append(p("Parameters: " + "; ".join(
            f"{k} = {v['value']} ({v['status']})" for k, v in d["parameters"].items())))
        for i, c in enumerate(arm.calls, 1):
            mc = c.get("model_config") or {}
            story += [PageBreak(),
                      p(f"{arm.arm.replace('_', ' ')} - call {i}: {_stage(c, arm.arm)} "
                        f"({mc.get('input_tokens', '?')} in / "
                        f"{mc.get('output_tokens', '?')} out tokens)", h2),
                      p("Instructions", h3), pre(c.get("instructions") or ""),
                      p("Inputs sent", h3), pre(_pretty(_shorten(c.get("inputs")))),
                      p("Raw output", h3),
                      pre(_pretty(c.get("raw_response") or c.get("error") or ""))]
        story.append(PageBreak())
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=12 * mm, rightMargin=12 * mm,
                      topMargin=12 * mm, bottomMargin=12 * mm).build(story)
    return True


# -- driver ---------------------------------------------------------------------


def run_live(runs: int, out: Path, *, case_dir: Path, case_id: str,
             specialist: str = "M1S2") -> dict[str, Any]:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from app.cli import _live_backend
    from app.models.orm import Base, Case
    from app.services.bo.comparison import (prepare_specialist, run_investigation,
                                            run_single_pass)
    from app.services.bo.roster_v3 import V3_SPEC_BY_CODE
    from app.services.bo.sectors import load_case_checks
    from app.services.pipeline.package import SourcePackage
    from app.services.pipeline.runner import CreditMemoPipeline

    backend = _live_backend()
    out.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"runs": []}
    for n in range(1, runs + 1):
        tmp = Path(tempfile.mkdtemp(prefix="m2live_"))
        engine = create_engine(f"sqlite:///{tmp}/case.db")
        Base.metadata.create_all(engine)
        session = Session(engine)
        prepared = CreditMemoPipeline(session, data_root=tmp / "data",
                                      output_root=tmp / "out").prepare_case(
            case_id, package=SourcePackage.load(case_dir / "package.json"))
        if session.get(Case, case_id) is None:
            session.add(Case(case_id=case_id, status="open"))
            session.flush()
        checks = case_dir / "coverage_checks.json"
        prep = prepare_specialist(
            session, case_id=case_id, evidence=prepared.evidence_snapshot,
            metrics=prepared.metrics, spec=V3_SPEC_BY_CODE[specialist],
            coverage_checks=load_case_checks(checks) if checks.exists() else ())
        arms, timings = [], {}
        for name, fn in (("single_pass", run_single_pass),
                         ("investigation", run_investigation)):
            started = time.monotonic()
            arms.append(fn(session, prep, backend=backend))
            timings[name] = round(time.monotonic() - started, 1)
        run = {"run": n, "case": case_id, "specialist": specialist,
               "model": getattr(backend, "model_for_tier", lambda t: "?")("narrow"),
               "fingerprint": prep.fingerprint(),
               "arms": {a.arm: {**describe_arm(a), "seconds": timings[a.arm]}
                        for a in arms}}
        run_dir = out / f"run_{n}"
        run_dir.mkdir(exist_ok=True)
        (run_dir / "run.json").write_text(json.dumps(run, indent=1, default=str),
                                          encoding="utf-8")
        render_html(run, arms, run_dir / "transcript.html")
        render_pdf(run, arms, run_dir / "transcript.pdf")
        summary["runs"].append(run)
        print(f"run {n}: " + "; ".join(
            f"{k}: {v['hypotheses']} hyp, {v['usable']} usable, "
            f"{v['usage']['input_tokens']}/{v['usage']['output_tokens']} tok, "
            f"${v['cost_usd']:.4f}, {v['seconds']}s"
            + (f", FAILED {v['failure']}" if v["failure"] else "")
            for k, v in run["arms"].items()), flush=True)
    total = sum(a["cost_usd"] for r in summary["runs"] for a in r["arms"].values())
    summary["total_cost_usd"] = round(total, 4)
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=str),
                                      encoding="utf-8")
    return summary


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--case-dir", type=Path, required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--specialist", default="M1S2")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", type=Path, default=Path("output/m2_live"))
    args = parser.parse_args(argv)
    summary = run_live(args.runs, args.out, case_dir=args.case_dir,
                       case_id=args.case_id, specialist=args.specialist)
    print(f"total cost about ${summary['total_cost_usd']:.4f}; outputs in {args.out}")


if __name__ == "__main__":
    main()
