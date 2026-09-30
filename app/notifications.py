"""
Outbound notification helpers.
Sends order confirmations and status updates proactively to customers.
"""

import logging
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.whatsapp import WhatsAppClient, WhatsAppError, normalize_phone

logger = logging.getLogger(__name__)

# Meta error code when free-form messages are sent outside the 24-hour customer service window.
OUTSIDE_WINDOW_CODES = {131047, 131026}

STATUS_MESSAGES = {
    "confirmed": "Your order *{order_id}* has been confirmed! We'll notify you when it ships.",
    "shipped": "Great news! Order *{order_id}* is on its way 🚚\n{detail}",
    "delivered": "Your order *{order_id}* has been delivered! Enjoy 🎉\n{detail}",
    "cancelled": "Order *{order_id}* has been cancelled.\n{detail}\nContact us if you have questions.",
}


class OrderNotification(BaseModel):
    phone: str = Field(description="Customer number with country code, e.g. +905551234567")
    order_id: str = Field(min_length=1, max_length=40)
    customer_name: str = Field(min_length=1, max_length=60)
    status: Literal["confirmed", "shipped", "delivered", "cancelled"]
    detail: str = Field(default="", max_length=500)

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, v: str) -> str:
        digits = normalize_phone(v)
        if not 8 <= len(digits) <= 15:
            raise ValueError("phone must include the country code, e.g. +905551234567")
        return digits


def render(notif: OrderNotification) -> str:
    body = STATUS_MESSAGES[notif.status].format(order_id=notif.order_id, detail=notif.detail).strip()
    return f"Hi {notif.customer_name}! 👋\n\n{body}\n\n— {settings.BUSINESS_NAME}"


async def send_order_notification(client: WhatsAppClient, notif: OrderNotification) -> str:
    """
    Send an order status update. Tries a free-form message first; if the customer has not written
    in the last 24 hours, falls back to the approved template (ORDER_TEMPLATE_NAME).
    Returns "text" or "template". Raises WhatsAppError when nothing could be delivered.
    """
    try:
        await client.send_text(notif.phone, render(notif))
        logger.info("Notification sent to %s — order=%s status=%s", notif.phone, notif.order_id, notif.status)
        return "text"
    except WhatsAppError as exc:
        if exc.code not in OUTSIDE_WINDOW_CODES or not settings.ORDER_TEMPLATE_NAME:
            raise
        logger.info("Outside 24h window for %s — sending template %s", notif.phone, settings.ORDER_TEMPLATE_NAME)

    # Template body expected: "Hi {{1}}, your order {{2}} is now {{3}}. {{4}}"
    await client.send_template(
        notif.phone,
        settings.ORDER_TEMPLATE_NAME,
        settings.ORDER_TEMPLATE_LANGUAGE,
        body_params=[notif.customer_name, notif.order_id, notif.status, notif.detail or "-"],
    )
    return "template"
