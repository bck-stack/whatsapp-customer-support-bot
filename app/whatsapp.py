"""
WhatsApp Cloud API client.
Sends text, template, interactive button and list messages; retries rate limits and server errors.
"""

import asyncio
import hashlib
import hmac
import logging
import re
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class WhatsAppError(Exception):
    def __init__(self, status: int, message: str, code: Optional[int] = None) -> None:
        super().__init__(f"WhatsApp API {status}: {message}")
        self.status = status
        self.code = code


def normalize_phone(phone: str) -> str:
    """Cloud API expects digits only with country code: '+90 555 123 45 67' -> '905551234567'."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("00"):
        digits = digits[2:]
    return digits


def verify_signature(raw_body: bytes, signature_header: str, app_secret: str) -> bool:
    """Validate Meta's X-Hub-Signature-256 header (HMAC-SHA256 of the raw body with the app secret)."""
    if not app_secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.split("=", 1)[1].strip().lower())


class WhatsAppClient:
    """Async wrapper around the Meta WhatsApp Cloud API with a shared connection pool."""

    MAX_ATTEMPTS = 3

    def __init__(self, token: str, phone_id: str, api_version: str, http: Optional[httpx.AsyncClient] = None) -> None:
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        self._url = f"https://graph.facebook.com/{api_version}/{phone_id}/messages"
        self._http = http

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=15)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()

    async def _post(self, payload: dict[str, Any]) -> dict:
        client = await self._client()
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                resp = await client.post(self._url, json=payload, headers=self._headers)
            except httpx.TransportError as exc:
                if attempt == self.MAX_ATTEMPTS:
                    raise WhatsAppError(0, f"network error: {exc}") from exc
                await asyncio.sleep(2 ** attempt)
                continue
            if resp.status_code < 400:
                return resp.json()
            error = (resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}).get("error", {})
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < self.MAX_ATTEMPTS:
                await asyncio.sleep(2 ** attempt)
                continue
            raise WhatsAppError(resp.status_code, error.get("message", resp.text[:200]), error.get("code"))
        raise WhatsAppError(0, "unreachable")

    async def send_text(self, to: str, body: str, preview_url: bool = False) -> dict:
        """Send a plain text message (only allowed within 24h of the customer's last message)."""
        return await self._post({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalize_phone(to),
            "type": "text",
            "text": {"preview_url": preview_url, "body": body[:4096]},
        })

    async def send_template(
        self,
        to: str,
        template_name: str,
        language_code: str = "en_US",
        body_params: Optional[list[str]] = None,
    ) -> dict:
        """Send a pre-approved template message (required to start a conversation)."""
        template: dict[str, Any] = {"name": template_name, "language": {"code": language_code}}
        if body_params:
            template["components"] = [{"type": "body", "parameters": [{"type": "text", "text": p} for p in body_params]}]
        return await self._post({"messaging_product": "whatsapp", "to": normalize_phone(to), "type": "template", "template": template})

    async def send_buttons(
        self,
        to: str,
        body_text: str,
        buttons: list[dict[str, str]],
        header_text: Optional[str] = None,
        footer_text: Optional[str] = None,
    ) -> dict:
        """Interactive message with up to 3 reply buttons (titles max 20 chars)."""
        interactive: dict[str, Any] = {
            "type": "button",
            "body": {"text": body_text[:1024]},
            "action": {"buttons": [{"type": "reply", "reply": {"id": b["id"], "title": b["title"][:20]}} for b in buttons[:3]]},
        }
        if header_text:
            interactive["header"] = {"type": "text", "text": header_text[:60]}
        if footer_text:
            interactive["footer"] = {"text": footer_text[:60]}
        return await self._post({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": normalize_phone(to),
            "type": "interactive",
            "interactive": interactive,
        })

    async def mark_as_read(self, message_id: str) -> dict:
        return await self._post({"messaging_product": "whatsapp", "status": "read", "message_id": message_id})


wa_client = WhatsAppClient(settings.WHATSAPP_TOKEN, settings.WHATSAPP_PHONE_ID, settings.GRAPH_API_VERSION)
