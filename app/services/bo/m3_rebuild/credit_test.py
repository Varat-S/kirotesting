"""One explicitly authorized management request through normal BO preparation."""
import argparse
from datetime import date, datetime, timezone
import json
from hashlib import sha256
from pathlib import Path

from sqlalchemy import select

from app.core.config import Settings
from app.models.base import create_engine_and_session, init_db
from app.models.orm import AgentRunRow, EvidencePacketRow, ModelRun, Snapshot
from app.schemas.evidence import EntityRecord
from app.services.bo.runner import BusinessOverviewRunner, M3_PACKET_AGENT_ID
from app.services.llm.client import LLMBackend, FakeLLMBackend
from app.services.llm.providers_vertex import VertexProviderBackend
from app.services.pipeline.package import Module3Input, SourceInput, SourcePackage
from .contracts import provider_schema
from .provider import AGENT_ID
from .workflow import write_json

MODEL = 'gemini-3.5-flash-lite'
EXPECTED = ['Samuel N. Hazen', 'Jennifer L. Berres', 'Michael S. Cuffe, M.D.',
            'Jon M. Foster', 'Michael A. Marks', 'Michael R. McAlevey', 'Sammie S. Mosier']


class OneRequestBackend(LLMBackend):
    """Persistent reservation prevents a second generation, including after failure."""

    renders_vertex_prompt = True
    model_id = MODEL

    def __init__(self, backend, ledger_path, prices, *, agent_id=AGENT_ID,
                 authorization='Milestone 3: one HCA management request'):
        self.backend, self.path, self.prices = backend, Path(ledger_path), prices
        self.agent_id,self.authorization=agent_id,authorization
        self.attempt_count = 0

    def generate(self, request):
        if request.prompt.name != self.agent_id or request.model_tier != 'narrow':
            raise RuntimeError('Only the specifically authorized M3 agent may use this budget')
        if self.backend.model_for_tier('narrow') != MODEL:
            raise RuntimeError('Requested model differs from authorized model; no substitution')
        if self.path.exists() or self.attempt_count:
            raise RuntimeError('One-request authorization exhausted; no retry')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        ledger = {'authorization':self.authorization,'agent_id':self.agent_id,
                  'requests':1, 'model':MODEL, 'state':'reserved', 'sdk_attempts':1,
                  'token_usage':None, 'estimated_cost_usd':None}
        # Exclusive creation precedes provider I/O, including interruption paths.
        with self.path.open('x', encoding='utf-8') as stream:
            json.dump(ledger, stream, indent=2)
        self.attempt_count = 1
        try:
            if hasattr(self.backend,'build_request_payload'):
                payload=self.backend.build_request_payload(request)
                if payload['config'].get('http_options',{}).get('retry_options',{}).get('attempts')!=1:
                    raise RuntimeError('Paid retries must be disabled')
                write_json(self.path.parent/'vertex_exact_request.json',payload)
                (self.path.parent/'vertex_rendered_prompt.txt').write_text(
                    payload['config']['system_instruction']+'\n\n'+payload['contents'],encoding='utf-8')
            result = self.backend.generate(request)
            usage = {k:result.model_config[k] for k in
                     ('input_tokens','output_tokens','thinking_tokens','total_tokens')
                     if k in result.model_config}
            ledger.update(state='answer_returned',token_usage=usage or None,
                          response_model_version=result.model_config.get('response_model_version'),
                          http_status=result.model_config.get('http_status'))
            rate=self.prices.get(MODEL)
            if rate and 'input_tokens' in usage and 'output_tokens' in usage:
                ledger['estimated_cost_usd']=(usage['input_tokens']*rate['input']+
                    (usage['output_tokens']+usage.get('thinking_tokens',0))*rate['output'])/1_000_000
                ledger['price_basis']='User-supplied USD/million tokens; output includes reported thinking tokens; not an invoice'
                ledger['rates']=rate
            return result
        except Exception as exc:
            ledger.update(state='failed',error_type=type(exc).__name__,
                          detail=getattr(exc,'public_message','Provider failure; no retry'))
            raise
        finally:
            write_json(self.path, ledger)


