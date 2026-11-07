import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from caseweave.main import create_app
from caseweave.config import Settings
from caseweave.analysis import check_case, analyze
from caseweave.samples import sample_cases
from caseweave.paypal import PayPalError, PayPal

@pytest.fixture
def app(tmp_path):
    return create_app(Settings(database_url=f"sqlite:///{tmp_path / 'test.db'}", admin_token="test-admin", paypal_client_id="test-id", paypal_client_secret="never-return-this-secret"))

@pytest.fixture
def client(app):
    with TestClient(app) as c:
        assert c.post('/api/session').status_code==200
        yield c


def first(client):
    return next(c for c in client.get('/api/cases').json() if c['key']=='before')


def prepared(client, case_id):
    c=client.post(f'/api/cases/{case_id}/analyze').json()
    assert c['analysis']['engine']=='rules'
    assert client.post(f'/api/cases/{case_id}/approve',json={'digest':c['digest']}).status_code==200
    return c


def test_chronology_before_after_missing():
    before,after,missing=map(check_case,sample_cases())
    assert 'follows' in before['findings'][0]['text']
    assert 'at or before' in after['findings'][0]['text']
    assert 'Cancellation confirmation with effective timestamp' in missing['missing']
    assert missing['coverage']==50


def test_rules_do_not_claim_ai(client):
    c=first(client)
    data=client.post(f"/api/cases/{c['id']}/analyze").json()
    assert data['analysis']['engine']=='rules'
    assert data['analysis']['model'] is None
    assert 'Connect an AI model' in data['analysis']['notice']


def test_session_isolation(app,client):
    id=first(client)['id']
    with TestClient(app) as other:
        other.post('/api/session')
        assert other.get('/api/cases/'+id).status_code==404
        assert other.post('/api/cases/'+id+'/analyze').status_code==404
        assert other.get('/api/cases/'+id+'/export').status_code==404


def test_unauthenticated_access(app):
    with TestClient(app) as c:
        assert c.get('/api/cases').status_code==401


def test_cross_origin_write(client):
    assert client.post('/api/sample/reset',headers={'Origin':'https://attacker.example'}).status_code==403


def test_complete_sample_flow_and_repeat(client):
    c=first(client);path='/api/cases/'+c['id']
    assert client.post(path+'/simulate').status_code==409
    prepared(client,c['id'])
    one=client.post(path+'/simulate').json()
    two=client.post(path+'/simulate').json()
    assert one['status']=='Sample completed'
    assert one['audit']==two['audit']
    assert one['paypal_id'] is None
    assert 'No evidence was sent' in one['audit'][-1]['detail']


def test_edit_invalidates_approval(client):
    c=first(client);path='/api/cases/'+c['id']
    prepared(client,c['id'])
    assert client.put(path+'/draft',json={'draft':'Updated review with a material correction.'}).status_code==200
    assert client.post(path+'/simulate').status_code==409


def test_evidence_invalidates_analysis_and_approval(client):
    c=first(client);path='/api/cases/'+c['id']
    prepared(client,c['id'])
    data=client.post(path+'/evidence',json={'title':'New evidence','kind':'agreement','occurred_at':'2026-10-01T12:00:00Z','text':'The applicable agreement provides further context.'}).json()
    assert 'analysis' not in data and 'approved_digest' not in data and 'draft' not in data
    assert data['evidence'][-1]['provenance']=='merchant'
    assert client.post(path+'/simulate').status_code==409


def test_stale_approval_rejected(client):
    c=first(client);path='/api/cases/'+c['id']
    data=client.post(path+'/analyze').json()
    client.put(path+'/draft',json={'draft':'A new draft for review with changed content.'})
    assert client.post(path+'/approve',json={'digest':data['digest']}).status_code==409


def test_reject_naive_timestamp(client):
    id=first(client)['id']
    r=client.post(f'/api/cases/{id}/evidence',json={'title':'Timestamp test','kind':'other','occurred_at':'2026-11-01T12:00:00','text':'No timezone provided.'})
    assert r.status_code==422


def test_record_limit_and_ambiguous_chronology(client):
    c=first(client);path='/api/cases/'+c['id']
    client.post(path+'/evidence',json={'title':'Another payment','kind':'charge','occurred_at':'2026-11-02T12:00:00Z','text':'Another payment needs reconciliation.'})
    data=client.post(path+'/analyze').json()
    assert 'Multiple cancellation' in data['analysis']['findings'][0]['text']


