"""Market Maker Methods confluence — read a symbol, score a side.

Bridges the feed (ohlc_service) to the pure mmm/ engine. Returns the weekly-cycle
read and a confluence verdict (confirms / diverges / neutral) for a proposed side,
so the setup builder can rank or caution a signal by the MMM edge.

Boot-safe: returns a 'none' verdict when OHLC is unavailable — never raises.
"""

from __future__ import annotations

from typing import List, Optional

from services.ohlc_service import fetch_ohlc, to_ohlc, DatedOHLC
from mmm.engine import weekly_setup, confluence as _confluence


async def read_with_daily(symbol: str, daily: List[DatedOHLC],
                          adr_n: int = 5) -> Optional[dict]:
    """MMM read reusing already-fetched daily bars (+ one weekly fetch)."""
    if len(daily) < 3:
        return None
    weekly = await fetch_ohlc(symbol, "1week", 4)
    if len(weekly) < 2:
        return None
    prev_week = to_ohlc([weekly[-2]])[0]          # last COMPLETED week
    return weekly_setup(symbol, daily[-1][0].weekday(),
                        prev_week, to_ohlc(daily), adr_n)


async def read(symbol: str, adr_n: int = 5) -> Optional[dict]:
    """MMM read from its own daily + weekly fetch."""
    daily = await fetch_ohlc(symbol, "1day", 60)
    return await read_with_daily(symbol, daily, adr_n)


def confluence(setup: Optional[dict], side: str) -> dict:
    """Pure passthrough to the engine verdict (confirms/diverges/neutral/none)."""
    return _confluence(setup, side)
