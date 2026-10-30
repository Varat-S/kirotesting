"""Offline integration of Agent 6's real deterministic retrieval feedback loop."""
import copy
import json

import pytest

from app.models.orm import AuditEvent, AgentRunRow, EvidencePacketRow, HypothesisRecordRow, ParameterResultRow, RetrievalAttemptRow
from app.services.bo.offline import ScriptedBoBackend
from app.services.bo.retrieval import BoEvidenceRetriever, RetrievalBudget
from app.services.bo.runner import BusinessOverviewRunner
from tests import bo_helpers as h
from tests.integration.test_bo_v3_complete import _script

A6 = "m1_resilience_integration"
NEW = "ev_feedback"
TEXT = "Zebra scheduling pilot remains untested; its effectiveness is uncertain."


def _request(purpose="contradict", terms=None, hid="M1A6-H001"):
    return {"purpose": purpose, "terms": terms or ["zebra scheduling"], "hypothesis_id": hid}


def _run(session, tmp_path, *, requests=None, second=None, retriever=None, trace_path=None, challenge=False, gap_source=None, gap_text=None):
    script = _script()
    initial = script[A6]
    initial["parameters"] = [h.param("compound_risk_assessment", "interaction_plausible_unquantified",
                                    "nursing shortage", ["M1A6-H001"])]
    if gap_source:
        gap_text = gap_text or "Reported outcomes of Zebra scheduling pilot."
        if gap_source == "response_level":
            initial["missing_information"] = [gap_text]
            initial["hypotheses"][0]["transmission_mechanism"] += " Zebra scheduling pilot outcomes affect this interaction."
        else:
            initial["hypotheses"][0]["missing_information"] = [gap_text]
    if requests is not None:
        initial["retrieval_requests"] = requests
    final = copy.deepcopy(initial)
    final["hypotheses"][0]["hypothesis_id"] = "M1A6-H002"
    final["hypotheses"][0]["claim"] = "The untested scheduling pilot may leave staffing and competitive pressures unresolved."
    final["hypotheses"][0]["contradicting_evidence_ids"] = [NEW]
    final["hypotheses"][0]["evidence_quotes"] = [{"evidence_id": NEW, "quote": TEXT}]
    final["parameters"][0]["hypothesis_ids"] = ["M1A6-H002"]
    final["retrieval_requests"] = [_request("mitigant_challenge", hid="M1A6-H002")]
    script[A6] = [initial, second if second is not None else final]

    if challenge:
        script["challenge"] = [{"challenges": [{
            "challenge_id": "feedback-challenge", "target": "v3_compound",
            "issue_type": "ignored_counterevidence", "severity": "material",
            "reason": "Recheck the interaction against competitive evidence.",
            "affected_agent_ids": ["m1_competitive_durability"],
            "requires_reanalysis": True,
            "requested_rerun_scope": ["m1_competitive_durability"],
        }]}, {"challenges": [], "overall_outcome": "accepted"}]

    class InspectBackend(ScriptedBoBackend):
        def generate(self, request):
            if request.method == A6 and self.calls(A6) == 1:
                # The entire first-pass candidate must remain out of canonical
                # storage, even while the second model call is in flight.
                assert session.query(HypothesisRecordRow).filter_by(agent_id=A6).count() == 0
                assert session.query(ParameterResultRow).filter_by(agent_id=A6).count() == 0
            return super().generate(request)

    backend = InspectBackend(script)
    runner = BusinessOverviewRunner(session, backend=backend, bo_roster="v3", sector=None,
                                   data_root=tmp_path / "data", output_root=tmp_path / "out")
    if retriever:
        runner.retriever = retriever
    snapshot = h.evidence_snapshot({**h.PASSAGES, NEW: ("Item 1A", TEXT)})
    original = snapshot.model_dump(mode="json")
    result = runner.run_prepared(h.CASE, snapshot, h.metrics(), write_outputs=False)
    assert snapshot.model_dump(mode="json") == original
    if trace_path:
        trace_path.write_text(json.dumps({"simulation": True,
            "feedback": result.audit["resilience_retrieval_feedback"],
            "agent6_inputs": [i for m, i in backend.requests if m == A6],
            "orchestrator_inputs": [i for m, i in backend.requests if m == "orchestrate"],
            "stages": result.record["stages"]}, indent=2))
    return result, backend