def test_admin_required_and_sample_cannot_submit(client):
    id=first(client)['id']
    assert client.post('/api/sandbox/import',json={'dispute_id':'PP-D-TEST123'}).status_code==403
    assert client.post(f'/api/cases/{id}/sandbox-submit',headers={'Authorization':'Bearer test-admin'}).status_code==409


def raw_dispute():
    return {'dispute_id':'PP-D-TEST123','status':'WAITING_FOR_SELLER_RESPONSE','reason':'CANCELED_RECURRING_BILLING','create_time':'2026-11-02T12:00:00Z','dispute_amount':{'value':'49.00','currency_code':'USD'},'links':[{'rel':'provide_evidence'}],'evidences':[{'source':'REQUESTED_FROM_SELLER','evidence_type':'OTHER'}]}


def imported(client,app,monkeypatch,raw=None):
    async def get(id):return raw or raw_dispute()
    monkeypatch.setattr(app.state.paypal,'dispute',get)
    response=client.post('/api/sandbox/import',headers={'Authorization':'Bearer test-admin'},json={'dispute_id':'PP-D-TEST123'})
    assert response.status_code==200
    return response.json()


def test_sandbox_import_does_not_invent_payment_or_cancellation(client,app,monkeypatch):
    c=imported(client,app,monkeypatch)
    assert c['mode']=='sandbox'
    assert c['evidence'][0]['provenance']=='paypal_sandbox'
    assert not any(e['kind'] in ('charge','cancellation') for e in c['evidence'])
    assert client.post('/api/cases/'+c['id']+'/simulate').status_code==409


def test_submission_once(client,app,monkeypatch):
    c=imported(client,app,monkeypatch);path='/api/cases/'+c['id']
    calls=[]
    async def submit(id,note):calls.append((id,note));return {'links':[]}
    monkeypatch.setattr(app.state.paypal,'submit',submit)
    prepared(client,c['id'])
    data=client.post(path+'/sandbox-submit',headers={'Authorization':'Bearer test-admin'}).json()
    assert data['submission_state']=='submitted'
    assert client.post(path+'/sandbox-submit',headers={'Authorization':'Bearer test-admin'}).status_code==409
    assert len(calls)==1
    assert client.put(path+'/draft',json={'draft':'Cannot alter a submitted case response.'}).status_code==409


def test_uncertain_submission_not_automatically_retried(client,app,monkeypatch):
    c=imported(client,app,monkeypatch);path='/api/cases/'+c['id']
    async def submit(id,note):raise PayPalError('Check PayPal before retrying',uncertain=True)
    monkeypatch.setattr(app.state.paypal,'submit',submit)
    prepared(client,c['id'])
    assert client.post(path+'/sandbox-submit',headers={'Authorization':'Bearer test-admin'}).json()['submission_state']=='uncertain'
    assert client.post(path+'/sandbox-submit',headers={'Authorization':'Bearer test-admin'}).status_code==409


def test_requested_evidence_type_guard(client,app,monkeypatch):
    raw=raw_dispute();raw['evidences'][0]['evidence_type']='PROOF_OF_REFUND'
    c=imported(client,app,monkeypatch,raw)
    prepared(client,c['id'])
    assert client.post('/api/cases/'+c['id']+'/sandbox-submit',headers={'Authorization':'Bearer test-admin'}).status_code==409


def test_closed_case_guard(client,app,monkeypatch):
    raw=raw_dispute();raw['status']='RESOLVED'
    c=imported(client,app,monkeypatch,raw)
    prepared(client,c['id'])
    assert client.post('/api/cases/'+c['id']+'/sandbox-submit',headers={'Authorization':'Bearer test-admin'}).status_code==409


def test_reset_preserves_sandbox_cases(client,app,monkeypatch):
    c=imported(client,app,monkeypatch)
    client.post('/api/sample/reset')
    assert client.get('/api/cases/'+c['id']).status_code==200
    assert len(client.get('/api/cases').json())==4


def test_config_does_not_expose_secrets(client):
    text=client.get('/api/config').text
    assert 'never-return-this-secret' not in text
    assert 'test-admin' not in text
    assert 'test-id' not in text


def model_mock(monkeypatch,payload):
    actual=httpx.AsyncClient
    def handler(request):
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(payload)}}]})
    monkeypatch.setattr('caseweave.analysis.httpx.AsyncClient',lambda **kw:actual(transport=httpx.MockTransport(handler),**kw))


def test_ai_rejects_invented_source(monkeypatch):
    model_mock(monkeypatch,{'draft':'Invented fact [E99]','claims':[{'text':'Invented fact','sources':['E99']}]})
    result=asyncio.run(analyze(sample_cases()[0],Settings(ai_api_key='test',ai_model='test-model')))
    assert result['engine']=='rules'
    assert 'failed source validation' in result['notice']


