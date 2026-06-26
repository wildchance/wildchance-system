"""Session setup digests — push the current actionable setups to Telegram.

Called by the GitHub cron at the configured session times (05:00 / 09:00 /
17:00 / 21:00 UTC). It compiles:
  • wildchance confluence signals with a directional verdict (LONG/SHORT) and
    confidence >= SESSION_MIN_CONFIDENCE (default 80)
  • the latest USD/JPY mean-reversion signal, if it is BUY/SELL
and sends ONE Telegram message. If nothing qualifies it stays quiet (no spam),
unless called with ?force=true.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Query
from decouple import config

from database.db import AsyncSessionLocal
from services import usdjpy_service as usvc
from services.wildchance_service import get_latest_feed

router = APIRouter(prefix="/alerts", tags=["alerts"])

BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default=None) or config("BOT_TOKEN", default=None)
CHAT_ID = config("TELEGRAM_CHAT_ID", default=None)
MIN_CONFIDENCE = config("SESSION_MIN_CONFIDENCE", default=80, cast=int)


async def _send(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("[alerts] telegram not configured — skipping send")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(url, json={
                "chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown",
            })
        return True
    except Exception as e:
        print(f"[alerts] send failed: {e}")
        return False


def _confluence_setups(feed: dict, min_conf: int):
    out = [
        s for s in (feed or {}).get("signals", [])
        if s.get("verdict") in ("LONG", "SHORT") and (s.get("confidence") or 0) >= min_conf
    ]
    out.sort(key=lambda s: s.get("confidence", 0), reverse=True)
    return out


@router.post("/session")
async def session_digest(
    label: str = Query("", description="session label, e.g. 'New York'"),
    min_confidence: int = Query(None, description="override the confidence floor"),
    force: bool = Query(False, description="send even when nothing qualifies"),
):
    min_conf = min_confidence if min_confidence is not None else MIN_CONFIDENCE

    feed = await get_latest_feed()
    setups = _confluence_setups(feed, min_conf)

    async with AsyncSessionLocal() as db:
        usig = await usvc.latest_signal(db)
    usdjpy_fire = usig and usig.get("signal") in ("BUY", "SELL")

    if not setups and not usdjpy_fire and not force:
        return {"sent": False, "reason": "no qualifying setups",
                "label": label, "min_confidence": min_conf}

    title = f"📡 *Setup digest{(' — ' + label) if label else ''}*"
    lines = [title]

    if setups:
        lines.append("")
        lines.append(f"*Confluence (conf ≥ {min_conf}):*")
        for s in setups:
            lines.append(
                f"• {s['pair']}  *{s['verdict']}*  conf {s['confidence']}  "
                f"@ {s.get('price')}  (retail {s.get('retail_long','?')}%L)"
            )

    if usdjpy_fire:
        lines.append("")
        lines.append(
            f"*USD/JPY mean-reversion:* {usig['signal']} @ {usig['close']} "
            f"(z {usig.get('z')})"
        )

    if not setups and not usdjpy_fire:
        lines.append("\n_No qualifying setups right now._")

    sent = await _send("\n".join(lines))
    return {
        "sent": sent,
        "label": label,
        "min_confidence": min_conf,
        "confluence_setups": len(setups),
        "usdjpy": usig.get("signal") if usig else None,
    }
