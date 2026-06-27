"""Performance scorecard + reflection endpoints.

  GET  /scorecard/summary            all-time + current-month + by-action, with
                                      reflection verdict and confidence factor
  GET  /scorecard/monthly            per-calendar-month breakdown
  POST /scorecard/digest             send the monthly scorecard to Telegram
                                      (called by the monthly cron, 1st of month)

All numbers come from CLOSED USD/JPY trades (realized R). No external APIs.
?stop_aware=true scores the conservative series (stop-breached trades -> -1R).
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Query
from decouple import config

from database.db import AsyncSessionLocal
from services import scorecard_service as svc

router = APIRouter(prefix="/scorecard", tags=["scorecard"])

BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default=None) or config("BOT_TOKEN", default=None)
CHAT_ID = config("TELEGRAM_CHAT_ID", default=None)


async def _send(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("[scorecard] telegram not configured — skipping send")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(url, json={
                "chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown",
            })
        return True
    except Exception as e:
        print(f"[scorecard] send failed: {e}")
        return False


@router.get("/summary")
async def summary(stop_aware: bool = Query(False)):
    async with AsyncSessionLocal() as db:
        return await svc.build_report(db, stop_aware=stop_aware)


@router.get("/monthly")
async def monthly(stop_aware: bool = Query(False)):
    async with AsyncSessionLocal() as db:
        report = await svc.build_report(db, stop_aware=stop_aware)
    return {
        "mode": report["mode"],
        "closed_trades": report["closed_trades"],
        "by_month": report["by_month"],
    }


@router.post("/digest")
async def digest(month: str = Query(None, description="YYYY-MM; default = latest"),
                 force: bool = Query(False, description="send even if nothing closed")):
    async with AsyncSessionLocal() as db:
        text = await svc.monthly_digest_text(db, month=month)
    if not text:
        if force:
            text = "📊 *Monthly Scorecard*\n\n_No closed trades yet this period._"
        else:
            return {"sent": False, "reason": "no closed trades in window"}
    sent = await _send(text)
    return {"sent": sent, "month": month}
