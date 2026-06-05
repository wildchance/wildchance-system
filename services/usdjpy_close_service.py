"""Fetch the USD/JPY daily close automatically — replaces the manual paste.

The tracker workflow was: read the day's USD/JPY close off TradingView and type
it in. TradingView has no free official data API, so we pull the same daily
close from the data feeds the app already uses (TwelveData primary, with a
couple of free fallbacks). Use whichever USD/JPY feed you trust — the strategy
only needs ONE consistent daily close per trading day.
"""

from __future__ import annotations

from datetime import date
from typing import Optional, Tuple

import httpx
from decouple import config

TWELVEDATA_KEY = config("TWELVEDATA_API_KEY", default=None)


async def _twelvedata_daily_close() -> Optional[Tuple[date, float]]:
    """Most recent completed daily close from TwelveData time_series."""
    if not TWELVEDATA_KEY:
        return None
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": "USD/JPY",
        "interval": "1day",
        "outputsize": 2,
        "apikey": TWELVEDATA_KEY,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, params=params)
            data = r.json()
        values = data.get("values")
        if not values:
            return None
        latest = values[0]
        d = date.fromisoformat(latest["datetime"][:10])
        return d, float(latest["close"])
    except Exception:
        return None


async def _frankfurter_close() -> Optional[Tuple[date, float]]:
    """Free ECB reference rate fallback (USD->JPY). Daily, no key required."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get("https://api.frankfurter.app/latest",
                                 params={"from": "USD", "to": "JPY"})
            data = r.json()
        rate = data.get("rates", {}).get("JPY")
        d = date.fromisoformat(data["date"])
        if rate is None:
            return None
        return d, float(rate)
    except Exception:
        return None


async def fetch_daily_close() -> Optional[Tuple[date, float, str]]:
    """Return (date, close, source) for the latest USD/JPY daily close."""
    res = await _twelvedata_daily_close()
    if res:
        return res[0], res[1], "auto:twelvedata"

    res = await _frankfurter_close()
    if res:
        return res[0], res[1], "auto:frankfurter"

    return None
