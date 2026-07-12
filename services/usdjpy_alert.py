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


async def attach_trend(sig: dict) -> None:
    """Attach the trend-extension TP ladder to a USD/JPY signal (best-effort).

    Shared by every USD/JPY path — the /close & /scan routes, the /signal read
    endpoint, and the autonomous scheduler — so the projected targets show up
    identically wherever the signal surfaces. Handles BOTH signal shapes: the live
    ingest result (``action``/``entry``/``ma``/``sd``) and the stored close row
    (``signal``/``close``/``ma20``/``sd20``). Direction follows the mean-reversion
    action (BUY→long / SELL→short); the reversion band (ma±sd) is the fallback
    reference impulse, with the 4H A→B→C swing preferred inside the helper. Never
    raises — a data hiccup must not stop an alert.
    """
    action = str(sig.get("action") or sig.get("signal") or "").upper()
    if not sig.get("is_trade") and action not in ("BUY", "SELL"):
        return
    try:
        from services import structure_service as ss
        bias = "long" if action == "BUY" else "short"
        ma = sig.get("ma") if sig.get("ma") is not None else sig.get("ma20")
        sd = sig.get("sd") if sig.get("sd") is not None else sig.get("sd20")
        ref_high = (ma + sd) if (ma is not None and sd is not None) else None
        ref_low = (ma - sd) if (ma is not None and sd is not None) else None
        entry = sig.get("entry") if sig.get("entry") is not None else sig.get("close")
        if entry is None:
            return
        tl = await ss.trend_targets("USD/JPY", bias, entry,
                                    ref_high=ref_high, ref_low=ref_low)
        if tl:
            sig["trend_targets"] = tl
    except Exception:
        pass


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

    from gold.signal import format_trend_lines
    text = "\n".join(lines) + format_trend_lines(signal)
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
