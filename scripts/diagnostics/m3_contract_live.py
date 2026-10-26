"""Budgeted contract-alignment diagnostics: at most 3 generations / 2 HCA runs.

No retries or fallbacks. A durable SQLite ledger reserves an attempt immediately
before the sole SDK generation. Offline preflight is the default. Each selected
case has exclusive, fresh private stores and cannot be rerun.
"""
import contextlib
import io
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

from app.core.hashing import content_hash
from app.services.bo.m3_contract import generation_schema, compact_generation_schema, provider_generation_schema, DRAFT_ADAPTER_VERSION

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "data/m3_contract_alignment_attempts_20261026.db"
CASES = {'probe': 'M3_CONTRACT_SCHEMA_PROBE_20261026_01',
         'hca1': 'HCA_DANIEL_M3_ALIGNED_20261026_01',
         'hca2': 'HCA_DANIEL_M3_ALIGNED_20261026_02'}


def reserve_attempt(kind, case, schema_hash):
    connection = sqlite3.connect(LEDGER)
    LEDGER.chmod(0o600)
    connection.execute('CREATE TABLE IF NOT EXISTS attempts (case_id TEXT PRIMARY KEY, kind TEXT, schema_hash TEXT, summary TEXT)')
    connection.execute('BEGIN IMMEDIATE')
    rows = list(connection.execute('SELECT case_id,kind FROM attempts'))
    if len(rows) >= 3 or (kind != 'probe' and sum(k != 'probe' for _, k in rows) >= 2):
        connection.close()
        raise RuntimeError('Authorised generation budget exhausted.')
    connection.execute('INSERT INTO attempts VALUES (?,?,?,NULL)', (case, kind, schema_hash))
    connection.commit()
    connection.close()


