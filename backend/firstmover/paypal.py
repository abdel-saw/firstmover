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
            raise PayPalError("PayPal did not return a conclusive response. Check the operation in PayPal before retrying.",uncertain=method!="GET") from exc
        if response.status_code>=400:
            raise PayPalError(f"PayPal sandbox returned HTTP {response.status_code}. Check the operation and app permissions.",uncertain=method!="GET" and response.status_code>=500,http_status=response.status_code)
        try:
            return response.json() if response.content else {}
        except ValueError as exc:
            raise PayPalError("PayPal returned an unreadable response.",uncertain=method!="GET") from exc

    async def request(self, method, path, **kwargs):
        async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
            access=await self.access_token(client)
            return await self.resource(client, access, method, path, **kwargs)
