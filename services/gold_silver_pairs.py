"""Gold–silver ratio mean-reversion endpoints (relative-value pairs trade).

  POST /pairs/signal      current LONG/SHORT/FLAT ratio call (+ Telegram)
  POST /pairs/backtest    replay history with hit-rate / avg% / TRAIN-TEST split
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from services import gold_silver_pairs as gsp

router = APIRouter(prefix="/pairs", tags=["pairs"])


@router.post("/signal")
async def signal(lookback: int = Query(20, ge=5, le=200),
                 entry_z: float = Query(2.0, gt=0),
                 exit_z: float = Query(0.5, ge=0),
                 stop_z: float = Query(3.5, gt=0),
                 trend_guard: bool = Query(True,
                     description="skip entries while the ratio is structurally trending"),
                 max_drift: float = Query(0.15, gt=0,
                     description="max |ratio drift| over the trend window to still trade"),
                 notify: bool = Query(False)):
    """Current gold/silver ratio mean-reversion signal."""
    return await gsp.scan(lookback=lookback, entry_z=entry_z, exit_z=exit_z,
                          stop_z=stop_z, trend_guard=trend_guard,
                          max_drift=max_drift, notify=notify)


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
    dates, gold, silver = await gsp.history_for_backtest(outputsize)
    if len(dates) < lookback + 5:
        raise HTTPException(status_code=502, detail="could not fetch aligned XAU/XAG history")
    res = _bt(dates, gold, silver, lookback=lookback, entry_z=entry_z, exit_z=exit_z,
              stop_z=stop_z, trend_guard=trend_guard, max_drift=max_drift)
    res.pop("trades", None)
    return res
