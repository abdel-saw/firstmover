"""Astropods worker: pulls jobs from Render; exposes no inference endpoint."""
import asyncio
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
import logging
import os
import re
import time
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, ValidationError

from .campaign_planner import propose_campaign
from .analysis import check_case
from .config import Settings
from .drafting import provider_draft, DraftFailure

log = logging.getLogger("caseweave.drafter")


@dataclass
class WorkerSettings:
    ai: Settings = field(default_factory=Settings)
    caseweave_url: str = field(default_factory=lambda: os.getenv("CASEWEAVE_URL", "").strip().rstrip("/"))
    shared_token: str = field(default_factory=lambda: os.getenv("DRAFTER_SHARED_TOKEN", ""))
    requests_per_minute: int = field(default_factory=lambda: int(os.getenv("DRAFTER_REQUESTS_PER_MINUTE", "6")))
    max_calls_per_process: int = field(default_factory=lambda: int(os.getenv("DRAFTER_MAX_CALLS", "100")))
    poll_seconds: float = field(default_factory=lambda: float(os.getenv("DRAFTER_POLL_SECONDS", "5")))

    def __post_init__(self):
        if self.ai.ai_transport != "direct":
            raise ValueError("The worker must use direct gateway inference, not another drafting queue")
        if not 1 <= self.requests_per_minute <= 30 or not 1 <= self.max_calls_per_process <= 1000:
            raise ValueError("Invalid worker call limits")
        if not 0.2 <= self.poll_seconds <= 30:
            raise ValueError("Invalid worker polling interval")
        if self.caseweave_url:
            parsed = urlsplit(self.caseweave_url)
            local_http = parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")
            if (not parsed.hostname or (parsed.scheme != "https" and not local_http)
                    or parsed.username or parsed.password or parsed.path not in ("", "/")
                    or parsed.query or parsed.fragment):
                raise ValueError("CASEWEAVE_URL must be an HTTPS origin (or loopback HTTP)")

    @property
    def configured(self):
        return bool(self.caseweave_url and len(self.shared_token) >= 32 and self.ai.ai_configured)


class Context(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=100)
    amount: str = Field(pattern=r"^\d{1,12}(\.\d{1,4})?$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^E[1-9]\d{0,2}$")
    title: str = Field(min_length=1, max_length=120)
    kind: Literal["request", "cancellation", "charge", "agreement", "other", "dispute"]
    occurred_at: str = Field(max_length=50)
    provenance: Literal["sample", "merchant", "paypal_sandbox"]
    text: str = Field(min_length=1, max_length=6000)

    @field_validator("occurred_at")
    @classmethod
    def zoned_timestamp(cls, value):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Evidence timestamps require a timezone")
        return value


class DraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case: Context
    evidence: list[Source] = Field(min_length=1, max_length=25)

    @field_validator("evidence")
    @classmethod
    def bounded_sources(cls, value):
        if len({e.id for e in value}) != len(value):
            raise ValueError("Evidence IDs must be unique")
        if sum(len(e.text) for e in value) > 30000:
            raise ValueError("Source text exceeds the drafting input budget")
        return value


