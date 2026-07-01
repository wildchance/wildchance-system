"""Benner cycle endpoints — macro countdown timer.

  GET /benner            status for the current UTC year
  GET /benner/{year}     status for a specific year
"""

from __future__ import annotations

import datetime as _dt

import httpx
from fastapi import APIRouter
from decouple import config

from benner.engine import benner_status

router = APIRouter(prefix="/benner", tags=["benner"])

BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default=None) or config("BOT_TOKEN", default=None)
CHAT_ID = config("TELEGRAM_CHAT_ID", default=None)


async def _send(text: str) -> bool:
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


@router.get("")
async def benner_now():
    year = _dt.datetime.now(_dt.timezone.utc).year
    return benner_status(year)


@router.get("/{year}")
async def benner_year(year: int):
    return benner_status(year)


@router.post("/alert")
async def benner_alert():
    """Push the current year's Benner phase + countdown to Telegram (weekly cron)."""
    s = benner_status(_dt.datetime.now(_dt.timezone.utc).year)
    lines = [
        f"🕰️ *Benner Cycle — {s['year']}*",
        f"Phase: *{s['phase'].upper()}*",
        f"_{s['action']}_",
        "",
        f"Next top (sell): {s['next_top']}  ·  next bottom (buy): {s['next_bottom']}",
        f"Next panic: {s['next_panic']}",
    ]
    sent = await _send("\n".join(lines))
    return {"sent": sent, "year": s["year"], "phase": s["phase"]}
