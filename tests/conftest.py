import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.update(
    WHATSAPP_TOKEN="test-token",
    WHATSAPP_PHONE_ID="111",
    VERIFY_TOKEN="verify-me",
    META_APP_SECRET="app-secret",
    NOTIFY_API_KEY="notify-key",
    ORDER_TEMPLATE_NAME="order_update",
    SUPPORT_FORWARD_NUMBER="905550000000",
)
