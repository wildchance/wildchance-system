"""PD Arrays API — FVGs & order blocks from intraday candles, with optional
CBDR-confluence (which arrays sit on a CBDR standard-deviation level).

  GET /pdarrays/{symbol}                      latest PD arrays (default 15min)
  GET /pdarrays/{symbol}?interval=1h
  GET /pdarrays/{symbol}?near=161.8&tol=0.1   only arrays at a given price level
  GET /pdarrays/{symbol}?cbdr=true            arrays that align with CBDR SD levels
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from pdarrays.engine import detect_pd_arrays, arrays_near
from services.pdarrays_service import fetch_candles
from cbdr.engine import build_cbdr
from services.cbdr_service import fetch_cbdr_window

router = APIRouter(prefix="/pdarrays", tags=["pdarrays"])


@router.get("/{symbol:path}")
async def pdarrays(
    symbol: str,
    interval: str = Query("15min"),
    near: Optional[float] = Query(None, description="filter to arrays at this price"),
    tol: Optional[float] = Query(None, description="match tolerance in price units"),
    cbdr: bool = Query(False, description="only arrays aligned with CBDR SD levels"),
    limit: int = Query(25, ge=1, le=200),
):
    candles = await fetch_candles(symbol, interval)
    if not candles:
        raise HTTPException(status_code=502, detail="could not fetch candles")

    arrays = detect_pd_arrays(candles)        # already most-recent-first
    result_extra = {}

    # CBDR confluence: keep only arrays that overlap a CBDR projection level.
    if cbdr:
        window = await fetch_cbdr_window(symbol)
        if window:
            box = build_cbdr(window["high"], window["low"])
            levels = {"high": box.high, "low": box.low, "mid": box.mid, **box.levels}
            t = tol if tol is not None else box.range * 0.10   # 10% of the box range
            keep, hits = [], []
            for name, lv in levels.items():
                for a in arrays_near(arrays, lv, t):
                    if a not in keep:
                        keep.append(a)
                    hits.append({"level": name, "level_price": round(lv, 6),
                                 "array": a.to_dict()})
            arrays = keep
            result_extra = {"cbdr_levels": {k: round(v, 6) for k, v in levels.items()},
                            "confluence": hits[:limit]}
    elif near is not None:
        t = tol if tol is not None else near * 0.001          # ~10 bps default
        arrays = arrays_near(arrays, near, t)

    return {
        "symbol": symbol,
        "interval": interval,
        "count": len(arrays),
        "arrays": [a.to_dict() for a in arrays[:limit]],
        **result_extra,
    }
