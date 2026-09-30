"""
Incoming message handler.
Parses webhook payloads and dispatches each message to the right conversation logic.
"""

import logging
import time
from collections import OrderedDict
from typing import Any, Optional

from app.config import settings
from app.orders import Order, OrderStore, extract_order_id
from app.whatsapp import WhatsAppClient, wa_client

logger = logging.getLogger(__name__)

GREETINGS = {"hi", "hello", "hey", "merhaba", "selam", "menu", "start", "menü"}
STATUS_TEXT = {
    "processing": "Processing ⏳",
    "confirmed": "Confirmed ✅",
    "shipped": "In Transit 🚚",
    "in_transit": "In Transit 🚚",
    "delivered": "Delivered 📦",
    "cancelled": "Cancelled ❌",
}


class SessionStore:
    """Per-customer conversation state with expiry (swap for Redis when running several instances)."""

    def __init__(self, ttl_seconds: int) -> None:
        self.ttl = ttl_seconds
        self._data: dict[str, tuple[float, dict]] = {}

    def get(self, key: str) -> dict:
        item = self._data.get(key)
        if not item or time.monotonic() - item[0] > self.ttl:
            self._data.pop(key, None)
            return {}
        return item[1]

    def set(self, key: str, value: dict) -> None:
        self._data[key] = (time.monotonic(), value)

    def clear(self, key: str) -> None:
        self._data.pop(key, None)


class SeenMessages:
    """Meta may deliver the same webhook more than once — remember recent message IDs."""

    def __init__(self, size: int = 5000) -> None:
        self.size = size
        self._ids: OrderedDict[str, None] = OrderedDict()

    def seen(self, msg_id: str) -> bool:
        if not msg_id:
            return False
        if msg_id in self._ids:
            return True
        self._ids[msg_id] = None
        if len(self._ids) > self.size:
            self._ids.popitem(last=False)
        return False


