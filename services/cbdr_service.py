"""Fetch intraday bars for a named CBDR window and build the box.

Supports multiple session windows (see cbdr.engine.WINDOWS):
  • "cbdr"      14:00–20:00 New York, 1h bars   (classic ICT CBDR)
  • "prelondon" 18:00–02:45 UTC, 15m bars       (pre-London accumulation box)

Each window declares its own timezone and bar interval. We ask TwelveData for
bars already converted to that timezone, bucket them into logical sessions
(honouring a midnight wrap), and return the most recently completed session's
high/low. ONE time_series request per call; returns None on any failure so the
caller degrades gracefully.

NOTE: query a window only AFTER it has closed (the schedulers do — e.g. the
pre-London alert fires at 03:00 UTC) so the latest bucket is a complete session.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

import httpx
from decouple import config

from cbdr.engine import WINDOWS, Window, in_window, session_key, cbdr_box

TWELVEDATA_KEY = config("TWELVEDATA_API_KEY", default=None) or config("TWELVEDATA_KEY", default=None)

# Bars to request — enough to cover ~3 sessions at the finest interval.
_OUTPUTSIZE = {"1h": 72, "15min": 320}


def _minute_of_day(ts: str) -> Optional[int]:
    """Minutes-from-midnight from a 'YYYY-MM-DD HH:MM:SS' timestamp."""
    if len(ts) < 16:
        return None
    try:
        return int(ts[11:13]) * 60 + int(ts[14:16])
    except ValueError:
        return None


async def fetch_cbdr_window(symbol: str, window: str = "cbdr") -> Optional[dict]:
    """Return {session, high, low, bars, window, interval, tz, label} for the most
    recent completed session of `window`, or None if unavailable/unknown."""
    if not TWELVEDATA_KEY:
        return None
    win: Optional[Window] = WINDOWS.get(window)
    if win is None:
        return None

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": win.interval,
        "outputsize": _OUTPUTSIZE.get(win.interval, 72),
        "timezone": win.tz,
        "apikey": TWELVEDATA_KEY,
    }
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            data = (await client.get(url, params=params)).json()
    except Exception:
        return None

    values = data.get("values")
    if not values:
        return None

    by_session_high = defaultdict(list)
    by_session_low = defaultdict(list)
    for v in values:
        ts = v.get("datetime", "")
        minute = _minute_of_day(ts)
        if minute is None or len(ts) < 10:
            continue
        if not in_window(minute, win.start_min, win.end_min):
            continue
        try:
            hi = float(v["high"])
            lo = float(v["low"])
        except (ValueError, KeyError):
            continue
        sk = session_key(ts[:10], minute, win.start_min, win.end_min)
        by_session_high[sk].append(hi)
        by_session_low[sk].append(lo)

    if not by_session_high:
        return None

    session = max(by_session_high.keys())
    highs, lows = by_session_high[session], by_session_low[session]
    high, low = cbdr_box(highs, lows)
    return {
        "session": session,
        "high": high,
        "low": low,
        "bars": len(highs),
        "window": win.key,
        "interval": win.interval,
        "tz": win.tz,
        "label": win.label,
    }