def test_retrieves_reconsiders_validates_and_only_publishes_final(db_session, tmp_path):
    # This trace is the controlled end-to-end demonstration (no network/model).
    import os
    from pathlib import Path
    trace = Path(os.environ.get("A6_DEMO_TRACE", str(tmp_path / "agent6_feedback_trace.json")))
    result, backend = _run(db_session, tmp_path, requests=[_request()], trace_path=trace)
    assert backend.calls(A6) == 2
    calls = [i for m, i in backend.requests if m == A6]
    assert NEW not in {p["evidence_id"] for p in calls[0]["evidence"]}
    assert NEW in {p["evidence_id"] for p in calls[1]["evidence"]}
    assert calls[1]["primary_hypotheses"] == calls[0]["primary_hypotheses"]
    assert calls[1]["indicators"] == calls[0]["indicators"]
    assert "provisional" in calls[1]["retrieval_reconsideration"]["instruction"]
    added = next(p for p in calls[1]["evidence"] if p["evidence_id"] == NEW)
    assert added["document_id"] == "doc_10k" and added["section"] == "Item 1A"
    assert added["retrieved_for"] == ["contradict"]
    final = db_session.query(HypothesisRecordRow).filter_by(agent_id=A6).one()
    assert final.hypothesis_id == "M1A6-H002"
    assert final.payload["claim_type"] == "inference"
    parameter = db_session.query(ParameterResultRow).filter_by(agent_id=A6).one()
    assert parameter.agent_run_id == final.agent_run_id
    assert final.payload["contradicting_evidence_ids"] == [NEW]
    assert final.payload["component_hypothesis_ids"] == ["M1S4-H001", "M1S5-H001"]
    assert final.payload["evidence_quotes"][0]["evidence_id"] == NEW
    synth = next(i for m, i in backend.requests if m == "orchestrate")
    assert {x["hypothesis_id"] for x in synth["resilience_findings"]} == {"M1A6-H002"}
    assert all(x["hypothesis_id"] != "M1A6-H001" for x in synth["hypotheses"])
    feedback = result.audit["resilience_retrieval_feedback"][0]
    assert feedback["status"] == "reconsidered"
    assert feedback["outstanding_requests"]  # no third model pass
    attempts = db_session.query(RetrievalAttemptRow).filter_by(agent_id=A6, origin="agent_request").all()
    assert len(attempts) == 1
    assert attempts[0].payload["hypothesis_id"] == "M1A6-H001"
    rows = db_session.query(AgentRunRow).filter_by(agent_id=A6).order_by(AgentRunRow.created_at).all()
    assert rows[0].run_id in rows[1].parent_run_ids
    assert rows[1].rerun_reason == "resilience_retrieval_follow_up"
    packets = db_session.query(EvidencePacketRow).filter_by(agent_id=A6).all()
    assert any(NEW in p.evidence_ids for p in packets)
    audit = db_session.query(AuditEvent).filter_by(event_type="agent6_retrieval_feedback").all()
    assert [e.after["status"] for e in audit] == ["provisional", "reconsidering", "reconsidered"]
    assert audit[0].after["provisional_hypotheses"][0]["hypothesis_id"] == "M1A6-H001"
    assert all(backend.calls(a) == 1 for a in _script() if a.startswith("m1_") and a != A6)


@pytest.mark.parametrize("requests,status", [
    (None, "no_actionable_requests"),
    ([_request(terms=["nonexistentquux"])], "no_new_evidence"),
    ([_request(terms=["nursing shortage"])], "no_new_evidence"),
    ([_request(hid="unknown")], "no_actionable_requests"),
    ([_request(terms=["  "])], "no_actionable_requests"),
])
def test_single_pass_and_unresolved_gaps(db_session, tmp_path, requests, status):
    result, backend = _run(db_session, tmp_path, requests=requests)
    assert backend.calls(A6) == 1
    assert result.audit["resilience_retrieval_feedback"][0]["status"] == status
    assert db_session.query(HypothesisRecordRow).filter_by(agent_id=A6).one().hypothesis_id == "M1A6-H001"
    if requests:
        assert result.audit["resilience_retrieval_feedback"][0]["unresolved_requests"]


def test_overlap_deduplicated_opposite_purposes_preserved(db_session, tmp_path):
    result, backend = _run(db_session, tmp_path, requests=[
        _request("support"), _request("support", [" ZEBRA   scheduling "]), _request("contradict")])
    feedback = result.audit["resilience_retrieval_feedback"][0]
    assert [o["outcome"] for o in feedback["requests"]] == ["eligible", "duplicate", "eligible"]
    assert {s["purpose"] for s in feedback["searches"]} == {"support", "contradict"}
    assert backend.calls(A6) == 2


