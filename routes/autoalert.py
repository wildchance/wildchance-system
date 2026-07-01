"""Autonomous confluence alerting — a full signal card per qualifying setup.

Where /alerts/session sends ONE digest, this fires an individual Entry/SL/TP1-3
card (via the setup engine) for EACH wildchance confluence signal that clears the
confidence floor AND the prop-firm risk gate. Sends to Telegram + WhatsApp.

  POST /autoalert/confluence?min_confidence=85&window=cbdr&balance=2500&tier=6
        &force=false   (force sends even gate-blocked cards)

Wire it into the 6h wildchance cron so the system alerts new setups on its own.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Query
from decouple import config

from services.wildchance_service import get_latest_feed
from services import signal_engine as se
from services.whatsapp_service import send_text as wa_send

router = APIRouter(prefix="/autoalert", tags=["autoalert"])

BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default=None) or config("BOT_TOKEN", default=None)
CHAT_ID = config("TELEGRAM_CHAT_ID", default=None)
MIN_CONFIDENCE = config("SESSION_MIN_CONFIDENCE", default=80, cast=int)


async def _tg(text: str) -> bool:
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


@router.post("/confluence")
async def confluence_alerts(min_confidence: int = Query(None),
                            window: str = Query("cbdr"),
                            balance: float = Query(2500.0),
                            tier: str = Query("6"),
                            force: bool = Query(False)):
    min_conf = min_confidence if min_confidence is not None else MIN_CONFIDENCE
    feed = await get_latest_feed()
    signals = [s for s in (feed or {}).get("signals", [])
               if s.get("verdict") in ("LONG", "SHORT")
               and (s.get("confidence") or 0) >= min_conf]

    seen, sent, blocked = set(), [], []
    for s in signals:
        pair = s.get("pair")
        if not pair or pair in seen:
            continue
        seen.add(pair)
        side = "long" if s["verdict"] == "LONG" else "short"
        sig = await se.build_auto(pair, side, window=window, balance=balance, tier=tier)
        if sig.get("error"):
            continue
        allowed = sig["gate"]["allow"]
        if not (allowed or force):
            blocked.append(pair)
            continue
        card = se.format_card(sig)
        card = f"conf {s.get('confidence')} · {card}"
        await _tg(card)
        try:
            await wa_send(card.replace("*", "").replace("`", ""))
        except Exception:
            pass
        sent.append(pair)

    return {"min_confidence": min_conf, "qualifying": len(signals),
            "sent": sent, "gate_blocked": blocked}
