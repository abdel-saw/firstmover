"""Exercise actual FirstMover routes with provider HTTP mocked at the network boundary."""
import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from caseweave.main import create_app
from caseweave.config import Settings
from caseweave.db import FirstMoverRecord as Record
from caseweave.drafter import DraftWorker,WorkerSettings

ADMIN={'Authorization':'Bearer operator-test'}
TERMS={'title':'Printmaking together','description':'Three people commit; early participants receive a bonus if the group does not fill.',
       'threshold':3,'price_cents':1000,'early_slots':2,'bonus_cents':100,'duration_minutes':15}

class Gateway:
    def __init__(self):
        self.orders={};self.auths={};self.captures={};self.payouts={};self.refunds={};self.calls=[]
        self.payout_status='SUCCESS';self.capture_timeout=False;self.capture_refused=False;self.bad_amount=False;self.bad_payout=False
    def handle(self,request):
        self.calls.append(request)
        assert request.url.host=='api-m.sandbox.paypal.com'
        if request.url.path.endswith('/oauth2/token'):return httpx.Response(200,json={'access_token':'private-paypal-token'})
        assert request.headers['authorization']=='Bearer private-paypal-token'
        path=request.url.path;method=request.method
        body=json.loads(request.content) if request.content else {}
        if path=='/v2/checkout/orders' and method=='POST':
            oid='ORDER'+str(len(self.orders)+1).zfill(6)
            order={'id':oid,'intent':body['intent'],'status':'CREATED','purchase_units':body['purchase_units'],'payer':{}}
            order['purchase_units'][0]['payee']={'merchant_id':'MERCHANT123'}
            self.orders[oid]=order
            return httpx.Response(201,json=order)
        if path.startswith('/v2/checkout/orders/'):
            bits=path.split('/');order=self.orders[bits[4]]
            if method=='POST':
                assert bits[5]=='authorize' and request.headers.get('paypal-request-id')
                aid='AUTH'+str(len(self.auths)+1).zfill(6)
                auth={'id':aid,'status':'CREATED','amount':order['purchase_units'][0]['amount']}
                self.auths[aid]=auth;order['purchase_units'][0]['payments']={'authorizations':[auth]};order['status']='COMPLETED'
            return httpx.Response(200,json=order)
        if path.startswith('/v2/payments/authorizations/'):
            bits=path.split('/');aid=bits[4];auth=self.auths[aid]
            if method=='POST' and bits[5]=='capture':
                assert request.headers.get('paypal-request-id')
                if self.capture_refused:return httpx.Response(422,json={'private':'provider error'})
                capid='CAP'+str(len(self.captures)+1).zfill(6)
                cap={'id':capid,'status':'COMPLETED','amount':dict(auth['amount']),'supplementary_data':{'related_ids':{'authorization_id':aid}}}
                if self.bad_amount:cap['amount']['value']='1.00'
                self.captures[capid]=cap;auth['status']='CAPTURED'
                order=next(o for o in self.orders.values() if any(a['id']==aid for a in o['purchase_units'][0].get('payments',{}).get('authorizations',[])))
                order['purchase_units'][0]['payments']['captures']=[cap]
                if self.capture_timeout:raise httpx.ReadTimeout('Lost capture acknowledgement',request=request)
                return httpx.Response(201,json=cap)
            if method=='POST' and bits[5]=='void':auth['status']='VOIDED';return httpx.Response(204)
            return httpx.Response(200,json=auth)
        if path.startswith('/v2/payments/captures/'):
            bits=path.split('/');cap=self.captures[bits[4]]
            if method=='GET':return httpx.Response(200,json=cap)
            rid='REFUND'+str(len(self.refunds)+1).zfill(6);refund={'id':rid,'amount':body['amount'],'status':'COMPLETED'}
            self.refunds[rid]=refund;return httpx.Response(201,json=refund)
        if path.startswith('/v2/payments/refunds/'):return httpx.Response(200,json=self.refunds[path.split('/')[-1]])
        if path=='/v1/payments/payouts' and method=='POST':
            bid='PAYOUT'+str(len(self.payouts)+1).zfill(6)
            raw={'batch_header':{'payout_batch_id':bid,'sender_batch_header':body['sender_batch_header']},
                 'items':[{'payout_item':body['items'][0],'transaction_status':self.payout_status}]}
            if self.bad_payout:raw['items'][0]['payout_item']['receiver']='attacker@example.test'
            self.payouts[bid]=raw
            return httpx.Response(201,json={'batch_header':raw['batch_header']})
        if path.startswith('/v1/payments/payouts/'):
            raw=self.payouts[path.split('/')[-1]]
            if self.payout_status=='SUCCESS':raw['items'][0]['transaction_status']='SUCCESS'
            return httpx.Response(200,json=raw)
        raise AssertionError((method,path))

