"""Run the Financial Overview over one filing folder and record the result.

The FO counterpart of ``app.services.bo.runner``: load the filing from
``edgar_filings/``, run the pre-checks (going concern, ``precheck.py``; covenants,
``covenants.py``; acquisitions and divestitures, ``acquisitions.py``; prepaid model,
``prepaid.py``), compute the measures (DSO and Debt/EBITDAR adjusted for the year's deals; DSO on
billed revenue, or its weight factored, for a prepaid model), score them against the industry
benchmark under the pre-checks' gate, run the trigger agents the scores set off (DSCR T1,
``dscr_triggers.py``) and record one ``financial_overview_runs`` row holding the scored
overview exactly as produced (content-hashed). Every number is computed by deterministic
code. The only model calls are the Pre-check Agent's and the Trigger Agent's, when a backend
is given; their findings are verified before they count.

Anything escalated holds the run at ``awaiting_review`` until an analyst decides
(``review.py``): a going-concern FAIL is continue (composite 10, measures superseded) or
stop; a covenant breach escalates the liquidity-and-maturity measure, an unresolved
acquisition or divestiture escalates DSO and/or Debt/EBITDAR, and an unresolved trigger
(its agent and verified evidence disagree) escalates its measure (DSCR), whose score the
analyst accepts or overrides.
"""

from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.hashing import canonical_json, content_hash
from app.models.base import utcnow
from app.models.orm import FinancialOverviewRunRow

from . import loader
from .acquisitions import MEASURE_NAMES as DEAL_MEASURE_NAMES
from .acquisitions import acquisitions_precheck, apply_deal_adjustments
from .covenants import covenant_precheck
from .dscr_triggers import T1_ID, T1_THRESHOLD, apply_t1, dscr_t1_trigger
from .prepaid import apply_prepaid_adjustments, prepaid_precheck
from .industry import classify_industry, reference_hash
from .measures import build_financial_overview, credit_rating, risk_level
from .precheck import going_concern_precheck
from .scoring import (
    COVENANT_BREACH_SCORE,
    COVENANT_RELIEF_UPLIFT,
    GC_WATCH_LIQUIDITY_FLOOR,
    composite_risk,
    headline_score,
    score_financial_overview,
    scoring_settings,
)

ENGINE_VERSION = "fo-engine-1.7.0"
AWAITING_REVIEW = "awaiting_review"
NOT_SCORED = "Not scored: going-concern fail"
LIQUIDITY_NAME = "Liquidity and debt maturity"


def gate_for(gc_status: str, covenant_status: str = "compliant", acquisitions: dict | None = None,
             prepaid: dict | None = None) -> dict:
    """What the pre-checks do to the rest of the run. ``agent_triggers`` is what every agent a measure triggers
    (today DSCR T1, ``dscr_triggers.py``) honours: "skipped" on a going-concern fail."""
    ad, pp = acquisitions or {}, prepaid or {}
    adjusted = [k for k, v in (ad.get("adjustments") or {}).items() if v]
    return {"going_concern": gc_status,
            "liquidity_floor": GC_WATCH_LIQUIDITY_FLOOR if gc_status == "watch" else None,
            "covenants": covenant_status,
            "liquidity_adjustment": ({"breach": f"revolver availability 0; score set to {COVENANT_BREACH_SCORE}",
                                      "relief": f"+{COVENANT_RELIEF_UPLIFT} after calculation"}
                                     .get(covenant_status)),
            "acquisitions": ad.get("status", "none"),
            "deal_adjustments": adjusted,
            "deal_unresolved": list(ad.get("unresolved_measures") or []),
            "prepaid": pp.get("status", "not_applicable"),
            "dso_weight_factor": pp.get("factor"),
            "measures_counted": gc_status != "fail",
            "agent_triggers": "skipped" if gc_status == "fail" else "enabled",
            "requires_decision": gc_status == "fail" or covenant_status == "breach"
            or ad.get("status") == "unresolved" or pp.get("status") == "unresolved"}


