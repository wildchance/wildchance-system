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
