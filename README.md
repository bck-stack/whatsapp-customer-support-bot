# WhatsApp Customer Support Bot

An intelligent customer service bot built natively on the Meta Cloud API to handle order tracking, FAQ routing, and proactive SMS-style notifications automatically.

✔ Drastically reduces customer support tickets by allowing users to self-serve order statuses via chat
✔ Increases customer satisfaction with instant, interactive button responses instead of rigid text menus
✔ Seamlessly integrates with your existing backend to trigger proactive shipping and delivery alerts

## Use Cases
- **E-Commerce Order Tracking:** Automatically reply to "Where is my order?" messages by querying your CRM in real-time.
- **Appointment Reminders:** Send proactive WhatsApp alerts to patients or clients to drastically reduce no-show rates.
- **Lead Qualification:** Use interactive buttons (e.g. "Get Pricing", "Talk to Sales") to instantly route high-intent leads to the right human agent.

## Project Structure

```
whatsapp-customer-support-bot/
├── main.py                 # FastAPI app: webhook verify/receive, notifications, health
├── app/
│   ├── config.py           # Settings from .env
│   ├── whatsapp.py         # Cloud API client (retries, phone normalisation, signature check)
│   ├── handlers.py         # Conversation logic, sessions with expiry, duplicate filtering
│   ├── orders.py           # Order lookup (JSON sample — swap for your DB/API)
│   └── notifications.py    # Outbound order updates with template fallback
├── data/orders.json        # Sample orders
├── tests/                  # pytest (no Meta account needed)
├── requirements.txt
└── .env.example
```

## Setup

### 1. Meta App Configuration
1. Create an app at [Meta for Developers](https://developers.facebook.com/)
2. Add **WhatsApp** product → get your `Phone Number ID` and a permanent (system user) `Access Token`
3. Copy the **App secret** (App settings → Basic) to `META_APP_SECRET`
4. Set webhook URL to `https://yourdomain.com/webhook` and `Verify Token` to match your `.env`
5. Subscribe to the `messages` field

### 2. Install & Run

```bash
pip install -r requirements.txt
cp .env.example .env
# Fill in WHATSAPP_TOKEN, WHATSAPP_PHONE_ID, VERIFY_TOKEN, META_APP_SECRET, NOTIFY_API_KEY
uvicorn main:app --host 0.0.0.0 --port 8000
```

For local testing, use [ngrok](https://ngrok.com/) to expose localhost:
```bash
ngrok http 8000
# Use the https URL as your webhook in Meta Dashboard
```

## Security & reliability

- **Signed webhooks** — every POST is checked against `X-Hub-Signature-256` with your app secret.
- **Protected notifications** — `/notify/order` requires `X-API-Key`, so nobody else can message your customers.
- **Fast acknowledgement** — Meta gets `200` immediately; messages are processed in the background after the response is sent.
- **Duplicate deliveries** (Meta retries) are processed once; conversation state expires after `SESSION_TTL_MINUTES`.
- **Privacy** — an order that has a phone number stored is only shown to that number.
- **Retries** — 429/5xx responses from the Graph API are retried with backoff.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/webhook` | Meta webhook verification |
| `POST` | `/webhook` | Receive incoming messages (signature checked) |
| `POST` | `/notify/order` | Send order notification to customer (`X-API-Key`) |
| `GET` | `/health` | Health check |

## Example — Order Notification

```bash
curl -X POST http://localhost:8000/notify/order \
  -H "Content-Type: application/json" \
  -H "X-API-Key: change_me" \
  -d '{
    "phone": "+905551234567",
    "order_id": "ORD-1042",
    "customer_name": "Alice",
    "status": "shipped",
    "detail": "Tracking: TRK-9988"
  }'
# {"sent": true, "via": "text", ...}
```

`status` must be `confirmed`, `shipped`, `delivered` or `cancelled`. WhatsApp only allows free-form messages
within 24 hours of the customer's last message; outside that window the bot automatically sends the approved
template set in `ORDER_TEMPLATE_NAME` (`"via": "template"`).

## Conversation Flow

```
User: "hi" / "merhaba" / "menu"
Bot:  [Interactive buttons] Track Order | Talk to Support | Pricing

User: [taps Track Order]
Bot:  "Please share your order ID (e.g. ORD-1042)"

User: "it's ord1042"
Bot:  "Order ORD-1042 — Status: In Transit 🚚
       Items: 2× Desk Lamp
       Expected delivery: Tomorrow by 6 PM
       Tracking: TRK-9988"

User: "I need help with my invoice"
Bot:  "I've let our team know — someone will reply here soon."   (+ forwarded to SUPPORT_FORWARD_NUMBER)
```

Order IDs are recognised anywhere in a message. After three unreadable IDs the bot offers human support.

## Connecting your order system

Replace `OrderStore.get()` in `app/orders.py` with a query to your database or shop API and return an
`Order(id, status, phone, eta, tracking, items)`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Tech Stack

`fastapi` · `uvicorn` · `httpx` · `pydantic-settings`

## Screenshot

![Preview](screenshots/preview.png)

