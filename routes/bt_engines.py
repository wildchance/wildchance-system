"""Backtest-engine routes — SELF-CONTAINED validation endpoints for the two
survivors of the AMD cull (Bumblebee session-continuity, Warthog HTF OTE catapult).

  POST /btx/bumblebee   replay sweep→continuity (bias-gated) — hit-rate / expectancy-R
                        / TRAIN-TEST / by-side / by-session
  POST /btx/warthog     replay the HTF OTE limit-fill — hit-rate / expectancy-R /
                        TRAIN-TEST / by-side

Fetch lives inside this file on purpose — one file to deploy, no edits to the
contested gold route. The pure harnesses (backtest.bumblebee_backtest,
backtest.warthog_backtest) do the maths against the real engines.

Mount in main.py with two lines:
    from routes.bt_engines import router as bt_engines_router
    app.include_router(bt_engines_router)
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from services.ohlc_service import fetch_hourly_raw, fetch_ohlc_raw

router = APIRouter(prefix="/btx", tags=["backtest-engines"])


@router.post("/bumblebee")
async def bumblebee_backtest(
        symbol: str = Query("XAU/USD"),
        outputsize: int = Query(5000, ge=100, le=5000,
            description="hourly bars to replay (5000 ≈ this year)"),
        sessions: str = Query(None,
            description="comma list of asian,london,newyork,london_open,ny_open (blank = all)"),
        target_pips: float = Query(250.0, ge=1,
            description="cheetah continuation target distance (pips)"),
        pip: float = Query(0.1, gt=0),
        buffer: float = Query(0.0, ge=0),
        max_hold: int = Query(10, ge=1, le=48),
        hold_to_session: bool = Query(False,
            description="hold to the session-close hour instead of max_hold bars"),
        require_bias: bool = Query(False,
            description="gate continuity on the trailing-SMA daily bias (the engine's HTF confluence)"),
        bias_window: int = Query(50, ge=5, le=400)):
    """Replay Bumblebee sweep→continuity; report hit-rate, expectancy-R, splits."""
    from backtest.bumblebee_backtest import backtest as _bt
    hbars = await fetch_hourly_raw(symbol, "UTC", outputsize)
    if len(hbars) < 50:
        raise HTTPException(status_code=502,
            detail=f"could not fetch {symbol} hourly history ({len(hbars)} bars) — rate limit or symbol")
    sess = [s.strip() for s in sessions.split(",")] if sessions else None
    res = _bt(hbars, sessions=sess, buffer=buffer, target_pips=target_pips, pip=pip,
              max_hold=max_hold, hold_to_session=hold_to_session,
              require_bias=require_bias, bias_window=bias_window)
    res.pop("trades", None)
    res["symbol"] = symbol
    return res


@router.post("/warthog")
async def warthog_backtest(
        symbol: str = Query("XAU/USD"),
        interval: str = Query("1day", description="HTF: 1day | 4h | 1h"),
        outputsize: int = Query(300, ge=30, le=5000,
            description="HTF bars to replay (300 daily ≈ this year+)"),
        window: int = Query(60, ge=8, le=400,
            description="HTF lookback fed to the engine each step"),
        rr: float = Query(2.0, gt=0, description="target in R multiples (2 = first target)"),
        max_wait: int = Query(20, ge=1, le=200,
            description="bars the OTE limit stays live waiting to fill"),
        max_hold: int = Query(60, ge=1, le=400),
        require_catapult: bool = Query(False,
            description="only take setups retraced >60% into the OTE (engine 'catapult')")):
    """Replay Warthog as a pending-limit strategy; report hit-rate, expectancy-R, splits."""
    from backtest.warthog_backtest import backtest as _bt
    raw = await fetch_ohlc_raw(symbol, interval, outputsize)
    if len(raw) < window + 5:
        raise HTTPException(status_code=502,
            detail=f"could not fetch enough {symbol} {interval} history ({len(raw)} bars) — rate limit or symbol")
    for b in raw:                                  # map 'time' → 'date' for the split
        if "date" not in b and b.get("time"):
            b["date"] = str(b["time"])[:10]
    res = _bt(raw, window=window, rr=rr, max_wait=max_wait, max_hold=max_hold,
              require_catapult=require_catapult)
    res.pop("trades", None)
    res["symbol"] = symbol
    res["interval"] = interval
    return res