def test_retrieval_failure_withholds_provisional(db_session, tmp_path):
    class Broken(BoEvidenceRetriever):
        def follow_up(self, spec, *args, **kwargs):
            if spec.agent_id == A6:
                raise RuntimeError("synthetic retrieval failure")
            return super().follow_up(spec, *args, **kwargs)
    result, backend = _run(db_session, tmp_path, requests=[_request()], retriever=Broken())
    assert result.audit["resilience_retrieval_feedback"][0]["status"] == "retrieval_failed"
    assert result.record["agents"][A6]["status"] == "failed"
    assert db_session.query(HypothesisRecordRow).filter_by(agent_id=A6).count() == 0
    assert not next(i for m, i in backend.requests if m == "orchestrate")["resilience_findings"]


def test_invalid_reconsideration_withholds_initial(db_session, tmp_path):
    second = {"parameters": [], "hypotheses": [{"hypothesis_id": "M1A6-H002", "disposition": "invented"}]}
    result, backend = _run(db_session, tmp_path, requests=[_request()], second=second)
    assert result.audit["resilience_retrieval_feedback"][0]["status"] == "reconsideration_failed"
    assert db_session.query(HypothesisRecordRow).filter_by(agent_id=A6).count() == 0
    assert not next(i for m, i in backend.requests if m == "orchestrate")["resilience_findings"]


def test_disabled_followup_budget_recorded(db_session, tmp_path):
    result, backend = _run(db_session, tmp_path, requests=[_request()],
                          retriever=BoEvidenceRetriever(RetrievalBudget(max_followup_rounds=0)))
    assert backend.calls(A6) == 1
    assert result.audit["resilience_retrieval_feedback"][0]["status"] == "retrieval_disabled"


def test_challenger_keeps_bounded_feedback_and_append_only_history(db_session, tmp_path):
    result, backend = _run(db_session, tmp_path, requests=[_request()], challenge=True)
    assert backend.calls(A6) == 3  # first pass + reconsideration + challenger rerun
    feedback = result.audit["resilience_retrieval_feedback"]
    assert [f["status"] for f in feedback] == ["reconsidered", "no_new_evidence"]
    assert backend.calls("challenge") == 2
    rows = db_session.query(HypothesisRecordRow).filter_by(agent_id=A6).all()
    assert {r.acceptance_state for r in rows} == {"superseded", "accepted"}
    assert len([r for r in rows if r.acceptance_state == "accepted"]) == 1
    searches = db_session.query(RetrievalAttemptRow).filter_by(agent_id=A6, origin="agent_request").all()
    assert {a.payload["round_index"] for a in searches} == {1, 2}


def test_unsupported_final_fact_is_still_withheld_by_validator(db_session, tmp_path):
    final = copy.deepcopy(_script()[A6])
    item = final["hypotheses"][0]
    item.update(claim_type="fact", disposition="supported", supporting_evidence_ids=[],
                contradicting_evidence_ids=[], component_hypothesis_ids=[], evidence_quotes=[])
    result, backend = _run(db_session, tmp_path, requests=[_request()], second=final)
    synth = next(i for m, i in backend.requests if m == "orchestrate")
    assert not synth["resilience_findings"]
    records = db_session.query(HypothesisRecordRow).filter_by(agent_id=A6).all()
    assert records and all(r.admissibility == "audit_only" for r in records)
    assert result.audit["resilience_retrieval_feedback"][0]["final_validator_adjustments"]


def test_budget_cannot_enable_more_than_one_cycle(db_session, tmp_path):
    result, backend = _run(db_session, tmp_path, requests=[_request()],
                          retriever=BoEvidenceRetriever(RetrievalBudget(max_followup_rounds=8)))
    assert backend.calls(A6) == 2
    assert len(result.audit["resilience_retrieval_feedback"][0]["searches"]) == 1


def test_provider_parse_failure_never_releases_provisional(db_session, tmp_path):
    result, backend = _run(db_session, tmp_path, requests=[_request()], second="not valid JSON")
    assert result.audit["resilience_retrieval_feedback"][0]["status"] == "reconsideration_failed"
    assert db_session.query(HypothesisRecordRow).filter_by(agent_id=A6).count() == 0
    assert db_session.query(ParameterResultRow).filter_by(agent_id=A6).count() == 0
    assert not next(i for m, i in backend.requests if m == "orchestrate")["resilience_findings"]


