"""Lunar-cycle overlay endpoints.

  GET /lunar/state                    phase, bias, observation window, next pivots
  GET /lunar/check?direction=BUY      is a setup aligned with the half-cycle?
  GET /lunar/backtest?symbol=XAU/USD  evidence check on daily history (do this
                                      BEFORE setting LUNAR_GATE_ENABLED=true)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from lunar import engine as lu

router = APIRouter(prefix="/lunar", tags=["lunar"])


@router.get("/state")
async def state(observation_hours: float = Query(24, ge=0, le=72),
                new_moon_hold_days: int = Query(14, ge=1, le=20),
                full_moon_hold_days: int = Query(12, ge=1, le=20)):
    return lu.lunar_state(None, observation_hours, new_moon_hold_days, full_moon_hold_days)


@router.get("/check")
async def check(direction: str = Query(..., description="BUY or SELL"),
                observation_hours: float = Query(24, ge=0, le=72),
                pause_during_observation: bool = Query(False)):
    return lu.evaluate_alignment(direction, None, observation_hours, pause_during_observation)


@router.get("/backtest")
async def backtest(symbol: str = Query("XAU/USD"), hold_days: int = Query(14, ge=1, le=30),
                   bars: int = Query(2500, ge=200, le=5000)):
    from services.ohlc_service import fetch_ohlc
    data = await fetch_ohlc(symbol, "1day", bars)
    if not data:
        raise HTTPException(502, f"no daily data for {symbol} (check TWELVEDATA_API_KEY)")
    return {"symbol": symbol, **lu.phase_returns(data, hold_days)}
