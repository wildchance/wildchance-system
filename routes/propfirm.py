"""Prop-firm risk gate + session grid endpoints (TENDAJI rules).

  GET  /propfirm/limits?balance=2500          all USD targets/caps + max lot
  GET  /propfirm/sessions?balance=2500         which sessions are open now + budget
  GET  /propfirm/check?balance=&risk=&day_pnl=&open_risk=   gate a proposed trade
  GET  /propfirm/lockin?balance=&closed=&day_pnl=           graduated lock-in scale

The session grid POST takes the open-trades list as a JSON body for richer state.
"""

from __future__ import annotations

import datetime as _dt
from typing import List, Optional

from fastapi import APIRouter, Body, Query

from propfirm import engine as pf

router = APIRouter(prefix="/propfirm", tags=["propfirm"])


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


@router.get("/limits")
async def limits(balance: float = Query(2500.0),
                 tier: str = Query("6", description="target tier: 6 | 8 | 12")):
    return {
        "balance": balance,
        "max_lot": pf.max_lot(balance),
        "limits_usd": pf.risk_limits(balance, tier),
    }


@router.get("/tiers")
async def tiers(balance: float = Query(2500.0)):
    """Side-by-side view of the 6 / 8 / 12% target tiers for this balance."""
    return {
        "balance": balance,
        "max_lot": pf.max_lot(balance),
        "tiers": {t: pf.risk_limits(balance, t) for t in pf.TARGET_TIERS},
        "default": pf.DEFAULT_TIER,
    }


@router.get("/sessions")
async def sessions(balance: float = Query(2500.0), tier: str = Query("6")):
    now = _now()
    return pf.grid_state(now, balance, open_trades=[], tier=tier)


@router.get("/check")
async def check(balance: float = Query(2500.0),
                risk: float = Query(..., description="proposed trade risk in USD"),
                day_pnl: float = Query(0.0),
                open_risk: float = Query(0.0),
                tier: str = Query("6")):
    return pf.evaluate_trade(balance, risk, day_pnl_usd=day_pnl,
                             open_risk_usd=open_risk, tier=tier)


@router.get("/lockin")
async def lockin(balance: float = Query(2500.0),
                 closed: int = Query(0),
                 day_pnl: float = Query(0.0),
                 tier: str = Query("6")):
    return pf.lockin(closed, day_pnl, balance, tier=tier)


@router.post("/grid")
async def grid(balance: float = Query(2500.0),
               tier: str = Query("6"),
               open_trades: List[dict] = Body(default_factory=list)):
    """Full session grid given the current open trades.

    Body: [{"id": "...", "session": "trade1", "risk_usd": 1.5, "symbol": "EUR/USD"}]
    """
    return pf.grid_state(_now(), balance, open_trades, tier=tier)


# --- challenge mode (FundingPips / FTMO evaluations) -------------------------------
from propfirm import challenge as ch  # noqa: E402


@router.get("/challenge/presets")
async def challenge_presets():
    """Firm rule presets. Rules change — verify on the firm's site."""
    return {"default": ch.DEFAULT_PRESET, "presets": ch.PRESETS,
            "guard_buffers": {"daily": ch.DAILY_BUFFER, "max": ch.MAX_BUFFER}}


@router.get("/challenge/plan")
async def challenge_plan(balance: float = Query(10000.0),
                         preset: str = Query(ch.DEFAULT_PRESET),
                         risk_pct: float = Query(0.5, gt=0, le=5),
                         avg_r: float = Query(1.8, gt=0),
                         win_rate: float = Query(0.45, ge=0, le=1),
                         max_concurrent: int = Query(3, ge=1, le=20)):
    return ch.challenge_plan(balance, preset, risk_pct, avg_r, win_rate, max_concurrent)


@router.get("/challenge/guard")
async def challenge_guard(equity: float = Query(...),
                          initial_balance: float = Query(...),
                          day_start_balance: float = Query(...),
                          day_start_equity: Optional[float] = Query(None),
                          phase: int = Query(1, ge=0, le=2, description="0 = funded"),
                          preset: str = Query(ch.DEFAULT_PRESET),
                          peak_equity: Optional[float] = Query(None, description="trailing presets"),
                          trading_days: Optional[int] = Query(None, ge=0,
                              description="days traded so far (profitable days for Zero)")):
    """The same kill-switch the VPS bridge and the EA run, for a dashboard/what-if."""
    return ch.equity_guard(equity, initial_balance, day_start_balance,
                           day_start_equity if day_start_equity is not None else day_start_balance,
                           phase, preset, peak_equity=peak_equity,
                           trading_days=trading_days)


@router.get("/lots")
async def lots(balance: float = Query(2500.0, gt=0),
               symbol: str = Query("EURUSD"),
               sl_pips: float = Query(10.0, gt=0),
               target_usd: Optional[float] = Query(None, gt=0,
                   description="default ≈ 0.52 % of balance ($13 on 2.5k)"),
               price: Optional[float] = Query(None, description="needed for USDxxx pairs"),
               pip_value: Optional[float] = Query(None, description="USD per pip per lot (crosses)"),
               max_risk_pct: float = Query(0.5, gt=0, le=5)):
    """Target-based FX lot ladder (owner's sheet): lot per TP rung 1.3R…5.2R."""
    from propfirm.target_lots import ladder
    return ladder(balance, symbol, sl_pips, target_usd, price, pip_value, max_risk_pct)
