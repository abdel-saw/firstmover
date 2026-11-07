import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator, ConfigDict
from sqlalchemy import select, delete, update, func, or_
from sqlalchemy.exc import IntegrityError

from .config import Settings
from .db import database, Visitor, Case, Submission, DraftJob
from .draft_queue import DraftQueue
from .samples import sample_cases
from .analysis import analyze, timestamp
from .paypal import PayPal, PayPalError, normalize, can_submit


def now():
    return datetime.now(timezone.utc).isoformat()


def digest_for(case):
    return hashlib.sha256(json.dumps({"evidence":case["evidence"],"draft":case.get("draft",""),"paypal_id":case.get("paypal_id")},sort_keys=True).encode()).hexdigest()


class EvidenceInput(BaseModel):
    title: str = Field(min_length=3,max_length=120)
    kind: Literal["request","cancellation","charge","agreement","other"]
    occurred_at: str
    text: str = Field(min_length=5,max_length=6000)

    @field_validator("occurred_at")
    @classmethod
    def valid_time(cls, value):
        try:
            return timestamp(value).isoformat()
        except ValueError:
            raise ValueError("Use an ISO timestamp with timezone")

class DraftInput(BaseModel):
    draft: str = Field(min_length=10,max_length=2000)

class ApprovalInput(BaseModel):
    digest: str = Field(min_length=64,max_length=64)

class WorkerCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_token: str = Field(min_length=32, max_length=128)
    result: dict | None = None
    error: Literal["invalid_input", "gateway_failed"] | None = None

    @model_validator(mode="after")
    def one_outcome(self):
        if (self.result is None) == (self.error is None):
            raise ValueError("Provide exactly one result or error")
        return self


class ImportInput(BaseModel):
    dispute_id: str = Field(pattern=r"^PP-[A-Z0-9-]{3,60}$")


