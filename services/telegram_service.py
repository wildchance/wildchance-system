import httpx
from decouple import config

# Accept either name so deployments using BOT_TOKEN or TELEGRAM_BOT_TOKEN both
# work. default=None means a missing token NEVER crashes the app at startup —
# it just disables Telegram sending and logs a warning.
BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default=None) or config("BOT_TOKEN", default=None)
CHAT_ID = config("TELEGRAM_CHAT_ID", default=None)
TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage" if BOT_TOKEN else None


def _format(payload) -> str:
    """Accept a plain string OR a dict and return the message text.

    core/signals.py passes a string; other callers pass a dict. Handling both
    avoids an AttributeError when a string is passed.
    """
    if isinstance(payload, str):
        return payload
    return (
        "📢 *Wildchance Signal Alert*\n\n"
        f"🔹 *Pair:* {payload.get('pair') or payload.get('symbol')}\n"
        f"🔹 *Action:* {payload.get('action')}\n"
        f"🔹 *Lot Size:* {payload.get('lot_size', 'N/A')}\n"
        f"🔹 *Price:* {payload.get('price', 'N/A')}\n\n"
        "🚀 *Stay Profitable with Wildchance Alerts*"
    )


async def send_telegram_message(payload):
    """Send a Telegram message. No-ops safely if Telegram isn't configured."""
    if not BOT_TOKEN or not CHAT_ID:
        print("[telegram] not configured (set TELEGRAM_BOT_TOKEN/BOT_TOKEN and "
              "TELEGRAM_CHAT_ID) — skipping send.")
        return

    message = _format(payload)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                TELEGRAM_API,
                json={"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"},
            )
    except Exception as e:
        print(f"[telegram] send failed: {e}")
