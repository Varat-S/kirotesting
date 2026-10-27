"""Two independent components, production CLI/BO/registry/viewer/downstream boundaries."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.services.bo.m3_rebuild import workflow
from app.services.bo.m3_rebuild.workflow import run_rebuild
from app.services.bo.m3_rebuild.adapters import synthesis_block
from app.services.bo.m3_rebuild.contracts import provider_schema
from app.services.bo.m3_rebuild.provider import VertexExtractor
from app.services.registry.indexer import build_manifest
from app.services.registry.graph import build_pipeline_graph
from .test_management import hca, synthetic
from .test_structure import fixture
from .review_fixtures import FixtureReviewer


@pytest.fixture
def selected_filing(monkeypatch):
    payload,sources=hca()
    monkeypatch.setattr(workflow,'locate_sources',lambda *args:sources)
    return payload,sources


def run(tmp_path, selected_filing, **kwargs):
    payload,s=selected_filing
    directory=tmp_path/'m3_rebuild'/'case'/'run_offline'
    inputs=dict(case_id='case',structure_input=fixture(),filing_pdf=Path('selected.pdf'),company=s.company,
                filing_period=s.filing_period,extraction=payload,reviewer=FixtureReviewer())
    inputs.update(kwargs)
    return directory,run_rebuild(directory,**inputs)


@pytest.mark.parametrize('broken',['management','structure'])
def test_independent_success_when_other_component_fails(tmp_path,selected_filing,broken):
    d,m=run(tmp_path,selected_filing,**({'extraction':{'bad':'shape'}} if broken=='management' else {'structure_input':{'bad':'shape'}}))
    other='structure' if broken=='management' else 'management'
    assert m['components'][broken]['status']=='failed'
    assert m['components'][other]['status']=='complete'
    assert (d/m['components'][other]['artifact']).is_file()
    block=synthesis_block(d/'m3_rebuild_manifest.json',case_id='case')
    assert block['m3_rebuild'][other]['artifact']
    assert block['m3_rebuild'][broken]['artifact'] is None
    assert block['findings']==[]


def test_model_request_is_not_attempted_for_saved_extraction(tmp_path,selected_filing):
    d,m=run(tmp_path,selected_filing)
    diag=json.loads((d/'management_diagnostics.json').read_text(encoding="utf-8"))
    assert diag['request_attempted'] is False and diag['answer_returned'] is False
    assert diag['http_status'] is None and diag['token_usage'] is None and diag['model'] is None
    assert diag['parsing']=='passed' and diag['schema_validation']=='passed'
    assert diag['extraction_mode']=='saved_offline_extraction'
    with pytest.raises(FileExistsError):run(tmp_path,selected_filing)


def test_registry_auto_discovery_graph_and_viewer(tmp_path,selected_filing):
    from app.services.registry.explorer import discover_cases, load_artifact_bytes, manifest_for_case
    d,m=run(tmp_path,selected_filing)
    manifest=build_manifest(tmp_path,'case',repo_root=tmp_path)
    assert len(manifest.artifacts)==2
    assert {a.status.value for a in manifest.artifacts}=={'validated'}
    assert {a.producer_agent for a in manifest.artifacts}=={'m3_structure_conversion','m3_management_assessment'}
    assert all(a.provenance.snapshot_equivalence.value=='unverified' for a in manifest.artifacts)
    graph=build_pipeline_graph(manifest)
    rebuild_nodes=[n for n in graph['nodes'] if n['id'].startswith('artifact.m3.rebuild')]
    assert len(rebuild_nodes)==2 and all(n['status']=='validated' for n in rebuild_nodes)
    ids={n['id'] for n in graph['nodes']}
    assert all(e['source'] in ids and e['target'] in ids for e in graph['edges'])
    assert any(c.case_id=='case' for c in discover_cases(tmp_path,repo_root=tmp_path,include_demo=False))
    assert len(manifest_for_case('case',tmp_path,repo_root=tmp_path).artifacts)==2
    raw,_,_=load_artifact_bytes('case',manifest.artifacts[0].artifact_id,tmp_path,repo_root=tmp_path)
    assert json.loads(raw)['companies']['HCA']['nodes']


def test_tamper_and_case_mismatch_rejected(tmp_path,selected_filing):
    d,m=run(tmp_path,selected_filing)
    with pytest.raises(ValueError,match='different case'):synthesis_block(d/'m3_rebuild_manifest.json',case_id='other')
    artifact=d/m['components']['management']['artifact']
    artifact.write_text('{}')
    block=synthesis_block(d/'m3_rebuild_manifest.json')
    assert block['m3_rebuild']['management']['status']=='failed'
    assert 'integrity' in block['m3_rebuild']['management']['error']
    assert block['m3_rebuild']['structure']['status']=='complete'


def test_malformed_model_answer_records_attempt_separately(tmp_path,selected_filing):
    class Extractor:
        def extract(self,sources,diag):
            diag.update(request_attempted=True,http_status=200,answer_returned=True,model='mock')
            return 'not json'
    d,m=run(tmp_path,selected_filing,extraction=None,extractor=Extractor())
    diag=json.loads((d/'management_diagnostics.json').read_text(encoding="utf-8"))
    assert diag['request_attempted'] and diag['http_status']==200 and diag['answer_returned']
    assert diag['error_stage']=='parsing' and diag['parsing']=='failed'
    assert m['components']['management']['status']=='failed'
    assert m['components']['structure']['status']=='complete'


def test_paid_call_gate_fails_before_client_creation():
    p,s=synthetic();diag=workflow.diagnostics()
    with pytest.raises(PermissionError):VertexExtractor().extract(s,diag)
    assert not diag['request_attempted']


def mock_vertex(response=None,error=None):
    from app.core.config import Settings
    from app.services.llm.providers_vertex import VertexProviderBackend
    calls=[]
    class Models:
        def generate_content(self,**kwargs):
            calls.append(kwargs)
            if error:raise error
            return response
    settings=Settings(_env_file=None,llm_provider='vertex',vertex_model_narrow='gemini-mock',
                      google_cloud_project='synthetic-project',google_cloud_location='global')
    backend=VertexProviderBackend(settings,client_factory=lambda:SimpleNamespace(models=Models()),schema_transform=provider_schema)
    return backend,calls


def shared_request(db_session):
    from app.prompts.registry import PromptRegistry
    from app.services.llm.client import LLMRequest
    from app.services.bo.m3_rebuild.provider import AGENT_ID, PROMPT
    from app.services.bo.m3_rebuild.contracts import EXTRACTION_SCHEMA
    prompt = PromptRegistry(db_session).register(AGENT_ID, template=PROMPT, response_schema=EXTRACTION_SCHEMA)
    return LLMRequest(method='analyze', prompt=prompt, inputs={}, model_tier='narrow')


def test_mocked_provider_uses_derived_schema_and_real_available_telemetry(db_session):
    p,s=synthetic()
    response=SimpleNamespace(text=json.dumps(p),sdk_http_response=SimpleNamespace(status_code=200),
                             usage_metadata=SimpleNamespace(prompt_token_count=10,candidates_token_count=20,total_token_count=30))
    backend,calls=mock_vertex(response)
    result=backend.generate(shared_request(db_session))
    assert json.loads(result.raw_response)==p
    assert len(calls)==1 and calls[0]['config']['response_json_schema']==provider_schema()
    assert result.model_config['request_attempted'] and result.model_config['http_status']==200
    assert result.model_config['total_tokens']==30


def test_mock_http_rejection_is_not_analytical_failure_or_retried(db_session):
    class Rejection(Exception):code=400
    backend,calls=mock_vertex(error=Rejection('private SDK body'))
    with pytest.raises(RuntimeError,match='not retried'):
        backend.generate(shared_request(db_session))
    assert len(calls)==1
    assert calls[0]['config']['http_options']['retry_options']['attempts']==1


def test_missing_configuration_is_not_attempted():
    from app.core.config import Settings
    from app.services.llm.providers_vertex import VertexProviderBackend
    backend=VertexProviderBackend(Settings(_env_file=None,llm_provider='fake'))
    _,s=synthetic();diag=workflow.diagnostics()
    with pytest.raises(Exception):VertexExtractor(authorized=True,backend=backend).extract(s,diag)
    assert diag['request_attempted'] is False


def test_normal_bo_rejects_prebuilt_manifest_side_path(db_session,tmp_path,selected_filing):
    from app.services.bo.runner import BusinessOverviewRunner
    from app.services.bo.offline import ScriptedBoBackend
    from tests.integration.test_bo_v3_complete import _script
    d,_=run(tmp_path,selected_filing)
    with pytest.raises(ValueError,match='private inputs are unsupported'):
        BusinessOverviewRunner(
            db_session,backend=ScriptedBoBackend(_script()),bo_roster='v3', m3_version='v2',
            data_root=tmp_path/'data',output_root=tmp_path/'out',
            m3_manifest=d/'m3_rebuild_manifest.json',
        )


def test_downstream_so_preserves_independent_contracts(tmp_path,selected_filing):
    from tests.integration.test_structural_overview_thin_slice import _handoff,_rehash
    from app.services.structural_overview.adapter import adapt_business_overview
    d,m=run(tmp_path,selected_filing)
    handoff=_handoff('v2');handoff['borrower_name']='HCA Healthcare, Inc.';handoff['as_of_date']='2024-12-31'
    handoff['module_conclusions']['M3']=synthesis_block(d/'m3_rebuild_manifest.json')
    assessment=adapt_business_overview(_rehash(handoff))
    assert assessment.m3_rebuild['structure']['artifact']['companies']
    assert assessment.m3_rebuild['management']['artifact']['profiles']
    assert assessment.corporate_structure.source_status=='unavailable'
    assert assessment.corporate_structure.source_status=='unavailable'


def test_main_cli_selects_replacement_and_preserves_legacy_defaults(tmp_path,capsys):
    from app.cli import main
    with pytest.raises(SystemExit) as ex:
        main(['run-m3-v2','case','--structure',str(Path(__file__).resolve().parents[2]/'examples/m3_rebuild/hca_input.json'),'--output',str(tmp_path)])
    assert ex.value.code==0
    assert 'm3-rebuild' in capsys.readouterr().out
    assert list((tmp_path/'m3_rebuild'/'case').glob('*/m3_rebuild_manifest.json'))


@pytest.mark.parametrize('mismatch',['company','period','cutoff'])
def test_consumers_reject_inconsistent_context(tmp_path,selected_filing,mismatch):
    from app.services.bo.m3_rebuild.adapters import validate_context
    d,_=run(tmp_path,selected_filing)
    block=synthesis_block(d/'m3_rebuild_manifest.json')
    kwargs={'company':'HCA Healthcare, Inc.','filing_period':'2024-12-31','cutoff':'2025-03-01'}
    kwargs[{'company':'company','period':'filing_period','cutoff':'cutoff'}[mismatch]]={'company':'Other Company','period':'2025-12-31','cutoff':'2025-01-01'}[mismatch]
    with pytest.raises(ValueError):validate_context(block,**kwargs)


def test_http200_without_answer_or_usage_is_observable(db_session):
    backend,calls=mock_vertex(SimpleNamespace(text='',usage_metadata=None))
    with pytest.raises(RuntimeError,match='no structured output'):
        backend.generate(shared_request(db_session))
    assert len(calls)==1


def test_structure_schema_failure_has_actionable_stage(tmp_path,selected_filing):
    d,m=run(tmp_path,selected_filing,structure_input={})
    diag=json.loads((d/'structure_diagnostics.json').read_text(encoding="utf-8"))
    assert diag['error_stage']=='schema_validation' and diag['schema_validation']=='failed'
    assert diag['adapter_version']=='deterministic-structure-1.0'
    assert not diag['request_attempted']


def test_saved_schema_failure_cannot_hide_other_component(tmp_path,selected_filing):
    from hashlib import sha256
    d,m=run(tmp_path,selected_filing)
    item=m['components']['management'];file=d/item['artifact']
    payload=json.loads(file.read_text(encoding="utf-8"));payload['observations'][0]['direction']='mixed'
    file.write_text(json.dumps(payload));item['artifact_sha256']=sha256(file.read_bytes()).hexdigest()
    (d/'m3_rebuild_manifest.json').write_text(json.dumps(m))
    block=synthesis_block(d/'m3_rebuild_manifest.json')
    assert block['m3_rebuild']['management']['status']=='failed'
    assert 'ValidationError' in block['m3_rebuild']['management']['error']
    assert block['m3_rebuild']['structure']['status']=='complete'
    entries=build_manifest(tmp_path,'case',repo_root=tmp_path).artifacts
    assert {e.producer_agent:e.status.value for e in entries}=={'m3_structure_conversion':'validated','m3_management_assessment':'failed'}


def test_synthetic_probe_is_gated_before_provider(monkeypatch):
    from app.services.bo.m3_rebuild.probe import main
    monkeypatch.setattr(VertexExtractor,'extract',lambda *args:pytest.fail('unexpected paid probe request'))
    with pytest.raises(SystemExit) as exc:main([])
    assert exc.value.code==2