def main():
    args = sys.argv[1:]
    if not args or args[0] not in CASES or args[1:] not in ([], ['--execute-approved']):
        raise SystemExit('Choose probe/hca1/hca2; default is offline. Execution requires --execute-approved.')
    kind = args[0];case = CASES[kind]
    database = ROOT / ('data/' + case.lower() + '.db')
    transport = ROOT / ('data/' + case.lower() + '_transport.db')
    output = ROOT / 'output/bo_m3/contract_alignment' / case
    for p in [database, transport, output, *(Path(str(p)+suffix) for p in (database,transport) for suffix in ('-wal','-shm','-journal'))]:
        if p.exists(): raise SystemExit('Case storage already exists; refusing overwrite or another generation.')
    schema = provider_generation_schema(compact_generation_schema());schema_hash = content_hash(schema)
    if kind == 'probe':
        probe_plan = json.loads((ROOT/'examples/bo_m3/contract_alignment/probe_plan.json').read_text())
        if probe_plan['schema_sha256'] != schema_hash:
            raise SystemExit('Historical probe plan does not match the active schema; no storage or generation attempted.')
    import jsonschema
    jsonschema.Draft202012Validator.check_schema(schema)
    if len(args) == 1:
        print(json.dumps({'offline_preflight': 'passed','provider_calls':0,'case_id':case,'schema_sha256':schema_hash}))
        return
    os.chdir(ROOT)
    logging.disable(logging.CRITICAL)
    os.umask(0o077)
    from app.core.config import Settings, get_settings
    project = Settings().google_cloud_project
    if not project: raise SystemExit('Existing Vertex project unavailable; no generation attempted.')
    os.environ.update(LLM_PROVIDER='vertex',VERTEX_API_KEY='',GOOGLE_CLOUD_PROJECT=project,
        GOOGLE_CLOUD_LOCATION='global',VERTEX_MODEL_NARROW='gemini-3.1-flash-lite',
        LLM_TIMEOUT_SECONDS='120',LLM_MAX_OUTPUT_TOKENS='12000',DEBUG='false')
    get_settings.cache_clear()
    output.mkdir(parents=True,exist_ok=False)
    os.close(os.open(transport,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600))
    witness=sqlite3.connect(transport)
    witness.execute('CREATE TABLE records (name TEXT PRIMARY KEY,payload_json TEXT NOT NULL)')
    def record(name,data):
        witness.execute('INSERT INTO records VALUES (?,?)',(name,json.dumps(data,ensure_ascii=False)))
        witness.commit()
    state={'sdk_generation_invocations':0,'generation_http_attempts':0,'http_status':None,'finish_reasons':[],
           'input_tokens':None,'output_tokens':None,'total_tokens':None,'application_retries':0,'fallbacks':False}
    clients=[]
    def on_request(request):
        if request.method=='POST' and request.url.path.endswith(':generateContent'):
            if state['generation_http_attempts'] >= 1: raise RuntimeError('Second generation HTTP request blocked.')
            state['generation_http_attempts']+=1
            record('http_request',{'path':request.url.path,'body':json.loads(request.content)})
    def on_response(response):
        if response.request.url.path.endswith(':generateContent'):
            state['http_status']=response.status_code
            response.read()
            record('http_response',{'status':response.status_code,'body':response.content.decode('utf-8')})
    def guarded_client(backend=None):
        from google import genai
        sdk=genai.Client(vertexai=True,project=project,location='global',http_options={
            'timeout':120000,'retry_options':{'attempts':1},'client_args':{'event_hooks':{'request':[on_request],'response':[on_response]}}})
        clients.append(sdk)
        def generate_content(**kwargs):
            assert kwargs['model']=='gemini-3.1-flash-lite'
            config=kwargs['config']
            assert content_hash(config['response_json_schema'])==schema_hash
            assert config['http_options']['retry_options']['attempts']==1
            if state['sdk_generation_invocations']: raise RuntimeError('Second SDK generation blocked.')
            reserve_attempt(kind,case,schema_hash)
            state['sdk_generation_invocations']=1
            record('configured_request',kwargs)
            try:
                response=sdk.models.generate_content(**kwargs)  # Only paid generation invocation.
            except Exception as exc:
                from app.services.llm.vertex_m3_diagnostics import failure_metadata
                record('provider_failure',failure_metadata(exc,{},sdk_started=True,private=True))
                raise
            record('original_sdk_response',response.model_dump(mode='json',exclude_none=True,exclude={'sdk_http_response'}))
            state['finish_reasons']=[getattr(c.finish_reason,'value',c.finish_reason) for c in response.candidates or []]
            usage=response.usage_metadata
            if usage: state.update(input_tokens=usage.prompt_token_count,output_tokens=usage.candidates_token_count,total_tokens=usage.total_token_count)
            return response
        return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    record('scope',{'case_id':case,'kind':kind,'schema_sha256':schema_hash,'schema_adapter':DRAFT_ADAPTER_VERSION,
        'maximum_total_generations':3,'maximum_hca_generations':2,'authentication':'existing_ADC'})
    exit_code=0;stdout=io.StringIO()
    try:
        if kind=='probe':
            from app.services.bo.m3_contract import assemble_draft
            plan=json.loads((ROOT/'examples/bo_m3/contract_alignment/probe_plan.json').read_text())
            response=guarded_client().models.generate_content(model='gemini-3.1-flash-lite',contents=plan['contents'],config={
                'system_instruction':plan['system_instruction'],'response_mime_type':'application/json',
                'response_json_schema':schema,'temperature':1.0,'max_output_tokens':2048,
                'http_options':{'timeout':120000,'retry_options':{'attempts':1}}})
            text=response.text or '';record('original_model_text',{'text':text})
            assembled,audit=assemble_draft(json.loads(text),window_years=3)
            assert assembled['m3_assessment']['management_stability']['executives'][0]['facts']
            assert assembled['m3_assessment']['structural_credit_findings'] and assembled['m3_assessment']['management_assessments']
            record('probe_validation',audit)
            state['draft_validation']='valid';state['assembly_validation']='valid'
        else:
            from app.services.llm.providers_vertex import VertexProviderBackend
            from app import cli
            os.close(os.open(database,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600))
            VertexProviderBackend._client=guarded_client
            with contextlib.redirect_stdout(stdout):
                cli.main(['--database',str(database),'--output',str(output),'run-m3',case,
                    '--package','examples/healthcare/hca-2025/package.json',
                    '--corporate-structure','data/test_inputs/hca_simulated_corporate_structure.json','--live'])
    except SystemExit as exc:
        exit_code=exc.code if isinstance(exc.code,int) else 1
    except Exception as exc:
        exit_code=1
        from app.services.llm.vertex_m3_diagnostics import redact
        record('local_failure',{'error_type':type(exc).__name__,'detail':redact(str(exc))})
    finally:
        summary={**state,'case_id':case,'kind':kind,'schema_sha256':schema_hash,'exit_code':exit_code,
                 'private_database':str(database) if kind!='probe' else None,'private_transport_recording':str(transport),'output':str(output)}
        record('cli_stdout',{'text':stdout.getvalue(),'exit_code':exit_code})
        record('summary',summary)
        with (output/'transport_summary.json').open('x') as file:json.dump(summary,file,indent=2)
        if state['sdk_generation_invocations']:
            with sqlite3.connect(LEDGER) as ledger:ledger.execute('UPDATE attempts SET summary=? WHERE case_id=?',(json.dumps(summary),case))
        for client in clients:client.close()
        witness.close()
        print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
