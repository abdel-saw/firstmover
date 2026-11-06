import json
from datetime import datetime, timezone
import httpx

BASE="https://api-m.sandbox.paypal.com"

class PayPalError(Exception):
    def __init__(self, message, uncertain=False):
        self.message=message
        self.uncertain=uncertain
        super().__init__(message)

class PayPal:
    def __init__(self, settings):
        self.settings=settings

    async def request(self, method, path, **kwargs):
        if not self.settings.paypal_configured:
            raise PayPalError("PayPal sandbox credentials are not configured.")
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            try:
                token=await client.post(BASE+"/v1/oauth2/token",auth=(self.settings.paypal_client_id,self.settings.paypal_client_secret),data={"grant_type":"client_credentials"})
                if token.status_code!=200:
                    raise PayPalError("PayPal authentication failed. Check sandbox credentials and app permissions.")
                access=token.json()["access_token"]
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                raise PayPalError("Could not authenticate with PayPal sandbox.") from exc
            try:
                response=await client.request(method,BASE+path,headers={"Authorization":"Bearer "+access},**kwargs)
            except httpx.HTTPError as exc:
                raise PayPalError("PayPal did not return a conclusive response. Check the case in PayPal before retrying.",uncertain=method!="GET") from exc
            if response.status_code>=400:
                raise PayPalError(f"PayPal sandbox returned HTTP {response.status_code}. Check case status and app permissions.",uncertain=method!="GET" and response.status_code>=500)
            try:
                return response.json() if response.content else {}
            except ValueError as exc:
                raise PayPalError("PayPal returned an unreadable response.",uncertain=method!="GET") from exc

    async def dispute(self, dispute_id):
        return await self.request("GET",f"/v1/customer/disputes/{dispute_id}")

    async def submit(self, dispute_id, note):
        # Multipart format from PayPal's Disputes integration guide; no arbitrary remote URLs.
        body={"evidences":[{"evidence_type":"OTHER","notes":note}]}
        return await self.request("POST",f"/v1/customer/disputes/{dispute_id}/provide-evidence",files={"input":(None,json.dumps(body),"application/json")})


def can_submit(raw):
    return raw.get("status")=="WAITING_FOR_SELLER_RESPONSE" and any(link.get("rel")=="provide_evidence" or link.get("rel")=="provide-evidence" for link in raw.get("links",[]))


def normalize(raw):
    """Deliberately import only case facts; payment/cancellation records require separate evidence."""
    amount=raw.get("dispute_amount",{})
    reason=raw.get("reason","OTHER")
    requested=[e.get("evidence_type") for e in raw.get("evidences",[]) if e.get("source")=="REQUESTED_FROM_SELLER"]
    created=raw.get("create_time") or datetime.now(timezone.utc).isoformat()
    return {"key":"sandbox","title":"PayPal sandbox dispute", "customer":"Sandbox customer", "amount":amount.get("value","0.00"),"currency":amount.get("currency_code","USD"),"reason":reason,"paypal_id":raw["dispute_id"],"mode":"sandbox","status":"Needs review","deadline":raw.get("seller_response_due_date"),"summary":"Imported from PayPal sandbox. Add supporting records before preparing a response.","paypal_status":raw.get("status"),"requested_types":requested,"can_submit":can_submit(raw),"evidence":[{"id":"E1","title":"PayPal sandbox dispute record","kind":"dispute","occurred_at":created,"provenance":"paypal_sandbox","text":json.dumps({"dispute_id":raw["dispute_id"],"reason":reason,"status":raw.get("status"),"dispute_amount":amount,"requested_evidence":requested},indent=2)}]}