@pytest.fixture
def setup(tmp_path,monkeypatch):
    settings=Settings(database_url=f'sqlite:///{tmp_path/"firstmover.db"}',admin_token='operator-test',organizer_password='organizer-test',paypal_client_id='sandbox-id',paypal_client_secret='private-secret')
    app=create_app(settings);gateway=Gateway();actual=httpx.AsyncClient
    monkeypatch.setattr('caseweave.paypal.httpx.AsyncClient',lambda **kw:actual(transport=httpx.MockTransport(gateway.handle),**kw))
    with TestClient(app) as client:
        client.post('/api/session')
        yield app,client,gateway

def post(c,path,body=None,admin=False):return c.post('/api/firstmover'+path,json=body,headers=ADMIN if admin else {})
def launch(c,mode='demo'):
    r=post(c,'/campaigns',{'terms':TERMS,'mode':mode,'approved':True},admin=True);assert r.status_code==200,r.text;return r.json()
def probe(c):
    r=post(c,'/payout-check',{'recipient':'buyer@example.test','approved':True},True);assert r.status_code==200,r.text;return r.json()
def join(c,row,name='Alex'):
    r=post(c,'/campaigns/'+row['id']+'/join',{'name':name,'terms_digest':row['terms_digest'],'accepted':True},admin=row['mode']=='demo');assert r.status_code==200,r.text;return r.json()
def sandbox_join(c,g,row,payer='BUYER123'):
    result=join(c,row);g.orders[result['order_id']]['status']='APPROVED';g.orders[result['order_id']]['payer']={'payer_id':payer}
    r=post(c,'/pledges/'+result['commitment_id']+'/authorize');assert r.status_code==200,r.text
    return r.json(),result['commitment_id']
def expire(app,cid):
    with app.state.factory() as db:
        r=db.get(Record,cid);d=json.loads(r.data);d['deadline']=time.time()-1;r.data=json.dumps(d);db.commit()
def settle(c,row,admin=True):
    r=post(c,'/campaigns/'+row['id']+'/close',admin=admin);assert r.status_code==200,r.text
    for _ in range(15):
        r=post(c,'/campaigns/'+row['id']+'/step',admin=admin);assert r.status_code==200,r.text
        if r.json()['state'] in ('funded','released','recovered'):return r.json()
    raise AssertionError(r.text)


def test_demo_filled_group_and_repeated_settlement(setup):
    app,c,g=setup;row=launch(c)
    for name in ['Alex','Jordan','Taylor']:join(c,row,name)
    result=settle(c,row)
    assert result['state']=='funded'
    assert [p['state'] for p in result['pledges']]==['captured']*3
    assert len(result['operations'])==3
    assert all(op['status']=='SIMULATED' for op in result['operations'])
    again=post(c,'/campaigns/'+row['id']+'/step',admin=True).json();assert len(again['operations'])==3
    assert not g.calls


def test_demo_failure_releases_and_pays_only_early_slots(setup):
    app,c,g=setup;row=launch(c);join(c,row)
    result=settle(c,row)
    assert result['state']=='released' and result['pledges'][0]['state']=='bonus_paid'
    assert len(result['operations'])==2 and result['outcome']=='release'
    assert not g.calls


def test_consent_limits_and_isolation(setup):
    app,c,g=setup
    assert post(c,'/campaigns',{'terms':TERMS,'mode':'demo','approved':False},admin=True).status_code==422
    assert post(c,'/campaigns',{'terms':{**TERMS,'early_slots':4},'mode':'demo','approved':True},admin=True).status_code==422
    row=launch(c)
    assert post(c,'/campaigns/'+row['id']+'/join',{'name':'Alex','terms_digest':'a'*64,'accepted':True},admin=True).status_code==409
    with TestClient(app) as other:
        other.post('/api/session')
        assert other.get('/api/firstmover/campaigns').status_code==403
        assert other.get('/api/firstmover/public-campaigns/'+row['id']).status_code==404
        assert post(other,'/campaigns/'+row['id']+'/close').status_code==404
    expire(app,row['id'])
    assert post(c,'/campaigns/'+row['id']+'/join',{'name':'Alex','terms_digest':row['terms_digest'],'accepted':True},admin=True).status_code==409
    assert not g.calls


