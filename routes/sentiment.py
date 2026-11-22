"""AI market sentiment (NVIDIA API) — the flowchart's MARKET SENTIMENT box.

  GET /sentiment/scan            score the swing universe (cached SENTIMENT_TTL_MIN)
  GET /sentiment/{symbol}        one instrument, e.g. /sentiment/XAUUSD?force=true
  GET /sentiment/polymarket      prediction-market read per asset
  GET /sentiment/composite/{sym} news + Polymarket + Myfxbook retail (contrarian)
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
    """Refresh every source, then report the composite per asset."""
    from services.swing_service import universe, display
    from services import market_sentiment as ms, polymarket_service as pm
    from wildchance import wildchance_scraper as wc
    syms = [display(s) for s in universe()]
    rows = await asyncio.gather(*(_one(s, force) for s in syms))
    poly = await pm.fetch()
    await asyncio.to_thread(wc.fetch_retail)
    return {"auto_bias": ai.AUTO_BIAS, "model": ai.NVIDIA_MODEL,
            "polymarket": {k: poly[k] for k in ("ok", "relevant", "used", "as_of")},
            "retail": wc.retail_status(),
            "locks": ms.cached_locks(syms),
            "composite": [ms.read(s) for s in syms], "news": list(rows)}


@router.get("/polymarket")
async def polymarket():
    from services import polymarket_service as pm
    return await pm.fetch()


@router.get("/composite/{symbol}")
async def composite(symbol: str):
    from services import market_sentiment as ms
    return ms.read(symbol)


@router.get("/{symbol}")
async def one(symbol: str, force: bool = Query(False)):
    return await ai.score(symbol, force)
