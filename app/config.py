"""Configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    # Meta / WhatsApp Cloud API
    WHATSAPP_TOKEN: str               # System-user / permanent access token
    WHATSAPP_PHONE_ID: str            # Phone number ID (not the actual number)
    VERIFY_TOKEN: str                 # Arbitrary string you set in the Meta webhook config
    META_APP_SECRET: str = ""         # App secret — used to verify X-Hub-Signature-256 on webhooks
    GRAPH_API_VERSION: str = "v21.0"

    # Protects POST /notify/order (send as X-API-Key)
    NOTIFY_API_KEY: str = ""

    # Business details used in replies
    BUSINESS_NAME: str = "My Business"
    SUPPORT_EMAIL: str = "support@example.com"
    SUPPORT_HOURS: str = "Mon–Fri 9AM–6PM"
    # Optional: staff number (digits, with country code) that receives "talk to a human" requests
    SUPPORT_FORWARD_NUMBER: str = ""

    # Order lookup source (JSON file with an "orders" list — replace with your DB / API)
    ORDERS_FILE: str = str(BASE_DIR / "data" / "orders.json")

    # Template used when a notification must start a new conversation (outside the 24h window)
    ORDER_TEMPLATE_NAME: str = ""
    ORDER_TEMPLATE_LANGUAGE: str = "en_US"

    SESSION_TTL_MINUTES: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
