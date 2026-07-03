"""EdgeFinder endpoints — per-pair macro bias scoreboard.

  GET  /edgefinder                 ranked bias board (retail + COT)
  GET  /edgefinder?mmm=true&season=true   fold in MMM cycle + seasonality (slower)
  POST /edgefinder/digest          push top biases to Telegram
  POST /edgefinder/snapshot        persist today's board (daily cron)
  GET  /edgefinder/shifts          pairs whose bias flipped since the last snapshot
  GET  /edgefinder/history/{pair}  score/bias trend for a pair
  GET  /edgefinder/{symbol}        deep single-pair read (MMM + seasonality + news)

Aggregates the layers the system already computes into one signed bias score per
pair, most-conviction first.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from decouple import config

from database.db import get_db
from services import edgefinder_service
from services import concentration_service

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
async def board(mmm: bool = Query(False, description="fold in MMM weekly cycle (slower)"),
                season: bool = Query(False, description="fold in seasonality (slower)")):
    return await edgefinder_service.scoreboard(with_mmm=mmm, with_season=season)


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


@router.post("/snapshot")
async def snapshot(db: AsyncSession = Depends(get_db)):
    """Persist today's board so bias trend/shift can be tracked (daily cron)."""
    return await edgefinder_service.snapshot(db)


@router.get("/shifts")
async def shifts(db: AsyncSession = Depends(get_db)):
    """Pairs whose bias flipped between the two most recent daily snapshots."""
    return await edgefinder_service.shifts(db)


@router.get("/history/{pair:path}")
async def history(pair: str, days: int = Query(30, ge=1, le=365),
                  db: AsyncSession = Depends(get_db)):
    """Score/bias trend for one pair over the trailing ``days``."""
    return await edgefinder_service.history(db, pair, days)


@router.get("/concentration")
async def concentration(threshold: float = Query(0.7, ge=0.1, le=1.0),
                        min_score: int = Query(2, ge=1, le=6)):
    """Warn when strong biases stack correlated exposure (concentration risk)."""
    return await concentration_service.concentration(threshold=threshold,
                                                     min_score=min_score)


@router.get("/{symbol:path}")
async def pair(symbol: str):
    row = await edgefinder_service.pair_read(symbol)
    if row is None:
        raise HTTPException(status_code=404,
                            detail=f"{symbol} not in the wildchance watchlist / feed")
    return row
