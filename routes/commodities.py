"""Commodities endpoints — gold, oil (WTI/Brent), natgas, copper, silver.

  GET /commodities                 list available keys + units
  GET /commodities/{key}           latest value (FRED, EIA fallback for oil)
  GET /commodities/{key}/series?n= last n daily points

Free sources: FRED_KEY + EIA_KEY. Also a Finnhub price probe at /commodities/finnhub/{symbol}.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from services import commodities_service as com
from services import finnhub_service as fh

router = APIRouter(prefix="/commodities", tags=["commodities"])


@router.get("")
async def list_commodities():
    return {"available": {k: v.get("unit") for k, v in com.COMMODITIES.items()},
            "fred_configured": bool(com.FRED_KEY),
            "eia_configured": bool(com.EIA_KEY)}


@router.get("/finnhub/{symbol:path}")
async def finnhub_price(symbol: str):
    """Probe a Finnhub quote (stocks/crypto), e.g. AAPL or BINANCE:BTCUSDT."""
    p = await fh.price(symbol)
    return {"symbol": symbol, "price": p, "configured": fh.configured()}


@router.get("/{key}")
async def commodity_latest(key: str):
    key = key.lower()
    if key not in com.COMMODITIES:
        raise HTTPException(status_code=404, detail=f"unknown commodity '{key}'")
    out = await com.latest(key)
    if out is None:
        raise HTTPException(status_code=502,
                            detail=f"no data for {key} (set FRED_KEY / EIA_KEY)")
    return out


@router.get("/{key}/series")
async def commodity_series(key: str, n: int = Query(30, ge=1, le=500)):
    key = key.lower()
    if key not in com.COMMODITIES:
        raise HTTPException(status_code=404, detail=f"unknown commodity '{key}'")
    rows = await com.series(key, n)
    if rows is None:
        raise HTTPException(status_code=502, detail=f"no series for {key}")
    return {"key": key, "unit": com.COMMODITIES[key].get("unit"),
            "count": len(rows), "series": rows}
