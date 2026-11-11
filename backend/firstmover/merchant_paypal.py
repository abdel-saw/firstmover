"""Merchant PayPal credentials and their authenticated storage."""
import base64
import hashlib
import json
from dataclasses import replace

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException
from pydantic import BaseModel, Field, SecretStr

from .db import FirstMoverRecord as Record
from .organizer import MERCHANT_PREFIX
from .paypal import PayPal, PayPalError


class Connection(BaseModel):
    client_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,256}$")
    client_secret: SecretStr = Field(min_length=8, max_length=512)


class MerchantPayPal:
    def __init__(self, factory, settings, legacy):
        self.factory = factory
        self.settings = settings
        self.legacy = legacy
        key = settings.merchant_encryption_key
        self.cipher = Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())) if len(key) >= 32 else None

    def connection(self, owner):
        with self.factory() as db:
            return db.get(Record, "fm-paypal:"+owner)

    def client_id(self, owner):
        if not owner.startswith(MERCHANT_PREFIX):
            return self.settings.paypal_client_id if self.settings.paypal_configured else ""
        record = self.connection(owner)
        return json.loads(record.data)["client_id"] if record and self.cipher else ""

    def make_client(self, client_id, secret):
        return PayPal(replace(self.settings, paypal_client_id=client_id, paypal_client_secret=secret))

    def for_campaign(self, owner):
        if not owner.startswith(MERCHANT_PREFIX):
            return self.legacy
        record = self.connection(owner)
        if not record or not self.cipher:
            raise HTTPException(409, "This merchant's PayPal connection is unavailable.")
        values = json.loads(record.data)
        try:
            stored = json.loads(self.cipher.decrypt(values["secret"].encode()))
        except InvalidToken:
            raise HTTPException(503, "Merchant PayPal credentials could not be decrypted.") from None
        if stored["owner"] != owner or stored["client_id"] != values["client_id"]:
            raise HTTPException(503, "Merchant PayPal connection could not be verified.")
        return self.make_client(values["client_id"], stored["secret"])

    async def verify(self, owner, body):
        if not self.cipher:
            raise HTTPException(503, "Merchant PayPal setup is unavailable on this deployment.")
        secret = body.client_secret.get_secret_value()
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            try:
                await self.make_client(body.client_id, secret).access_token(client)
            except PayPalError as exc:
                raise HTTPException(502, exc.message) from None
        sealed = self.cipher.encrypt(json.dumps(
            {"owner": owner, "client_id": body.client_id, "secret": secret}
        ).encode()).decode()
        return {"client_id": body.client_id, "secret": sealed}
