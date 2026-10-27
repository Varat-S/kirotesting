"""Milestone 3: shared BO generation, model judgments and fragmented evidence."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.models.orm import AgentRunRow, ModelRun, EvidencePacketRow
from app.services.bo.offline import ScriptedBoBackend
from app.services.bo.runner import BusinessOverviewRunner, M3_PACKET_AGENT_ID
from app.services.bo.m3_rebuild.contracts import provider_schema
from .test_management import assess_management
from .review_fixtures import FixtureReviewerBackend
from app.services.bo.m3_rebuild.provider import AGENT_ID, PROMPT
from app.services.bo.m3_rebuild.sources import sources_from_evidence_packet
from app.services.llm.client import LLMBackend, LLMRawResult
from app.services.llm.providers_vertex import VertexProviderBackend
from tests.integration.test_bo_v3_complete import _script
from .test_shared_input_integration import _package, OfflinePacketExtractor
from .test_management import synthetic


def response_for(packet):
    sources = sources_from_evidence_packet(packet)
    payload = json.loads(OfflinePacketExtractor().extract(sources, {}))
    for judgment in payload['judgments']:
        judgment.update(signal='neutral', explanation='The disclosed appointment and internal history establish responsibilities, but provide no realized credit outcome.')
    payload['overall_assessment'].update(signal='neutral', explanation='Biography evidence supports continuity of responsibilities; financial execution remains unestablished.')
    return payload


def test_normal_bo_shared_vertex_task_and_exact_audit_are_offline(db_session, tmp_path, monkeypatch):
    calls = []
    class Models:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                text=json.dumps(response_for(json.loads(kwargs['contents']))),
                sdk_http_response=SimpleNamespace(status_code=200),
                usage_metadata=SimpleNamespace(prompt_token_count=123, candidates_token_count=456,
                                               thoughts_token_count=7, total_token_count=586))
    backend = VertexProviderBackend(Settings(
        llm_provider='vertex', google_cloud_project='test', google_cloud_location='global',
        vertex_model_narrow='gemini-3.5-flash-lite', llm_max_output_tokens=35000,
    ), client_factory=lambda: SimpleNamespace(models=Models()), schema_transform=provider_schema)
    from app.services.bo.m3_rebuild import workflow
    monkeypatch.setattr(workflow, 'locate_sources', lambda *args: pytest.fail('PDF reopened during M3 execution'))
    result = BusinessOverviewRunner(
        db_session, backend=ScriptedBoBackend(_script()), bo_roster='v3', m3_version='v2',
        data_root=tmp_path/'data', output_root=tmp_path/'out', m3_backend=backend,
        m3_reviewer_backend=FixtureReviewerBackend(),
    ).run_case('CREDIT_M3', package=_package(), write_outputs=False)
    assert len(calls) == 1
    request = calls[0]
    assert request['model'] == 'gemini-3.5-flash-lite'
    assert request['config']['max_output_tokens'] == 35000
    assert request['config']['http_options']['retry_options']['attempts'] == 1
    packet = db_session.scalars(select(EvidencePacketRow).where(
        EvidencePacketRow.agent_id == M3_PACKET_AGENT_ID,
        EvidencePacketRow.analysis_run_id == result.analysis_run_id,
    )).one()
    assert json.loads(request['contents']) == packet.payload
    model = db_session.scalars(select(ModelRun).where(ModelRun.prompt_name == AGENT_ID)).one()
    agent = db_session.scalars(select(AgentRunRow).where(AgentRunRow.agent_id == AGENT_ID)).one()
    assert agent.input_hash == packet.packet_hash
    assert model.raw_response == agent.raw_response
    assert agent.validation_status == 'valid'
    assert model.model_config_json['thinking_tokens'] == 7
    block = result.handoff['module_conclusions']['M3']
    artifact = block['m3_rebuild']['management']['artifact']
    # Recent tenure must not replace a neutral model judgment with a negative.
    assert artifact['profiles'][0]['recency'] == 'recent'
    assert artifact['profiles'][0]['tenure']['minimum_days'] > 0
    assert {o['signal'] for o in artifact['observations']} == {'neutral'}
    assert artifact['overall_assessment']['outcome'] == 'neutral'
    from pathlib import Path
    folder = Path(block['source_manifest']).parent
    rendered = (folder/'management_rendered_prompt.txt').read_text(encoding="utf-8")
    assert rendered == request['config']['system_instruction']+'\n\n'+request['contents']
    assert (folder/'management_response.txt').read_text(encoding="utf-8") == model.raw_response
    assert json.loads((folder/'management_validation.json').read_text(encoding="utf-8"))['evidence'] == 'complete'


@pytest.mark.parametrize('mutation', ['quote', 'fact_reference', 'fact_statement', 'overall_reference', 'competence'])
def test_unfounded_facts_or_judgments_do_not_pass(mutation):
    payload, sources = synthetic()
    if mutation == 'quote': payload['facts'][0]['citations'][0]['quote'] = 'Invented source quotation'
    if mutation == 'fact_statement': payload['facts'][0]['statement'] = 'Management delivered superior cash returns'
    if mutation == 'fact_reference': payload['judgments'][0]['supporting_fact_ids'] = ['not-admitted']
    if mutation == 'overall_reference': payload['overall_assessment']['supporting_judgment_ids'] = ['not-admitted']
    if mutation == 'competence':
        payload['judgments'][0]['explanation'] = 'Long tenure proves management competence.'
    artifact = assess_management(payload, sources)
    assert artifact['completion_status'] == 'partial'


def test_adjacent_chunks_keep_individual_ids_and_support_split_appointment():
    payload, original = synthetic()
    texts = [original.pages['pdf:p1'], original.pages['pdf:p2']]
    split = texts[1].index('effective May')
    passages = [dict(evidence_id='cover', document_id='doc', page=1, location=0, text=texts[0]),
                dict(evidence_id='part-a', document_id='doc', page=2, location=0, text=texts[1][:split]),
                dict(evidence_id='part-b', document_id='doc', page=2, location=split, text=texts[1][split:])]
    packet = dict(context={'company':original.company,'filing_period':original.filing_period,
                          'management_document_id':'doc','management_document_sha256':'digest'},
                  narrative_evidence=passages, evidence_ids=['cover','part-a','part-b'])
    sources = sources_from_evidence_packet(packet)
    assert sources.section_ids == ['part-a','part-b']
    assert sources.coverage_verifiable
    executive = payload['executives'][0]
    executive.update(source_id='part-a', quote=texts[1][:split].split('Alex Sample was appointed')[1])
    executive['quote'] = 'Alex Sample was appointed'+executive['quote']
    executive['supporting_citations'] = [{'source_id':'part-b','quote':texts[1][split:]}]
    role = executive['prior_internal_roles'][0]
    role.update(source_id='part-b', quote=executive['supporting_citations'][0]['quote'].split('Alex Sample previously')[1])
    role['quote']='Alex Sample previously'+role['quote']
    payload.update(roster_source_id='part-a')
    payload['facts'][0].update(statement=executive['quote'], citations=[{'source_id':'part-a','quote':executive['quote']}])
    artifact = assess_management(payload, sources)
    assert artifact['completion_status'] == 'complete'
    assert {c['source_id'] for c in artifact['citations']} == {'part-a','part-b'}


def test_unrelated_as_of_date_in_canonical_chunk_is_not_an_executive_roster():
    payload, sources = synthetic()
    sources.pages['pdf:p2'] += '\nItem 1A. Risk Factors\nAs of December 31, 2024, total debt was disclosed.'
    result = assess_management(payload, sources)
    assert result['completion_status'] == 'complete'
    sources.pages['pdf:p2'] += '\nAs of March 1, 2025, our executive officers were as follows:'
    result=assess_management(payload,sources)
    roster=result['validation']['claim_results'][0]
    assert roster['outcome']=='unsupported' and 'consistent roster date' in roster['reason']
    assert result['facts']  # Independent source claims survive an invalid roster date.
