import asyncio
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.handlers import Bot
from app.notifications import OrderNotification, send_order_notification
from app.orders import OrderStore, extract_order_id
from app.whatsapp import WhatsAppError, normalize_phone, verify_signature


class FakeWA:
    def __init__(self, fail_text_code=None):
        self.sent = []
        self.fail_text_code = fail_text_code

    async def send_text(self, to, body, preview_url=False):
        if self.fail_text_code:
            raise WhatsAppError(400, "outside window", self.fail_text_code)
        self.sent.append(("text", to, body))

    async def send_buttons(self, to, body_text, buttons, header_text=None, footer_text=None):
        self.sent.append(("buttons", to, [b["id"] for b in buttons]))

    async def send_template(self, to, name, lang="en_US", body_params=None):
        self.sent.append(("template", to, name, body_params))

    async def mark_as_read(self, msg_id):
        pass


def make_bot(tmp_path, orders=None):
    f = tmp_path / "orders.json"
    f.write_text(json.dumps({"orders": orders or [
        {"id": "ORD-1042", "status": "shipped", "eta": "Tomorrow", "tracking": "TRK-1"},
        {"id": "ORD-2000", "status": "delivered", "phone": "+90 555 111 22 33"},
    ]}))
    wa = FakeWA()
    return Bot(wa, OrderStore(str(f))), wa


def text_msg(body, mid="m1", sender="905551234567"):
    return {"from": sender, "id": mid, "type": "text", "text": {"body": body}}


def run(coro):
    return asyncio.run(coro)


def test_helpers():
    assert normalize_phone("+90 (555) 123-45-67") == "905551234567"
    assert normalize_phone("0090 555") == "90555"
    assert extract_order_id("where is ord 1042?") is None
    assert extract_order_id("where is ord1042?") == "ORD-1042"
    assert extract_order_id("my order is ORD-1042 thanks") == "ORD-1042"
    body = b'{"a":1}'
    sig = "sha256=" + hmac.new(b"s", body, hashlib.sha256).hexdigest()
    assert verify_signature(body, sig, "s") and not verify_signature(body, sig, "other")


def test_greeting_shows_menu(tmp_path):
    bot, wa = make_bot(tmp_path)
    run(bot.dispatch(text_msg("Merhaba"), "Ada Lovelace"))
    assert wa.sent[0][0] == "buttons" and wa.sent[0][2] == ["track_order", "support", "pricing"]


def test_order_flow_real_lookup(tmp_path):
    bot, wa = make_bot(tmp_path)
    run(bot.dispatch({"from": "9055", "id": "b1", "type": "interactive", "interactive": {"button_reply": {"id": "track_order"}}}))
    run(bot.dispatch(text_msg("ORD-1042", mid="m2", sender="9055")))
    reply = wa.sent[-1][2]
    assert "ORD-1042" in reply and "In Transit" in reply and "TRK-1" in reply


def test_unknown_order_and_retry_limit(tmp_path):
    bot, wa = make_bot(tmp_path)
    run(bot.dispatch(text_msg("track my order", mid="a")))
    run(bot.dispatch(text_msg("ORD-9999", mid="b")))
    assert "couldn't find" in wa.sent[-1][2]
    run(bot.dispatch(text_msg("order", mid="c")))
    for i in range(3):
        run(bot.dispatch(text_msg("blah", mid=f"x{i}")))
    assert "support" in wa.sent[-1][2]


def test_orders_of_other_customers_are_hidden(tmp_path):
    bot, wa = make_bot(tmp_path)
    run(bot.dispatch(text_msg("ORD-2000", sender="905559999999")))
    assert "couldn't find" in wa.sent[-1][2]
    run(bot.dispatch(text_msg("ORD-2000", mid="m9", sender="905551112233")))
    assert "Delivered" in wa.sent[-1][2]


def test_duplicate_deliveries_processed_once(tmp_path):
    bot, wa = make_bot(tmp_path)
    run(bot.dispatch(text_msg("pricing", mid="dup")))
    run(bot.dispatch(text_msg("pricing", mid="dup")))
    assert len(wa.sent) == 1


def test_support_forwards_to_staff(tmp_path):
    bot, wa = make_bot(tmp_path)
    run(bot.dispatch(text_msg("I need help with my invoice"), "Ada"))
    recipients = [s[1] for s in wa.sent]
    assert "905550000000" in recipients


def test_notification_falls_back_to_template():
    wa = FakeWA(fail_text_code=131047)
    notif = OrderNotification(phone="+90 555 123 45 67", order_id="ORD-1", customer_name="Ada", status="shipped")
    assert run(send_order_notification(wa, notif)) == "template"
    assert wa.sent[0][:3] == ("template", "905551234567", "order_update")


def test_notification_validation():
    with pytest.raises(ValueError):
        OrderNotification(phone="123", order_id="O", customer_name="A", status="shipped")
    with pytest.raises(ValueError):
        OrderNotification(phone="+905551234567", order_id="O", customer_name="A", status="lost")


@pytest.fixture()
def client(monkeypatch):
    import main

    handled = []

    async def fake_handle(payload):
        handled.append(payload)

    monkeypatch.setattr(main.bot, "handle_webhook", fake_handle)
    with TestClient(main.app) as c:
        c.handled = handled
        yield c


def test_webhook_verification(client):
    ok = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    assert client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "no", "hub.challenge": "1"}).status_code == 403


def test_webhook_requires_valid_signature(client):
    body = json.dumps({"object": "whatsapp_business_account", "entry": []}).encode()
    bad = client.post("/webhook", content=body, headers={"X-Hub-Signature-256": "sha256=00"})
    assert bad.status_code == 401
    sig = "sha256=" + hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()
    good = client.post("/webhook", content=body, headers={"X-Hub-Signature-256": sig})
    assert good.status_code == 200 and client.handled


def test_notify_requires_api_key(client):
    body = {"phone": "+905551234567", "order_id": "ORD-1", "customer_name": "Ada", "status": "shipped"}
    assert client.post("/notify/order", json=body).status_code == 401
    assert client.post("/notify/order", json={**body, "status": "weird"}, headers={"X-API-Key": "notify-key"}).status_code == 422