def escalations_for(gc: dict, cov: dict, metrics: list[dict], ad: dict | None = None,
                    pp: dict | None = None, triggers: dict | None = None) -> list[dict]:
    """What waits for an analyst. A going-concern fail is a run decision (continue or stop); with it, the measures
    are superseded and nothing else is escalated. Otherwise each unresolved pre-check escalates the measure it
    adjusts, for the analyst to accept the engine's score or override it: a covenant breach (outcome a) escalates
    liquidity and debt maturity; an unresolved acquisition or divestiture escalates DSO and/or Debt/EBITDAR. A
    trigger that fired escalates its measure only when unresolved: its agent and verified evidence disagree (DSCR
    T1: DSCR); otherwise its verified outcome stands. Where the agent read a check more severely than its verified
    evidence supports (``disagreements``), that is escalated too."""
    if gc["status"] == "fail":
        return [{"id": "going_concern", "type": "run_decision", "check": "going_concern", "reason": gc["reason"]}]
    by_key = {m["key"]: m for m in metrics}
    out = []
    if cov["status"] == "breach":
        liquidity = by_key.get("liquidity")
        out.append({"id": "covenants:liquidity", "type": "measure_override", "check": "covenants",
                    "title": "covenant breach", "measure": "liquidity", "measure_name": LIQUIDITY_NAME,
                    "engine_score": headline_score(liquidity)[0] if liquidity else None,
                    "calculated_score": ((liquidity["columns"][0].get("covenant_adjustment") or {}).get("calculated")
                                         if liquidity and liquidity["columns"] else None),
                    "reason": cov["reason"],
                    "detail": f"The engine set revolver availability to 0 and the score to {COVENANT_BREACH_SCORE}."})
    for measure in (ad or {}).get("unresolved_measures") or []:
        deals = [d["deal_id"] for d in ad["deals"] if measure in d["effects"]["unresolved"]]
        _escalate(out, by_key, "acquisitions", measure, "acquisition or divestiture unresolved",
                  ad["reason"] + (f" ({', '.join(deals)})" if deals else ""),
                  "The engine left this measure as reported, without the deal's figures.")
    if (pp or {}).get("status") == "unresolved":
        _escalate(out, by_key, "prepaid", "dso", "prepaid model unresolved", pp["reason"],
                  "The engine kept DSO as calculated, at its normal weight.")
    t1 = (triggers or {}).get(T1_ID)
    if t1 and t1.get("fired") and t1.get("unresolved", True):  # unresolved: agent and verified evidence disagree
        said = str(((t1.get("agent") or {}).get("stated_status") or "none")).replace("_", " ")
        _escalate(out, by_key, T1_ID, "dscr", f"DSCR trigger T1 unresolved (score {T1_THRESHOLD} or more)",
                  f"DSCR scored {t1['score']:g} in {t1['column']}: the agent read {said}; the verified evidence "
                  f"gives {t1['status'].replace('_', ' ')} ({t1['reason']})", T1_DETAIL, disagreement=True)
        e = next(e for e in out if e.get("measure") == "dscr")
        e["calculated_score"] = e.get("calculated_score") or t1["score"]
    for d in disagreements(gc, cov, ad, pp):  # the agent read it more severely than the verified evidence
        if d["measure"] == "run":
            out.insert(0, {"id": "going_concern:disagreement", "type": "run_decision", "check": "going_concern",
                           "disagreement": True, "title": "agent and verified evidence disagree",
                           "reason": d["reason"], "agent": d["agent"], "verified": d["verified"]})
        else:
            _escalate(out, by_key, d["check"], d["measure"], "agent and verified evidence disagree", d["reason"],
                      DISAGREEMENT_DETAIL, disagreement=True)
    return out


MEASURE_LABELS = {"liquidity": LIQUIDITY_NAME, "dscr": "Debt service coverage ratio (DSCR)", **DEAL_MEASURE_NAMES}
T1_DETAIL = ("The Trigger Agent's answer and its verified evidence disagree, so T1 is unresolved. The engine applied the "
             "verified outcome (T1a: rollover debt with a qualifying backstop removed from principal; T1b: −2 for an "
             "executed refinancing). Read the agent's answer and the held-back evidence below: accept the engine's "
             "score, or override it with yours.")