class DraftWorker:
    def __init__(self, settings):
        self.settings = settings
        self.timestamps = deque()
        self.calls = 0
        self.last_error = None
        self.last_stage = "starting"

    def can_infer(self):
        current = time.monotonic()
        while self.timestamps and self.timestamps[0] <= current - 60:
            self.timestamps.popleft()
        return self.calls < self.settings.max_calls_per_process and len(self.timestamps) < self.settings.requests_per_minute

    async def complete(self, client, job, **kwargs):
        body = {"lease_token": job["lease_token"], **kwargs}
        url = self.settings.caseweave_url + "/api/internal/drafting/jobs/" + job["id"] + "/complete"
        for attempt in range(2):
            try:
                response = await client.post(url, json=body)
                response.raise_for_status()
                return
            except httpx.HTTPError:
                if attempt:
                    raise
                # Retry only the same completion; never run gateway inference again.
                await asyncio.sleep(0.2)

    def render_client(self):
        # Keep TLS state and connections across polls; never share the service token with the gateway.
        return httpx.AsyncClient(timeout=10, follow_redirects=False,
                                 limits=httpx.Limits(max_connections=2, max_keepalive_connections=1,
                                                     keepalive_expiry=30),
                                 headers={"Authorization": "Bearer " + self.settings.shared_token})

    async def run_once(self, client=None):
        if not self.settings.configured or not self.can_infer():
            return False
        if client is None:
            async with self.render_client() as owned_client:
                return await self.run_once(owned_client)
        self.last_stage = "claim"
        response = await client.post(self.settings.caseweave_url + "/api/internal/drafting/claim")
        response.raise_for_status()
        job = response.json().get("job")
        if job is None:
            self.last_stage = "idle"
            return False
        if (not isinstance(job, dict) or not re.fullmatch(r"[a-f0-9]{32}", str(job.get("id", "")))
                or not isinstance(job.get("lease_token"), str) or not 32 <= len(job["lease_token"]) <= 128):
            raise ValueError("Invalid leased job")
        if job.get("kind")=="firstmover_plan":
            brief=job.get("payload",{}).get("brief")
            if not isinstance(brief,str) or not 15<=len(brief)<=4000:
                await self.complete(client,job,error="invalid_input")
                return True
            self.timestamps.append(time.monotonic())
            self.calls+=1
            self.last_stage="gateway"
            try:
                result=await propose_campaign(brief,self.settings.ai)
            except DraftFailure as exc:
                self.last_error=exc.code
                log.warning("Campaign proposal failure: %s; HTTP status: %s",exc.code,exc.http_status or "none")
                self.last_stage="completion"
                await self.complete(client,job,error=exc.code)
                return True
            self.last_stage="completion"
            await self.complete(client,job,result=result)
            self.last_stage="completed"
            self.last_error=None
            return True
        try:
            body = DraftRequest.model_validate(job["payload"])
        except (ValidationError, KeyError, TypeError):
            self.last_error = "invalid_input"
            log.warning("Drafting failure: invalid_input")
            await self.complete(client, job, error="invalid_input")
            return True
        data = {**body.case.model_dump(), "evidence": [e.model_dump() for e in body.evidence]}
        checks = check_case(data)
        prompt = {"case": body.case.model_dump(), "evidence": data["evidence"],
                  "checks": checks["findings"], "missing": checks["missing"]}
        self.timestamps.append(time.monotonic())
        self.calls += 1
        try:
            self.last_stage = "gateway"
            generated = await provider_draft(prompt, self.settings.ai)
            result = {**generated, "model": self.settings.ai.ai_model}
        except DraftFailure as exc:
            self.last_error = exc.code
            log.warning("Drafting failure: %s; HTTP status: %s", exc.code, exc.http_status or "none")
            self.last_stage = "completion"
            await self.complete(client, job, error=exc.code)
            return True
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            self.last_error = "gateway_failed"
            log.warning("Drafting failure: gateway_failed")
            await self.complete(client, job, error="gateway_failed")
            return True
        self.last_stage = "completion"
        await self.complete(client, job, result=result)
        self.last_stage = "completed"
        self.last_error = None
        return True

    async def run(self, stop):
        async with self.render_client() as client:
            while not stop.is_set():
                try:
                    await self.run_once(client)
                except httpx.HTTPStatusError as exc:
                    self.last_error = "render_http_" + str(exc.response.status_code)
                    log.warning("Render worker request failed at %s: HTTP %s. No gateway retry.", self.last_stage, exc.response.status_code)
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    self.last_error = "worker_transport_failed"
                    log.warning("Render worker request failed at %s: %s. No gateway retry.", self.last_stage, type(exc).__name__)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.settings.poll_seconds)
                except asyncio.TimeoutError:
                    pass


def create_worker_app(settings=None):
    settings = settings or WorkerSettings()
    worker = DraftWorker(settings)

    @asynccontextmanager
    async def lifespan(app):
        stop = asyncio.Event()
        if not settings.configured:
            missing = []
            if not settings.caseweave_url: missing.append("CASEWEAVE_URL")
            if len(settings.shared_token) < 32: missing.append("DRAFTER_SHARED_TOKEN")
            if not settings.ai.ai_base_url: missing.append("ASTRO_GATEWAY_URL/AI_BASE_URL")
            if not settings.ai.ai_api_key: missing.append("ASTRO_GATEWAY_API_KEY/AI_API_KEY")
            if not settings.ai.ai_model: missing.append("MODEL_DEFAULT/AI_MODEL")
            log.warning("Draft worker inactive; missing or invalid settings: %s", ", ".join(missing))
        task = asyncio.create_task(worker.run(stop)) if settings.configured else None
        try:
            yield
        finally:
            stop.set()
            if task:
                await task

    app = FastAPI(title="CaseWeave Astropods worker", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.worker = worker

    @app.get("/")
    async def status():
        return {"service": "CaseWeave drafting worker", "transport": "outbound queue",
                "configured": settings.configured, "last_stage": worker.last_stage,
                "last_error": worker.last_error, "inference_attempts": worker.calls,
                "note": "Use the CaseWeave application on Render to analyze a case. This worker has no chat or inbound inference API."}

    @app.get("/health")
    async def health():
        # Configuration presence only; not proof of live gateway or Render authentication.
        return JSONResponse({"status": "configured" if settings.configured else "not_configured"}, status_code=200 if settings.configured else 503)

    return app


app = create_worker_app()
