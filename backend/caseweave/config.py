import os
from dataclasses import dataclass

@dataclass
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./caseweave.db")
    admin_token: str = os.getenv("ADMIN_TOKEN", "")
    paypal_client_id: str = os.getenv("PAYPAL_CLIENT_ID", "")
    paypal_client_secret: str = os.getenv("PAYPAL_CLIENT_SECRET", "")
    ai_base_url: str = os.getenv("AI_BASE_URL", "https://api.openai.com/v1")
    ai_api_key: str = os.getenv("AI_API_KEY", "")
    ai_model: str = os.getenv("AI_MODEL", "")
    cookie_secure: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    max_sessions: int = int(os.getenv("MAX_SESSIONS", "1000"))

    @property
    def ai_configured(self):
        return bool(self.ai_api_key and self.ai_model)

    @property
    def paypal_configured(self):
        return bool(self.paypal_client_id and self.paypal_client_secret)
