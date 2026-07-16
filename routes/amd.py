"""AMD hourly-triad — SELF-CONTAINED route (range→sweep→reaction intraday playbook).

  POST /amd/signal        the live triad read for the most recent trigger (+ Telegram)
  POST /amd/backtest      replay: hit-rate / expectancy-R / TRAIN-TEST + by-trigger

Fetch lives inside this file on purpose — one file to deploy. Pure engines
(indicators.amd_triad, backtest.amd_triad_backtest) do the maths.
"""

from __future__ import annotations

import datetime as _dt
from typing import List

from fastapi import APIRouter, HTTPException, Query

from services.ohlc_service import fetch_hourly_raw
from services.gold_scan import _tg
from indicators.amd_triad import TRIGGERS, triad_signal

router = APIRouter(prefix="/amd", tags=["amd"])


def _bar_at(bars: List[dict], date: str, hour: int):
    for b in bars:
        if b["date"] == date and b["hour"] == hour:
            return b
    return None


@router.post("/signal")
async def signal(symbol: str = Query("XAU/USD"),
                 trigger: int = Query(None, ge=0, le=23,
                     description="trigger hour UTC (14/7/0). blank = the latest completed triad"),
                 buffer: float = Query(0.0, ge=0),
                 notify: bool = Query(False)):
    """Most recent AMD triad read for ``symbol`` (fade-the-sweep LONG/SHORT/NONE)."""
    hbars = await fetch_hourly_raw(symbol, "UTC", 96)
    if len(hbars) < 3:
        return {"signal": "NONE", "reason": f"no {symbol} 1h bars"}
    now = _dt.datetime.now(_dt.timezone.utc)
    triggers = [trigger] if trigger is not None else list(TRIGGERS)

    # pick the most recent date+trigger whose full triad (H, H+1, H+2) has closed
    best = None
    for b in sorted(hbars, key=lambda x: (x["date"], x["hour"]), reverse=True):
        for h in triggers:
            r = _bar_at(hbars, b["date"], h)
            m = _bar_at(hbars, b["date"], h + 1)
            x = _bar_at(hbars, b["date"], h + 2)
            if r and m and x:
                cand = (b["date"], h, r, m, x)
                if best is None or (cand[0], cand[1]) > (best[0], best[1]):
                    best = cand
        if best:
            break
    if not best:
        return {"signal": "NONE", "reason": "no completed triad in the window"}

    date, h, r, m, x = best
    sig = triad_signal(r, m, x, buffer=buffer)
    sig["symbol"] = symbol
    sig["as_of"] = f"{date} {h + 2:02d}:00 UTC (trigger {h:02d})"
    if notify and sig.get("signal") in ("LONG", "SHORT"):
        arrow = "🟢 LONG" if sig["signal"] == "LONG" else "🔴 SHORT"
        sig["sent"] = await _tg(
            f"⏱️ *AMD triad {symbol}* {arrow}  (trigger {h:02d}:00 UTC)\n"
            f"_{sig['reason']}_\n"
            f"entry `{sig['entry']}`  SL `{sig['stop']}`  TP `{sig['target']}`  ·  {sig['as_of']}")
    return sig


@router.post("/backtest")
async def backtest(symbol: str = Query("XAU/USD"),
                   outputsize: int = Query(1000, ge=100, le=5000,
                       description="hourly bars to replay (1000 ≈ 40 trading days)"),
                   buffer: float = Query(0.0, ge=0),
                   max_hold: int = Query(12, ge=1, le=48),
                   require_bias: bool = Query(False,
                       description="only fade WITH trend (long above the SMA, short below) "
                                   "— the daily-bias confluence"),
                   bias_window: int = Query(50, ge=10, le=200),
                   trigger: int = Query(None, ge=0, le=23,
                       description="restrict to one trigger hour; blank = 14/7/0")):
    """Replay the AMD triad; report hit-rate, expectancy-R, TRAIN/TEST + by-trigger + by-side."""
    from backtest.amd_triad_backtest import backtest as _bt
    hbars = await fetch_hourly_raw(symbol, "UTC", outputsize)
    if len(hbars) < 50:
        raise HTTPException(status_code=502,
            detail=f"could not fetch {symbol} hourly history ({len(hbars)} bars) — rate limit or symbol")
    triggers = (trigger,) if trigger is not None else TRIGGERS
    res = _bt(hbars, triggers=triggers, buffer=buffer, max_hold=max_hold,
              require_bias=require_bias, bias_window=bias_window)
    res.pop("trades", None)
    res["symbol"] = symbol
    return res
