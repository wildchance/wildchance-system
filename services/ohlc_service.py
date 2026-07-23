"""Generic OHLC bar fetch for any instrument — feeds ATR + MMM engines.

TwelveData time_series returns OHLC for FX, metals and indices. This normalizes
a symbol (EURUSD → EUR/USD, XAUUSD → XAU/USD) and returns bars oldest-first so
the pure engines can consume them. Empty list on any failure (boot-safe).
"""

from __future__ import annotations

import datetime as dt
import re
from typing import List, Optional, Tuple

import httpx
from decouple import config

TWELVEDATA_KEY = config("TWELVEDATA_API_KEY", default=None)

# (date, open, high, low, close)
DatedOHLC = Tuple[dt.date, float, float, float, float]


def normalize_symbol(symbol: str) -> str:
    """EURUSD → EUR/USD; XAUUSD → XAU/USD; already-slashed passes through."""
    s = (symbol or "").upper().strip()
    if "/" in s:
        return s
    if re.fullmatch(r"[A-Z]{6}", s):
        return f"{s[:3]}/{s[3:]}"
    return s


async def fetch_ohlc(symbol: str, interval: str = "1day",
                     outputsize: int = 60) -> List[DatedOHLC]:
    """Recent OHLC bars, oldest-first. interval: 1day / 1week / 1h / 4h …"""
    if not TWELVEDATA_KEY:
        return []
    params = {
        "symbol": normalize_symbol(symbol),
        "interval": interval,
        "outputsize": max(1, min(int(outputsize), 5000)),
        "apikey": TWELVEDATA_KEY,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get("https://api.twelvedata.com/time_series",
                                 params=params)
            data = r.json()
        values = data.get("values") or []
        out: List[DatedOHLC] = []
        for v in values:
            try:
                out.append((
                    dt.date.fromisoformat(v["datetime"][:10]),
                    float(v["open"]), float(v["high"]),
                    float(v["low"]), float(v["close"]),
                ))
            except (KeyError, ValueError, TypeError):
                continue
        out.sort(key=lambda t: t[0])          # ascending (feed is newest-first)
        return out
    except Exception:
        return []


async def fetch_hourly_raw(symbol: str, timezone: str = "UTC",
                           outputsize: int = 48) -> List[dict]:
    """1h bars WITH the hour preserved, in ``timezone``, oldest-first.

    fetch_ohlc collapses timestamps to a date; the session-level map needs the hour
    (1am / 7am) in the chart's timezone (e.g. America/New_York), so this keeps it.
    Returns [{date, hour, open, high, low, close}]. Empty on any failure.
    """
    if not TWELVEDATA_KEY:
        return []
    params = {"symbol": normalize_symbol(symbol), "interval": "1h",
              "outputsize": max(1, min(int(outputsize), 5000)),
              "timezone": timezone, "apikey": TWELVEDATA_KEY}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            data = (await client.get("https://api.twelvedata.com/time_series",
                                     params=params)).json()
        out: List[dict] = []
        for v in data.get("values") or []:
            ts = v.get("datetime", "")
            if len(ts) < 13:
                continue
            try:
                out.append({"date": ts[:10], "hour": int(ts[11:13]),
                            "open": float(v["open"]), "high": float(v["high"]),
                            "low": float(v["low"]), "close": float(v["close"])})
            except (KeyError, ValueError, TypeError):
                continue
        out.sort(key=lambda b: (b["date"], b["hour"]))
        return out
    except Exception:
        return []


def to_ohlc(bars: List[DatedOHLC]) -> List[Tuple[float, float, float, float]]:
    """Strip dates → (open, high, low, close) tuples for the MMM engine."""
    return [(o, h, l, c) for (_d, o, h, l, c) in bars]


async def fetch_ohlc_raw(symbol: str, interval: str = "4h", outputsize: int = 30,
                         timezone: str = "UTC") -> List[dict]:
    """OHLC bars with the FULL datetime preserved (oldest-first) for any interval.

    fetch_ohlc collapses timestamps to a date — fine for daily/weekly but it
    scrambles intraday order and loses the hour. This keeps the whole timestamp so
    the 4H b2b bomber can sequence candles and read session anchors. Returns
    [{time, open, high, low, close}]. Empty on any failure.
    """
    if not TWELVEDATA_KEY:
        return []
    params = {"symbol": normalize_symbol(symbol), "interval": interval,
              "outputsize": max(1, min(int(outputsize), 5000)),
              "timezone": timezone, "apikey": TWELVEDATA_KEY}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            data = (await client.get("https://api.twelvedata.com/time_series",
                                     params=params)).json()
        out: List[dict] = []
        for v in data.get("values") or []:
            ts = v.get("datetime", "")
            if not ts:
                continue
            try:
                out.append({"time": ts, "open": float(v["open"]), "high": float(v["high"]),
                            "low": float(v["low"]), "close": float(v["close"])})
            except (KeyError, ValueError, TypeError):
                continue
        out.sort(key=lambda b: b["time"])          # ascending (feed is newest-first)
        return out
    except Exception:
        return []


def to_hlc(bars: List[DatedOHLC]) -> List[Tuple[float, float, float]]:
    """(high, low, close) tuples for the ATR engine."""
    return [(h, l, c) for (_d, _o, h, l, c) in bars]
