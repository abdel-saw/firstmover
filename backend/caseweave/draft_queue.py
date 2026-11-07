"""Authenticated outbound worker queue. Model calls are never automatically re-leased."""
import asyncio
import hashlib
import hmac
import json
import secrets
import time

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .db import Case, DraftJob
from .drafting import validate_draft, DraftFailure


def envelope(case):
    return {"case": {k: case[k] for k in ("reason", "amount", "currency")}, "evidence": case["evidence"]}


def input_digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class DraftQueue:
    def __init__(self, factory, settings):
        self.factory, self.settings = factory, settings
        self.last_worker_contact = None

    async def run(self, case):
        payload = envelope(case)
        digest = input_digest(payload)
        with self.factory() as db:
            job = db.scalar(select(DraftJob).where(DraftJob.case_id == case["id"], DraftJob.input_digest == digest))
            if not job:
                job = DraftJob(id=secrets.token_hex(16), case_id=case["id"], input_digest=digest,
                               state="pending", expires=time.time() + self.settings.drafter_wait_seconds,
                               payload=json.dumps(payload))
                db.add(job)
                try:
                    db.commit()
                except IntegrityError:
                    db.rollback()
                    job = db.scalar(select(DraftJob).where(DraftJob.case_id == case["id"], DraftJob.input_digest == digest))
            if job is None:
                raise DraftFailure("worker_job_removed")
            job_id = job.id
        while True:
            with self.factory() as db:
                job = db.get(DraftJob, job_id)
                if not job:
                    raise DraftFailure("worker_job_removed")
                if job.state == "completed":
                    return json.loads(job.result)
                if job.state == "failed":
                    raise DraftFailure(job.error or "gateway_failed")
                if job.state == "superseded":
                    raise DraftFailure("worker_job_inactive")
                if job.state == "expired":
                    raise DraftFailure("worker_timeout_claimed" if job.lease_hash else "worker_timeout_unclaimed")
                if job.expires <= time.time():
                    db.execute(update(DraftJob).where(DraftJob.id == job_id, DraftJob.state.in_(("pending", "leased"))).values(state="expired"))
                    db.commit()
                    raise DraftFailure("worker_timeout_claimed" if job.lease_hash else "worker_timeout_unclaimed")
            await asyncio.sleep(0.2)

    def claim(self):
        current = time.time()
        self.last_worker_contact = current
        with self.factory() as db:
            db.execute(update(DraftJob).where(DraftJob.state.in_(("pending", "leased")), DraftJob.expires <= current).values(state="expired"))
            candidates = db.scalars(select(DraftJob).where(DraftJob.state == "pending", DraftJob.expires > current).order_by(DraftJob.expires).limit(10)).all()
            for job in candidates:
                case = db.get(Case, job.case_id)
                if not case or input_digest(envelope(json.loads(case.data))) != job.input_digest:
                    db.execute(update(DraftJob).where(DraftJob.id == job.id, DraftJob.state == "pending").values(state="superseded"))
                    continue
                lease = secrets.token_urlsafe(32)
                claimed = db.execute(update(DraftJob).where(DraftJob.id == job.id, DraftJob.state == "pending", DraftJob.expires > time.time()).values(state="leased", lease_hash=hashlib.sha256(lease.encode()).hexdigest()))
                if claimed.rowcount == 1:
                    result = {"id": job.id, "lease_token": lease, "payload": json.loads(job.payload)}
                    db.commit()
                    return {"job": result}
            db.commit()
            return {"job": None}

    def complete(self, job_id, lease_token, result=None, error=None):
        with self.factory() as db:
            job = db.get(DraftJob, job_id)
            digest = hashlib.sha256(lease_token.encode()).hexdigest()
            if not job or not job.lease_hash or not hmac.compare_digest(job.lease_hash, digest):
                raise HTTPException(404, "Draft job not found.")
            if result is not None:
                payload = json.loads(job.payload)
                try:
                    clean = validate_draft(result, payload["evidence"])
                    model = result.get("model")
                    if not isinstance(model, str) or not 1 <= len(model) <= 120:
                        raise ValueError("Missing model")
                    clean["model"] = model
                except (ValueError, KeyError, TypeError):
                    raise HTTPException(422, "Invalid source-linked draft result.")
                encoded = json.dumps(clean, sort_keys=True)
                state = "completed"
            else:
                encoded, state = "{}", "failed"
            if job.state in ("completed", "failed"):
                if job.state == state and job.result == encoded and job.error == error:
                    return {"accepted": True}  # Completion acknowledgment can be safely retried.
                raise HTTPException(409, "A different completion was already recorded.")
            if job.state != "leased" or job.expires <= time.time():
                raise HTTPException(409, "Draft job is inactive or expired.")
            updated = db.execute(update(DraftJob).where(DraftJob.id == job.id, DraftJob.state == "leased", DraftJob.expires > time.time()).values(state=state, result=encoded, error=error))
            if updated.rowcount != 1:
                db.rollback()
                raise HTTPException(409, "Draft job changed.")
            db.commit()
            return {"accepted": True}