DISAGREEMENT_DETAIL = ("The engine scored this measure on the verified evidence only. Read the agent's answer and the "
                       "held-back quotes in the pre-check below: accept the engine's score, or override it with yours "
                       "(for example the score the agent's reading implies).")


def _escalate(out, by_key, check, measure, title, reason, detail, disagreement=False):
    """Add a measure escalation, or merge it into the one already open for that measure (one decision per measure;
    the escalation then carries every check's reason)."""
    same = next((e for e in out if e.get("measure") == measure), None)
    if same:
        same.update(reason=f"{same['reason']}; and: {reason}",
                    checks=list(dict.fromkeys([*same.get("checks", [same["check"]]), check])),
                    title=f"{same['title']}; {title}", disagreement=same.get("disagreement", False) or disagreement)
        return
    m = by_key.get(measure)
    out.append({"id": f"{check}:{measure}", "type": "measure_override", "check": check, "title": title,
                "measure": measure, "measure_name": MEASURE_LABELS[measure],
                "engine_score": headline_score(m)[0] if m else None, "calculated_score": None, "reason": reason,
                "detail": detail, **({"disagreement": True} if disagreement else {})})


GC_SEVERITY = {"pass": 0, "watch": 1, "fail": 2}
COVENANT_SEVERITY = {"compliant": 0, "routine": 0, "unclear": 0, "relief": 1, "breach": 2}


def _said(result: dict | None) -> str | None:
    return ((result or {}).get("agent") or {}).get("stated_status")


def disagreements(gc: dict, cov: dict, ad: dict | None, pp: dict | None) -> list[dict]:
    """Where the agent's own answer is more severe than the outcome its verified evidence supports. The verified
    outcome still drives the measures, but each of these goes to an analyst instead of being settled by either side:

    - going concern: the agent said FAIL, the evidence gives less (a run decision); the agent said WATCH, the evidence
      gives PASS (liquidity: the watch floor of 7 wasn't applied);
    - covenants: the agent said breach, or relief, and the evidence gives an outcome with less effect (liquidity);
    - acquisitions: a deal the agent called unresolved that the evidence resolves, or that was held back (DSO, and
      Debt/EBITDAR for a divestiture after the year-end);
    - prepaid model: the agent said unresolved, the evidence gives another outcome (DSO).

    A trigger (DSCR T1) is escalated whenever its agent and verified evidence disagree, in either direction: that is
    what makes it unresolved (``escalations_for``)."""
    out = []
    said = _said(gc)
    if said in GC_SEVERITY and GC_SEVERITY[said] > GC_SEVERITY.get(gc["status"], 0):
        out.append({"check": "going_concern", "measure": "run" if said == "fail" else "liquidity", "agent": said,
                    "verified": gc["status"],
                    "reason": f"Going concern: the agent read {said.upper()}; the verified evidence gives "
                              f"{gc['status'].upper()}"})
    said = _said(cov)
    if said in COVENANT_SEVERITY and COVENANT_SEVERITY[said] > COVENANT_SEVERITY.get(cov["status"], 0):
        out.append({"check": "covenants", "measure": "liquidity", "agent": said, "verified": cov["status"],
                    "reason": f"Covenants: the agent read {said}; the verified evidence gives {cov['status']}"})
    if ad and ad.get("agent"):
        deals = [(d["deal_id"], d["kind"], d["timing"], d["presentation"], d["outcome"])
                 for d in ad["deals"] if d["stated"].get("outcome") == "unresolved" and d["outcome"] != "unresolved"]
        deals += [(h["deal_id"], h.get("kind"), h.get("timing"), h.get("presentation"), "held back")
                  for h in ad["agent"].get("held_back_deals") or [] if h.get("outcome") == "unresolved"]
        for did, kind, timing, presentation, verified in deals:
            measures = []
            if kind == "acquisition" or timing == "in_year" or presentation == "held_for_sale":
                measures.append("dso")
            if kind == "divestiture" and timing == "after_year_end":
                measures.append("leverage")
            for measure in measures:
                out.append({"check": "acquisitions", "measure": measure, "agent": "unresolved", "verified": verified,
                            "reason": f"Acquisitions: the agent called {did} unresolved; the verified evidence gives "
                                      f"{verified.replace('_', ' ')}"})
    said = _said(pp)
    if said == "unresolved" and pp["status"] != "unresolved":
        out.append({"check": "prepaid", "measure": "dso", "agent": said, "verified": pp["status"],
                    "reason": f"Prepaid model: the agent read unresolved; the verified evidence gives "
                              f"{pp['status'].replace('_', ' ')}"})
    return out


