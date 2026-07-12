"""Fibonacci structure endpoints — invalidation-stop trade plans for any symbol.

The fix for the tight-stop problem, as an API. Detects the recent swing leg, then
frames a trade whose STOP sits at structure invalidation (beyond the swing extreme)
and whose TARGETS are fib extensions of that leg.

  GET  /structure/{symbol}?interval=4h&side=short&buffer=0.1
        auto-detect the swing and return entry / invalidation SL / ext TPs / R:R
  GET  /structure/levels?low=&high=            raw retracement + extension ladders
  POST /structure/plan?low=&high=&side=&buffer=  plan a trade off an explicit leg

Trend-based (3-point A→B→C) extension — anticipate continuation target zones:
  GET  /structure/trend/{symbol}?interval=1h   auto-detect A→B→C + project the ladder
  POST /structure/trend/plan?a=&b=&c=&side=     project off explicit A/B/C points
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from indicators import fibonacci as fib
from services import structure_service as ss

router = APIRouter(prefix="/structure", tags=["structure"])


@router.get("/levels")
async def levels(low: float = Query(...), high: float = Query(...)):
    """Retracement + extension price ladders for a leg (no trade framing)."""
    return {"low": low, "high": high, **fib.levels(low, high),
            "ote_long": fib.ote_zone(low, high, "long"),
            "ote_short": fib.ote_zone(low, high, "short")}


@router.post("/plan")
async def plan_leg(low: float = Query(...), high: float = Query(...),
                   side: str = Query("long"), entry: Optional[float] = Query(None),
                   buffer: float = Query(0.0), min_rr: float = Query(3.0)):
    """Plan a trade off an EXPLICIT leg you already read off the chart."""
    p = fib.plan_trade(low, high, side, entry=entry, buffer=buffer, min_rr=min_rr)
    p["levels"] = fib.levels(low, high)
    return p


@router.post("/trend/plan")
async def trend_plan_abc(a: float = Query(...), b: float = Query(...),
                        c: float = Query(...), side: Optional[str] = Query(None),
                        entry: Optional[float] = Query(None),
                        buffer: float = Query(0.0), min_rr: float = Query(2.0)):
    """Project a trend-based extension off EXPLICIT A/B/C points read off the chart.

    A→B is the impulse, C the retracement anchor. Returns the continuation plan +
    the full projection ladder and reaction/target zones.
    """
    p = fib.trend_plan(a, b, c, side=side, entry=entry, buffer=buffer, min_rr=min_rr)
    p["ladder"] = fib.trend_levels(a, b, c)
    return p


@router.get("/trend/{symbol}")
async def trend_structure(symbol: str, interval: str = Query("1h"),
                          lookback: int = Query(80), side: Optional[str] = Query(None),
                          entry: Optional[float] = Query(None),
                          buffer: float = Query(0.0), min_rr: float = Query(2.0)):
    """Auto-detect ``symbol``'s A→B→C swing and project the trend-based extension.

    Anticipates the continuation target zones for a buy (up-impulse) or sell
    (down-impulse). ``side`` overrides the auto-detected direction.
    """
    return await ss.trend(symbol, interval=interval, lookback=lookback, side=side,
                          entry=entry, buffer=buffer, min_rr=min_rr)


@router.get("/{symbol}")
async def structure(symbol: str, interval: str = Query("4h"),
                    lookback: int = Query(60), side: Optional[str] = Query(None),
                    entry: Optional[float] = Query(None),
                    buffer: float = Query(0.0), min_rr: float = Query(3.0)):
    """Auto-detect the recent swing for ``symbol`` and return the fib trade plan.

    ``side`` overrides the auto-detected direction; ``buffer`` pads the stop off
    the wick (e.g. 0.1 for USD/JPY). URL-encode a slash pair (XAU%2FUSD) or pass
    the joined form (XAUUSD / USDJPY).
    """
    return await ss.plan(symbol, interval=interval, lookback=lookback, side=side,
                         entry=entry, buffer=buffer, min_rr=min_rr)
