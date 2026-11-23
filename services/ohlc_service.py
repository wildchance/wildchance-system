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
# Opt-in free fallback when TwelveData returns nothing (no key / out of credits).
# OFF by default: Yahoo has no spot gold, so XAU/USD maps to COMEX futures (GC=F),
# which trades ~$20-40 above spot — fine for bias/regime, NOT for exact SL/TP levels.
YAHOO_FALLBACK = str(config("YAHOO_FALLBACK_ENABLED", default="false")).strip().lower() in (
    "1", "true", "yes", "on")

import logging as _logging
import time as _time

_log = _logging.getLogger("uvicorn.error")
_last_td_warn = [0.0]


def _td_error(data) -> None:
    """Log TwelveData's own error (bad key, out of credits, unknown symbol) at most once
    every 10 min. Without this the API silently returns [] and every signal goes quiet."""
    if isinstance(data, dict) and data.get("status") == "error":
        now = _time.time()
        if now - _last_td_warn[0] > 600:
            _last_td_warn[0] = now
            _log.warning("TwelveData error %s: %s", data.get("code"), data.get("message"))


_YAHOO_SYMBOLS = {"XAU/USD": "GC=F", "XAG/USD": "SI=F", "DXY": "DX-Y.NYB",
                  "US500": "^GSPC", "SPX": "^GSPC", "NAS100": "^NDX", "NDX": "^NDX",
                  "US30": "^DJI", "USOIL": "CL=F", "WTI": "CL=F", "BTC/USD": "BTC-USD"}
_YAHOO_INTERVALS = {"1day": ("1d", 1), "1week": ("1wk", 7), "1h": ("1h", 1 / 24)}


def yahoo_symbol(symbol: str) -> str:
    s = normalize_symbol(symbol)
    if s in _YAHOO_SYMBOLS:
        return _YAHOO_SYMBOLS[s]
    if re.fullmatch(r"[A-Z]{3}/[A-Z]{3}", s):
        return s.replace("/", "") + "=X"            # spot FX, e.g. EURUSD=X
    return s


def parse_yahoo_chart(data: dict) -> List[DatedOHLC]:
    """Yahoo v8 chart JSON → [(date, o, h, l, c)] oldest-first, skipping null bars."""
    try:
        res = data["chart"]["result"][0]
        ts = res["timestamp"]
        q = res["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError):
        return []
    out: List[DatedOHLC] = []
    for i, t in enumerate(ts):
        o, h, l, c = (q.get(k, [None])[i] if i < len(q.get(k, [])) else None
                      for k in ("open", "high", "low", "close"))
        if None in (o, h, l, c):
            continue
        out.append((dt.datetime.utcfromtimestamp(t).date(), float(o), float(h), float(l), float(c)))
    return out


async def _yahoo_ohlc(symbol: str, interval: str, outputsize: int) -> List[DatedOHLC]:
    if interval not in _YAHOO_INTERVALS:
        return []
    yi, days_per = _YAHOO_INTERVALS[interval]
    span = max(5, int(outputsize * days_per * 1.6) + 5)
    rng = "730d" if interval == "1h" else ("max" if span > 3650 else f"{span}d")
    try:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_symbol(symbol)}",
                params={"range": rng, "interval": yi})
        return parse_yahoo_chart(r.json())[-outputsize:]
    except Exception:
        return []

# (date, open, high, low, close)
DatedOHLC = Tuple[dt.date, float, float, float, float]


async def time_series(params: dict, broker: bool = True) -> dict:
    """Broker feed (EA-pushed, free, real-time) first; TwelveData when it's absent/stale.
    ``broker=False`` skips the EA feed (it only holds recent bars — useless for years)."""
    try:
        if not broker:
            raise LookupError
        from services import broker_feed
        b = await broker_feed.td_values(params["symbol"], params["interval"],
                                        int(params.get("outputsize", 60)),
                                        params.get("timezone") or "UTC")
        if b:
            _fresh(params["symbol"], "broker")
            return b
    except Exception:
        pass
    if not TWELVEDATA_KEY:
        return {}
    async with httpx.AsyncClient(timeout=15) as client:
        data = (await client.get("https://api.twelvedata.com/time_series",
                                 params={**params, "apikey": TWELVEDATA_KEY})).json()
    _td_error(data)
    if isinstance(data, dict) and data.get("values"):
        _fresh(params["symbol"], "twelvedata")
    return data


def _fresh(symbol: str, source: str) -> None:
    try:
        from services.freshness import record
        record(symbol, source)
    except Exception:
        pass


def normalize_symbol(symbol: str) -> str:
    """EURUSD → EUR/USD; XAUUSD → XAU/USD; already-slashed passes through."""
    s = (symbol or "").upper().strip()
    if "/" in s:
        return s
    if re.fullmatch(r"[A-Z]{6}", s):
        return f"{s[:3]}/{s[3:]}"
    return s


async def fetch_ohlc(symbol: str, interval: str = "1day",
                     outputsize: int = 60, broker: bool = True) -> List[DatedOHLC]:
    """Recent OHLC bars, oldest-first. interval: 1day / 1week / 1h / 4h …"""
    out = await _fetch_ohlc_td(symbol, interval, outputsize, broker)
    if not out and YAHOO_FALLBACK:
        out = await _yahoo_ohlc(symbol, interval, outputsize)
    return out


async def _fetch_ohlc_td(symbol: str, interval: str, outputsize: int,
                         broker: bool = True) -> List[DatedOHLC]:
    params = {
        "symbol": normalize_symbol(symbol),
        "interval": interval,
        "outputsize": max(1, min(int(outputsize), 5000)),
    }
    try:
        data = await time_series(params, broker=broker)
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
    params = {"symbol": normalize_symbol(symbol), "interval": "1h",
              "outputsize": max(1, min(int(outputsize), 5000)),
              "timezone": timezone}
    try:
        data = await time_series(params)
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
    params = {"symbol": normalize_symbol(symbol), "interval": interval,
              "outputsize": max(1, min(int(outputsize), 5000)),
              "timezone": timezone}
    try:
        data = await time_series(params)
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