def composite_for(payload: dict, overrides: dict | None = None, decisions: dict | None = None,
                  gc_status: str | None = None) -> dict:
    """The payload's composite re-weighted with an analyst's measure scores ({measure key: score}); ``decisions``
    ({measure key: "override" or "accept"}) labels each. ``gc_status`` replaces the going-concern result: "fail"
    when the analyst sided with the agent's FAIL against the verified evidence."""
    return composite_risk(payload["metrics"][1:], {"values": payload["scoring_settings"]["values"]},
                          gc_status or payload["gate"]["going_concern"], overrides, decisions)
SCOPE = "financial_overview_only"
CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


class FoRunError(ValueError):
    """The run request is invalid (bad case id)."""


def json_safe(value):
    """The payload as plain JSON: tuples become lists and non-finite floats (an infinite coverage when nothing
    falls due) become the strings "inf" / "-inf" / "nan", so it stores, hashes and serves identically."""
    if isinstance(value, float) and not math.isfinite(value):
        return "nan" if math.isnan(value) else ("inf" if value > 0 else "-inf")
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def default_case_id(meta: dict, folder: str) -> str:
    """TICKER_FY<year> from the filing's cover page, else the folder name made safe."""
    ticker, year = meta.get("ticker"), str(meta.get("fiscal_year") or "").strip()
    if ticker and year:
        candidate = f"{ticker}_FY{year}"
    else:
        candidate = re.sub(r"[^A-Za-z0-9_.-]+", "_", folder).strip("_.-")
    candidate = re.sub(r"[^A-Za-z0-9_.-]+", "_", candidate)[:64]
    return candidate if CASE_ID.fullmatch(candidate or "") else "FO_CASE"


def validate_case_id(case_id: str) -> str:
    if not CASE_ID.fullmatch(case_id or ""):
        raise FoRunError("Case id must be 1-64 letters, digits, '_', '.' or '-', starting with a letter or digit.")
    return case_id


def run_state(payload: dict) -> tuple[str, str]:
    """(run status, human review status) a new run starts in: anything escalated waits for an analyst."""
    escalated = payload.get("escalations")
    if escalated is None:  # runs recorded before the covenant pre-check
        escalated = ((payload.get("precheck") or {}).get("going_concern") or {}).get("review_required")
    return (AWAITING_REVIEW, "escalated") if escalated else ("completed", "pending")


def next_step(run_id: str, escalation: dict) -> str:
    if escalation["type"] == "run_decision":
        choices = "use_verified|continue|stop" if escalation.get("disagreement") else "continue|stop"
        return (f'python -m app.cli fo-review {run_id} --decision {choices} --reviewer "<name>" '
                '--reason "<why>"')
    return (f'python -m app.cli fo-override {run_id} --measure {escalation["measure"]} --decision accept|override '
            '[--score <1-10>] --reviewer "<name>" --reason "<why>"')