def create_app(settings=None):
    settings=settings or Settings()
    factory=database(settings.database_url)
    app=FastAPI(title="CaseWeave",version="0.1.0")
    app.state.factory=factory
    app.state.settings=settings
    paypal=PayPal(settings)
    app.state.paypal=paypal
    queue=DraftQueue(factory,settings)
    app.state.draft_queue=queue

    def db():
        with factory() as session:
            yield session

    def visitor(request:Request, session=Depends(db)):
        token=request.cookies.get("cw_session","")
        owner=hashlib.sha256(token.encode()).hexdigest()
        v=session.get(Visitor,owner)
        if not token or not v or datetime.fromisoformat(v.created)<datetime.now(timezone.utc)-timedelta(days=7):
            raise HTTPException(401,"Start a demo session first.")
        return owner

    def admin(request:Request):
        supplied=request.headers.get("authorization","")
        if not settings.admin_token or not hmac.compare_digest(supplied,"Bearer "+settings.admin_token):
            raise HTTPException(403,"A server-configured admin token is required for sandbox access.")

    def drafting_worker(request:Request):
        supplied=request.headers.get("authorization", "")
        if (not settings.queue_drafter_configured or not hmac.compare_digest(supplied, "Bearer " + settings.drafter_shared_token)):
            raise HTTPException(403, "An authorized drafting worker is required.")

    def load(session,case_id,owner):
        row=session.get(Case,case_id)
        if not row or row.owner!=owner:
            raise HTTPException(404,"Case not found.")
        return row,json.loads(row.data)

    def write(session,row,data):
        old=row.revision
        result=session.execute(update(Case).where(Case.id==row.id,Case.revision==old).values(data=json.dumps(data),revision=old+1))
        if result.rowcount!=1:
            session.rollback()
            raise HTTPException(409,"Case changed. Reload before trying again.")
        session.commit()

    def public(data):
        result=dict(data)
        result["digest"]=digest_for(data)
        return result

    def audit(data,action,detail):
        data.setdefault("audit",[]).append({"at":now(),"action":action,"detail":detail})

    def editable(data):
        if data.get("submission_state") in ("pending","uncertain","submitted"):
            raise HTTPException(409,"Submission is locked. Inspect the sandbox case before making further changes.")

    @app.middleware("http")
    async def headers(request, call_next):
        if request.method in ("POST","PUT","DELETE","PATCH"):
            # Same-origin browser requests only; API clients may omit Origin.
            origin=request.headers.get("origin")
            if origin and origin.rstrip("/")!=str(request.base_url).rstrip("/"):
                return Response("Cross-origin request rejected",status_code=403)
        result=await call_next(request)
        result.headers["X-Content-Type-Options"]="nosniff"
        result.headers["Referrer-Policy"]="same-origin"
        result.headers["X-Frame-Options"]="DENY"
        if request.url.path.startswith("/api"):
            result.headers["Cache-Control"]="no-store"
        return result

    @app.get("/api/health")
    def health():
        return {"status":"ok"}

    @app.get("/api/config")
    def config():
        return {"ai_configured":settings.ai_configured,"ai_model":("Astropods drafting worker" if settings.queue_drafter_configured else settings.ai_model) if settings.ai_configured else None,"ai_transport":"astropods_queue" if settings.queue_drafter_configured else "direct","paypal_configured":settings.paypal_configured,"paypal_environment":"sandbox","version":"0.1.0"}

    @app.post("/api/session")
    def start(request:Request,response:Response,session=Depends(db)):
        existing=request.cookies.get("cw_session","")
        owner=hashlib.sha256(existing.encode()).hexdigest()
        v=session.get(Visitor,owner)
        cutoff=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
        expired=select(Visitor.id).where(Visitor.created<cutoff)
        expired_cases=select(Case.id).where(Case.owner.in_(expired))
        session.execute(delete(DraftJob).where(DraftJob.case_id.in_(expired_cases)))
        session.execute(delete(Submission).where(Submission.case_id.in_(expired_cases)))
        session.execute(delete(Case).where(Case.owner.in_(expired)))
        session.execute(delete(Visitor).where(Visitor.created<cutoff))
        if v and v.created>=cutoff:
            session.commit()
            return {"started":True}
        if session.scalar(select(func.count()).select_from(Visitor))>=settings.max_sessions:
            raise HTTPException(503,"Demo capacity reached. Please try again later.")
        token=secrets.token_urlsafe(32)
        owner=hashlib.sha256(token.encode()).hexdigest()
        session.add(Visitor(id=owner,created=now()))
        for data in sample_cases():
            data["id"]=secrets.token_hex(12)
            data["audit"]=[{"at":now(),"action":"created","detail":"Synthetic sample loaded. No PayPal transaction was made."}]
            session.add(Case(id=data["id"],owner=owner,data=json.dumps(data)))
        session.commit()
        response.set_cookie("cw_session",token,httponly=True,secure=settings.cookie_secure,samesite="lax",max_age=604800)
        return {"started":True}

    @app.get("/api/cases")
    def cases(owner=Depends(visitor),session=Depends(db)):
        return [public(json.loads(row.data)) for row in session.scalars(select(Case).where(Case.owner==owner).order_by(Case.id))]

    @app.get("/api/cases/{case_id}")
    def case(case_id:str,owner=Depends(visitor),session=Depends(db)):
        return public(load(session,case_id,owner)[1])

    @app.post("/api/cases/{case_id}/evidence")
    def add_evidence(case_id:str,body:EvidenceInput,owner=Depends(visitor),session=Depends(db)):
        row,data=load(session,case_id,owner)
        editable(data)
        if len(data["evidence"])>=25:
            raise HTTPException(422,"A case supports up to 25 evidence records.")
        entry=body.model_dump()
        entry.update(id="E"+str(len(data["evidence"])+1),provenance="merchant")
        data["evidence"].append(entry)
        for key in ("analysis","draft","approved_digest"):
            data.pop(key,None)
        data["status"]="Needs review"
        audit(data,"evidence_added",entry["id"]+": "+entry["title"])
        write(session,row,data)
        return public(data)

    @app.post("/api/cases/{case_id}/analyze")
    async def analysis(case_id:str,owner=Depends(visitor),session=Depends(db)):
        row,data=load(session,case_id,owner)
        editable(data)
        recent=[a for a in data.get("audit",[]) if a["action"]=="analyzed" and datetime.fromisoformat(a["at"])>datetime.now(timezone.utc)-timedelta(minutes=1)]
        if len(recent)>=3:
            raise HTTPException(429,"Please wait a minute before analyzing this case again.")
        result=await analyze(data,settings,queue_runner=queue.run)
        data.update(analysis=result,draft=result["draft"],status="Ready for review")
        data.pop("approved_digest",None)
        audit(data,"analyzed","AI-assisted draft" if result["engine"]=="ai" else "Deterministic evidence checks; no AI response")
        write(session,row,data)
        return public(data)

    @app.put("/api/cases/{case_id}/draft")
    def draft(case_id:str,body:DraftInput,owner=Depends(visitor),session=Depends(db)):
        row,data=load(session,case_id,owner)
        editable(data)
        data["draft"]=body.draft
        data.pop("approved_digest",None)
        audit(data,"draft_edited","Merchant edited the response. Reapproval required.")
        write(session,row,data)
        return public(data)

    @app.post("/api/cases/{case_id}/approve")
    def approve(case_id:str,body:ApprovalInput,owner=Depends(visitor),session=Depends(db)):
        row,data=load(session,case_id,owner)
        editable(data)
        if not data.get("draft") or body.digest!=digest_for(data):
            raise HTTPException(409,"The draft or evidence changed. Review the current version.")
        data["approved_digest"]=body.digest
        data["status"]="Approved"
        audit(data,"approved","Merchant reviewed and approved this exact evidence and draft version.")
        write(session,row,data)
        return public(data)

    @app.get("/api/cases/{case_id}/export")
    def export(case_id:str,owner=Depends(visitor),session=Depends(db)):
        _,data=load(session,case_id,owner)
        return Response(json.dumps(public(data),indent=2),media_type="application/json",headers={"Content-Disposition":f'attachment; filename="caseweave-{case_id}.json"'})

    @app.post("/api/cases/{case_id}/simulate")
    def simulate(case_id:str,owner=Depends(visitor),session=Depends(db)):
        row,data=load(session,case_id,owner)
        if data["mode"]!="sample":
            raise HTTPException(409,"Only sample cases support simulated submission.")
        if data.get("approved_digest")!=digest_for(data):
            raise HTTPException(409,"Review and approve the current draft first.")
        if data.get("status")!="Sample completed":
            data["status"]="Sample completed"
            audit(data,"sample_submission","Simulated receipt only. No evidence was sent to PayPal.")
            write(session,row,data)
        return public(data)

    @app.post("/api/sample/reset")
    def reset(owner=Depends(visitor),session=Depends(db)):
        for row in session.scalars(select(Case).where(Case.owner==owner)):
            if json.loads(row.data)["mode"]=="sample":
                session.execute(delete(DraftJob).where(DraftJob.case_id==row.id))
                session.delete(row)
        for data in sample_cases():
            data["id"]=secrets.token_hex(12)
            data["audit"]=[{"at":now(),"action":"created","detail":"Synthetic sample reset."}]
            session.add(Case(id=data["id"],owner=owner,data=json.dumps(data)))
        session.commit()
        return {"reset":True}

    @app.post("/api/internal/drafting/claim", dependencies=[Depends(drafting_worker)])
    def claim_drafting_job():
        return queue.claim()

    @app.post("/api/internal/drafting/jobs/{job_id}/complete", dependencies=[Depends(drafting_worker)])
    def complete_drafting_job(job_id:str, body:WorkerCompletion):
        return queue.complete(job_id, body.lease_token, body.result, body.error)

    @app.post("/api/sandbox/import",dependencies=[Depends(admin)])
    async def import_case(body:ImportInput,owner=Depends(visitor),session=Depends(db)):
        if session.scalar(select(func.count()).select_from(Case).where(Case.owner==owner))>=30:
            raise HTTPException(422,"Session case limit reached.")
        try:
            raw=await paypal.dispute(body.dispute_id)
            data=normalize(raw)
        except PayPalError as exc:
            raise HTTPException(502,exc.message)
        except (KeyError,TypeError,ValueError):
            raise HTTPException(502,"PayPal returned unexpected dispute details.")
        data["id"]=secrets.token_hex(12)
        data["audit"]=[{"at":now(),"action":"sandbox_import","detail":"Retrieved dispute details from PayPal sandbox API."}]
        session.add(Case(id=data["id"],owner=owner,data=json.dumps(data)))
        session.commit()
        return public(data)

    @app.post("/api/cases/{case_id}/sandbox-submit",dependencies=[Depends(admin)])
    async def submit(case_id:str,owner=Depends(visitor),session=Depends(db)):
        row,data=load(session,case_id,owner)
        if data["mode"]!="sandbox":
            raise HTTPException(409,"Sample cases cannot be sent to PayPal.")
        digest=digest_for(data)
        if data.get("approved_digest")!=digest:
            raise HTTPException(409,"Review and approve the current draft first.")
        existing=session.scalar(select(Submission).where(or_(Submission.case_id==case_id,Submission.paypal_dispute_id==data["paypal_id"])))
        if existing:
            raise HTTPException(409,"A submission already exists for this case. Check PayPal before taking further action.")
        try:
            raw=await paypal.dispute(data["paypal_id"])
        except PayPalError as exc:
            raise HTTPException(502,exc.message)
        requested=[e.get("evidence_type") for e in raw.get("evidences",[]) if e.get("source")=="REQUESTED_FROM_SELLER"]
        if not can_submit(raw) or (requested and "OTHER" not in requested):
            raise HTTPException(409,"This case is not currently accepting OTHER evidence. Use PayPal's requested evidence type.")
        operation=Submission(id=secrets.token_hex(12),case_id=case_id,paypal_dispute_id=data["paypal_id"],digest=digest,state="pending",result="{}")
        session.add(operation)
        data["submission_state"]="pending"
        audit(data,"submission_started","Sending approved note to PayPal sandbox.")
        try:
            write(session,row,data)
        except IntegrityError:
            session.rollback()
            raise HTTPException(409,"Submission already started.")
        try:
            result=await paypal.submit(data["paypal_id"],data["draft"])
            operation.state="submitted"
            operation.result=json.dumps({"accepted":True})
            data.update(submission_state="submitted",status="Submitted to sandbox")
            audit(data,"sandbox_submission","PayPal sandbox accepted the evidence request. This is not a case decision.")
        except PayPalError as exc:
            operation.state="uncertain" if exc.uncertain else "failed"
            operation.result=json.dumps({"message":exc.message})
            data.update(submission_state=operation.state,status="Check PayPal" if exc.uncertain else "Submission failed")
            audit(data,"submission_"+operation.state,exc.message)
        fresh=session.get(Case,case_id,populate_existing=True)
        write(session,fresh,data)
        return public(data)

    dist=Path(__file__).resolve().parents[2]/"frontend"/"dist"
    if (dist/"assets").exists():
        app.mount("/assets",StaticFiles(directory=dist/"assets"),name="assets")
    @app.get("/")
    def index():
        if (dist/"index.html").exists():
            return FileResponse(dist/"index.html")
        return {"message":"CaseWeave API ready. Build frontend with npm ci && npm run build in frontend/."}
    return app

app=create_app()
