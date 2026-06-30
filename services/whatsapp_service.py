"""WhatsApp sender — Meta Cloud API (preferred) with CallMeBot fallback. Guarded.

No-op (returns False) unless a provider is configured, so the app runs fine
without WhatsApp. Two options:
  • Meta Cloud API: set WHATSAPP_TOKEN, WHATSAPP_PHONE_ID, WHATSAPP_TO
  • CallMeBot (simple): set WHATSAPP_PHONE, WHATSAPP_API_KEY
"""

from __future__ import annotations

from typing import Optional

import httpx
from decouple import config

# Meta WhatsApp Business Cloud API
WHATSAPP_TOKEN = config("WHATSAPP_TOKEN", default=None)
WHATSAPP_PHONE_ID = config("WHATSAPP_PHONE_ID", default=None)
WHATSAPP_TO = config("WHATSAPP_TO", default=None)
GRAPH_VERSION = config("WHATSAPP_GRAPH_VERSION", default="v18.0")

# CallMeBot fallback
WHATSAPP_PHONE = config("WHATSAPP_PHONE", default=None)
WHATSAPP_API_KEY = config("WHATSAPP_API_KEY", default=None)


def configured() -> bool:
    meta = bool(WHATSAPP_TOKEN and WHATSAPP_PHONE_ID and WHATSAPP_TO)
    cmb = bool(WHATSAPP_PHONE and WHATSAPP_API_KEY)
    return meta or cmb


async def send_text(message: str, to: Optional[str] = None) -> bool:
    """Send via Meta Cloud API if configured, else CallMeBot, else no-op."""
    if WHATSAPP_TOKEN and WHATSAPP_PHONE_ID and (to or WHATSAPP_TO):
        url = f"https://graph.facebook.com/{GRAPH_VERSION}/{WHATSAPP_PHONE_ID}/messages"
        payload = {"messaging_product": "whatsapp", "to": to or WHATSAPP_TO,
                   "type": "text", "text": {"body": message}}
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(url, json=payload, headers=headers)
                return r.status_code < 400
        except Exception as e:
            print(f"[whatsapp] meta send failed: {e}")
            return False

    if WHATSAPP_PHONE and WHATSAPP_API_KEY:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get("https://api.callmebot.com/whatsapp.php",
                                params={"phone": WHATSAPP_PHONE, "text": message,
                                        "apikey": WHATSAPP_API_KEY})
                return r.status_code < 400
        except Exception as e:
            print(f"[whatsapp] callmebot send failed: {e}")
            return False

    print("[whatsapp] not configured — skipping send")
    return False