def test_probe_access_consent_idempotency_and_sandbox_gate(setup):
    app,c,g=setup
    assert post(c,'/payout-check',{'recipient':'buyer@example.test','approved':True}).status_code==403
    assert post(c,'/payout-check',{'recipient':'buyer@example.test','approved':False},True).status_code==422
    assert post(c,'/campaigns',{'terms':TERMS,'mode':'sandbox','approved':True},True).status_code==409
    assert probe(c)['verified']
    assert probe(c)['verified']
    assert len(g.payouts)==1
    assert c.get('/api/firstmover/config').json()['payout_verified']
    row=launch(c,'sandbox');assert row['mode']=='sandbox'
    assert 'private-secret' not in json.dumps(row)
    assert post(c,'/campaigns/'+row['id']+'/close').status_code==403


def test_pending_or_mismatched_payout_does_not_unlock_sandbox(setup):
    app,c,g=setup;g.payout_status='PENDING'
    assert not probe(c)['verified']
    g.payout_status='SUCCESS';assert probe(c)['verified'];assert len(g.payouts)==1


def test_mismatched_payout_recipient_stays_unverified(setup):
    app,c,g=setup;g.bad_payout=True
    r=post(c,'/payout-check',{'recipient':'buyer@example.test','approved':True},True)
    assert r.status_code==502
    assert not c.get('/api/firstmover/config').json()['payout_verified']
    assert 'attacker' not in r.text
    post(c,'/payout-check',{'recipient':'buyer@example.test','approved':True},True)
    assert len(g.payouts)==1


def test_sandbox_success_and_payer_deduplication(setup):
    app,c,g=setup;probe(c);row=launch(c,'sandbox')
    row,pid=sandbox_join(c,g,row)
    duplicate=join(c,row,'Duplicate');g.orders[duplicate['order_id']]['status']='APPROVED';g.orders[duplicate['order_id']]['payer']={'payer_id':'BUYER123'}
    assert post(c,'/pledges/'+duplicate['commitment_id']+'/authorize').status_code==409
    assert len(g.auths)==1
    # An unapproved/duplicate checkout has no hold, and does not count toward the target.
    post(c,'/pledges/'+duplicate['commitment_id']+'/reconcile')
    row,_=sandbox_join(c,g,row,'BUYER234');row,_=sandbox_join(c,g,row,'BUYER345')
    result=settle(c,row,True)
    assert result['state']=='funded' and len(g.captures)==3
    assert all(op['state']=='completed' for op in result['operations'])
    post(c,'/campaigns/'+row['id']+'/step',admin=True)
    assert len(g.captures)==3


def test_sandbox_unfilled_releases_before_bonus_and_respects_deadline(setup):
    app,c,g=setup;probe(c);row=launch(c,'sandbox');row,pid=sandbox_join(c,g,row)
    assert post(c,'/campaigns/'+row['id']+'/close',admin=True).status_code==409
    expire(app,row['id']);result=settle(c,row,True)
    assert result['state']=='released'
    assert g.auths[next(iter(g.auths))]['status']=='VOIDED'
    assert len(g.payouts)==2  # permission probe + one early bonus
    assert not g.captures
    mutations=[r.url.path for r in g.calls if r.method=='POST' and not r.url.path.endswith('/token')]
    assert mutations.index('/v2/payments/authorizations/'+next(iter(g.auths))+'/void') < len(mutations)-1
    bonus=list(g.payouts.values())[-1]['items'][0]['payout_item']
    assert bonus['recipient_type']=='PAYPAL_ID' and bonus['receiver']=='BUYER123'


def test_capture_timeout_reconciles_without_second_capture(setup):
    app,c,g=setup;probe(c);row=launch(c,'sandbox')
    for i in range(3):row,_=sandbox_join(c,g,row,'BUYER00'+str(i))
    post(c,'/campaigns/'+row['id']+'/close',admin=True);g.capture_timeout=True
    assert post(c,'/campaigns/'+row['id']+'/step',admin=True).status_code==502
    assert len(g.captures)==1
    g.capture_timeout=False
    assert post(c,'/campaigns/'+row['id']+'/step',admin=True).status_code==200
    assert len(g.captures)==1
    assert post(c,'/campaigns/'+row['id']+'/step',admin=True).status_code==200
    assert len(g.captures)==2


