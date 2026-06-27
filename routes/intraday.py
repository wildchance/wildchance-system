"""Intraday breach endpoints — catch mid-session stretches between daily scans.

  GET  /intraday/scan     run the scan, return every instrument's breach state
  POST /intraday/scan     run the scan, send a Telegram alert for FRESH breaches
                          (called hourly by the cron; quiet when nothing is fresh)

z_threshold / window / symbols are query-overridable for tuning.
"""

from __future__ import annotations

from typing import Optional

import httpx
from fastapi import APIRouter, Query
from decouple import config

from services import intraday_service as isvc

router = APIRouter(prefix="/intraday", tags=["intraday"])

BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default=None) or config("BOT_TOKEN", default=None)
CHAT_ID = config("TELEGRAM_CHAT_ID", default=None)


async def _send(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("[intraday] telegram not configured — skipping send")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(url, json={
                "chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown",
            })
        return True
    except Exception as e:
        print(f"[intraday] send failed: {e}")
        return False


def _symbols(symbols: Optional[str]):
    if not symbols:
        return None
    return [s.strip() for s in symbols.split(",") if s.strip()]


@router.get("/scan")
async def scan(window: int = Query(20), z_threshold: float = Query(2.0),
               symbols: str = Query(None, description="comma-separated override")):
    results = await isvc.scan(_symbols(symbols), window=window, z_threshold=z_threshold)
    return {
        "scanned": len(results),
        "fresh": isvc.fresh_breaches(results),
        "results": results,
    }


@router.post("/scan")
async def scan_and_alert(window: int = Query(20), z_threshold: float = Query(2.0),
                         symbols: str = Query(None),
                         force: bool = Query(False)):
    results = await isvc.scan(_symbols(symbols), window=window, z_threshold=z_threshold)
    fresh = isvc.fresh_breaches(results)
    sent = False
    if fresh:
        sent = await _send(isvc.format_alert(fresh))
    elif force:
        sent = await _send("⚡ *Intraday scan* — no fresh breaches right now.")
    return {"scanned": len(results), "fresh_count": len(fresh),
            "sent": sent, "fresh": fresh}