def test_ai_accepts_valid_citations_with_human_review_notice(monkeypatch):
    model_mock(monkeypatch,{'draft':'The sample records a cancellation [E2].','claims':[{'text':'The fixture records a cancellation','sources':['E2']}]})
    result=asyncio.run(analyze(sample_cases()[0],Settings(ai_api_key='test',ai_model='test-model')))
    assert result['engine']=='ai'
    assert 'not factual entailment' in result['notice']


def test_ai_api_failure_falls_back_honestly(monkeypatch):
    actual=httpx.AsyncClient
    monkeypatch.setattr('caseweave.analysis.httpx.AsyncClient',lambda **kw:actual(transport=httpx.MockTransport(lambda req:httpx.Response(503)),**kw))
    result=asyncio.run(analyze(sample_cases()[0],Settings(ai_api_key='test',ai_model='test-model')))
    assert result['engine']=='rules'


def test_paypal_uses_sandbox_and_multipart(monkeypatch):
    calls=[];actual=httpx.AsyncClient
    def handler(req):
        calls.append(req)
        assert req.url.host=='api-m.sandbox.paypal.com'
        if req.url.path.endswith('/token'):return httpx.Response(200,json={'access_token':'sandbox-test-token'})
        return httpx.Response(200,json={'links':[]})
    monkeypatch.setattr('caseweave.paypal.httpx.AsyncClient',lambda **kw:actual(transport=httpx.MockTransport(handler),**kw))
    asyncio.run(PayPal(Settings(paypal_client_id='id',paypal_client_secret='secret')).submit('PP-D-TEST123','Reviewed evidence'))
    req=calls[-1]
    assert 'multipart/form-data' in req.headers['content-type']
    assert b'"evidence_type": "OTHER"' in req.content
    assert req.url.path.endswith('/provide-evidence')


def test_ai_rate_limit(client):
    path='/api/cases/'+first(client)['id']+'/analyze'
    for _ in range(3):assert client.post(path).status_code==200
    assert client.post(path).status_code==429


def test_reimport_does_not_bypass_submission_lock(client,app,monkeypatch):
    c=imported(client,app,monkeypatch)
    async def submit(id,note):return {}
    monkeypatch.setattr(app.state.paypal,'submit',submit)
    prepared(client,c['id'])
    headers={'Authorization':'Bearer test-admin'}
    assert client.post('/api/cases/'+c['id']+'/sandbox-submit',headers=headers).json()['submission_state']=='submitted'
    another=imported(client,app,monkeypatch)
    prepared(client,another['id'])
    assert client.post('/api/cases/'+another['id']+'/sandbox-submit',headers=headers).status_code==409


def test_astropods_native_environment(monkeypatch):
    for key in ('AI_BASE_URL','AI_API_KEY','AI_MODEL','AI_JSON_MODE'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('ASTRO_GATEWAY_URL','https://gateway.example/')
    monkeypatch.setenv('ASTRO_GATEWAY_API_KEY','native-test-secret')
    monkeypatch.setenv('MODEL_DEFAULT','claude-haiku-4-5')
    settings=Settings()
    assert settings.ai_base_url=='https://gateway.example/v1'
    assert settings.ai_api_key=='native-test-secret'
    assert settings.ai_model=='claude-haiku-4-5'
    assert settings.ai_json_mode=='prompt'
    assert settings.ai_configured
    monkeypatch.setenv('ASTRO_GATEWAY_URL','https://gateway.example/v1/')
    assert Settings().ai_base_url=='https://gateway.example/v1'


def test_explicit_ai_settings_override_astropods(monkeypatch):
    monkeypatch.setenv('ASTRO_GATEWAY_URL','https://gateway.example')
    monkeypatch.setenv('ASTRO_GATEWAY_API_KEY','native-secret')
    monkeypatch.setenv('MODEL_DEFAULT','native-model')
    monkeypatch.setenv('AI_BASE_URL','https://explicit.example/v1/')
    monkeypatch.setenv('AI_API_KEY','explicit-secret')
    monkeypatch.setenv('AI_MODEL','explicit-model')
    monkeypatch.setenv('AI_JSON_MODE','json_object')
    settings=Settings()
    assert settings.ai_base_url=='https://explicit.example/v1'
    assert settings.ai_api_key=='explicit-secret'
    assert settings.ai_model=='explicit-model'
    assert settings.ai_json_mode=='json_object'


def test_portable_json_request_omits_response_format_and_caps_output(monkeypatch):
    requests=[];actual=httpx.AsyncClient
    def handler(req):
        requests.append(json.loads(req.content))
        payload={'draft':'The sample records a cancellation [E2].','claims':[{'text':'A sample cancellation record exists','sources':['E2']}]}
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(payload)}}]})
    monkeypatch.setattr('caseweave.analysis.httpx.AsyncClient',lambda **kw:actual(transport=httpx.MockTransport(handler),**kw))
    result=asyncio.run(analyze(sample_cases()[0],Settings(ai_api_key='test',ai_model='claude-haiku-4-5',ai_json_mode='prompt',ai_max_tokens=1200)))
    assert result['engine']=='ai'
    assert 'response_format' not in requests[0]
    assert requests[0]['max_tokens']==1200


