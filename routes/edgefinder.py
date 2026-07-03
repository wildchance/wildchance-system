"""EdgeFinder endpoints — per-pair macro bias scoreboard.

  GET /edgefinder            ranked bias board (retail + COT + confluence)
  GET /edgefinder?mmm=true   also fold in the MMM weekly-cycle bias (slower)
  GET /edgefinder/{symbol}   deep single-pair read (always includes MMM + news)

Aggregates the layers the system already computes into one signed bias score per
pair, most-conviction first.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Query
from decouple import config

from services import edgefinder_service

router = APIRouter(prefix="/edgefinder", tags=["edgefinder"])

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
async def board(mmm: bool = Query(False, description="fold in MMM weekly cycle (slower)")):
    return await edgefinder_service.scoreboard(with_mmm=mmm)


@router.post("/digest")
async def digest(top: int = Query(5, ge=1, le=20),
                 min_score: int = Query(2, ge=1, le=6),
                 mmm: bool = Query(False),
                 force: bool = Query(False, description="send even when nothing is strong")):
    """Push the top-conviction biases to Telegram (daily cron). Quiet if nothing
    clears ``min_score`` unless ``force``."""
    text = await edgefinder_service.digest_text(top, min_score, mmm)
    if not text:
        if not force:
            return {"sent": False, "reason": f"no bias ≥ {min_score}"}
        text = "🧭 *EdgeFinder* — no strong biases right now."
    return {"sent": await _send(text)}


@router.get("/{symbol:path}")
async def pair(symbol: str):
    row = await edgefinder_service.pair_read(symbol)
    if row is None:
        raise HTTPException(status_code=404,
                            detail=f"{symbol} not in the wildchance watchlist / feed")
    return row
