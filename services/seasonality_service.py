"""Seasonality service — feed monthly OHLC into the pure seasonality engine.

Pulls ~N years of monthly bars for a symbol and returns the seasonal bias for the
CURRENT calendar month. Boot-safe: None on any fetch failure / thin history.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from services.ohlc_service import fetch_ohlc
from seasonality.engine import seasonal_bias


async def seasonal_for(symbol: str, years: int = 8,
                       month: Optional[int] = None) -> Optional[dict]:
    """Seasonal bias for ``symbol`` this month, from monthly closes."""
    bars = await fetch_ohlc(symbol, "1month", years * 12 + 4)
    if len(bars) < 14:                                   # need a couple of years
        return None
    dated = [(d, c) for (d, _o, _h, _l, c) in bars]      # DatedOHLC → (date, close)
    m = month or _dt.date.today().month
    return seasonal_bias(dated, m)