def test_wrong_capture_amount_never_marks_group_funded(setup):
    app,c,g=setup;probe(c);row=launch(c,'sandbox')
    for i in range(3):row,_=sandbox_join(c,g,row,'BUYER00'+str(i))
    post(c,'/campaigns/'+row['id']+'/close',admin=True);g.bad_amount=True
    assert post(c,'/campaigns/'+row['id']+'/step',admin=True).status_code==502
    result=c.get('/api/firstmover/campaigns/'+row['id'],headers=ADMIN).json()
    assert result['state']=='review' and not any(p['state']=='captured' for p in result['pledges'])
    assert post(c,'/campaigns/'+row['id']+'/recover',admin=True).status_code==409


def test_technical_recovery_refunds_captures_and_voids_other_holds(setup):
    app,c,g=setup;probe(c);row=launch(c,'sandbox')
    for i in range(3):row,_=sandbox_join(c,g,row,'BUYER00'+str(i))
    post(c,'/campaigns/'+row['id']+'/close',admin=True)
    assert post(c,'/campaigns/'+row['id']+'/step',admin=True).status_code==200
    assert len(g.captures)==1
    assert post(c,'/campaigns/'+row['id']+'/recover',admin=True).status_code==200
    for _ in range(5):
        result=post(c,'/campaigns/'+row['id']+'/step',admin=True)
        assert result.status_code==200,result.text
        if result.json()['state']=='recovered':break
    assert result.json()['state']=='recovered'
    assert len(g.refunds)==1 and len(g.payouts)==1
    assert sum(a['status']=='VOIDED' for a in g.auths.values())==2


def test_firstmover_ai_worker_round_trip(tmp_path,monkeypatch):
    token='x'*40
    settings=Settings(database_url=f'sqlite:///{tmp_path/"ai.db"}',ai_transport='astropods_queue',drafter_shared_token=token)
    app=create_app(settings)
    actual=httpx.AsyncClient
    proposal={**TERMS,'questions':['Who teaches the workshop?'],'rationale':'These illustrative terms cap the organizer bonus liability at USD 2, excluding fees.'}
    async def handler(req):
        if req.url.host=='render.example':return await httpx.ASGITransport(app=app).handle_async_request(req)
        assert req.url.host=='gateway.example'
        payload=json.loads(req.content)
        assert 'private-paypal' not in json.dumps(payload)
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(proposal)}}]})
    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self,req):return await handler(req)
    monkeypatch.setattr('caseweave.drafter.httpx.AsyncClient',lambda **kw:actual(transport=Transport(),**kw))
    worker=DraftWorker(WorkerSettings(ai=Settings(ai_base_url='https://gateway.example/v1',ai_api_key='model-test',ai_model='haiku',ai_json_mode='prompt'),caseweave_url='https://render.example',shared_token=token))
    async def scenario():
        task=asyncio.create_task(app.state.campaign_ai.run('test-owner','A printmaking workshop for three people with USD 10 tickets.'))
        await asyncio.sleep(0)
        assert await worker.run_once()
        result=await task
        cached=await app.state.campaign_ai.run('test-owner','A printmaking workshop for three people with USD 10 tickets.')
        assert cached==result
        return result
    result=asyncio.run(scenario())
    assert result['proposal']==proposal and result['model']=='haiku' and worker.calls==1


def test_rejected_capture_can_recover_without_repeating_write(setup):
    app,c,g=setup;probe(c);row=launch(c,'sandbox')
    for i in range(3):row,_=sandbox_join(c,g,row,'BUYER00'+str(i))
    post(c,'/campaigns/'+row['id']+'/close',admin=True)
    post(c,'/campaigns/'+row['id']+'/step',admin=True)
    g.capture_refused=True
    assert post(c,'/campaigns/'+row['id']+'/step',admin=True).status_code==502
    attempted=sum(r.method=='POST' and r.url.path.endswith('/capture') for r in g.calls)
    assert post(c,'/campaigns/'+row['id']+'/step',admin=True).status_code==409
    assert sum(r.method=='POST' and r.url.path.endswith('/capture') for r in g.calls)==attempted
    assert post(c,'/campaigns/'+row['id']+'/recover',admin=True).status_code==200
    for _ in range(5):
        r=post(c,'/campaigns/'+row['id']+'/step',admin=True)
        assert r.status_code==200,r.text
        if r.json()['state']=='recovered':break
    assert r.json()['state']=='recovered' and len(g.refunds)==1