def test_json_object_request_preserved(monkeypatch):
    requests=[];actual=httpx.AsyncClient
    def handler(req):
        requests.append(json.loads(req.content))
        return httpx.Response(503)
    monkeypatch.setattr('caseweave.analysis.httpx.AsyncClient',lambda **kw:actual(transport=httpx.MockTransport(handler),**kw))
    asyncio.run(analyze(sample_cases()[0],Settings(ai_api_key='test',ai_model='test',ai_json_mode='json_object')))
    assert requests[0]['response_format']=={'type':'json_object'}
    assert len(requests)==1  # Fallback does not incur another inference request.


@pytest.mark.parametrize('mode,tokens',[('unsupported',1800),('prompt',0),('prompt',10000)])
def test_reject_invalid_ai_limits(mode,tokens):
    with pytest.raises(ValueError):
        Settings(ai_json_mode=mode,ai_max_tokens=tokens)


def test_incomplete_native_gateway_does_not_send_key_to_default_provider(monkeypatch):
    for key in ('AI_BASE_URL','AI_API_KEY','AI_MODEL','ASTRO_GATEWAY_URL'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('ASTRO_GATEWAY_API_KEY','native-only-secret')
    monkeypatch.setenv('MODEL_DEFAULT','claude-haiku-4-5')
    settings=Settings()
    assert settings.ai_base_url==''
    assert not settings.ai_configured

@pytest.mark.parametrize('auth_status,list_status,items,authenticated,access',[
    (401,200,[],False,False),
    (200,403,[],True,False),
    (200,200,[],True,True),
    (200,200,[{'dispute_id':'PP-D-TEST123','status':'WAITING_FOR_SELLER_RESPONSE','reason':'CANCELED_RECURRING_BILLING','buyer_email':'private@example.com'}],True,True),
])
def test_paypal_connection_stages_and_secret_separation(client,monkeypatch,auth_status,list_status,items,authenticated,access):
    actual=httpx.AsyncClient
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.url.host=='api-m.sandbox.paypal.com'
        if request.url.path.endswith('/token'):
            return httpx.Response(auth_status,json={'access_token':'private-access-token','error':'private-provider-error'})
        assert request.method=='GET'
        assert request.url.path=='/v1/customer/disputes'
        assert request.url.params['page_size']=='10'
        assert 'create_time_after' in request.url.params
        assert request.headers['authorization']=='Bearer private-access-token'
        return httpx.Response(list_status,json={'items':items,'error':'private-provider-error'})
    monkeypatch.setattr('caseweave.paypal.httpx.AsyncClient',lambda **kw:actual(transport=httpx.MockTransport(handler),**kw))
    assert client.post('/api/sandbox/check').status_code==403
    assert not calls
    response=client.post('/api/sandbox/check',headers={'Authorization':'Bearer test-admin'})
    assert response.status_code==200
    result=response.json()
    assert result['authenticated']==authenticated
    assert result['disputes_access']==access
    assert len(calls)==(2 if authenticated else 1)
    for secret in ['private-access-token','private-provider-error','never-return-this-secret','private@example.com']:
        assert secret not in response.text
    if items and access:
        assert set(result['disputes'][0])=={'dispute_id','status','reason'}


def test_paypal_connection_rejects_malformed_list(client,monkeypatch):
    actual=httpx.AsyncClient
    def handler(request):
        if request.url.path.endswith('/token'):
            return httpx.Response(200,json={'access_token':'private-access-token'})
        return httpx.Response(200,json={'items':'invalid'})
    monkeypatch.setattr('caseweave.paypal.httpx.AsyncClient',lambda **kw:actual(transport=httpx.MockTransport(handler),**kw))
    result=client.post('/api/sandbox/check',headers={'Authorization':'Bearer test-admin'}).json()
    assert result['authenticated']
    assert not result['disputes_access']
    assert result['stage']=='disputes'
