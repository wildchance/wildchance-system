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


DXY_BAND = (70.0, 135.0)          # the index has lived here since 1985; anything else is another ticker


def is_index_level(closes: Optional[List[float]]) -> bool:
    return bool(closes) and DXY_BAND[0] <= closes[-1] <= DXY_BAND[1]


async def fetch_dxy(interval: str = "1h", outputsize: int = 60) -> Tuple[Optional[List[float]], str]:
    """Return (closes, source). source is the symbol used, or 'proxy:1/EURUSD'.
    A ticker that resolves to something that is not the index (e.g. a $25 ETF / stock)
    is rejected; Yahoo DX-Y.NYB from the price pool is tried before the EUR proxy."""
    for sym in DXY_SYMBOLS:
        closes = await _series(sym, interval, outputsize)
        if is_index_level(closes):
            return closes, sym
    try:
        from services.price_pool import alt_values
        d = await alt_values("DXY", interval, outputsize)
        closes = [float(v["close"]) for v in reversed((d or {}).get("values") or [])]
        if is_index_level(closes):
            return closes, d.get("source") or "pool"
    except Exception:
        pass
    eur = await _series("EUR/USD", interval, outputsize)
    if eur:
        return [1.0 / p for p in eur if p], "proxy:1/EURUSD"
    return None, "unavailable"


async def latest_dxy(interval: str = "1h") -> Optional[dict]:
    """The latest DXY close for the flip/extreme logic.

    ``is_level`` is True only for a genuine index symbol — the 1/EUR-USD proxy
    tracks DIRECTION but not the price LEVEL (it prints ~0.92, not ~100), so it is
    never fed into the 110/117 level gate; callers fall back to the anticipated
    structure when is_level is False.
    """
    closes, source = await fetch_dxy(interval, outputsize=5)
    if not closes:
        return None
    is_level = not source.startswith("proxy")
    return {"price": round(closes[-1], 3), "source": source, "is_level": is_level}