def test_payout_permission_rejection_can_retry_after_operator_fixes_access(setup,monkeypatch):
    app,c,g=setup
    original=g.handle
    denied=True
    def handler(request):
        if denied and request.method=='POST' and request.url.path=='/v1/payments/payouts':return httpx.Response(403,json={'error':'private'})
        return original(request)
    from httpx._client import AsyncClient
    actual=AsyncClient
    monkeypatch.setattr('caseweave.paypal.httpx.AsyncClient',lambda **kw:actual(transport=httpx.MockTransport(handler),**kw))
    assert post(c,'/payout-check',{'recipient':'buyer@example.test','approved':True},True).status_code==502
    assert not c.get('/api/firstmover/config').json()['payout_verified'] and not g.payouts
    denied=False
    assert probe(c)['verified']
    assert probe(c)['verified'] and len(g.payouts)==1


def test_campaign_concurrent_close_is_reserved_once(setup):
    app,c,g=setup;row=launch(c)
    join(c,row)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results=list(workers.map(lambda _:post(c,'/campaigns/'+row['id']+'/close',admin=True),range(2)))
    assert all(r.status_code in (200,409) for r in results)
    stored=c.get('/api/firstmover/campaigns/'+row['id'],headers=ADMIN).json()
    assert stored['outcome']=='release' and len([a for a in stored['audit'] if a['action']=='settlement_approved'])==1


def test_missing_authorization_after_dispatched_write_cannot_be_abandoned(setup):
    app,c,g=setup;probe(c);row=launch(c,'sandbox');joined=join(c,row)
    # A process has dispatched authorize but cannot yet observe an authorization on GET.
    pid=joined['commitment_id']
    with app.state.factory() as db:
        pledge=db.get(Record,pid);pledge.state='review'
        import hashlib
        key='fmop-'+hashlib.sha256(('authorize:'+pid).encode()).hexdigest()[:40]
        db.add(Record(id=key,kind='operation',owner=pledge.owner,parent=row['id'],state='review',data=json.dumps({'label':'Authorize participant hold','at':time.time()})))
        db.commit()
    result=post(c,'/pledges/'+pid+'/reconcile')
    assert result.status_code==409 and 'not proof' in result.text
    assert post(c,'/campaigns/'+row['id']+'/close',admin=True).status_code==409


def test_completed_bonus_cannot_be_downgraded_by_participant_reconciliation(setup):
    app,c,g=setup;probe(c);row=launch(c,'sandbox');row,pid=sandbox_join(c,g,row)
    expire(app,row['id']);settle(c,row,True)
    result=post(c,'/pledges/'+pid+'/reconcile')
    assert result.status_code==200
    assert result.json()['pledges'][0]['state']=='bonus_paid'


def test_failed_ai_proposal_can_be_explicitly_retried_without_accepting_stale_completion(tmp_path):
    from fastapi import HTTPException
    settings=Settings(database_url=f'sqlite:///{tmp_path/"retry.db"}',ai_transport='astropods_queue',drafter_shared_token='x'*40)
    app=create_app(settings);ai=app.state.campaign_ai
    brief='A short printmaking workshop for three sandbox participants.'
    proposal={**TERMS,'questions':[],'rationale':'An illustrative proposal requiring organizer review, with capped bonus liability.'}
    async def scenario():
        task=asyncio.create_task(ai.run('owner',brief));await asyncio.sleep(0)
        first=ai.claim()['job'];ai.complete(first['id'],first['lease_token'],error='gateway_failed')
        with pytest.raises(HTTPException):await task
        task=asyncio.create_task(ai.run('owner',brief,retry=True));await asyncio.sleep(0)
        second=ai.claim()['job'];assert second['lease_token']!=first['lease_token']
        with pytest.raises(HTTPException):ai.complete(first['id'],first['lease_token'],error='gateway_failed')
        ai.complete(second['id'],second['lease_token'],result={'proposal':proposal,'model':'test-model'})
        result=await task;assert result['proposal']==proposal
    asyncio.run(scenario())


