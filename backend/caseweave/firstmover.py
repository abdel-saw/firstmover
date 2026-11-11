"""FirstMover sandbox experiment. AI proposes; people consent; recorded operations settle.

No cross-payment atomicity, segregated reserve, production assurance contract, or automatic
retries of uncertain writes is implied. Re-entering an operation only re-reads provider state.
"""
import hashlib
import json
import re
import secrets
import time
from decimal import Decimal, InvalidOperation
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, update, func
from sqlalchemy.exc import IntegrityError
from .db import FirstMoverRecord as Record
from .paypal import PayPalError
from .firstmover_ai import CampaignAI
from .organizer import WORKSPACE, install_organizer_login


def amount(cents): return f'{cents/100:.2f}'
def cents(value):
    try:
        n=Decimal(str(value))*100
        if n!=n.to_integral_value():raise ValueError()
        return int(n)
    except (InvalidOperation,ValueError,TypeError):raise ValueError('Invalid money amount')
def digest(data):return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()
def data(row):return json.loads(row.data)
def audit(d,action,detail):d.setdefault('audit',[]).append({'at':time.time(),'action':action,'detail':detail})


class Terms(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    title: str=Field(min_length=3,max_length=80)
    description: str=Field(min_length=10,max_length=600)
    threshold: int=Field(ge=2,le=6)
    price_cents: int=Field(ge=100,le=2000)
    early_slots: int=Field(ge=1,le=3)
    bonus_cents: int=Field(ge=1,le=200)
    duration_minutes: int=Field(ge=1,le=120)
    @model_validator(mode='after')
    def early(self):
        if self.early_slots>self.threshold:raise ValueError('Early places cannot exceed the group size.')
        return self

class Launch(BaseModel):
    terms: Terms
    mode: str=Field(pattern='^(demo|sandbox)$')
    approved: bool
class Join(BaseModel):
    name: str=Field(min_length=1,max_length=40)
    terms_digest: str=Field(min_length=64,max_length=64)
    accepted: bool
class Brief(BaseModel):
    retry: bool=False
    brief: str=Field(min_length=15,max_length=4000)
class Probe(BaseModel):
    recipient: str=Field(pattern=r'^[^\s@]{1,80}@[^\s@]{1,100}\.[^\s@]{2,30}$')
    approved: bool


def install_firstmover(app,factory,settings,paypal,visitor,find_visitor,admin):
    ai=CampaignAI(factory,settings)
    app.state.campaign_ai=ai
    signed_in = install_organizer_login(app, factory, settings)

    def get(key,kind=None):
        with factory() as db:
            row=db.get(Record,key)
            if not row or kind and row.kind!=kind:raise HTTPException(404,'Record not found.')
            return row
    def add(row):
        with factory() as db:
            db.add(row)
            try:db.commit()
            except IntegrityError:raise HTTPException(409,'This participant or operation is already recorded.')
        return row
    def save(row,d=None,state=None):
        with factory() as db:
            r=db.execute(update(Record).where(Record.id==row.id,Record.revision==row.revision).values(
                data=json.dumps(d if d is not None else data(row)),state=state or row.state,revision=row.revision+1))
            if r.rowcount!=1:raise HTTPException(409,'Record changed. Refresh before continuing.')
            db.commit()
        return get(row.id)
    def children(cid,kind='pledge'):
        with factory() as db:return db.scalars(select(Record).where(Record.parent==cid,Record.kind==kind).order_by(Record.id)).all()
    def organizer(request: Request, owner=Depends(find_visitor)):
        if signed_in(request):
            return WORKSPACE
        require_organizer(request)
        if owner is None:
            raise HTTPException(401, "Start a session first.")
        return owner

    def owner_action(row, owner, request):
        if row.owner == owner and signed_in(request):
            return
        if row.owner != owner:
            raise HTTPException(404, 'Record not found.')
        require_organizer(request)

    def require_organizer(request):
        if 'authorization' not in request.headers:
            raise HTTPException(403, 'Sign in as organizer to manage campaigns.')
        admin(request)

    def viewer(row, request, visitor_id):
        if signed_in(request) and row.owner in (WORKSPACE, visitor_id):
            return row.owner
        return visitor_id

    def summary(row,owner=None,*,manage=False):
        d=data(row);pledges=children(row.id)
        out={**d,'id':row.id,'state':row.state,'is_owner':manage and row.owner==owner,
             'pledges':[],'operations':[]}
        out.pop('lock_until',None)
        authorized_order=sorted([p for p in pledges if p.state in ('authorized','captured','released','bonus_paid')],key=lambda x:data(x).get('authorized_at',data(x).get('joined_at',0)))
        early_ids=set(d.get('eligible_ids',[p.id for p in authorized_order[:d['terms']['early_slots']]]))
        for p in sorted(pledges,key=lambda x:data(x).get('joined_at',0)):
            if not manage and p.owner != owner:
                continue
            pd=data(p)
            out['pledges'].append({'id':p.id,'name':pd['name'],'state':p.state,'early':p.id in early_ids,
                                   'is_mine':p.owner==owner,'order_id':pd.get('order_id') if p.owner==owner or manage else None,
                                   'authorization_id':pd.get('authorization_id') if manage else None})
        if manage and row.owner==owner:
            out['operations']=[{'id':op.id,'state':op.state,**{k:v for k,v in data(op).items() if k in ('label','provider_id','status','message','at')}} for op in children(row.id,'operation')]
        else:
            out.pop('audit',None)
            out.pop('eligible_ids',None)
        out['authorized_count']=sum(p.state in ('authorized','captured','released','bonus_paid') for p in pledges)
        out['max_bonus_cents']=d['terms']['early_slots']*d['terms']['bonus_cents']
        return out

    def money(raw,expected):
        if raw.get('currency_code')!='USD' or cents(raw.get('value'))!=expected:raise ValueError('Provider amount/currency mismatch')
    def ident(raw,expected=None):
        pid=raw.get('id')
        if not isinstance(pid,str) or not re.fullmatch(r'[A-Z0-9-]{3,80}',pid) or expected and pid!=expected:raise ValueError('Provider identity mismatch')
        return pid
    def unit(order,p,terms):
        ident(order,p.get('order_id'))
        units=order.get('purchase_units',[])
        if order.get('intent')!='AUTHORIZE' or len(units)!=1 or units[0].get('custom_id')!=p['commitment_id']:raise ValueError('Order does not match commitment')
        money(units[0]['amount'],terms['price_cents'])
        merchant=units[0].get('payee',{}).get('merchant_id')
        if not merchant or p.get('merchant_id') and merchant!=p['merchant_id']:raise ValueError('Merchant identity mismatch')
        return units[0]
    def payer_identity(order):
        old=order.get('payer',{}).get('payer_id')
        new=order.get('payment_source',{}).get('paypal',{}).get('account_id')
        if old and new and old!=new:raise HTTPException(502,'PayPal payer identities disagree.')
        return old or new

    async def check_order(p,terms):
        raw=await paypal.request('GET','/v2/checkout/orders/'+p['order_id'])
        unit(raw,p,terms);return raw

    async def once(key,owner,parent,label,call,read,sim=False):
        """Reserve before POST. An existing operation can only GET/reconcile, never POST again."""
        oid='fmop-'+hashlib.sha256(key.encode()).hexdigest()[:40]
        with factory() as db:op=db.get(Record,oid)
        fresh=op is None
        if fresh:
            op=add(Record(id=oid,kind='operation',owner=owner,parent=parent,state='pending',
                          data=json.dumps({'label':label,'at':time.time()})))
        if op.state=='completed':return data(op)
        if op.state=='failed':raise HTTPException(409,'The provider rejected this operation. Inspect the rejection and use recovery; it will not be sent again.')
        d=data(op);dispatch_done=False
        try:
            if sim:
                d.update(provider_id='SIM-'+oid[-10:].upper(),status='SIMULATED',message='Local simulation; no PayPal call.')
                save(op,d,'completed');return d
            if fresh:
                response=await call(oid)
                dispatch_done=True
                if isinstance(response,dict) and response.get('id'):d['provider_id']=ident(response)
                elif isinstance(response,dict) and response.get('batch_header',{}).get('payout_batch_id'):
                    d['provider_id']=ident({'id':response['batch_header']['payout_batch_id']})
                op=save(op,d,'pending')
            verified=await read(d,oid)
            d.update(verified)
            state='completed' if verified.get('verified') else 'pending'
            save(op,d,state);return d
        except (PayPalError,ValueError,KeyError,TypeError,IndexError) as exc:
            d['message']=exc.message if isinstance(exc,PayPalError) else 'Provider response could not be matched to this operation. Inspect PayPal before continuing.'
            rejected=bool(fresh and not dispatch_done and isinstance(exc,PayPalError) and exc.http_status in (400,401,403,422,429))
            d['status']='REJECTED' if rejected else 'UNRESOLVED'
            save(get(oid),d,'failed' if rejected else 'review')
            raise HTTPException(502,d['message'])

    async def payout(key,owner,parent,recipient,recipient_type,value,label,sim=False):
        async def call(oid):
            return await paypal.request('POST','/v1/payments/payouts',headers={'PayPal-Request-Id':oid},json={
                'sender_batch_header':{'sender_batch_id':oid,'email_subject':'FirstMover sandbox participation bonus'},
                'items':[{'recipient_type':recipient_type,'receiver':recipient,'amount':{'value':amount(value),'currency':'USD'},
                          'sender_item_id':oid,'note':'FirstMover sandbox experiment. Virtual funds.'}]})
        async def read(d,oid):
            if not d.get('provider_id'):raise ValueError('Payout identifier unknown')
            raw=await paypal.request('GET','/v1/payments/payouts/'+d['provider_id'])
            header=raw['batch_header']
            if header.get('payout_batch_id')!=d['provider_id'] or header.get('sender_batch_header',{}).get('sender_batch_id')!=oid:raise ValueError()
            items=raw.get('items',[])
            if len(items)!=1:raise ValueError()
            item=items[0];body=item['payout_item']
            if (body.get('receiver','').lower()!=recipient.lower() or body.get('recipient_type')!=recipient_type or body.get('sender_item_id')!=oid):raise ValueError()
            money({'currency_code':body['amount']['currency'],'value':body['amount']['value']},value)
            status=item['transaction_status']
            return {'provider_id':d['provider_id'],'status':status,'verified':status=='SUCCESS',
                    'message':'Recipient success verified with PayPal.' if status=='SUCCESS' else 'Payout not confirmed successful. Status: '+status}
        return await once(key,owner,parent,label,call,read,sim)

    def probe_ok():
        with factory() as db:
            row=db.get(Record,'fm-payout-probe')
            return bool(row and row.state=='completed')

    @app.get('/api/firstmover/config')
    def config():
        return {'paypal_configured':settings.paypal_configured,'paypal_client_id':settings.paypal_client_id if settings.paypal_configured else '',
                'ai_configured':settings.ai_configured,'payout_verified':probe_ok(),'environment':'sandbox'}

    @app.post('/api/firstmover/payout-check')
    async def probe(body:Probe,owner=Depends(organizer)):
        if not body.approved:raise HTTPException(422,'Approve the USD 0.01 sandbox test payout.')
        with factory() as db:row=db.get(Record,'fm-payout-probe')
        pd=data(row) if row else {'recipient':body.recipient,'attempt':0}
        previous_key='payout-probe-v1:'+str(pd.get('attempt',0))
        previous_id='fmop-'+hashlib.sha256(previous_key.encode()).hexdigest()[:40]
        with factory() as db:previous=db.get(Record,previous_id)
        rejected=previous and previous.state=='failed'
        if pd['recipient'].lower()!=body.recipient.lower() and not rejected:
            raise HTTPException(409,'Reconcile the original payout with its original recipient.')
        if row and rejected:
            pd.update(recipient=body.recipient,attempt=pd.get('attempt',0)+1)
            row=save(row,pd)
        elif not row:
            row=add(Record(id='fm-payout-probe',kind='probe',owner=owner,state='pending',data=json.dumps(pd)))
        result=await payout('payout-probe-v1:'+str(pd.get('attempt',0)),owner,'fm-payout-probe',body.recipient,'EMAIL',1,'USD 0.01 sandbox permission test')
        if result.get('verified') or result.get('status')=='SUCCESS':save(get(row.id),pd,'completed')
        return {'verified':probe_ok(),'status':result.get('status'),'message':result.get('message'),'payout_batch_id':result.get('provider_id')}

    @app.post('/api/firstmover/plan')
    async def plan(body:Brief,owner=Depends(organizer)):
        return await ai.run(owner,body.brief,body.retry)

    @app.get('/api/firstmover/campaigns')
    def listing(owner=Depends(organizer), browser_owner=Depends(find_visitor)):
        with factory() as db:rows=db.scalars(select(Record).where(Record.kind=='campaign',Record.owner.in_((owner,browser_owner))).order_by(Record.id)).all()
        return [summary(row,row.owner,manage=True) for row in rows]

    @app.post('/api/firstmover/campaigns')
    def launch(body:Launch,owner=Depends(organizer)):
        if not body.approved:raise HTTPException(422,'Review and approve the campaign terms.')
        if body.mode=='sandbox':
            if not settings.paypal_configured or not probe_ok():raise HTTPException(409,'Verify a successful sandbox Payouts test first.')
        with factory() as db:
            count=db.scalar(select(func.count()).select_from(Record).where(Record.kind=='campaign',Record.owner==owner))
            if count>=12:raise HTTPException(429,'Campaign limit reached for this session.')
        terms=body.terms.model_dump();created=time.time()
        d={'terms':terms,'terms_digest':digest(terms),'mode':body.mode,'created':created,'deadline':created+terms['duration_minutes']*60,
           'outcome':None,'audit':[]}
        audit(d,'terms_approved','Organizer approved these exact terms. Bonus exposure excludes PayPal fees and is not a segregated reserve.')
        row=add(Record(id=secrets.token_hex(16),kind='campaign',owner=owner,state='open',data=json.dumps(d)))
        return summary(row,owner,manage=True)

    @app.get('/api/firstmover/public-campaigns')
    def public_campaigns():
        with factory() as db:
            rows = db.scalars(select(Record).where(
                Record.kind == 'campaign', Record.state == 'open',
            )).all()
        current = time.time()
        return [
            summary(row) for row in sorted(rows, key=lambda row: data(row)['deadline'])
            if data(row)['mode'] == 'sandbox' and data(row)['deadline'] > current
        ]

    @app.get('/api/firstmover/public-campaigns/{cid}')
    def public_campaign(cid: str, owner=Depends(find_visitor)):
        row = get(cid, 'campaign')
        if data(row)['mode'] != 'sandbox':
            raise HTTPException(404, 'Campaign not found.')
        return summary(row, owner)

    @app.get('/api/firstmover/campaigns/{cid}')
    def show(cid: str, request: Request, owner=Depends(find_visitor)):
        row = get(cid, 'campaign')
        identity = viewer(row, request, owner)
        owner_action(row, identity, request)
        return summary(row, identity, manage=True)

    def reserve_join(cid,body,owner):
        row=get(cid,'campaign');d=data(row)
        if row.state!='open' or time.time()>=d['deadline']:raise HTTPException(409,'This campaign is closed for new commitments.')
        if not body.accepted or body.terms_digest!=d['terms_digest']:raise HTTPException(409,'Accept the exact published terms first.')
        if len(children(cid))>=12:raise HTTPException(409,'Campaign checkout limit reached.')
        # Reserve under the same campaign revision used by close/authorize.
        with factory() as db:
            changed=db.execute(update(Record).where(Record.id==cid,Record.revision==row.revision,Record.state=='open').values(revision=row.revision+1))
            if not changed.rowcount:raise HTTPException(409,'Campaign changed. Refresh.')
            pid=secrets.token_hex(16)
            p={'name':body.name,'terms_digest':body.terms_digest,'commitment_id':pid,'joined_at':time.time(),'early':False}
            pledge=Record(id=pid,kind='pledge',owner=owner,parent=cid,state='creating',data=json.dumps(p));db.add(pledge);db.commit()
        return row,d,pledge,p

    @app.post('/api/firstmover/campaigns/{cid}/join')
    async def join(cid:str,body:Join,request:Request,owner=Depends(find_visitor)):
        campaign = get(cid,'campaign')
        demo = data(campaign)['mode']=='demo'
        if demo:
            owner = viewer(campaign,request,owner)
            owner_action(campaign,owner,request)
        elif owner is None:
            raise HTTPException(401, "Start a participant session first.")
        row,d,pledge,p=reserve_join(cid,body,owner)
        if d['mode']=='demo':
            existing=sorted([x for x in children(cid) if x.state in ('authorized','creating')],key=lambda x:data(x)['joined_at'])
            if pledge.id not in [x.id for x in existing[:d['terms']['threshold']]]:
                save(pledge,p,'not_joined');raise HTTPException(409,'The group is full.')
            p.update(authorized_at=time.time(),early=next(i for i,x in enumerate(existing) if x.id==pledge.id)<d['terms']['early_slots'],order_id='SIM-ORDER-'+pledge.id[:8].upper(),authorization_id='SIM-AUTH-'+pledge.id[:8].upper())
            save(pledge,p,'authorized');return summary(get(cid),owner,manage=True)
        async def call(oid):
            return await paypal.request('POST','/v2/checkout/orders',headers={'PayPal-Request-Id':oid,'Prefer':'return=representation'},json={
                'intent':'AUTHORIZE','purchase_units':[{'reference_id':cid,'custom_id':pledge.id,
                'description':'FirstMover conditional workshop commitment','amount':{'currency_code':'USD','value':amount(d['terms']['price_cents'])}}],
                'application_context':{'shipping_preference':'NO_SHIPPING','brand_name':'FirstMover sandbox'}})
        async def read(od,oid):
            if not od.get('provider_id'):raise ValueError('Order ID unknown')
            pp={**p,'order_id':od['provider_id']}
            order=await paypal.request('GET','/v2/checkout/orders/'+od['provider_id'])
            pu=unit(order,pp,d['terms'])
            return {'verified':True,'provider_id':od['provider_id'],'merchant_id':pu['payee']['merchant_id'],'status':order['status']}
        try:
            result=await once('order:'+pledge.id,owner,cid,'Create authorization order',call,read)
            p.update(order_id=result['provider_id'],merchant_id=result['merchant_id'])
            save(pledge,p,'awaiting_approval')
            return {'commitment_id':pledge.id,'order_id':p['order_id']}
        except HTTPException:
            save(get(pledge.id),p,'review');raise

    @app.post('/api/firstmover/pledges/{pid}/authorize')
    async def authorize(pid:str,owner=Depends(visitor)):
        pledge=get(pid,'pledge')
        if pledge.owner != owner:raise HTTPException(404,'Record not found.')
        p=data(pledge);row=get(pledge.parent,'campaign');d=data(row)
        if d['mode']!='sandbox':raise HTTPException(409,'Demo commitments do not contact PayPal.')
        if pledge.state=='authorized':return summary(row,owner)
        if pledge.state!='awaiting_approval':raise HTTPException(409,'This authorization needs reconciliation before another action.')
        order=await check_order(p,d['terms'])
        if order.get('status')!='APPROVED':raise HTTPException(409,'Approve the order in PayPal first.')
        payer=payer_identity(order)
        if not isinstance(payer,str) or not re.fullmatch(r'[A-Z0-9]{5,32}',payer):raise HTTPException(502,'PayPal did not identify the payer.')
        current=get(row.id);cd=data(current)
        if current.state!='open' or time.time()>=cd['deadline']:raise HTTPException(409,'Campaign no longer accepts authorizations.')
        if sum(x.state in ('authorized','authorizing') for x in children(row.id))>=cd['terms']['threshold']:raise HTTPException(409,'The group is full.')
        p['payer_id']=payer
        with factory() as db:
            changed=db.execute(update(Record).where(Record.id==current.id,Record.revision==current.revision,Record.state=='open').values(revision=current.revision+1))
            if not changed.rowcount:raise HTTPException(409,'Campaign changed. Refresh.')
            try:
                r=db.execute(update(Record).where(Record.id==pid,Record.revision==pledge.revision,Record.state=='awaiting_approval').values(state='authorizing',data=json.dumps(p),revision=pledge.revision+1,unique_key='payer:'+current.id+':'+payer))
                if not r.rowcount:raise HTTPException(409,'Commitment changed.')
                db.commit()
            except IntegrityError:
                db.rollback()
                raise HTTPException(409,'This PayPal account already has a commitment in this campaign.')
        async def call(oid):return await paypal.request('POST','/v2/checkout/orders/'+p['order_id']+'/authorize',headers={'PayPal-Request-Id':oid,'Prefer':'return=representation'},json={})
        async def read(od,oid):
            order=await check_order(p,cd['terms']);auths=order['purchase_units'][0].get('payments',{}).get('authorizations',[])
            if len(auths)!=1:raise ValueError('Authorization not visible yet')
            aid=ident(auths[0]);raw=await paypal.request('GET','/v2/payments/authorizations/'+aid)
            ident(raw,aid);money(raw['amount'],cd['terms']['price_cents'])
            return {'provider_id':aid,'status':raw['status'],'verified':raw['status']=='CREATED'}
        try:
            result=await once('authorize:'+pid,owner,row.id,'Authorize participant hold',call,read)
            if result.get('verified'):p.update(authorized_at=time.time(),authorization_id=result['provider_id'],early=sum(x.state=='authorized' for x in children(row.id))<cd['terms']['early_slots']);save(get(pid),p,'authorized')
            return summary(get(row.id),owner)
        except HTTPException:save(get(pid),p,'review');raise

    @app.post('/api/firstmover/campaigns/{cid}/close')
    def close(cid:str,request:Request,owner=Depends(find_visitor)):
        row=get(cid,'campaign');owner=viewer(row,request,owner);owner_action(row,owner,request);d=data(row)
        if row.state!='open':return summary(row,owner,manage=True)
        pledges=children(cid)
        if any(x.state in ('creating','authorizing','review') for x in pledges):raise HTTPException(409,'Resolve unfinished checkouts before closing. Use participant reconciliation or abandon an unapproved checkout.')
        ready=sorted([x for x in pledges if x.state=='authorized'],key=lambda x:data(x).get('authorized_at',data(x)['joined_at']))
        filled=len(ready)>=d['terms']['threshold']
        if not filled and time.time()<d['deadline'] and d['mode']!='demo':raise HTTPException(409,'Wait until the published deadline before declaring the group unfilled.')
        d['outcome']='fund' if filled else 'release'
        d['eligible_ids']=[x.id for x in ready[:d['terms']['early_slots']]]
        if d['mode']=='demo' and not filled:
            audit(d,'simulation_clock','Demo deadline advanced locally; no sandbox clock was changed.')
        audit(d,'settlement_approved','Organizer approved '+('capturing the filled group.' if filled else 'releasing holds and paying eligible bonuses.'))
        row=save(row,d,'settling')
        return summary(row,owner,manage=True)

    async def financial_step(row,pledge,action):
        d=data(row);p=data(pledge);terms=d['terms'];sim=d['mode']=='demo'
        aid=p.get('authorization_id');key=action+':'+pledge.id
        async def call(oid):
            if action=='capture':
                return await paypal.request('POST','/v2/payments/authorizations/'+aid+'/capture',headers={'PayPal-Request-Id':oid,'Prefer':'return=representation'},json={'amount':{'currency_code':'USD','value':amount(terms['price_cents'])},'final_capture':True})
            if action=='void':return await paypal.request('POST','/v2/payments/authorizations/'+aid+'/void',headers={'PayPal-Request-Id':oid})
            return await paypal.request('POST','/v2/payments/captures/'+p['capture_id']+'/refund',headers={'PayPal-Request-Id':oid,'Prefer':'return=representation'},json={'amount':{'currency_code':'USD','value':amount(terms['price_cents'])}})
        async def read(od,oid):
            if action=='void':
                raw=await paypal.request('GET','/v2/payments/authorizations/'+aid);ident(raw,aid);money(raw['amount'],terms['price_cents'])
                return {'provider_id':aid,'status':raw['status'],'verified':raw['status']=='VOIDED'}
            if action=='capture':
                order=await check_order(p,terms)
                captures=order['purchase_units'][0].get('payments',{}).get('captures',[])
                if len(captures)!=1:raise ValueError('Exactly one capture must be visible on this order')
                capid=ident(captures[0]);raw=await paypal.request('GET','/v2/payments/captures/'+capid)
                ident(raw,capid);money(raw['amount'],terms['price_cents'])
                if od.get('provider_id') and capid!=od['provider_id']:raise ValueError('Capture ID mismatch')
                related=raw.get('supplementary_data',{}).get('related_ids',{})
                if related.get('authorization_id',aid)!=aid:raise ValueError('Authorization mismatch')
                return {'provider_id':capid,'status':raw['status'],'verified':raw['status']=='COMPLETED'}
            if not od.get('provider_id'):raise ValueError('Refund ID unknown; inspect PayPal before recovery')
            raw=await paypal.request('GET','/v2/payments/refunds/'+od['provider_id']);ident(raw,od['provider_id']);money(raw['amount'],terms['price_cents'])
            return {'provider_id':raw['id'],'status':raw['status'],'verified':raw['status']=='COMPLETED'}
        if not sim:
            # Re-read the bound order before any capture/refund; void operates on its saved authorization.
            order=await check_order(p,terms)
            auths=order['purchase_units'][0].get('payments',{}).get('authorizations',[])
            if not any(x.get('id')==aid for x in auths):raise HTTPException(502,'Saved authorization is not present on its original order.')
        result=await once(key,row.owner,row.id,{'capture':'Capture commitment','void':'Release authorization','refund':'Recover captured commitment'}[action],call,read,sim)
        if sim or result.get('verified'):
            p[action+'_id']=result['provider_id'];p['last_status']=result.get('status')
            save(get(pledge.id),p,{'capture':'captured','void':'released','refund':'refunded'}[action])
            return True
        return False

    @app.post('/api/firstmover/campaigns/{cid}/step')
    async def step(cid:str,request:Request,owner=Depends(find_visitor)):
        row=get(cid,'campaign');owner=viewer(row,request,owner);owner_action(row,owner,request);d=data(row)
        if row.state in ('funded','released','recovered'):return summary(row,owner,manage=True)
        if row.state not in ('settling','review','recovering'):raise HTTPException(409,'Approve settlement first.')
        if d.get('lock_until',0)>time.time():raise HTTPException(409,'A settlement step is already running. Refresh later.')
        d['lock_until']=time.time()+180;row=save(row,d)
        try:
            pledges=children(cid)
            if d.get('outcome')=='recover':
                for p in pledges:
                    if p.state=='captured':
                        await financial_step(row,p,'refund');return summary(get(cid),owner,manage=True)
                    if p.state=='authorized':
                        await financial_step(row,p,'void');return summary(get(cid),owner,manage=True)
                if any(p.state not in ('refunded','released','not_joined','awaiting_approval') for p in pledges):raise HTTPException(409,'Some commitments still need individual reconciliation.')
                audit(d,'recovery_verified','Captured commitments refunded and remaining holds released. No group-failure bonus is paid on technical recovery.')
                save(row,d,'recovered');return summary(get(cid),owner,manage=True)
            for p in pledges:
                if p.state=='authorized':
                    done=await financial_step(row,p,'capture' if d['outcome']=='fund' else 'void')
                    if not done:audit(d,'waiting','Provider operation is pending. Refresh and reconcile later.')
                    return summary(get(cid),owner,manage=True)
            if d['outcome']=='fund':
                if sum(p.state=='captured' for p in pledges)!=d['terms']['threshold']:raise HTTPException(409,'Capture count does not match the approved threshold. Recover the campaign.')
                audit(d,'funding_verified','Every required capture is complete. These results do not prove service delivery.')
                save(row,d,'funded');return summary(get(cid),owner,manage=True)
            for p in pledges:
                if p.id in d.get('eligible_ids',[]) and p.state=='released':
                    pd=data(p)
                    result=await payout('bonus:'+p.id,owner,cid,pd.get('payer_id','DEMO'),'PAYPAL_ID',d['terms']['bonus_cents'],'Early participation bonus',d['mode']=='demo')
                    if d['mode']=='demo' or result.get('verified'):
                        pd['bonus_id']=result['provider_id'];save(get(p.id),pd,'bonus_paid')
                    return summary(get(cid),owner,manage=True)
            if any(p.id in d.get('eligible_ids',[]) and p.state!='bonus_paid' for p in pledges):raise HTTPException(409,'Not all bonuses are confirmed.')
            audit(d,'release_verified','All holds released; eligible bonus payouts confirmed. Hold availability timing is controlled by the funding provider.')
            save(row,d,'released');return summary(get(cid),owner,manage=True)
        except HTTPException as exc:
            current=get(cid);cd=data(current);audit(cd,'needs_review',str(exc.detail));save(current,cd,'review');raise
        finally:
            current=get(cid);cd=data(current);cd['lock_until']=0;save(current,cd)

    @app.post('/api/firstmover/campaigns/{cid}/recover')
    def recovery(cid:str,request:Request,owner=Depends(find_visitor)):
        row=get(cid,'campaign');owner=viewer(row,request,owner);owner_action(row,owner,request);d=data(row)
        if row.state not in ('review','settling'):raise HTTPException(409,'Recovery is only available during an unfinished settlement.')
        if d.get('lock_until',0)>time.time():raise HTTPException(409,'A settlement step is still running.')
        # Unknown capture operations must be read back before deciding refund versus void.
        for p in children(cid):
            for op in children(cid,'operation'):
                if op.id=='fmop-'+hashlib.sha256(('capture:'+p.id).encode()).hexdigest()[:40] and op.state not in ('completed','failed'):
                    raise HTTPException(409,'Reconcile the uncertain capture using Resume before recovery.')
        d['outcome']='recover';audit(d,'recovery_approved','Organizer approved refunding captured payments and voiding remaining holds. No participation bonuses for technical recovery.')
        return summary(save(row,d,'recovering'),owner,manage=True)

    @app.post('/api/firstmover/pledges/{pid}/reconcile')
    async def reconcile(pid:str,request:Request,owner=Depends(find_visitor)):
        p_row=get(pid,'pledge');row=get(p_row.parent,'campaign');d=data(row);p=data(p_row)
        manage = p_row.owner != owner
        if manage:
            owner = viewer(row,request,owner)
            owner_action(row,owner,request)
        if d['mode']!='sandbox' or p_row.state not in ('review','creating','authorizing','awaiting_approval'):return summary(row,owner,manage=manage)
        if row.state!='open':raise HTTPException(409,'Use settlement reconciliation after a campaign closes.')
        if not p.get('order_id'):
            opid='fmop-'+hashlib.sha256(('order:'+pid).encode()).hexdigest()[:40]
            with factory() as db:op=db.get(Record,opid)
            if op and data(op).get('provider_id'):p['order_id']=data(op)['provider_id']
            else:raise HTTPException(409,'Order identity is unknown. Inspect PayPal before any new checkout; no automatic write retry is allowed.')
        order=await check_order(p,d['terms'])
        auths=order['purchase_units'][0].get('payments',{}).get('authorizations',[])
        if not auths:
            auth_op_id='fmop-'+hashlib.sha256(('authorize:'+pid).encode()).hexdigest()[:40]
            with factory() as db:auth_op=db.get(Record,auth_op_id)
            if auth_op and auth_op.state!='failed':
                raise HTTPException(409,'An authorization was dispatched with an unresolved outcome. Absence from a read is not proof of failure; inspect PayPal before closing.')
            if order.get('status') in ('CREATED','APPROVED','PAYER_ACTION_REQUIRED'):
                # No authorization exists. This checkout is excluded from settlement; never authorize a second time here.
                save(p_row,p,'not_joined');return summary(row,owner,manage=manage)
            raise HTTPException(409,'PayPal authorization state is unresolved.')
        if len(auths)!=1:raise HTTPException(409,'Unexpected authorization count. Inspect PayPal.')
        aid=ident(auths[0]);raw=await paypal.request('GET','/v2/payments/authorizations/'+aid);ident(raw,aid);money(raw['amount'],d['terms']['price_cents'])
        payer=payer_identity(order)
        if not payer or p.get('payer_id',payer)!=payer:raise HTTPException(502,'Payer identity mismatch.')
        p['payer_id']=payer
        p['authorization_id']=aid
        if raw['status']=='CREATED':
            p.setdefault('authorized_at',time.time());save(p_row,p,'authorized')
        elif raw['status']=='VOIDED':save(p_row,p,'released')
        else:raise HTTPException(409,'Authorization requires settlement reconciliation. Use Resume or inspect PayPal.')
        return summary(get(row.id),owner,manage=manage)

    return ai
