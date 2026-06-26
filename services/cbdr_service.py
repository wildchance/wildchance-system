"""Fetch the intraday bars for the CBDR window and build the box.

The CBDR window is 14:00–20:00 *New York* local time (DST-aware). We ask
TwelveData for 1h bars already converted to America/New_York, then keep the
most recent completed day's bars whose hour falls in [14, 20).

Rate-limit friendly: ONE time_series request per call. On any failure it
returns None and the caller degrades gracefully.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

import httpx
from decouple import config

from cbdr.engine import CBDR_START_HOUR, CBDR_END_HOUR, cbdr_box

TWELVEDATA_KEY = config("TWELVEDATA_API_KEY", default=None) or config("TWELVEDATA_KEY", default=None)
NY_TZ = "America/New_York"


async def fetch_cbdr_window(symbol: str) -> Optional[dict]:
    """Return {date, high, low, bars} for the most recent completed CBDR window,
    or None if data is unavailable."""
    if not TWELVEDATA_KEY:
        return None

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": "1h",
        "outputsize": 72,          # ~3 days of hourly bars
        "timezone": NY_TZ,         # bars already in New York local time
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

    # Group bar highs/lows by NY date, keeping only the CBDR hours.
    by_day_high = defaultdict(list)
    by_day_low = defaultdict(list)
    for v in values:
        ts = v.get("datetime", "")          # "YYYY-MM-DD HH:MM:SS" in NY time
        if len(ts) < 13:
            continue
        day = ts[:10]
        try:
            hour = int(ts[11:13])
            hi = float(v["high"])
            lo = float(v["low"])
        except (ValueError, KeyError):
            continue
        if CBDR_START_HOUR <= hour < CBDR_END_HOUR:
            by_day_high[day].append(hi)
            by_day_low[day].append(lo)

    if not by_day_high:
        return None

    # Most recent day that actually has window bars (Fri carries over weekends).
    day = max(by_day_high.keys())
    highs, lows = by_day_high[day], by_day_low[day]
    high, low = cbdr_box(highs, lows)
    return {"date": day, "high": high, "low": low, "bars": len(highs)}