def test_organizer_session_and_public_checkout_are_separate(setup):
    app, organizer, gateway = setup
    session = '/api/firstmover/organizer/session'
    base = '/api/firstmover'
    launch_body = {'terms': TERMS, 'mode': 'sandbox', 'approved': True}
    assert post(organizer, '/campaigns', launch_body).status_code == 403
    assert post(organizer, '/plan', {'brief': 'A workshop for three participants.'}).status_code == 403
    assert organizer.post(session, json={'password': 'wrong'}).status_code == 401
    login = organizer.post(session, json={'password': 'organizer-test'})
    assert login.status_code == 200
    assert 'HttpOnly' in login.headers['set-cookie'] and 'SameSite=strict' in login.headers['set-cookie']
    cookie = login.cookies['firstmover_organizer']
    assert post(organizer, '/payout-check', {'recipient': 'buyer@example.test', 'approved': True}).json()['verified']
    launched = post(organizer, '/campaigns', launch_body)
    assert launched.status_code == 200, launched.text
    row = launched.json()
    cid = row['id']
    with TestClient(app) as participant:
        participant.post('/api/session')
        assert participant.get(base+'/campaigns').status_code == 403
        assert participant.get(base+'/public-campaigns').json()[0]['id'] == cid
        assert participant.get(base+'/campaigns/'+cid).status_code == 404
        assert post(participant, '/campaigns/'+cid+'/close').status_code == 404
        own, pid = sandbox_join(participant, gateway, row)
        assert [pledge['id'] for pledge in own['pledges']] == [pid]
        assert own['pledges'][0]['authorization_id'] is None

    public = organizer.get(base+'/public-campaigns/'+cid).json()
    assert public['authorized_count'] == 1
    assert public['pledges'] == public['operations'] == []
    assert 'audit' not in public and 'eligible_ids' not in public
    assert organizer.get(base+'/campaigns/'+cid).json()['pledges'][0]['authorization_id']
    assert organizer.delete(session).status_code == 200
    assert organizer.get(session, headers={'Cookie': 'firstmover_organizer='+cookie}).json()['signed_in'] is False
    assert organizer.get(base+'/campaigns').status_code == 403
    with TestClient(app) as next_session:
        assert next_session.post(session, json={'password': 'organizer-test'}).status_code == 200
        assert next_session.get(base+'/campaigns').json()[0]['id'] == cid


def test_campaign_model_repairs_invalid_schema_once(monkeypatch):
    from caseweave.campaign_planner import propose_campaign
    actual_client = httpx.AsyncClient
    valid = {**TERMS, 'price_cents': str(TERMS['price_cents']), 'questions': [],
             'rationale': 'A short workshop for a group with a capped early bonus.'}
    payloads = []

    def handler(request):
        payloads.append(json.loads(request.content))
        content = json.dumps({**valid, 'price_cents': 9}) if len(payloads) == 1 else (
            '```json\n'+json.dumps(valid)+'\n```'
        )
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})

    monkeypatch.setattr('caseweave.campaign_planner.httpx.AsyncClient',
                        lambda **kw: actual_client(transport=httpx.MockTransport(handler), **kw))
    settings = Settings(ai_base_url='https://gateway.example/v1', ai_api_key='test', ai_model='haiku')
    result = asyncio.run(propose_campaign('A short printmaking workshop.', settings))
    assert result['proposal']['price_cents'] == TERMS['price_cents']
    assert len(payloads) == 2
    assert 'price_cents: greater_than_equal' in payloads[1]['messages'][-1]['content']


