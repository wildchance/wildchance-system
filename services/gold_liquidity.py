"""Assemble the gold session-liquidity map (network glue around the pure helpers).

Fetches hour-preserving 1h bars in the chart's timezone (America/New_York, so 1am /
7am line up with the ET chart) plus daily bars, and builds the full map: 1am/7am
highs-lows, the 8-hour range, PDH/PDL and PWH/PWL. Degrades to {} when data/keys
are missing.
"""

from __future__ import annotations

from services.ohlc_service import fetch_hourly_raw, fetch_ohlc
from gold.session_levels import build_liquidity

CHART_TZ = "America/New_York"      # 1am / 7am are ET, matching the chart


async def liquidity_map(symbol: str = "XAU/USD") -> dict:
    hourly = await fetch_hourly_raw(symbol, timezone=CHART_TZ, outputsize=48)
    daily = await fetch_ohlc(symbol, "1day", 15)
    return build_liquidity(hourly, daily)
