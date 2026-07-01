"""Finnhub price/candle source — guarded, additive.

Uses your FINNHUB_KEY (60 calls/min free). Best for US stocks + crypto quotes;
free-tier forex is limited, so FX calls degrade to None and the caller falls back
to TwelveData/Frankfurter. No-op (returns None) when FINNHUB_KEY is unset.

  price(symbol)          latest price (stocks/crypto via /quote)
  forex_candles(pair..)  1h/1d OANDA candles for an FX pair, if the plan allows
"""

from __future__ import annotations

from typing import List, Optional

import httpx
from decouple import config

FINNHUB_KEY = config("FINNHUB_KEY", default=None)
BASE = "https://finnhub.io/api/v1"


def configured() -> bool:
    return bool(FINNHUB_KEY)


def fx_symbol(pair: str) -> str:
    """'EUR/USD' -> 'OANDA:EUR_USD' (Finnhub forex symbology)."""
    return "OANDA:" + pair.strip().upper().replace("/", "_")


async def price(symbol: str) -> Optional[float]:
    """Latest price via /quote (stocks/crypto). Returns None if unavailable."""
    if not FINNHUB_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"{BASE}/quote", params={"symbol": symbol, "token": FINNHUB_KEY})
            data = r.json()
        cur = data.get("c")
        return float(cur) if cur not in (None, 0) else None
    except Exception:
        return None


async def forex_candles(pair: str, resolution: str = "60",
                        count: int = 60) -> Optional[List[float]]:
    """Closes (oldest-first) for an FX pair, or None. resolution: 1,5,15,60,D."""
    if not FINNHUB_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"{BASE}/forex/candle", params={
                "symbol": fx_symbol(pair), "resolution": resolution,
                "count": count, "token": FINNHUB_KEY})
            data = r.json()
        if data.get("s") != "ok" or not data.get("c"):
            return None
        return [float(x) for x in data["c"]]
    except Exception:
        return None