def test_merchant_signup_paypal_and_settlement_are_tenant_scoped(tmp_path, monkeypatch):
    from base64 import b64decode

    app = create_app(Settings(
        database_url=f'sqlite:///{tmp_path/"merchant.db"}',
        merchant_encryption_key='workspace-test-key-'*3,
        paypal_client_id='legacy-unused', paypal_client_secret='legacy-unused-secret',
    ))
    gateways = {'merchant-alpha': Gateway(), 'merchant-beta': Gateway()}
    secrets = {'merchant-alpha': 'alpha-private-secret', 'merchant-beta': 'beta-private-secret'}
    actual_client = httpx.AsyncClient

    def route(request):
        auth = request.headers['authorization']
        if request.url.path.endswith('/oauth2/token'):
            client_id, secret = b64decode(auth.removeprefix('Basic ')).decode().split(':', 1)
            assert client_id in secrets
            if secrets[client_id] != secret:
                return httpx.Response(401)
            return httpx.Response(200, json={'access_token':client_id})
        gateway = gateways[auth.removeprefix('Bearer ')]
        request.headers['authorization'] = 'Bearer private-paypal-token'
        return gateway.handle(request)

    monkeypatch.setattr('caseweave.paypal.httpx.AsyncClient',
                        lambda **kwargs: actual_client(transport=httpx.MockTransport(route), **kwargs))
    base = '/api/firstmover'
    signup = base+'/organizer/signup'
    connection = base+'/organizer/paypal'
    auth = base+'/organizer/session'
    config = base+'/organizer/config'
    campaign_path = base+'/campaigns/'
    password = 'a-long-test-password'
    campaign = {'terms':TERMS,'mode':'sandbox','approved':True}
    no_bonus = {**campaign, 'terms':{**TERMS,'early_slots':0,'bonus_cents':0}}

    with TestClient(app) as alpha, TestClient(app) as beta, TestClient(app) as shopper:
        assert shopper.put(connection,json={'client_id':'merchant-alpha','client_secret':secrets['merchant-alpha']}).status_code == 403
        for client, name in ((alpha,'Alpha'), (beta,'Beta')):
            registered = client.post(signup, json={'email':name+'@example.test','store_name':name+' Workshop','password':password})
            assert registered.status_code == 200, registered.text
            assert post(client, '/campaigns', campaign).status_code == 409
        assert shopper.post(signup,json={'email':'ALPHA@example.test','store_name':'Copy','password':password}).status_code == 409
        bad_input = alpha.put(connection,json={'client_id':'bad!', 'client_secret':'tiny'})
        assert bad_input.status_code == 422 and 'tiny' not in bad_input.text
        assert alpha.put(connection,json={'client_id':'merchant-alpha','client_secret':'wrong-test-secret'}).status_code == 502
        assert not alpha.get(config).json()['paypal_configured']
        for client, client_id in ((alpha,'merchant-alpha'), (beta,'merchant-beta')):
            connected = client.put(connection,json={'client_id':client_id,'client_secret':secrets[client_id]})
            assert connected.status_code == 200, connected.text
            assert secrets[client_id] not in connected.text and secrets[client_id] not in client.get(config).text
        assert alpha.put(connection,json={'client_id':'merchant-beta','client_secret':secrets['merchant-beta']}).status_code == 409

        payout_body = {'recipient':'buyer@example.test','approved':True}
        assert post(alpha, '/payout-check', payout_body).json()['verified']
        assert not beta.get(config).json()['payout_verified']
        assert post(beta, '/campaigns', campaign).status_code == 409
        alpha_offer = post(alpha, '/campaigns', campaign).json()
        beta_offer = post(beta, '/campaigns', no_bonus).json()
        alpha_id, beta_id = alpha_offer['id'], beta_offer['id']
        assert [offer['id'] for offer in beta.get(campaign_path.rstrip('/')).json()] == [beta_id]
        assert beta.get(campaign_path+alpha_id).status_code == 404
        assert post(beta, '/campaigns/'+alpha_id+'/close').status_code == 404

        shopper.post('/api/session')
        for offer, client_id in ((alpha_offer,'merchant-alpha'),(beta_offer,'merchant-beta')):
            public = shopper.get(base+'/public-campaigns/'+offer['id']).json()
            assert public['paypal_client_id'] == client_id and 'audit' not in public
            assert public['store_name'] == offer['store_name']
            assert secrets[client_id] not in json.dumps(public)
            sandbox_join(shopper, gateways[client_id], offer)
            expire(app, offer['id'])
        assert len(gateways['merchant-alpha'].orders) == len(gateways['merchant-beta'].orders) == 1

        assert alpha.delete(auth).status_code == 200
        assert alpha.post(auth,json={'email':'alpha@example.test','password':password}).status_code == 200
        assert alpha.get(campaign_path+alpha_id).status_code == 200
        assert settle(alpha, alpha_offer, admin=False)['pledges'][0]['state'] == 'bonus_paid'
        assert settle(beta, beta_offer, admin=False)['pledges'][0]['state'] == 'released'
        assert len(gateways['merchant-alpha'].payouts) == 2
        assert gateways['merchant-beta'].payouts == {}

    with app.state.factory() as db:
        rows = db.scalars(select(Record).where(Record.kind.in_(('paypal_connection','merchant')))).all()
        assert len(rows) == 4
        assert all(value not in row.data for row in rows for value in (*secrets.values(), password))
