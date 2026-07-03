"""Backtest service — fetch OHLC and run the variant comparison."""

from __future__ import annotations

from typing import Optional

from services.ohlc_service import fetch_ohlc, to_ohlc
from backtest.engine import compare


async def run(symbol: str, bars: int = 400, **kw) -> Optional[dict]:
    ohlc = await fetch_ohlc(symbol, "1day", bars)
    if len(ohlc) < 60:
        return None
    return compare(to_ohlc(ohlc), **kw)
