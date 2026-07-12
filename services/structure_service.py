"""Structure-trade service — auto-detect the recent swing leg and plan the fib trade.

Wires the pure ``indicators.fibonacci`` engine to live data: fetch OHLC for ANY
symbol, find the most recent impulse leg (reusing the Wade swing detector), decide
the trade side from that leg's direction (or an override), and return the full
plan — OTE entry, structure-invalidation stop, fib-extension targets, R:R.

This is the operational fix for the tight-stop issue: the stop is placed beyond the
swing extreme the market actually respects, not at a fixed pip distance.
"""

from __future__ import annotations

from typing import Optional

from indicators import fibonacci as fib
from gold.entry import break_of_structure, _swing_highs, _swing_lows
from services.ohlc_service import fetch_ohlc


def detect_leg(bars, side: Optional[str] = None) -> Optional[dict]:
    """The most recent impulse leg from OHLC bars → {low, high, side, source}.

    Prefers a Break-of-Structure leg (impulse that took out a prior swing). Falls
    back to the last confirmed swing-low→swing-high (or high→low) pivot pair. If
    ``side`` is given it forces the trade direction; otherwise the leg's own
    direction is used (BMS bullish → long, bearish → short).
    """
    ohlc = [(o, h, l, c) for (_d, o, h, l, c) in bars] if bars and len(bars[0]) == 5 else list(bars)
    if len(ohlc) < 6:
        return None

    bos = break_of_structure(ohlc)
    if bos.get("bms"):
        leg_side = "long" if bos["bms"] == "bullish" else "short"
        return {"low": bos["leg_low"], "high": bos["leg_high"],
                "side": side or leg_side, "source": f"BMS {bos['bms']}",
                "broke_level": bos.get("broke_level")}

    # Fallback: last swing high & low pivots frame the leg.
    sh, sl = _swing_highs(ohlc), _swing_lows(ohlc)
    if not sh or not sl:
        return None
    hi_i, lo_i = sh[-1], sl[-1]
    leg_high = ohlc[hi_i][1]
    leg_low = ohlc[lo_i][2]
    # Direction = which pivot is more recent (impulse points away from the older one).
    leg_side = "long" if lo_i < hi_i else "short"
    return {"low": round(leg_low, 6), "high": round(leg_high, 6),
            "side": side or leg_side, "source": "swing pivots"}


async def plan(symbol: str, interval: str = "4h", lookback: int = 60,
               side: Optional[str] = None, entry: Optional[float] = None,
               buffer: float = 0.0, min_rr: float = 3.0) -> dict:
    """Fetch bars for ``symbol`` and return the fib structure trade plan.

    ``buffer`` defaults to 0; pass a small pad (e.g. 0.1 for USD/JPY, a few dollars
    for gold) to keep the stop off the exact wick. Boot-safe: returns a NO PLAN
    dict (not an error) when data is thin or no leg is found.
    """
    bars = await fetch_ohlc(symbol, interval, lookback)
    if not bars or len(bars) < 6:
        return {"ok": False, "symbol": symbol, "reason": f"no {symbol} {interval} bars"}

    leg = detect_leg(bars, side=side)
    if not leg:
        return {"ok": False, "symbol": symbol, "reason": "no swing leg detected"}

    p = fib.plan_trade(leg["low"], leg["high"], leg["side"], entry=entry,
                       buffer=buffer, min_rr=min_rr)
    p["symbol"] = symbol
    p["interval"] = interval
    p["leg_source"] = leg["source"]
    p["levels"] = fib.levels(leg["low"], leg["high"])
    return p
