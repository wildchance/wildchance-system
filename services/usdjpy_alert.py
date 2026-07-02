"""Telegram alerting for USD/JPY signals.

Reuses whichever bot token the deployment has configured. Both BOT_TOKEN and
TELEGRAM_BOT_TOKEN are accepted because the existing code base uses both names.
"""

from __future__ import annotations

from typing import Optional

import httpx
from decouple import config

BOT_TOKEN = config("BOT_TOKEN", default=None) or config("TELEGRAM_BOT_TOKEN", default=None)
CHAT_ID = config("TELEGRAM_CHAT_ID", default=None)


async def alert_signal(signal: dict, trade_risk: Optional[dict] = None) -> bool:
    """Send a BUY/SELL alert. Returns False if telegram isn't configured."""
    if not BOT_TOKEN or not CHAT_ID:
        return False

    action = signal.get("action")
    lines = ["📢 *USD/JPY Mean-Reversion Signal*", ""]
    news = signal.get("news_warning")
    if news:
        lines += [news, ""]
    lines += [
        f"🔹 *Action:* {action}",
        f"🔹 *Date:* {signal.get('date')}",
        f"🔹 *Entry (close):* {signal.get('entry')}",
        f"🔹 *Stop:* {round(signal.get('stop'), 3) if signal.get('stop') else 'N/A'}"
        f"  ({round(signal.get('stop_pips'), 1) if signal.get('stop_pips') else 'N/A'} pips)",
        f"🔹 *z-score:* {round(signal.get('z'), 2) if signal.get('z') is not None else 'N/A'}",
    ]
    if trade_risk:
        lines += [
            "",
            f"💰 *Acct:* ${trade_risk['account_size']:,} | *Lot:* {trade_risk['lot']}",
            f"⚠️ *Est. risk:* ${trade_risk['estimated_risk_usd']} "
            f"(daily cap ${trade_risk['daily_max_loss_cap']})",
        ]
        if not trade_risk.get("within_daily_cap"):
            lines.append("🚨 *Stop distance exceeds daily max-loss cap — size down.*")
    lines += ["", "⏱ Exit at the close 3 trading days from entry (time-based)."]

    text = "\n".join(lines)
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "Markdown",
            })
        return True
    except Exception:
        return False