def hca_package(filing, corporate):
    available=datetime(2025,3,1,tzinfo=timezone.utc)
    return SourcePackage(
        as_of_date=date(2024,12,31), evidence_cutoff_timestamp=available,
        borrower_entity_id='HCA',
        entities=[EntityRecord(entity_id='HCA',legal_name='HCA Healthcare',
                               entity_type='borrower',borrower_flag=True,
                               expected_consolidation_scope='consolidated')],
        sources=[SourceInput(filename=Path(corporate).name,path=str(Path(corporate).resolve()),
                            parser='structured_json',available_at=available,
                            entity_id='HCA',scope='consolidated',accounting_basis='not_applicable',
                            notes=['Submitted simulation, not independently verified legal evidence.']),
                 SourceInput(filename=Path(filing).name,path=str(Path(filing).resolve()),
                             parser='pdf_text',available_at=available,
                             entity_id='HCA',scope='consolidated',accounting_basis='GAAP',
                             parse_options={'enable_ocr':False},
                             notes=['Locally supplied selected FY2024 10-K; availability at cutoff is declared for this test, not verified SEC publication metadata.'])],
        module3=Module3Input(corporate_source=Path(corporate).name,
                             management_source=Path(filing).name,
                             company='HCA Healthcare',filing_period='2024-12-31'),
    )


