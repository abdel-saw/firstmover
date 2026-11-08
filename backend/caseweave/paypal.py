import json
from datetime import datetime, timezone, timedelta
import httpx

BASE="https://api-m.sandbox.paypal.com"

class PayPalError(Exception):
    def __init__(self, message, uncertain=False, http_status=None):
        self.http_status=http_status
        self.message=message
        self.uncertain=uncertain
        super().__init__(message)

class PayPal:
    def __init__(self, settings):
        self.settings=settings

    async def access_token(self, client):
        if not self.settings.paypal_configured:
            raise PayPalError("PayPal sandbox credentials are not configured.")
        try:
            response=await client.post(BASE+"/v1/oauth2/token",auth=(self.settings.paypal_client_id,self.settings.paypal_client_secret),data={"grant_type":"client_credentials"})
            if response.status_code!=200:
                raise PayPalError("PayPal authentication failed. Check sandbox credentials and app permissions.",http_status=response.status_code)
            token=response.json()["access_token"]
            if not isinstance(token,str) or not token:
                raise ValueError("Missing access token")
            return token
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise PayPalError("Could not authenticate with PayPal sandbox.") from exc

    async def resource(self, client, access, method, path, **kwargs):
        try:
            response=await client.request(method,BASE+path,headers={"Authorization":"Bearer "+access,**kwargs.pop("headers",{})},**kwargs)
        except httpx.HTTPError as exc:
            raise PayPalError("PayPal did not return a conclusive response. Check the case in PayPal before retrying.",uncertain=method!="GET") from exc
        if response.status_code>=400:
            raise PayPalError(f"PayPal sandbox returned HTTP {response.status_code}. Check case status and app permissions.",uncertain=method!="GET" and response.status_code>=500,http_status=response.status_code)
        try:
            return response.json() if response.content else {}
        except ValueError as exc:
            raise PayPalError("PayPal returned an unreadable response.",uncertain=method!="GET") from exc

    async def request(self, method, path, **kwargs):
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            access=await self.access_token(client)
            return await self.resource(client, access, method, path, **kwargs)

    async def check_connection(self):
        # Operator-only diagnostics. No payments, disputes, or evidence are changed.
        result={"environment":"sandbox","authenticated":False,"disputes_access":False,"disputes":[]}
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            try:
                access=await self.access_token(client)
            except PayPalError as exc:
                return {**result,"stage":"authentication","http_status":exc.http_status,"message":exc.message}
            result["authenticated"]=True
            since=(datetime.now(timezone.utc)-timedelta(days=179)).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                raw=await self.resource(client,access,"GET","/v1/customer/disputes",params={"page_size":10,"create_time_after":since})
            except PayPalError as exc:
                message=("Authentication succeeded, but Disputes API access was denied. Enable Disputes for this sandbox app and verify its merchant account access."
                         if exc.http_status==403 else "Authentication succeeded. "+exc.message)
                return {**result,"stage":"disputes","http_status":exc.http_status,"message":message}
            if (not isinstance(raw,dict) or not isinstance(raw.get("items"),list)
                    or any(not isinstance(item,dict) or not isinstance(item.get("dispute_id"),str) for item in raw["items"])
                    or not isinstance(raw.get("links",[]),list)):
                return {**result,"stage":"disputes","http_status":200,"message":"Authentication succeeded, but PayPal returned an unexpected disputes list."}
            # Return only IDs/status/reason, never access tokens or customer details.
            disputes=[{key:item.get(key) for key in ("dispute_id","status","reason")} for item in raw["items"][:10]]
            return {**result,"stage":"complete","disputes_access":True,"http_status":200,
                    "disputes":disputes,"has_more":any(isinstance(link,dict) and link.get("rel")=="next" for link in raw.get("links",[])),
                    "message":"Authentication and Disputes API access verified. Showing up to 10 disputes created in the last 179 days." if disputes else "Authentication and Disputes API access verified. No disputes were returned for the last 179 days. A sandbox dispute is needed to test import and submission."}

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
