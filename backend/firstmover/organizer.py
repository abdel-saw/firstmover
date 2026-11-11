"""Merchant accounts and revocable organizer sessions."""
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import OrderedDict
from threading import Lock

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from .db import FirstMoverRecord as Record

COOKIE = "firstmover_organizer"
COOKIE_PATH = "/api/firstmover"
WORKSPACE = "firstmover-organizer"
MERCHANT_PREFIX = "fm-merchant-"
SESSION_SECONDS = 8 * 60 * 60
LOGIN_WINDOW = 5 * 60


class Login(BaseModel):
    email: str = Field(default="", max_length=254)
    password: SecretStr = Field(min_length=1, max_length=256)


class Signup(Login):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=12, max_length=256)
    store_name: str = Field(min_length=2, max_length=80)


def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1).hex()


def install_organizer_login(app, factory, settings):
    attempts = OrderedDict()
    attempts_lock = Lock()

    def session_id(token):
        return "fm-session-" + hashlib.sha256(token.encode()).hexdigest()

    def signed_in(request):
        token = request.cookies.get(COOKIE, "")
        if not token or len(token) > 128:
            return None
        with factory() as db:
            row = db.get(Record, session_id(token))
            if row and row.kind == "organizer_session" and float(row.data) > time.time():
                if row.owner != WORKSPACE or settings.organizer_password:
                    return row.owner
        return None

    def reserve_attempt(request):
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with attempts_lock:
            start, count = attempts.get(client, (now, 0))
            if now - start >= LOGIN_WINDOW:
                start, count = now, 0
            if count >= 10:
                raise HTTPException(429, "Too many attempts. Try again in five minutes.")
            attempts[client] = (start, count + 1)
            attempts.move_to_end(client)
            if len(attempts) > 2048:
                attempts.popitem(last=False)

    def begin_session(db, owner, request, response):
        now = time.time()
        token = secrets.token_urlsafe(32)
        db.execute(delete(Record).where(Record.kind == "organizer_session", Record.data < str(int(now))))
        previous = request.cookies.get(COOKIE, "")
        if previous and len(previous) <= 128:
            db.execute(delete(Record).where(Record.kind == "organizer_session", Record.id == session_id(previous)))
        db.add(Record(
            id=session_id(token), kind="organizer_session", owner=owner,
            state="active", data=str(now + SESSION_SECONDS),
        ))
        db.commit()
        response.set_cookie(
            COOKIE, token, httponly=True, secure=settings.cookie_secure,
            samesite="strict", max_age=SESSION_SECONDS, path=COOKIE_PATH,
        )
        return {"signed_in": True}

    @app.get("/api/firstmover/organizer/session")
    def status(request: Request):
        return {"signed_in": bool(signed_in(request)), "legacy_login": bool(settings.organizer_password)}

    @app.post("/api/firstmover/organizer/signup")
    def signup(body: Signup, request: Request, response: Response):
        reserve_attempt(request)
        email, name = body.email.strip().lower(), body.store_name.strip()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(name) < 2:
            raise HTTPException(422, "Enter a store name and valid email address.")
        salt = secrets.token_bytes(16)
        owner = MERCHANT_PREFIX + secrets.token_hex(16)
        with factory() as db:
            db.add(Record(id=owner, owner=owner, kind="merchant", state="active", unique_key="email:"+hashlib.sha256(email.encode()).hexdigest(),
                          data=json.dumps({"name":name,"salt":salt.hex(),
                                           "password":password_hash(body.password.get_secret_value(), salt)})))
            try:
                return begin_session(db, owner, request, response)
            except IntegrityError:
                db.rollback()
                raise HTTPException(409, "An account with this email already exists.") from None

    @app.post("/api/firstmover/organizer/session")
    def login(body: Login, request: Request, response: Response):
        reserve_attempt(request)
        email, supplied = body.email.strip().lower(), body.password.get_secret_value()
        with factory() as db:
            if email:
                merchant = db.scalar(select(Record).where(Record.kind == "merchant", Record.unique_key == "email:"+hashlib.sha256(email.encode()).hexdigest()))
                record = json.loads(merchant.data) if merchant else {"salt": "0"*32, "password": "0"*128}
                correct = hmac.compare_digest(password_hash(supplied, bytes.fromhex(record["salt"])), record["password"])
                owner = merchant.id if merchant and correct else None
            else:
                owner = WORKSPACE if settings.organizer_password and hmac.compare_digest(
                    supplied.encode(), settings.organizer_password.encode(),
                ) else None
            if not owner:
                raise HTTPException(401, "Incorrect email or password.")
            return begin_session(db, owner, request, response)

    @app.delete("/api/firstmover/organizer/session")
    def logout(request: Request, response: Response):
        token = request.cookies.get(COOKIE, "")
        if token and len(token) <= 128:
            with factory() as db:
                db.execute(delete(Record).where(Record.kind == "organizer_session", Record.id == session_id(token)))
                db.commit()
        response.delete_cookie(COOKIE, path=COOKIE_PATH)
        return {"signed_in": False}

    return signed_in
