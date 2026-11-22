"""AI market sentiment (NVIDIA API) — the flowchart's MARKET SENTIMENT box.

  GET /sentiment/scan            score the swing universe (cached SENTIMENT_TTL_MIN)
  GET /sentiment/{symbol}        one instrument, e.g. /sentiment/XAUUSD?force=true
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Query

from services import ai_sentiment as ai

router = APIRouter(prefix="/sentiment", tags=["sentiment"])
_SEM = asyncio.Semaphore(2)          # stay well inside NVIDIA's free rate limits


async def _one(sym: str, force: bool):
    async with _SEM:
        return await ai.score(sym, force)


@router.get("/scan")
async def scan(force: bool = Query(False)):
    from services.swing_service import universe, display
    syms = [display(s) for s in universe()]
    rows = await asyncio.gather(*(_one(s, force) for s in syms))
    return {"auto_bias": ai.AUTO_BIAS, "model": ai.NVIDIA_MODEL,
            "locks": ai.cached_locks(), "results": list(rows)}


@router.get("/{symbol}")
async def one(symbol: str, force: bool = Query(False)):
    return await ai.score(symbol, force)
