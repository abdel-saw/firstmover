import hashlib
import hmac
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, func, select

from .config import Settings
from .db import database, Visitor
from .drafting import DraftErrorCode
from .firstmover import install_firstmover
from .paypal import PayPal


def now():
    return datetime.now(timezone.utc).isoformat()


class WorkerCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_token: str = Field(min_length=32, max_length=128)
    result: dict | None = None
    error: DraftErrorCode | None = None

    @model_validator(mode="after")
    def one_outcome(self):
        if (self.result is None) == (self.error is None):
            raise ValueError("Provide exactly one result or error")
        return self


def create_app(settings=None):
    settings=settings or Settings()
    factory=database(settings.database_url)
    app=FastAPI(title="FirstMover",version="0.2.0")
    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        if request.url.path in (
            "/api/firstmover/organizer/session", "/api/firstmover/organizer/signup",
            "/api/firstmover/organizer/paypal",
        ):
            return JSONResponse({"detail":"Check the values entered in the form."}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    app.state.factory=factory
    app.state.settings=settings
    paypal=PayPal(settings)
    app.state.paypal=paypal
    def db():
        with factory() as session:
            yield session

    def find_visitor(request:Request, session=Depends(db)):
        token=request.cookies.get("cw_session","")
        owner=hashlib.sha256(token.encode()).hexdigest()
        v=session.get(Visitor,owner)
        if not token or not v or datetime.fromisoformat(v.created)<datetime.now(timezone.utc)-timedelta(days=7):
            return None
        return owner

    def visitor(owner=Depends(find_visitor)):
        if owner is None:
            raise HTTPException(401,"Start a participant session first.")
        return owner

    def admin(request:Request):
        supplied=request.headers.get("authorization","")
        if not settings.admin_token or not hmac.compare_digest(supplied,"Bearer "+settings.admin_token):
            raise HTTPException(403,"A server-configured admin token is required for sandbox access.")

    def drafting_worker(request:Request):
        supplied=request.headers.get("authorization", "")
        if (not settings.queue_drafter_configured or not hmac.compare_digest(supplied, "Bearer " + settings.drafter_shared_token)):
            raise HTTPException(403, "An authorized drafting worker is required.")

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

    @app.post("/api/session")
    def start(request:Request,response:Response,session=Depends(db)):
        existing=request.cookies.get("cw_session","")
        owner=hashlib.sha256(existing.encode()).hexdigest()
        v=session.get(Visitor,owner)
        cutoff=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
        session.execute(delete(Visitor).where(Visitor.created<cutoff))
        if v and v.created>=cutoff:
            session.commit()
            return {"started":True}
        if session.scalar(select(func.count()).select_from(Visitor))>=settings.max_sessions:
            raise HTTPException(503,"Participant session capacity reached. Please try again later.")
        token=secrets.token_urlsafe(32)
        owner=hashlib.sha256(token.encode()).hexdigest()
        session.add(Visitor(id=owner,created=now()))
        session.commit()
        response.set_cookie("cw_session",token,httponly=True,secure=settings.cookie_secure,samesite="lax",max_age=604800)
        return {"started":True}

    campaign_ai=install_firstmover(app,factory,settings,paypal,visitor,find_visitor,admin)

    @app.post("/api/internal/drafting/claim", dependencies=[Depends(drafting_worker)])
    def claim_drafting_job():
        return campaign_ai.claim()

    @app.post("/api/internal/drafting/jobs/{job_id}/complete", dependencies=[Depends(drafting_worker)])
    def complete_drafting_job(job_id: str, body: WorkerCompletion):
        return campaign_ai.complete(job_id, body.lease_token, body.result, body.error)

    dist=Path(__file__).resolve().parents[2]/"frontend"/"dist"
    if (dist/"assets").exists():
        app.mount("/assets",StaticFiles(directory=dist/"assets"),name="assets")
    @app.get("/")
    @app.get("/organizer")
    @app.get("/campaign/{campaign_id}")
    def index():
        if (dist/"index.html").exists():
            return FileResponse(dist/"index.html")
        return {"message":"FirstMover API ready. Build frontend with npm ci && npm run build in frontend/."}
    return app

app=create_app()