class Bot:
    def __init__(self, client: WhatsAppClient, orders: OrderStore, session_ttl_minutes: int = 30) -> None:
        self.wa = client
        self.orders = orders
        self.sessions = SessionStore(session_ttl_minutes * 60)
        self.seen = SeenMessages()

    # -- entry point --------------------------------------------------------
    async def handle_webhook(self, payload: dict[str, Any]) -> None:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if value.get("metadata", {}).get("phone_number_id") not in (None, settings.WHATSAPP_PHONE_ID):
                    continue  # message for another number on the same app
                names = {c.get("wa_id"): c.get("profile", {}).get("name", "") for c in value.get("contacts", [])}
                for msg in value.get("messages", []):
                    try:
                        await self.dispatch(msg, names.get(msg.get("from"), ""))
                    except Exception:
                        logger.exception("Failed to handle message %s", msg.get("id"))
                for st in value.get("statuses", []):
                    if st.get("status") == "failed":
                        logger.warning("Delivery failed to %s: %s", st.get("recipient_id"), st.get("errors"))

    async def dispatch(self, msg: dict[str, Any], name: str = "") -> None:
        from_number = msg.get("from", "")
        msg_id = msg.get("id", "")
        if self.seen.seen(msg_id):
            return

        try:
            await self.wa.mark_as_read(msg_id)
        except Exception as exc:
            logger.debug("mark_as_read failed: %s", exc)

        msg_type = msg.get("type", "")
        if msg_type == "text":
            await self.handle_text(from_number, msg.get("text", {}).get("body", ""), name)
        elif msg_type == "interactive":
            interactive = msg.get("interactive", {})
            reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
            await self.handle_button(from_number, reply.get("id", ""), name)
        elif msg_type == "button":  # quick-reply button on a template message
            await self.handle_text(from_number, msg.get("button", {}).get("text", ""), name)
        else:
            await self.wa.send_text(from_number, "I can read text messages and button replies. Type *menu* to see the options.")

    # -- conversation -------------------------------------------------------
    async def send_menu(self, to: str, name: str = "") -> None:
        hello = f"Hi {name.split()[0]}! " if name else ""
        await self.wa.send_buttons(
            to=to,
            header_text=f"Welcome to {settings.BUSINESS_NAME}",
            body_text=f"{hello}How can I help you today?",
            footer_text="Tap a button or type your question.",
            buttons=[
                {"id": "track_order", "title": "Track Order"},
                {"id": "support", "title": "Talk to Support"},
                {"id": "pricing", "title": "Pricing"},
            ],
        )

    def order_message(self, order: Optional[Order], order_id: str) -> str:
        if not order:
            return (
                f"I couldn't find order *{order_id}* for this number. Please check the ID "
                f"(it looks like ORD-1042) or type *support* to reach our team."
            )
        lines = [f"Order *{order.id}* — Status: *{STATUS_TEXT.get(order.status, order.status.title())}*"]
        if order.items:
            lines.append(f"Items: {order.items}")
        if order.eta and order.status not in ("delivered", "cancelled"):
            lines.append(f"Expected delivery: {order.eta}")
        if order.tracking:
            lines.append(f"Tracking: {order.tracking}")
        return "\n".join(lines)

    async def lookup_order(self, to: str, order_id: str) -> None:
        self.sessions.clear(to)
        await self.wa.send_text(to, self.order_message(self.orders.get(order_id, to), order_id))

    async def handle_text(self, to: str, raw: str, name: str = "") -> None:
        text = raw.strip().lower()
        session = self.sessions.get(to)
        order_id = extract_order_id(raw)

        if text in GREETINGS:
            self.sessions.clear(to)
            await self.send_menu(to, name)
        elif order_id:
            await self.lookup_order(to, order_id)
        elif session.get("state") == "awaiting_order_id":
            attempts = session.get("attempts", 0) + 1
            if attempts >= 3:
                self.sessions.clear(to)
                await self.wa.send_text(to, "I still couldn't read an order ID. Type *support* and a team member will help you.")
            else:
                self.sessions.set(to, {"state": "awaiting_order_id", "attempts": attempts})
                await self.wa.send_text(to, "That doesn't look like an order ID. It should look like *ORD-1042*.")
        elif "order" in text or "track" in text or "sipariş" in text:
            await self.ask_order_id(to)
        elif "price" in text or "pricing" in text or "fiyat" in text:
            await self.send_pricing(to)
        elif any(w in text for w in ("support", "help", "human", "agent", "destek", "yardım")):
            await self.handoff(to, name, raw)
        else:
            await self.wa.send_text(
                to,
                "Thanks for your message! A team member will get back to you shortly.\n\nType *menu* to see what I can do.",
            )
            await self.forward_to_staff(to, name, raw)

    async def handle_button(self, to: str, reply_id: str, name: str = "") -> None:
        if reply_id == "track_order":
            await self.ask_order_id(to)
        elif reply_id == "support":
            await self.handoff(to, name, "(pressed Talk to Support)")
        elif reply_id == "pricing":
            await self.send_pricing(to)
        else:
            await self.wa.send_text(to, "Unknown option. Type *menu* to start over.")

    async def ask_order_id(self, to: str) -> None:
        self.sessions.set(to, {"state": "awaiting_order_id", "attempts": 0})
        await self.wa.send_text(to, "Please share your order ID (e.g. *ORD-1042*) and I'll look it up for you.")

    async def send_pricing(self, to: str) -> None:
        await self.wa.send_text(
            to,
            "Our plans:\n\n• *Starter* — $29/mo\n• *Pro* — $79/mo\n• *Enterprise* — Contact us\n\nVisit our website for full details.",
        )

    async def handoff(self, to: str, name: str, text: str) -> None:
        await self.wa.send_text(
            to,
            f"I've let our team know — someone will reply here soon.\nSupport hours: {settings.SUPPORT_HOURS}\nEmail: {settings.SUPPORT_EMAIL}",
        )
        await self.forward_to_staff(to, name, text)

    async def forward_to_staff(self, customer: str, name: str, text: str) -> None:
        if not settings.SUPPORT_FORWARD_NUMBER:
            return
        try:
            await self.wa.send_text(
                settings.SUPPORT_FORWARD_NUMBER,
                f"📩 Customer +{customer}{f' ({name})' if name else ''} needs help:\n\n{text[:1000]}",
            )
        except Exception as exc:  # staff number outside the 24h window, etc.
            logger.warning("Could not forward to support number: %s", exc)


bot = Bot(wa_client, OrderStore(settings.ORDERS_FILE), settings.SESSION_TTL_MINUTES)


async def handle_webhook(payload: dict[str, Any]) -> None:
    """Backwards-compatible entry point."""
    await bot.handle_webhook(payload)