@dataclass(frozen=True)
class FoRunResult:
    run_id: str
    case_id: str
    payload: dict
    content_hash: str
    paths: dict = field(default_factory=dict)

    def summary(self) -> dict:
        composite = self.payload["composite"]
        gc = self.payload["precheck"]["going_concern"]
        cov = self.payload["precheck"]["covenants"]
        ad = self.payload["precheck"].get("acquisitions")
        pp = self.payload["precheck"].get("prepaid")
        status, review = run_state(self.payload)
        withheld = status == AWAITING_REVIEW
        escalations = self.payload["escalations"]
        return {
            "case_id": self.case_id,
            "run_id": self.run_id,
            "scope": SCOPE,
            "run_status": status,
            "filing_folder": self.payload["filing"]["folder"],
            "entity_name": self.payload["meta"].get("entity_name"),
            "period_end": self.payload["meta"].get("period_end"),
            "industry_group": self.payload["industry"].get("industry_group"),
            "agent_model": self.payload.get("agent_model"),
            "going_concern": {"status": gc["status"], "decided_by": gc["decided_by"], "mode": gc["mode"],
                              "reason": gc["reason"], "effect": gc["effect"]},
            "covenants": {"status": cov["status"], "outcome": cov["outcome"], "decided_by": cov["decided_by"],
                          "mode": cov["mode"], "reason": cov["reason"], "effect": cov["effect"],
                          "info_flags": cov["info_flags"]},
            **({"acquisitions": {"status": ad["status"], "decided_by": ad["decided_by"], "mode": ad["mode"],
                                 "reason": ad["reason"],
                                 "deals": [{"deal_id": d["deal_id"], "kind": d["kind"], "outcome": d["outcome"]}
                                           for d in ad["deals"]],
                                 "adjusted": self.payload["gate"].get("deal_adjustments", []),
                                 "unresolved": ad["unresolved_measures"], "info_flags": ad["info_flags"]}}
               if ad else {}),
            **({"prepaid": {"status": pp["status"], "decided_by": pp["decided_by"], "mode": pp["mode"],
                            "reason": pp["reason"], "model": pp["model"], "dso_weight_factor": pp["factor"]}}
               if pp else {}),
            "agent_triggers": self.payload["gate"]["agent_triggers"],
            "triggers": {k: {"status": t["status"], "score": t["score"], "fired": t["fired"],
                             "unresolved": bool(t["fired"] and t.get("unresolved", True))}
                         for k, t in (self.payload.get("triggers") or {}).items()},
            "composite_score": None if withheld else composite["score"],
            "credit_rating": None if withheld else composite["rating"],
            "risk_level": None if withheld else composite["risk_level"],
            **({"provisional_composite_score": composite["score"]}
               if withheld and gc["status"] != "fail" else {}),
            "measures": {m["key"]: headline_score(m)[0] for m in self.payload["metrics"][1:]},
            "human_review_status": review,
            "escalations": [{k: e[k] for k in ("id", "type", "reason", "disagreement") if k in e}
                            | ({"measure": e["measure"], "engine_score": e["engine_score"]}
                               if e["type"] == "measure_override" else {}) for e in escalations],
            **({"next_step": next_step(self.run_id, escalations[0])} if withheld else {}),
            "content_hash": self.content_hash,
            "outputs": {name: str(path) for name, path in self.paths.items()},
        }


