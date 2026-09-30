"""
WhatsApp Business API Bot
FastAPI app exposing webhook verification (GET), message handler (POST) and order notifications.
"""

import json
import logging
import secrets
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.handlers import bot
from app.notifications import OrderNotification, send_order_notification
from app.whatsapp import WhatsAppError, verify_signature, wa_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("main")

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    if not settings.META_APP_SECRET:
        logger.warning("META_APP_SECRET is not set — webhook signatures are NOT verified (development only).")
    if not settings.NOTIFY_API_KEY:
        logger.warning("NOTIFY_API_KEY is not set — POST /notify/order is disabled.")
    yield
    await wa_client.aclose()


app = FastAPI(
    title="WhatsApp Business Bot",
    description="Webhook receiver and outbound notification system for WhatsApp Cloud API.",
    version="2.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Webhook verification (Meta requires a GET endpoint)
# ---------------------------------------------------------------------------

@app.get("/webhook", response_class=PlainTextResponse, tags=["webhook"])
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
) -> str:
    """Return the challenge when Meta's verify token matches."""
    if hub_mode == "subscribe" and secrets.compare_digest(hub_verify_token, settings.VERIFY_TOKEN):
        logger.info("Webhook verified successfully.")
        return hub_challenge
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Verification failed.")


# ---------------------------------------------------------------------------
# Incoming messages
# ---------------------------------------------------------------------------

async def _handle_safely(payload: dict) -> None:
    try:
        await bot.handle_webhook(payload)
    except Exception:
        logger.exception("Webhook processing failed")


@app.post("/webhook", status_code=status.HTTP_200_OK, tags=["webhook"])
async def receive_message(
    request: Request, background: BackgroundTasks, x_hub_signature_256: str = Header(default="")
) -> dict:
    """
    Verify Meta's signature, acknowledge immediately (Meta retries slow endpoints)
    and process the messages in the background.
    """
    raw = await request.body()
    if settings.META_APP_SECRET and not verify_signature(raw, x_hub_signature_256, settings.META_APP_SECRET):
        logger.warning("Rejected webhook with invalid signature")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature.")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON.")
    if payload.get("object") != "whatsapp_business_account":
        return {"status": "ignored"}

    # Runs after the 200 response has been sent, so Meta is acknowledged immediately.
    background.add_task(_handle_safely, payload)
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Outbound notification endpoint (called by your backend)
# ---------------------------------------------------------------------------

def require_api_key(x_api_key: str = Header(default="")) -> None:
    if not settings.NOTIFY_API_KEY:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="NOTIFY_API_KEY is not configured.")
    if not secrets.compare_digest(x_api_key, settings.NOTIFY_API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key.")


@app.post("/notify/order", tags=["notifications"], dependencies=[Depends(require_api_key)])
async def notify_order(notif: OrderNotification) -> dict:
    """Send an order status notification to a customer via WhatsApp (requires X-API-Key)."""
    try:
        via = await send_order_notification(wa_client, notif)
    except WhatsAppError as exc:
        logger.error("Notification to %s failed: %s", notif.phone, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"WhatsApp rejected the message: {exc}")
    return {"sent": True, "via": via, "phone": notif.phone, "order_id": notif.order_id}


@app.get("/health", tags=["ops"])
async def health() -> dict:
    return {"status": "ok", "signature_check": bool(settings.META_APP_SECRET)}