@pytest.mark.parametrize("source", ["response_level", "hypothesis_level"])
def test_missing_information_alone_triggers_existing_feedback(db_session, tmp_path, source):
    result, backend = _run(db_session, tmp_path, gap_source=source)
    assert backend.calls(A6) == 2
    trace = result.audit["resilience_retrieval_feedback"][0]
    gap = next(g for g in trace["information_gaps"] if g["source"] == source and g["eligible"])
    assert gap["attempt_ids"] and gap["newly_admitted_evidence_ids"] == [NEW]
    assert gap["reconsideration_occurred"] and gap["unresolved"]
    assert trace["searches"][0]["request_origin"] == "missing_information_bridge"
    assert gap["hypothesis_id"] == (None if source == "response_level" else "M1A6-H001")
    synth = next(i for m, i in backend.requests if m == "orchestrate")
    assert {h["hypothesis_id"] for h in synth["resilience_findings"]} == {"M1A6-H002"}
    assert any(x["field"].startswith("agent6_information_gap:") for x in synth["data_limitations"])
    assert all(backend.calls(a) == 1 for a in _script() if a.startswith("m1_") and a != A6)


def test_explicit_request_wins_shared_budget_over_derived_gap(db_session, tmp_path):
    result, backend = _run(db_session, tmp_path, requests=[_request(terms=["nonexistentquux"])],
        gap_source="hypothesis_level", retriever=BoEvidenceRetriever(RetrievalBudget(max_followup_requests=1)))
    trace = result.audit["resilience_retrieval_feedback"][0]
    assert backend.calls(A6) == 1
    assert trace["information_gaps"][0]["outcome"] == "request_budget_exhausted"
    assert len(trace["searches"]) == 1 and trace["searches"][0]["request_origin"] == "agent_explicit"


def test_explicit_and_derived_same_need_share_one_search(db_session, tmp_path):
    result, backend = _run(db_session, tmp_path, requests=[_request("support", ["Zebra scheduling pilot"])], gap_source="hypothesis_level")
    trace = result.audit["resilience_retrieval_feedback"][0]
    assert len(trace["searches"]) == 1 and backend.calls(A6) == 2
    assert trace["information_gaps"][0]["outcome"] == "duplicate"
    assert trace["information_gaps"][0]["attempt_ids"] == [trace["searches"][0]["attempt_id"]]


def test_derived_search_without_hit_keeps_single_pass_and_gap(db_session, tmp_path):
    result, backend = _run(db_session, tmp_path, gap_source="hypothesis_level", gap_text="Quux milestone calendar disclosure.")
    trace = result.audit["resilience_retrieval_feedback"][0]
    assert backend.calls(A6) == 1 and trace["status"] == "no_new_evidence"
    assert trace["information_gaps"][0]["attempt_ids"]
    assert not trace["information_gaps"][0]["retrieved_evidence_ids"]
    assert trace["information_gaps"][0]["unresolved"]


def test_derived_retrieval_failure_preserves_existing_withholding(db_session, tmp_path):
    class Broken(BoEvidenceRetriever):
        def follow_up(self, spec, *args, **kwargs):
            if spec.agent_id == A6:
                raise RuntimeError("synthetic bridge search failure")
            return super().follow_up(spec, *args, **kwargs)
    result, _ = _run(db_session, tmp_path, gap_source="hypothesis_level", retriever=Broken())
    trace = result.audit["resilience_retrieval_feedback"][0]
    assert trace["status"] == "retrieval_failed" and trace["information_gaps"][0]["unresolved"]
    assert db_session.query(HypothesisRecordRow).filter_by(agent_id=A6).count() == 0
    assert db_session.query(ParameterResultRow).filter_by(agent_id=A6).count() == 0


def test_derived_reconsideration_failure_cannot_release_provisional(db_session, tmp_path):
    result, _ = _run(db_session, tmp_path, gap_source="hypothesis_level", second="not valid JSON")
    trace = result.audit["resilience_retrieval_feedback"][0]
    assert trace["status"] == "reconsideration_failed"
    assert trace["information_gaps"][0]["reconsideration_occurred"]
    assert db_session.query(HypothesisRecordRow).filter_by(agent_id=A6).count() == 0