class FinancialOverviewRunner:
    """FO runs over ``edgar_filings/`` folders, recorded in ``financial_overview_runs``.

    ``agent_backend`` runs the Pre-check Agent (``agent_mode`` "live" or "scripted"). It is required whenever a
    pre-check has passages to interpret (an auditor's report or going-concern language; a covenant search hit):
    without it, or when its output is rejected, ``PrecheckAgentError`` and nothing is recorded. The keyword rules
    only cross-check. With an ``output_root`` each run also writes, as BO does under
    ``<output>/bo/``: ``<output>/fo/<case_id>/<run_id>/financial_overview.json`` (the recorded payload),
    ``financial_overview.html`` (the standalone scorecard) and, when the agent ran,
    ``precheck_transcript.json`` (exactly what it was sent and returned)."""

    def __init__(self, session: Session, *, output_root: str | Path | None = None,
                 agent_backend=None, agent_mode: str = "live", check_stop=None) -> None:
        self.session = session
        self.output_root = Path(output_root) if output_root is not None else None
        self.agent_backend = agent_backend
        self.agent_mode = agent_mode if agent_backend is not None else "none"
        # Called between steps with the next step's name; raises to stop the run (``cancel.checker``).
        self.check_stop = check_stop or (lambda step: None)

    def build(self, folder: str, case_id: str | None = None) -> dict:
        """The scored overview for one filing folder (not recorded; the agent's model run is logged)."""
        data, info = loader.load(folder)
        industry = classify_industry(data)
        checks = {}
        for check, run in (("going_concern", going_concern_precheck), ("covenants", covenant_precheck),
                           ("acquisitions", acquisitions_precheck), ("prepaid", prepaid_precheck)):
            self.check_stop(f"the {check.replace('_', '-')} pre-check")
            checks[check] = run(data, session=self.session, backend=self.agent_backend, mode=self.agent_mode,
                                case_id=case_id)
        gc, cov, ad, pp = (checks[k] for k in ("going_concern", "covenants", "acquisitions", "prepaid"))
        self.check_stop("scoring the measures")
        settings = scoring_settings({}, industry["industry_group"])
        measures = apply_prepaid_adjustments(apply_deal_adjustments(build_financial_overview(data), ad), pp)
        metrics = score_financial_overview(measures, settings, gc["status"], cov["status"])
        gate = gate_for(gc["status"], cov["status"], ad, pp)
        # Trigger agents: a measure that trips its trigger sets its agent off, unless the gate skips them.
        self.check_stop("the trigger agents")
        triggers = {T1_ID: dscr_t1_trigger(data, metrics, gate, session=self.session, backend=self.agent_backend,
                                           mode=self.agent_mode, case_id=case_id)}
        if triggers[T1_ID]["fired"]:
            metrics = apply_t1(metrics, triggers[T1_ID])
            metrics[0] = composite_risk(metrics[1:], settings, gc["status"])
        score = metrics[0]["columns"][0]["score"]
        escalations = escalations_for(gc, cov, metrics, ad, pp, triggers)
        gate.update(requires_decision=gate["requires_decision"] or bool(escalations),
                    disagreements=[e["id"] for e in escalations if e.get("disagreement")],
                    triggers={k: t["status"] for k, t in triggers.items()})
        return json_safe({
            "engine_version": ENGINE_VERSION,
            "reference_hash": reference_hash(),
            "agent_model": agent_model(self.agent_backend),
            "precheck": checks,
            "triggers": triggers,
            "gate": gate,
            "escalations": escalations,
            "meta": data["meta"],
            "industry": industry,
            "scoring_settings": {k: settings[k] for k in ("sector", "values", "defaults", "changed", "errors")},
            "filing": {
                "folder": info["name"],
                "signature": info["signature"],
                "fact_count": len(data["facts"]),
                "context_count": data["context_count"],
                "section_count": len(data["sections"]),
                "working_capital_lines": len(data["working_capital"] or {}),
                "files": data["meta"]["files"],
                "company_info": bool(data.get("company")),
            },
            "composite": {
                "score": score,
                "rating": credit_rating(score) if score is not None else None,
                "risk_level": risk_level(score)[0] if score is not None else None,
                "reason": metrics[0]["columns"][0]["reason"],
            },
            "metrics": metrics,
        })

    def write_outputs(self, case_id: str, run_id: str, payload: dict, run: dict) -> dict[str, Path]:
        """Write the run's JSON and HTML under ``<output_root>/fo/<case_id>/<run_id>/``."""
        from .reporting import render_overview_html

        folder = output_folder(self.output_root, case_id, run_id)
        folder.mkdir(parents=True, exist_ok=True)
        paths = {"financial_overview.json": folder / "financial_overview.json",
                 "financial_overview.html": folder / "financial_overview.html"}
        paths["financial_overview.json"].write_text(canonical_json(payload) + "\n", encoding="utf-8", newline="\n")
        paths["financial_overview.html"].write_text(render_overview_html(payload, run=run),
                                                    encoding="utf-8", newline="\n")
        agents = {check: transcript(result["agent"], check) for check, result in payload["precheck"].items()
                  if result.get("agent")}
        if agents:
            paths["precheck_transcript.json"] = folder / "precheck_transcript.json"
            paths["precheck_transcript.json"].write_text(canonical_json(agents) + "\n", encoding="utf-8", newline="\n")
        fired = {key: transcript(t["agent"], key) for key, t in (payload.get("triggers") or {}).items()
                 if t.get("agent")}
        if fired:
            paths["trigger_transcript.json"] = folder / "trigger_transcript.json"
            paths["trigger_transcript.json"].write_text(canonical_json(fired) + "\n", encoding="utf-8", newline="\n")
        return paths

    def run_filing(self, folder: str, case_id: str | None = None) -> FoRunResult:
        started = utcnow()
        if case_id:
            validate_case_id(case_id)
        else:
            case_id = default_case_id(loader.load(folder)[0]["meta"], folder)
        payload = self.build(folder, case_id)
        self.check_stop("recording the run")
        digest = content_hash(payload)
        run_id = f"fo-{uuid.uuid4().hex[:16]}"
        status, review = run_state(payload)
        withheld = status == AWAITING_REVIEW
        self.session.add(FinancialOverviewRunRow(
            run_id=run_id,
            case_id=case_id,
            filing_folder=payload["filing"]["folder"],
            folder_signature=payload["filing"]["signature"],
            entity_name=payload["meta"].get("entity_name"),
            ticker=payload["meta"].get("ticker"),
            period_end=payload["meta"].get("period_end"),
            engine_version=ENGINE_VERSION,
            reference_hash=payload["reference_hash"],
            status=status,
            composite_score=None if withheld else payload["composite"]["score"],
            credit_rating=None if withheld else payload["composite"]["rating"],
            human_review_status=review,
            payload=payload,
            content_hash=digest,
            started_at=started,
            completed_at=None if withheld else utcnow(),
        ))
        self.session.flush()
        paths = {}
        if self.output_root is not None:
            paths = self.write_outputs(case_id, run_id, payload, {
                "case_id": case_id, "run_id": run_id, "started_at": started.isoformat(), "content_hash": digest,
                "run_status": status})
        return FoRunResult(run_id, case_id, payload, digest, paths)


