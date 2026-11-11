import os
from dataclasses import dataclass, field


def ai_url():
    explicit = os.getenv("AI_BASE_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    gateway = os.getenv("ASTRO_GATEWAY_URL", "").strip().rstrip("/")
    if gateway:
        return gateway if gateway.endswith("/v1") else gateway + "/v1"
    if os.getenv("ASTRO_GATEWAY_API_KEY") and not os.getenv("AI_API_KEY"):
        # An incomplete native configuration must never send its key to the default provider.
        return ""
    return "https://api.openai.com/v1"


def json_mode():
    # Astropods recommends prompt-based JSON as the portable gateway approach.
    return os.getenv("AI_JSON_MODE", "prompt" if os.getenv("ASTRO_GATEWAY_URL") else "json_object")

@dataclass
class Settings:
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///./caseweave.db"))
    admin_token: str = field(default_factory=lambda: os.getenv("ADMIN_TOKEN", ""))
    organizer_password: str = field(default_factory=lambda: os.getenv("ORGANIZER_PASSWORD", ""))
    paypal_client_id: str = field(default_factory=lambda: os.getenv("PAYPAL_CLIENT_ID", ""))
    paypal_client_secret: str = field(default_factory=lambda: os.getenv("PAYPAL_CLIENT_SECRET", ""))
    ai_base_url: str = field(default_factory=ai_url)
    ai_api_key: str = field(default_factory=lambda: os.getenv("AI_API_KEY") or os.getenv("ASTRO_GATEWAY_API_KEY", ""))
    ai_model: str = field(default_factory=lambda: os.getenv("AI_MODEL") or os.getenv("MODEL_DEFAULT", ""))
    ai_json_mode: str = field(default_factory=json_mode)
    ai_max_tokens: int = field(default_factory=lambda: int(os.getenv("AI_MAX_TOKENS", "1800")))
    ai_transport: str = field(default_factory=lambda: os.getenv("AI_TRANSPORT", "direct"))
    drafter_shared_token: str = field(default_factory=lambda: os.getenv("DRAFTER_SHARED_TOKEN", ""))
    drafter_wait_seconds: int = field(default_factory=lambda: int(os.getenv("DRAFTER_WAIT_SECONDS", "55")))
    cookie_secure: bool = field(default_factory=lambda: os.getenv("COOKIE_SECURE", "false").lower() == "true")
    max_sessions: int = field(default_factory=lambda: int(os.getenv("MAX_SESSIONS", "1000")))

    def __post_init__(self):
        if self.ai_json_mode not in ("prompt", "json_object"):
            raise ValueError("AI_JSON_MODE must be prompt or json_object")
        if not 256 <= self.ai_max_tokens <= 4096:
            raise ValueError("AI_MAX_TOKENS must be between 256 and 4096")
        if self.ai_transport not in ("direct", "astropods_queue"):
            raise ValueError("AI_TRANSPORT must be direct or astropods_queue")
        if self.ai_transport == "astropods_queue" and len(self.drafter_shared_token) < 32:
            raise ValueError("DRAFTER_SHARED_TOKEN must contain at least 32 characters")
        if not 5 <= self.drafter_wait_seconds <= 90:
            raise ValueError("DRAFTER_WAIT_SECONDS must be between 5 and 90")

    @property
    def queue_drafter_configured(self):
        return self.ai_transport == "astropods_queue" and len(self.drafter_shared_token) >= 32

    @property
    def ai_configured(self):
        return self.queue_drafter_configured or bool(self.ai_base_url and self.ai_api_key and self.ai_model)

    @property
    def paypal_configured(self):
        return bool(self.paypal_client_id and self.paypal_client_secret)
