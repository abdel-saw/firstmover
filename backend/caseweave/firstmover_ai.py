import asyncio
import hashlib
import hmac
import json
import secrets
import time
from fastapi import HTTPException
from sqlalchemy import select, update, func
from sqlalchemy.exc import IntegrityError
from .db import FirstMoverRecord as Record
from .campaign_planner import CampaignProposal, propose_campaign
from .drafting import DraftFailure


class CampaignAI:
    def __init__(self,factory,settings):self.factory,self.settings=factory,settings

    async def run(self,owner,brief):
        key='plan:'+owner+':'+hashlib.sha256(brief.encode()).hexdigest()
        with self.factory() as db:
            job=db.scalar(select(Record).where(Record.unique_key==key))
            if job is None:
                recent=db.scalar(select(func.count()).select_from(Record).where(Record.kind=='plan',Record.owner==owner))
                if recent>=10:raise HTTPException(429,'AI proposal limit reached for this session.')
                job=Record(id=secrets.token_hex(16),kind='plan',owner=owner,state='pending',unique_key=key,
                           data=json.dumps({'brief':brief,'expires':time.time()+self.settings.drafter_wait_seconds}))
                db.add(job)
                try:db.commit()
                except IntegrityError:
                    db.rollback();job=db.scalar(select(Record).where(Record.unique_key==key))
            job_id=job.id
        if not self.settings.queue_drafter_configured:
            if not self.settings.ai_configured:raise HTTPException(503,'AI is not configured. You can edit a preset manually.')
            leased=self.claim(job_id)
            if leased['job']:
                try: result=await propose_campaign(brief,self.settings);error=None
                except DraftFailure as exc:result=None;error=exc.code
                self.complete(job_id,leased['job']['lease_token'],result,error)
        while True:
            with self.factory() as db:
                job=db.get(Record,job_id);data=json.loads(job.data)
                if job.state=='completed':return data['result']
                if job.state=='failed':raise HTTPException(502,'AI proposal failed: '+data.get('error','gateway_failed')+'. Edit the preset instead.')
                if data['expires']<=time.time():raise HTTPException(504,'AI worker did not complete the proposal. Check that the updated Astropods worker is deployed.')
            await asyncio.sleep(.3)

    def claim(self,job_id=None):
        with self.factory() as db:
            query=select(Record).where(Record.kind=='plan',Record.state=='pending')
            if job_id:query=query.where(Record.id==job_id)
            for row in db.scalars(query.limit(10)).all():
                data=json.loads(row.data)
                if data['expires']<=time.time():continue
                lease=secrets.token_urlsafe(32);data['lease_hash']=hashlib.sha256(lease.encode()).hexdigest()
                done=db.execute(update(Record).where(Record.id==row.id,Record.state=='pending').values(state='leased',data=json.dumps(data)))
                if done.rowcount:
                    db.commit()
                    return {'job':{'id':row.id,'kind':'firstmover_plan','lease_token':lease,'payload':{'brief':data['brief']}}}
            return {'job':None}

    def complete(self,job_id,lease,result=None,error=None):
        with self.factory() as db:
            row=db.get(Record,job_id)
            if not row or row.kind!='plan':raise HTTPException(404,'Proposal job not found.')
            data=json.loads(row.data)
            if not hmac.compare_digest(data.get('lease_hash',''),hashlib.sha256(lease.encode()).hexdigest()):raise HTTPException(404,'Proposal job not found.')
            clean=None
            if result is not None:
                try:
                    proposal=CampaignProposal.model_validate(result['proposal'])
                    if proposal.early_slots>proposal.threshold:raise ValueError()
                    if not isinstance(result['model'],str) or not 1<=len(result['model'])<=120:raise ValueError()
                    clean={'proposal':proposal.model_dump(),'model':result['model']}
                except (ValueError,KeyError,TypeError):raise HTTPException(422,'Invalid campaign proposal.')
            if row.state in ('completed','failed'):
                if data.get('result')==clean and data.get('error')==error:return {'accepted':True}
                raise HTTPException(409,'A different result was already recorded.')
            if row.state!='leased' or data['expires']<=time.time():raise HTTPException(409,'Proposal job expired.')
            data.update(result=clean,error=error)
            done=db.execute(update(Record).where(Record.id==job_id,Record.state=='leased').values(state='completed' if clean else 'failed',data=json.dumps(data)))
            if not done.rowcount:raise HTTPException(409,'Proposal job changed.')
            db.commit();return {'accepted':True}
