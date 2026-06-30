"""Gated signal emission — alerts only fire when the prop-firm gate ALLOWS.

  POST /emit/signal   build a signal (auto or manual) and, IF the risk gate
                      passes, send it to Telegram + WhatsApp. Blocked signals are
                      returned but NOT sent.

This is the programmatic path (cron / confluence engine can call it). The Telegram
/create_signal command is the manual path.
"""

from __future__ import annotations

from typing import List, Optional

import httpx
from fastapi import APIRouter, Query
from decouple import config

from services import signal_engine as se
from services.whatsapp_service import send_text as wa_send

router = APIRouter(prefix="/emit", tags=["emit"])

BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default=None) or config("BOT_TOKEN", default=None)
CHAT_ID = config("TELEGRAM_CHAT_ID", default=None)


async def _tg_send(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(url, json={"chat_id": CHAT_ID, "text": text,
                                    "parse_mode": "Markdown"})
        return True
    except Exception:
        return False


@router.post("/signal")
async def emit_signal(symbol: str = Query(...),
                      side: str = Query(..., pattern="^(long|short|buy|sell)$"),
                      window: str = Query("cbdr"),
                      mode: str = Query("continuation"),
                      balance: float = Query(2500.0),
                      tier: str = Query("6"),
                      entry: Optional[float] = Query(None),
                      sl: Optional[float] = Query(None),
                      tps: Optional[str] = Query(None, description="comma-separated TP prices (manual)"),
                      force: bool = Query(False, description="send even if gate blocks")):
    side = "long" if side in ("long", "buy") else "short"
    if entry is not None and sl is not None and tps:
        tp_list = [float(x) for x in tps.split(",") if x.strip()]
        sig = se.build_manual(symbol.upper(), side, entry, sl, tp_list, balance, tier)
    else:
        sig = await se.build_auto(symbol.upper(), side, window=window, mode=mode,
                                  balance=balance, tier=tier, entry=entry)

    if sig.get("error"):
        return {"sent": False, "error": sig["error"]}

    allowed = sig["gate"]["allow"]
    card = se.format_card(sig)
    sent = False
    if allowed or force:
        sent_tg = await _tg_send(card)
        try:
            await wa_send(card.replace("*", "").replace("`", ""))
        except Exception:
            pass
        sent = sent_tg
    return {"sent": sent, "gate_allowed": allowed,
            "reason": sig["gate"]["reason"], "signal": sig}
