"""Stocks data — Polygon (daily bars) + Finnhub (live quote), both optional.

  POLYGON_API_KEY   /v2/aggs/ticker/{T}/range/1/day/{from}/{to} → daily OHLC (free tier:
                    5 calls/min, 2y history) — cached 12h
  FINNHUB_API_KEY   /api/v1/quote → last/high/low/open/prev close (free tier) — cached 60s
Without keys every call returns empty/None and nothing else changes. Used as the
yearly-CBDR fallback for stock symbols (NVDA, AAPL, …) after TwelveData and Yahoo.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Dict, List, Optional, Tuple

import httpx
from decouple import config

POLYGON_KEY = config("POLYGON_API_KEY", default=None)
FINNHUB_KEY = config("FINNHUB_KEY", default=None) or config("FINNHUB_API_KEY", default=None)
_cache: Dict[str, Tuple[float, object]] = {}


def _get(key: str, ttl: float):
    hit = _cache.get(key)
    return hit[1] if hit and time.time() - hit[0] < ttl else None


def is_stock(symbol: str) -> bool:
    s = symbol.upper()
    return s.isalpha() and 1 <= len(s) <= 5 and s not in ("DXY", "SPX", "NDX", "DJI")


def parse_polygon(data: Dict) -> List[Tuple[dt.date, float, float, float, float]]:
    out = []
    for r in (data or {}).get("results") or []:
        try:
            d = dt.datetime.fromtimestamp(r["t"] / 1000, dt.timezone.utc).date()
            out.append((d, float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"])))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out)


async def daily(symbol: str, days: int = 730) -> List[Tuple[dt.date, float, float, float, float]]:
    if not POLYGON_KEY or not is_stock(symbol):
        return []
    key = f"poly:{symbol}:{days}"
    hit = _get(key, 12 * 3600)
    if hit is not None:
        return hit
    to = dt.date.today()
    url = (f"https://api.polygon.io/v2/aggs/ticker/{symbol.upper()}/range/1/day/"
           f"{to - dt.timedelta(days=days)}/{to}")
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url, params={"adjusted": "true", "sort": "asc", "limit": 50000,
                                         "apiKey": POLYGON_KEY})
            bars = parse_polygon(r.json()) if r.status_code == 200 else []
    except Exception:
        bars = []
    if bars:
        _cache[key] = (time.time(), bars)
    return bars


async def quote(symbol: str) -> Optional[Dict]:
    if not FINNHUB_KEY or not is_stock(symbol):
        return None
    key = f"fh:{symbol}"
    hit = _get(key, 60)
    if hit is not None:
        return hit
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://finnhub.io/api/v1/quote",
                            params={"symbol": symbol.upper(), "token": FINNHUB_KEY})
            d = r.json() if r.status_code == 200 else {}
    except Exception:
        d = {}
    if not d.get("c"):
        return None
    q = {"symbol": symbol.upper(), "price": d["c"], "high": d.get("h"), "low": d.get("l"),
         "open": d.get("o"), "prev_close": d.get("pc")}
    _cache[key] = (time.time(), q)
    return q


def status() -> Dict:
    return {"polygon": bool(POLYGON_KEY), "finnhub": bool(FINNHUB_KEY)}
