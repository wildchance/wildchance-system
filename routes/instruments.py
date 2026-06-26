"""Instruments API — the expanded universe + one-call multi-asset analysis.

  GET /instruments                      the whole catalog (metadata)
  GET /instruments?asset_class=index    filter by class
  GET /instruments/{symbol}             full analysis: price + CBDR + PD arrays
                                        + their confluence, e.g. /instruments/BTC/USD

Works for any catalog instrument (FX, metals, crypto, indices) — it routes data
calls through each instrument's TwelveData symbol and degrades gracefully when a
symbol isn't covered by your data plan.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from instruments.catalog import catalog as catalog_list, by_class, get
from services.cbdr_service import fetch_cbdr_window
from cbdr.engine import build_cbdr, read_bias
from services.pdarrays_service import fetch_candles
from pdarrays.engine import detect_pd_arrays, arrays_near
from utils.price_fetcher import get_forex_price

router = APIRouter(prefix="/instruments", tags=["instruments"])


@router.get("")
async def list_instruments(asset_class: Optional[str] = Query(None)):
    if asset_class:
        return {"asset_class": asset_class,
                "instruments": [i.to_dict() for i in by_class(asset_class)]}
    return {"count": len(catalog_list()), "instruments": catalog_list()}


@router.get("/{symbol:path}")
async def analysis(symbol: str, interval: str = Query("15min")):
    ins = get(symbol)
    if ins is None:
        raise HTTPException(status_code=404, detail=f"unknown instrument '{symbol}'")
    td = ins.td_symbol

    # Candles first — also our price fallback for crypto/indices.
    candles = await fetch_candles(td, interval)

    price = None
    try:
        price = await get_forex_price(td)
    except Exception:
        price = None
    if price is None and candles:
        price = candles[-1]["close"]

    # CBDR box for this instrument's 2–8pm NY window.
    box = None
    cbdr_out = None
    window = await fetch_cbdr_window(td)
    if window:
        box = build_cbdr(window["high"], window["low"])
        cbdr_out = {"window_date_ny": window["date"], "box": box.to_dict()}
        if price is not None:
            cbdr_out["bias"] = read_bias(price, box)

    # PD arrays + confluence with the CBDR levels.
    pd_out = None
    if candles:
        arrays = detect_pd_arrays(candles)
        confluence = []
        if box is not None:
            levels = {"high": box.high, "low": box.low, "mid": box.mid, **box.levels}
            t = box.range * 0.10
            for name, lv in levels.items():
                for a in arrays_near(arrays, lv, t):
                    confluence.append({"level": name, "level_price": round(lv, 6),
                                       "array": a.to_dict()})
        pd_out = {
            "interval": interval,
            "count": len(arrays),
            "recent": [a.to_dict() for a in arrays[:10]],
            "cbdr_confluence": confluence[:20],
        }

    return {
        "symbol": ins.symbol,
        "meta": ins.to_dict(),
        "price": price,
        "cbdr": cbdr_out,
        "pd_arrays": pd_out,
    }