def run_test(directory, filing, corporate, *, authorized=False, settings=None, saved_response=None,
             review_fixture=None, authorized_reviewer=False, review_corrections=None):
    directory=Path(directory).resolve()
    directory.mkdir(parents=True,exist_ok=False)
    settings=settings or Settings()
    if authorized and saved_response is not None:
        raise ValueError('Choose one authorized request or an offline saved response')
    if authorized_reviewer and (authorized or review_fixture is not None or saved_response is None):
        raise ValueError('Live Validator 7 requires only the saved specialist response and no offline review fixture')
    if authorized_reviewer:
        expected='31979bba87dabe512dbe308fe3f5034ef66286b1beffe9dea747cea5df768c68'
        if sha256(Path(saved_response).read_bytes()).hexdigest()!=expected:
            raise ValueError('Live review must use the unchanged Milestone 3 HCA response')
    if authorized or authorized_reviewer:
        if settings.vertex_model_narrow != MODEL or settings.llm_provider != 'vertex':
            raise ValueError('Set the exact authorized Vertex model; no substitution')
        if settings.vertex_api_key and settings.vertex_api_key.get_secret_value():
            raise ValueError('This test requires existing ADC and an empty VERTEX_API_KEY')
        import google.auth
        credentials,_=google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
        write_json(directory/'auth_preflight.json',{
            'authentication':'Application Default Credentials',
            'credential_type':type(credentials).__name__, 'api_key_empty':True,
            'project':settings.google_cloud_project,'location':settings.google_cloud_location,
            'model':MODEL,'model_accessibility':'Verified by the sole authorized request; no separate generation probe',
        })
    package=hca_package(filing,corporate)
    write_json(directory/'package.json',package.model_dump(mode='json'))
    engine,factory=create_engine_and_session(f"sqlite:///{directory/'case.db'}")
    init_db(engine)
    with factory() as session:
        backend=(OneRequestBackend(
            VertexProviderBackend(settings,schema_transform=provider_schema),
            directory/'request_ledger.json',settings.llm_prices,
        ) if authorized else None)
        if saved_response is not None:
            backend=FakeLLMBackend('saved-gemini-response-offline-replay')
            backend.register('analyze',Path(saved_response).read_text(encoding='utf-8'))
        reviewer_backend=None
        if review_fixture is not None:
            reviewer_backend=FakeLLMBackend('saved-validator7-response-offline-replay')
            reviewer_backend.register('analyze',Path(review_fixture).read_text(encoding='utf-8'))
        if authorized_reviewer:
            reviewer_backend=OneRequestBackend(
                VertexProviderBackend(settings,schema_transform=provider_schema),
                directory/'request_ledger.json',settings.llm_prices,
                agent_id='m3_validator_7',authorization='Milestone 4 verification: exactly one live HCA Validator 7 request; specialist and other agents offline')
        runner=BusinessOverviewRunner(
            session,bo_roster='v3',data_root=directory/'data',output_root=directory/'output',
            m3_backend=backend,m3_reviewer_backend=reviewer_backend,max_rerun_rounds=0,
            m3_review_corrections=review_corrections,
        )
        # The ordinary BO backend is offline. At most one explicitly selected M3 backend can reach Vertex.
        result=runner.run_case('HCA_M3_CREDIT_2024',package=package,write_outputs=True)
        session.commit()
        snapshots=session.scalars(select(Snapshot).where(
            Snapshot.case_id==result.case_id,Snapshot.snapshot_type=='canonical_evidence')).all()
        write_json(directory/'canonical_snapshot.json',snapshots[-1].payload)
        packet=session.scalars(select(EvidencePacketRow).where(
            EvidencePacketRow.analysis_run_id==result.analysis_run_id,
            EvidencePacketRow.agent_id==M3_PACKET_AGENT_ID)).one()
        write_json(directory/'m3_packet.json',packet.payload)
        from .sources import sources_from_evidence_packet
        sources=sources_from_evidence_packet(packet.payload)
        write_json(directory/'source_preflight.json',{
            'roster':sources.expected_roster,'coverage_verifiable':sources.coverage_verifiable,
            'canonical_section_ids':sources.section_ids,'physical_pages':sources.physical_pages,
            'expected_seven_executives':EXPECTED,
        })
        manifests=list((directory/'output/m3_rebuild'/result.case_id).glob('*/m3_rebuild_manifest.json'))
        manifest=json.loads(manifests[-1].read_text())
        component=manifest['components']['management']
        artifact=(json.loads((manifests[-1].parent/component['artifact']).read_text())
                  if component['artifact'] else None)
        profiles=[p['name'] for p in artifact['profiles']] if artifact else []
        models=session.scalars(select(ModelRun).where(ModelRun.prompt_name==AGENT_ID)).all()
        agents=session.scalars(select(AgentRunRow).where(AgentRunRow.agent_id==AGENT_ID)).all()
        reviewer_models=session.scalars(select(ModelRun).where(ModelRun.prompt_name=='m3_validator_7')).all()
        for model in reviewer_models:
            write_json(directory/'validator7_model_run.json',{
                'run_id':model.run_id,'model_id':model.model_id,'prompt_id':model.prompt_id,
                'model_config':model.model_config_json,'validation_outcome':model.validation_outcome,
                'validation_detail':model.validation_detail,'evidence_ids':model.evidence_ids})
            try:
                parsed=json.loads(model.raw_response)
            except json.JSONDecodeError:
                parsed={'parsing':'failed','raw_response_file':'validator7_response.txt'}
            write_json(directory/'validator7_parsed_response.json',parsed)
        summary={'analysis_run_id':result.analysis_run_id,'management':component,
                 'manifest':str(manifests[-1]),'expected_seven_executives':EXPECTED,
                 'admitted_profiles':profiles,'all_seven_admitted':set(profiles)==set(EXPECTED),
                 'shared_model_runs':len(models),'shared_agent_runs':len(agents),
                 'validator7_runs':len(session.scalars(select(AgentRunRow).where(
                     AgentRunRow.agent_id=='m3_validator_7')).all()),
                 'request_count':getattr(backend,'attempt_count',0)+getattr(reviewer_backend,'attempt_count',0),
                 'specialist_paid_requests':getattr(backend,'attempt_count',0),
                 'reviewer_paid_requests':getattr(reviewer_backend,'attempt_count',0),
                 'other_modules':'offline empty responses; no financial or business conclusions asserted',
                 'limitations':['Availability timestamp is declared at test cutoff, not independently verified SEC metadata.',
                                'A single live review is not a general performance evaluation; downstream revision remains unverified.' if authorized_reviewer else
                                'Validator 7 is replayed offline when supplied; provenance and explicit corrections are recorded separately; downstream revision remains unverified.']}
        write_json(directory/'summary.json',summary)
    engine.dispose()
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--filing',type=Path,required=True)
    parser.add_argument('--corporate',type=Path,required=True)
    parser.add_argument('--authorize-one-paid-request',action='store_true')
    parser.add_argument('--saved-response',type=Path)
    parser.add_argument('--review-fixture',type=Path)
    parser.add_argument('--authorize-one-paid-review',action='store_true')
    parser.add_argument('--review-corrections',type=Path)
    args=parser.parse_args()
    summary=run_test(args.directory,args.filing,args.corporate,authorized=args.authorize_one_paid_request,
                     saved_response=args.saved_response,review_fixture=args.review_fixture,
                     authorized_reviewer=args.authorize_one_paid_review,
                     review_corrections=json.loads(args.review_corrections.read_text()) if args.review_corrections else None)
    print(json.dumps(summary,indent=2))


if __name__=='__main__':
    main()