def agent_model(backend) -> str | None:
    """The model the Pre-check Agent runs on (its narrow tier), as the run records it."""
    if backend is None:
        return None
    pick = getattr(backend, "model_for_tier", None)
    return (pick("narrow") if pick else None) or getattr(backend, "model_id", getattr(backend, "_model_id", None))


def output_folder(output_root: str | Path, case_id: str, run_id: str) -> Path:
    return Path(output_root) / "fo" / case_id / run_id


def transcript(agent: dict, check: str = "going_concern") -> dict:
    """Exactly what the Pre-check Agent was sent and returned for one check, and what validation kept."""
    from app.prompts import fo_prompts

    template, schema = {"going_concern": (fo_prompts.FO_GOING_CONCERN_TEMPLATE, fo_prompts.FO_GOING_CONCERN_SCHEMA),
                        "covenants": (fo_prompts.FO_COVENANTS_TEMPLATE, fo_prompts.FO_COVENANTS_SCHEMA),
                        "acquisitions": (fo_prompts.FO_ACQUISITIONS_TEMPLATE,
                                         fo_prompts.FO_ACQUISITIONS_SCHEMA),
                        "prepaid": (fo_prompts.FO_PREPAID_TEMPLATE, fo_prompts.FO_PREPAID_SCHEMA),
                        T1_ID: (fo_prompts.FO_DSCR_T1_TEMPLATE, fo_prompts.FO_DSCR_T1_SCHEMA)}[check]
    return {
        "prompt_id": agent["prompt_id"], "prompt_hash": agent["prompt_hash"], "model_id": agent["model_id"],
        "model_run_id": agent["model_run_id"],
        "system_prompt": template, "response_schema": schema,
        "inputs": agent["inputs"], "raw_response": agent["raw_response"],
        "validation_outcome": agent["validation_outcome"],
        "validation_detail": agent.get("validation_detail"),
        "accepted": agent.get("accepted"), "held_back": agent.get("held_back"),
        **({"headroom": agent.get("headroom"), "headroom_held_back": agent.get("headroom_held_back")}
           if check == "covenants" else {}),
        **({"accepted_deals": agent.get("accepted_deals"), "held_back_deals": agent.get("held_back_deals"),
            "held_back_evidence": agent.get("held_back_evidence")} if check == "acquisitions" else {}),
        **({"held_back": agent.get("held_back")} if check in ("prepaid", T1_ID) else {}),
        "stated_status": agent.get("stated_status"), "derived_status": agent.get("derived_status"),
    }
