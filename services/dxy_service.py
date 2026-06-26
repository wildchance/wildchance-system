"""DXY (US Dollar Index) data, with a graceful proxy fallback.

TwelveData's free tier often doesn't carry the dollar index directly, so we try
a few index symbols and, failing that, fall back to a 1/EUR-USD proxy (EUR is
~57.6% of the DXY basket and strongly inverse, so it tracks DXY direction well).
"""

from __future__ import annotations

from typing import List, Optional, Tuple

import httpx
from decouple import config

TWELVEDATA_KEY = config("TWELVEDATA_API_KEY", default=None) or config("TWELVEDATA_KEY", default=None)

DXY_SYMBOLS = ("DXY", "USDX", "DX", "DX1!")


async def _series(symbol: str, interval: str = "1h", outputsize: int = 60) -> Optional[List[float]]:
    """Closes oldest-first for a symbol, or None."""
    if not TWELVEDATA_KEY:
        return None
    url = "https://api.twelvedata.com/time_series"
    params = {"symbol": symbol, "interval": interval,
              "outputsize": outputsize, "apikey": TWELVEDATA_KEY}
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            data = (await client.get(url, params=params)).json()
    except Exception:
        return None
    values = data.get("values")
    if not values:
        return None
    closes = []
    for v in reversed(values):
        try:
            closes.append(float(v["close"]))
        except (KeyError, ValueError, TypeError):
            continue
    return closes or None


async def instrument_closes(td_symbol: str, interval: str = "1h",
                            outputsize: int = 60) -> Optional[List[float]]:
    return await _series(td_symbol, interval, outputsize)


async def fetch_dxy(interval: str = "1h", outputsize: int = 60) -> Tuple[Optional[List[float]], str]:
    """Return (closes, source). source is the symbol used, or 'proxy:1/EURUSD'."""
    for sym in DXY_SYMBOLS:
        closes = await _series(sym, interval, outputsize)
        if closes:
            return closes, sym
    eur = await _series("EUR/USD", interval, outputsize)
    if eur:
        return [1.0 / p for p in eur if p], "proxy:1/EURUSD"
    return None, "unavailable"
