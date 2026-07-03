"""Backtest endpoint — measure the confluence stack's lift on real history.

  GET /backtest/{symbol}?bars=400&hold=3&atr_mult=1.5
        → baseline vs ATR-floored stop vs ATR+structure filter, with the
          win-rate / expectancy lift over the baseline fade.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from services import backtest_service

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.get("/{symbol:path}")
async def backtest(symbol: str,
                   bars: int = Query(400, ge=80, le=5000),
                   lookback: int = Query(20, ge=5, le=100),
                   hold: int = Query(3, ge=1, le=20),
                   atr_mult: float = Query(1.5, gt=0)):
    res = await backtest_service.run(symbol, bars=bars, lookback=lookback,
                                     hold=hold, atr_mult=atr_mult)
    if res is None:
        raise HTTPException(status_code=502, detail=f"insufficient OHLC for {symbol}")
    return {"symbol": symbol, **res}
