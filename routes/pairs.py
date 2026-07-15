"""Gold–silver ratio mean-reversion — SELF-CONTAINED route (relative-value pairs).

  POST /pairs/signal      current LONG/SHORT/FLAT ratio call (+ Telegram)
  POST /pairs/backtest    replay history: hit-rate / avg% / TRAIN-TEST split

Fetch+align+signal live INSIDE this file on purpose — one file to deploy, so a
wrong-file paste can't split the logic across two modules again. Pure engines
(indicators.pairs, backtest.pairs_backtest) do the maths; this just wires data.
"""

from __future__ import annotations

from typing import List, Tuple

from fastapi import APIRouter, HTTPException, Query

from services.ohlc_service import fetch_ohlc
from services.gold_scan import _tg
from indicators.pairs import pair_signal

router = APIRouter(prefix="/pairs", tags=["pairs"])


def _day(d) -> str:
    return d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]


async def _aligned(outputsize: int) -> Tuple[List[str], List[float], List[float]]:
    """Fetch XAU + XAG daily, intersect dates → (dates, gold, silver) sorted."""
    g = await fetch_ohlc("XAU/USD", "1day", outputsize)
    s = await fetch_ohlc("XAG/USD", "1day", outputsize)
    if not g or not s:
        return [], [], []
    gmap = {_day(r[0]): r[4] for r in g}
    smap = {_day(r[0]): r[4] for r in s}
    dates = sorted(set(gmap) & set(smap))
    return dates, [gmap[d] for d in dates], [smap[d] for d in dates]


def _format(sig: dict) -> str:
    arrow = "🟢 LONG ratio" if sig["signal"] == "LONG_RATIO" else "🔴 SHORT ratio"
    legs = sig.get("legs", {})
    leg_txt = "  ·  ".join(f"{'🟢 BUY' if v == 'buy' else '🔴 SELL'} {k}" for k, v in legs.items())
    return (f"⚖️ *GOLD–SILVER Pair* {arrow}  (z {sig['z']:+.2f})\n"
            f"_{sig['reason']}_\n"
            f"ratio {sig['ratio']}  ·  mean {sig['mean']}  ·  as of {sig.get('as_of')}\n"
            f"{leg_txt}\n"
            f"_exit at |z| ≤ {sig['exit_z']} (revert) · cut at |z| ≥ {sig['stop_z']}_")


@router.post("/signal")
async def signal(lookback: int = Query(20, ge=5, le=200),
                 entry_z: float = Query(2.0, gt=0),
                 exit_z: float = Query(0.5, ge=0),
                 stop_z: float = Query(3.5, gt=0),
                 trend_guard: bool = Query(True,
                     description="skip entries while the ratio is structurally trending"),
                 trend_window: int = Query(100, ge=10, le=400),
                 max_drift: float = Query(0.15, gt=0,
                     description="max |ratio drift| over the trend window to still trade"),
                 notify: bool = Query(False)):
    """Current gold/silver ratio mean-reversion signal."""
    dates, gold, silver = await _aligned(max(300, lookback + trend_window + 10))
    if len(dates) < lookback + 1:
        return {"signal": "FLAT", "reason": "no aligned XAU/XAG history"}
    sig = pair_signal(gold, silver, lookback=lookback, entry_z=entry_z, exit_z=exit_z,
                      stop_z=stop_z, trend_guard=trend_guard,
                      trend_window=trend_window, max_drift=max_drift)
    sig["as_of"] = dates[-1]
    sig["gold"] = gold[-1]
    sig["silver"] = silver[-1]
    if notify and sig.get("signal") in ("LONG_RATIO", "SHORT_RATIO"):
        sig["sent"] = await _tg(_format(sig))
    return sig


@router.post("/backtest")
async def backtest(outputsize: int = Query(300, ge=60, le=1000,
                       description="daily bars of XAU/XAG history to replay"),
                   lookback: int = Query(20, ge=5, le=200),
                   entry_z: float = Query(2.0, gt=0),
                   exit_z: float = Query(0.5, ge=0),
                   stop_z: float = Query(3.5, gt=0),
                   trend_guard: bool = Query(True),
                   max_drift: float = Query(0.15, gt=0)):
    """Replay the ratio mean-reversion; report hit-rate, avg%, and TRAIN/TEST split."""
    from backtest.pairs_backtest import backtest as _bt
    dates, gold, silver = await _aligned(outputsize)
    if len(dates) < lookback + 5:
        raise HTTPException(status_code=502, detail="could not fetch aligned XAU/XAG history")
    res = _bt(dates, gold, silver, lookback=lookback, entry_z=entry_z, exit_z=exit_z,
              stop_z=stop_z, trend_guard=trend_guard, max_drift=max_drift)
    res.pop("trades", None)
    return res
