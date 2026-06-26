"""DXY correlation / divergence + COT-driven intraweek setups.

  GET /dxy                       current DXY level, recent change, direction
  GET /correlation/{symbol}      instrument vs DXY: rolling correlation +
                                 directional confirmation, e.g. /correlation/EUR/USD
  GET /setups/intraweek          rank the latest confluence signals by whether the
                                 dollar confirms them, with COT-extreme flags
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from instruments.catalog import get as get_instrument
from correlation.engine import (
    dxy_alignment, dxy_direction, signal_vs_dxy, cot_extreme,
)
from services.dxy_service import fetch_dxy, instrument_closes
from services.wildchance_service import get_latest_feed

router = APIRouter(tags=["dxy"])

# wildchance feed pair -> CFTC base code used in feed["cot"]
_COT_CODE = {
    "EUR/USD": "EUR", "GBP/USD": "GBP", "USD/JPY": "JPY",
    "USD/CAD": "CAD", "AUD/USD": "AUD", "USD/CHF": "CHF", "XAU/USD": "XAU",
}


@router.get("/dxy")
async def dxy(interval: str = Query("1h")):
    closes, source = await fetch_dxy(interval)
    if not closes:
        raise HTTPException(status_code=502, detail="DXY data unavailable")
    return {
        "source": source,
        "interval": interval,
        "last": round(closes[-1], 4),
        "change_pct": round((closes[-1] - closes[0]) / closes[0] * 100, 3) if closes[0] else None,
        "direction": dxy_direction(closes),
        "bars": len(closes),
    }


@router.get("/correlation/{symbol:path}")
async def correlation(symbol: str, interval: str = Query("1h")):
    ins = get_instrument(symbol)
    if ins is None:
        raise HTTPException(status_code=404, detail=f"unknown instrument '{symbol}'")

    dxy_closes, source = await fetch_dxy(interval)
    instr = await instrument_closes(ins.td_symbol, interval)
    if not dxy_closes or not instr:
        raise HTTPException(status_code=502, detail="price data unavailable")

    align = dxy_alignment(instr, dxy_closes, ins.dxy_sign)
    return {
        "symbol": ins.symbol,
        "dxy_source": source,
        "dxy_sign": ins.dxy_sign,
        "dxy_direction": dxy_direction(dxy_closes),
        "alignment": align,
    }


@router.get("/setups/intraweek")
async def intraweek_setups(min_confidence: int = Query(70),
                           interval: str = Query("1h")):
    """Confluence signals ranked by dollar confirmation, with COT-extreme flags."""
    dxy_closes, source = await fetch_dxy(interval)
    dxy_dir = dxy_direction(dxy_closes) if dxy_closes else "unknown"

    feed = await get_latest_feed()
    signals = (feed or {}).get("signals", [])
    cot = (feed or {}).get("cot", {})

    setups = []
    for s in signals:
        verdict = s.get("verdict")
        if verdict not in ("LONG", "SHORT") or (s.get("confidence") or 0) < min_confidence:
            continue
        pair = s.get("pair")
        ins = get_instrument(pair)
        dxy_sign = ins.dxy_sign if ins else 0
        confirm = signal_vs_dxy(verdict, dxy_sign, dxy_dir)

        # COT extreme from the stored net series for this pair.
        code = _COT_CODE.get(pair)
        nets = [row.get("lev") for row in cot.get(code, [])] if code else []
        is_ext, side, rank = cot_extreme([n for n in nets if n is not None]) if nets else (False, "none", None)

        setups.append({
            "pair": pair,
            "verdict": verdict,
            "confidence": s.get("confidence"),
            "dxy_confirmation": confirm,        # confirmed | divergent | neutral
            "cot_extreme": is_ext,
            "cot_side": side,
            "cot_rank": rank,
        })

    # Confirmed + COT-extreme first, then by confidence.
    order = {"confirmed": 0, "neutral": 1, "divergent": 2}
    setups.sort(key=lambda x: (order.get(x["dxy_confirmation"], 1),
                               0 if x["cot_extreme"] else 1,
                               -(x["confidence"] or 0)))
    return {
        "dxy_source": source,
        "dxy_direction": dxy_dir,
        "min_confidence": min_confidence,
        "count": len(setups),
        "setups": setups,
    }
