"""Queue transport tests: actual ASGI handlers, mocked model/network endpoints."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import secrets
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from caseweave.analysis import analyze
from caseweave.config import Settings
from caseweave.db import DraftJob
from caseweave.draft_queue import envelope, input_digest
from caseweave.drafter import DraftRequest, DraftWorker, WorkerSettings, create_worker_app
from caseweave.main import create_app

TOKEN='worker-test-'+ 'x'*40
HEADERS={'Authorization':'Bearer '+TOKEN}


def generated():
    return {'draft':'The sample cancellation [E2] precedes the payment [E3]. Review agreement terms [E4].','claims':[{'text':'The sample records a cancellation before the payment','sources':['E2','E3']}]}


@pytest.fixture
def setup(tmp_path):
    settings=Settings(database_url=f"sqlite:///{tmp_path/'queue.db'}",ai_transport='astropods_queue',drafter_shared_token=TOKEN)
    app=create_app(settings)
    with TestClient(app) as c:
        c.post('/api/session')
        case=next(x for x in c.get('/api/cases').json() if x['key']=='before')
        yield app,c,case,settings


def add_job(app,case,**kw):
    data=envelope(case)
    values={'id':secrets.token_hex(16),'case_id':case['id'],'input_digest':input_digest(data),'state':'pending','expires':time.time()+40,'payload':json.dumps(data)}
    values.update(kw)
    with app.state.factory() as db:
        job=DraftJob(**values);db.add(job);db.commit()
        return job.id


def worker_settings(**kw):
    return WorkerSettings(ai=Settings(ai_base_url='https://gateway.example/v1',ai_api_key='gateway-only-secret',ai_model='claude-haiku-4-5',ai_json_mode='prompt'),caseweave_url='https://caseweave.example',shared_token=TOKEN,**kw)


def transport(monkeypatch,app,invalid_model=False,lost_ack=False):
    actual=httpx.AsyncClient;asgi=httpx.ASGITransport(app=app)
    calls=[];lost=False
    class Dispatch(httpx.AsyncBaseTransport):
        async def handle_async_request(self,req):
            nonlocal lost
            calls.append(req)
            if req.url.host=='caseweave.example':
                assert req.headers['authorization']=='Bearer '+TOKEN
                assert 'cookie' not in req.headers
                assert 'gateway-only-secret' not in req.content.decode()
                res=await asgi.handle_async_request(req)
                if lost_ack and req.url.path.endswith('/complete') and not lost:
                    lost=True
                    await res.aread()
                    raise httpx.ReadTimeout('Simulated lost completion acknowledgement',request=req)
                return res
            assert req.url.host=='gateway.example'
            assert req.headers['authorization']=='Bearer gateway-only-secret'
            assert TOKEN not in req.content.decode()
            payload=json.loads(req.content)
            assert payload['max_tokens']==1800
            assert 'response_format' not in payload
            result={'draft':'Unsupported [E99]','claims':[{'text':'Invented','sources':['E99']}]} if invalid_model else generated()
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(result)}}]})
    monkeypatch.setattr('caseweave.drafting.httpx.AsyncClient',lambda **kw:actual(transport=Dispatch(),**kw))
    return calls


def test_render_queue_worker_gateway_round_trip(setup,monkeypatch):
    app,client,case,settings=setup
    calls=transport(monkeypatch,app)
    worker=DraftWorker(worker_settings())
    async def scenario():
        task=asyncio.create_task(analyze(case,settings,queue_runner=app.state.draft_queue.run))
        await asyncio.sleep(0)
        assert await worker.run_once()
        return await task
    result=asyncio.run(scenario())
    assert result['engine']=='ai'
    assert result['model']=='claude-haiku-4-5'
    assert worker.calls==1
    assert len(calls)==3
    # Completed identical inputs are reused without making another inference call.
    again=asyncio.run(app.state.draft_queue.run(case))
    assert again['draft']==result['draft']
    assert len(calls)==3


def test_worker_endpoints_require_separate_service_token(setup):
    app,c,case,settings=setup
    for headers in ({},{'Authorization':'Bearer wrong'}):
        assert c.post('/api/internal/drafting/claim',headers=headers).status_code==403
    assert c.post('/api/internal/drafting/claim',headers=HEADERS).json()=={'job':None}
    assert TOKEN not in c.get('/api/config').text
    assert c.get('/api/config').json()['ai_transport']=='astropods_queue'


def test_only_one_worker_can_claim_job_and_no_auto_reclaim(setup):
    app,c,case,settings=setup
    add_job(app,case)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results=list(executor.map(lambda _:app.state.draft_queue.claim(),range(2)))
    assert sum(r['job'] is not None for r in results)==1
    assert app.state.draft_queue.claim()=={'job':None}


def test_completion_is_idempotent_and_wrong_lease_is_rejected(setup):
    app,c,case,settings=setup
    id=add_job(app,case)
    leased=c.post('/api/internal/drafting/claim',headers=HEADERS).json()['job']
    body={'lease_token':leased['lease_token'],'result':{**generated(),'model':'claude-haiku-4-5'}}
    path='/api/internal/drafting/jobs/'+id+'/complete'
    assert c.post(path,headers=HEADERS,json={**body,'lease_token':'wrong'*10}).status_code==404
    assert c.post(path,headers=HEADERS,json=body).json()=={'accepted':True}
    assert c.post(path,headers=HEADERS,json=body).json()=={'accepted':True}
    altered={**body,'result':{**body['result'],'model':'different-model'}}
    assert c.post(path,headers=HEADERS,json=altered).status_code==409


def test_invalid_result_never_marked_completed(setup):
    app,c,case,settings=setup
    id=add_job(app,case);leased=app.state.draft_queue.claim()['job']
    body={'lease_token':leased['lease_token'],'result':{'draft':'Invented [E99]','claims':[{'text':'Invented','sources':['E99']}],'model':'model'}}
    assert c.post('/api/internal/drafting/jobs/'+id+'/complete',headers=HEADERS,json=body).status_code==422
    with app.state.factory() as db:assert db.get(DraftJob,id).state=='leased'


def test_expired_job_not_claimed_or_inferred(setup):
    app,c,case,settings=setup
    id=add_job(app,case,expires=time.time()-1)
    assert app.state.draft_queue.claim()=={'job':None}
    with app.state.factory() as db:assert db.get(DraftJob,id).state=='expired'
    with pytest.raises(ValueError):asyncio.run(app.state.draft_queue.run(case))


def test_job_with_changed_evidence_is_superseded(setup):
    app,c,case,settings=setup
    id=add_job(app,case)
    c.post('/api/cases/'+case['id']+'/evidence',json={'title':'Additional record','kind':'other','occurred_at':'2026-11-01T12:00:00Z','text':'New information changes the source snapshot.'})
    assert app.state.draft_queue.claim()=={'job':None}
    with app.state.factory() as db:assert db.get(DraftJob,id).state=='superseded'


def test_reset_removes_sample_jobs(setup):
    app,c,case,settings=setup
    id=add_job(app,case)
    c.post('/api/sample/reset')
    with app.state.factory() as db:assert db.get(DraftJob,id) is None


def test_gateway_failure_completes_job_as_failed_and_falls_back(setup,monkeypatch):
    app,c,case,settings=setup
    calls=transport(monkeypatch,app,invalid_model=True)
    worker=DraftWorker(worker_settings())
    async def scenario():
        task=asyncio.create_task(analyze(case,settings,queue_runner=app.state.draft_queue.run))
        await asyncio.sleep(0)
        await worker.run_once()
        return await task
    result=asyncio.run(scenario())
    assert result['engine']=='rules'
    assert 'failed source validation' in result['notice']
    assert worker.calls==1
    assert len(calls)==3
    assert result['fallback_reason']=='invalid_citations'
    assert worker.last_error=='invalid_citations'


def test_lost_completion_ack_retries_result_not_gateway(setup,monkeypatch):
    app,c,case,settings=setup
    add_job(app,case)
    calls=transport(monkeypatch,app,lost_ack=True)
    worker=DraftWorker(worker_settings())
    assert asyncio.run(worker.run_once())
    assert sum(r.url.host=='gateway.example' for r in calls)==1
    assert sum(r.url.path.endswith('/complete') for r in calls)==2


def test_call_budget_prevents_claiming_more_work(setup,monkeypatch):
    app,c,case,settings=setup
    add_job(app,case)
    calls=transport(monkeypatch,app)
    worker=DraftWorker(worker_settings(max_calls_per_process=1))
    assert asyncio.run(worker.run_once())
    assert not asyncio.run(worker.run_once())
    assert len(calls)==3


def test_worker_does_not_follow_render_login_redirect(monkeypatch):
    calls=[];actual=httpx.AsyncClient
    def handler(req):
        calls.append(req);return httpx.Response(302,headers={'Location':'https://login.example'})
    monkeypatch.setattr('caseweave.drafting.httpx.AsyncClient',lambda **kw:actual(transport=httpx.MockTransport(handler),**kw))
    with pytest.raises(httpx.HTTPStatusError):asyncio.run(DraftWorker(worker_settings()).run_once())
    assert len(calls)==1


def test_worker_has_no_inbound_model_endpoint_or_schema():
    settings=WorkerSettings(ai=Settings(ai_api_key='',ai_model=''),shared_token='')
    with TestClient(create_worker_app(settings)) as c:
        assert c.get('/health').status_code==503
        assert c.post('/v1/draft',json={}).status_code==404
        assert c.get('/openapi.json').status_code==404
        assert c.get('/').json()['transport']=='outbound queue'
        assert 'secret' not in c.get('/health').text


def test_worker_contract_rejects_duplicate_sources_and_extra_url(setup):
    app,c,case,settings=setup
    body=envelope(case);body['url']='https://attacker.example'
    with pytest.raises(ValueError):DraftRequest.model_validate(body)
    body=envelope(case);body['evidence'].append(dict(body['evidence'][0]))
    with pytest.raises(ValueError):DraftRequest.model_validate(body)


def test_completion_requires_one_outcome(setup):
    app,c,case,settings=setup
    body={'lease_token':'x'*40,'result':generated(),'error':'gateway_failed'}
    assert c.post('/api/internal/drafting/jobs/unknown/complete',headers=HEADERS,json=body).status_code==422


@pytest.mark.parametrize('url',['http://remote.example','https://user:password@remote.example','https://remote.example/?token=secret','https://remote.example/#token'])
def test_worker_origin_validation(url):
    with pytest.raises(ValueError):WorkerSettings(caseweave_url=url)


def test_queue_transport_rejects_short_token():
    with pytest.raises(ValueError):Settings(ai_transport='astropods_queue',drafter_shared_token='short')


def test_worker_cannot_recursively_use_queue_transport():
    with pytest.raises(ValueError):WorkerSettings(ai=Settings(ai_transport='astropods_queue',drafter_shared_token=TOKEN))


def test_application_analysis_endpoint_waits_for_authenticated_worker(setup,monkeypatch):
    app,c,case,settings=setup
    actual=httpx.AsyncClient
    transport(monkeypatch,app)
    worker=DraftWorker(worker_settings())
    async def scenario():
        async with actual(transport=httpx.ASGITransport(app=app),base_url='http://testserver',cookies=dict(c.cookies)) as client:
            task=asyncio.create_task(client.post('/api/cases/'+case['id']+'/analyze'))
            for _ in range(50):
                if await worker.run_once():
                    break
                await asyncio.sleep(0.01)
            return await asyncio.wait_for(task,timeout=5)
    response=asyncio.run(scenario())
    assert response.status_code==200
    assert response.json()['analysis']['engine']=='ai'
    assert response.json()['analysis']['model']=='claude-haiku-4-5'


def test_direct_mode_does_not_allow_worker_claims(tmp_path):
    app=create_app(Settings(database_url=f"sqlite:///{tmp_path/'direct.db'}",drafter_shared_token=TOKEN))
    with TestClient(app) as client:
        assert client.post('/api/internal/drafting/claim',headers=HEADERS).status_code==403


@pytest.mark.parametrize("status,code", [(401,"gateway_auth_failed"),(403,"gateway_auth_failed"),(429,"gateway_rate_limited"),(400,"gateway_request_rejected"),(503,"gateway_unavailable")])
def test_gateway_status_is_safe_and_actionable(monkeypatch,status,code):
    from caseweave.drafting import provider_draft,DraftFailure
    actual=httpx.AsyncClient
    def reply(request):
        return httpx.Response(status,json={"error":"secret-provider-body"})
    monkeypatch.setattr('caseweave.drafting.httpx.AsyncClient',lambda **kw:actual(transport=httpx.MockTransport(reply),**kw))
    with pytest.raises(DraftFailure) as error:
        asyncio.run(provider_draft({"evidence":[]},worker_settings().ai))
    assert error.value.code==code
    assert error.value.http_status==status
    assert 'secret-provider-body' not in str(error.value)


@pytest.mark.parametrize("content,finish,code", [('{bad','stop','invalid_model_json'),('{}','length','model_output_truncated'),('{}','stop','invalid_model_schema'),(json.dumps({'draft':'x'*2001,'claims':[]}),'stop','draft_too_long')])
def test_model_output_failure_categories(monkeypatch,content,finish,code):
    from caseweave.drafting import provider_draft,DraftFailure
    actual=httpx.AsyncClient
    def reply(request):
        return httpx.Response(200,json={"choices":[{"finish_reason":finish,"message":{"content":content}}]})
    monkeypatch.setattr('caseweave.drafting.httpx.AsyncClient',lambda **kw:actual(transport=httpx.MockTransport(reply),**kw))
    with pytest.raises(DraftFailure) as error:
        asyncio.run(provider_draft({"evidence":[]},worker_settings().ai))
    assert error.value.code==code


def test_single_json_fence_preserves_validated_sources(monkeypatch):
    from caseweave.drafting import provider_draft
    actual=httpx.AsyncClient
    def reply(request):
        return httpx.Response(200,json={"choices":[{"message":{"content":"```json\n"+json.dumps(generated())+"\n```"}}]})
    monkeypatch.setattr('caseweave.drafting.httpx.AsyncClient',lambda **kw:actual(transport=httpx.MockTransport(reply),**kw))
    result=asyncio.run(provider_draft({"evidence":[{"id":x} for x in ['E2','E3','E4']]},worker_settings().ai))
    assert result==generated()


def test_worker_reuses_client_across_idle_polls_and_closes_it(monkeypatch):
    actual=httpx.AsyncClient
    clients=[]
    requests=[]
    stop=asyncio.Event()
    def reply(request):
        requests.append(request)
        if len(requests)==3:
            stop.set()
        return httpx.Response(200,json={"job":None})
    def create(**kwargs):
        client=actual(transport=httpx.MockTransport(reply),**kwargs)
        clients.append(client)
        return client
    monkeypatch.setattr('caseweave.drafter.httpx.AsyncClient',create)
    worker=DraftWorker(worker_settings(poll_seconds=0.2))
    asyncio.run(worker.run(stop))
    assert len(requests)==3
    assert len(clients)==1
    assert clients[0].is_closed
    assert worker.calls==0
