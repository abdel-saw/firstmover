"""Password sign-in for the deployment's organizer account."""
import hashlib
import hmac
import secrets
import time
from collections import OrderedDict
from threading import Lock

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from .db import FirstMoverRecord as Record

COOKIE = "firstmover_organizer"
COOKIE_PATH = "/api/firstmover"
WORKSPACE = "firstmover-organizer"
SESSION_SECONDS = 8 * 60 * 60
LOGIN_WINDOW = 5 * 60


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=256)


def install_organizer_login(app, factory, settings):
    password = settings.organizer_password.encode()
    attempts = OrderedDict()
    attempts_lock = Lock()

    def session_id(token):
        return "fm-session-" + hmac.digest(password, token.encode(), "sha256").hex()

    def signed_in(request):
        token = request.cookies.get(COOKIE, "")
        if not password or not token or len(token) > 128:
            return False
        with factory() as db:
            row = db.get(Record, session_id(token))
            return bool(row and row.kind == "organizer_session" and float(row.data) > time.time())

    @app.get("/api/firstmover/organizer/session")
    def status(request: Request):
        return {"signed_in": signed_in(request), "configured": bool(password)}

    @app.post("/api/firstmover/organizer/session")
    def login(body: Login, request: Request, response: Response):
        if not password:
            raise HTTPException(503, "Organizer sign-in is awaiting deployment configuration.")
        client = request.client.host if request.client else "unknown"
        now = time.time()
        with attempts_lock:
            start, count = attempts.get(client, (now, 0))
            if now - start >= LOGIN_WINDOW:
                start, count = now, 0
            if count >= 10:
                raise HTTPException(429, "Too many sign-in attempts. Try again in five minutes.")
            attempts[client] = (start, count + 1)
            attempts.move_to_end(client)
            if len(attempts) > 2048:
                attempts.popitem(last=False)
        if not hmac.compare_digest(body.password.encode(), password):
            raise HTTPException(401, "Incorrect organizer password.")
        with attempts_lock:
            attempts.pop(client, None)

        token = secrets.token_urlsafe(32)
        with factory() as db:
            sessions = db.scalars(select(Record).where(Record.kind == "organizer_session")).all()
            previous = request.cookies.get(COOKIE, "")
            previous_id = session_id(previous) if previous else None
            for row in sessions:
                if float(row.data) <= now or row.id == previous_id:
                    db.delete(row)
            db.add(Record(
                id=session_id(token), kind="organizer_session", owner=WORKSPACE,
                state="active", data=str(now + SESSION_SECONDS),
            ))
            db.commit()
        response.set_cookie(
            COOKIE, token, httponly=True, secure=settings.cookie_secure,
            samesite="strict", max_age=SESSION_SECONDS, path=COOKIE_PATH,
        )
        return {"signed_in": True}

    @app.delete("/api/firstmover/organizer/session")
    def logout(request: Request, response: Response):
        token = request.cookies.get(COOKIE, "")
        if token and len(token) <= 128:
            with factory() as db:
                db.execute(delete(Record).where(
                    Record.kind == "organizer_session", Record.id == session_id(token),
                ))
                db.commit()
        response.delete_cookie(COOKIE, path=COOKIE_PATH)
        return {"signed_in": False}

    return signed_in
