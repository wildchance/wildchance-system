"""CBDR (Central Bank Dealers Range) API.

  GET /cbdr/{symbol}            box + SD projection levels for the latest
                               completed 2pm–8pm NY window, e.g. /cbdr/USD/JPY
  GET /cbdr/{symbol}?price=..  also returns the bias read at that price

Symbol uses the TwelveData form with a slash, e.g. USD/JPY, XAU/USD — pass it
as the path, e.g. /cbdr/USD/JPY.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from cbdr.engine import build_cbdr, read_bias, nearest_levels
from services.cbdr_service import fetch_cbdr_window
from utils.price_fetcher import get_forex_price

router = APIRouter(prefix="/cbdr", tags=["cbdr"])


@router.get("/{symbol:path}")
async def cbdr(symbol: str, price: Optional[float] = Query(None)):
    window = await fetch_cbdr_window(symbol)
    if not window:
        raise HTTPException(status_code=502, detail="could not fetch CBDR window data")

    box = build_cbdr(window["high"], window["low"])

    out = {
        "symbol": symbol,
        "window_date_ny": window["date"],
        "window": "14:00-20:00 America/New_York",
        "bars_used": window["bars"],
        "box": box.to_dict(),
    }

    # If no price supplied, try to fetch the current one for the bias read.
    if price is None:
        try:
            price = await get_forex_price(symbol)
        except Exception:
            price = None

    if price is not None:
        out["price"] = price
        out["bias"] = read_bias(price, box)
        out["nearest_levels"] = nearest_levels(price, box, n=3)

    return out
