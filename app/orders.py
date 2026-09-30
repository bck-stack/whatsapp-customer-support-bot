"""
Order lookup.
The default implementation reads data/orders.json; replace `OrderStore.get` with a call to
your database, Shopify/WooCommerce API, ERP, etc.
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ORDER_ID_RE = re.compile(r"\b(ORD-?\d{3,10})\b", re.IGNORECASE)


@dataclass
class Order:
    id: str
    status: str
    phone: str = ""
    eta: str = ""
    tracking: str = ""
    items: str = ""


def extract_order_id(text: str) -> Optional[str]:
    """Find an order ID like ORD-1042 / ord1042 in free text and normalise it to ORD-1042."""
    m = ORDER_ID_RE.search(text or "")
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(1))
    return f"ORD-{digits}"


class OrderStore:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def _load(self) -> dict[str, Order]:
        if not self.path.is_file():
            logger.warning("Orders file %s not found", self.path)
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Could not read orders file: %s", exc)
            return {}
        return {o["id"].upper(): Order(**o) for o in data.get("orders", []) if "id" in o and "status" in o}

    def get(self, order_id: str, requester_phone: str = "") -> Optional[Order]:
        """Return the order, only if it belongs to the requesting phone number (when one is stored)."""
        order = self._load().get(order_id.upper())
        if order and order.phone and requester_phone:
            if re.sub(r"\D", "", order.phone) != re.sub(r"\D", "", requester_phone):
                return None  # don't reveal other customers' orders
        return order
