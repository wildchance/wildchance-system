"""Fetch intraday candles for PD-array detection.

One TwelveData time_series request per call (rate-limit friendly). Returns
candles oldest-first as {datetime, open, high, low, close}, or None on failure.
"""

from __future__ import annotations

from typing import List, Optional

import httpx
from decouple import config

TWELVEDATA_KEY = config("TWELVEDATA_API_KEY", default=None) or config("TWELVEDATA_KEY", default=None)


async def fetch_candles(symbol: str, interval: str = "15min",
                        outputsize: int = 120) -> Optional[List[dict]]:
    if not TWELVEDATA_KEY:
        return None
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVEDATA_KEY,
    }
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(url, params=params)
            data = r.json()
    except Exception:
        return None

    values = data.get("values")
    if not values:
        return None

    candles: List[dict] = []
    for v in reversed(values):            # API returns newest-first; we want oldest-first
        try:
            candles.append({
                "datetime": v.get("datetime"),
                "open": float(v["open"]),
                "high": float(v["high"]),
                "low": float(v["low"]),
                "close": float(v["close"]),
            })
        except (KeyError, ValueError, TypeError):
            continue
    return candles or None
