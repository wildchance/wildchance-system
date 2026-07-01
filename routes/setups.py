"""Trade-setup endpoints — Entry/SL/TP1-3 zones off any CBDR window.

  GET /setups/{symbol}?window=cbdr&side=long&mode=continuation
        Build a full plan (entry, stop, TP1/TP2/TP3 with R) from the latest box
        of `window` (cbdr | prelondon | trade1..6). entry defaults to live price.
        Add &balance=2500&tier=6 to also size the position (lot + USD risk).

window picks the session: 'cbdr' = Asian/NY CBDR (2pm-8pm NY), 'prelondon' =
18:00-02:45 UTC London box, 'trade1'..'trade6' = the NY session grid.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from cbdr.engine import build_cbdr
from services.cbdr_service import fetch_cbdr_window
from setups.engine import build_setup
from utils.price_fetcher import get_forex_price
from propfirm.engine import max_lot, risk_limits
from services import flow_service

router = APIRouter(prefix="/setups", tags=["setups"])


@router.get("/{symbol:path}")
async def setup(symbol: str,
                window: str = Query("cbdr"),
                side: str = Query("long", pattern="^(long|short)$"),
                mode: str = Query("continuation", pattern="^(continuation|reversal)$"),
                entry: Optional[float] = Query(None),
                balance: Optional[float] = Query(None),
                tier: str = Query("6")):
    win = await fetch_cbdr_window(symbol, window)
    if not win:
        raise HTTPException(status_code=502,
                            detail=f"could not fetch '{window}' box for {symbol}")
    box = build_cbdr(win["high"], win["low"])

    price = entry
    if price is None:
        try:
            price = await get_forex_price(symbol)
        except Exception:
            price = None
    if price is None:
        price = box.mid        # fall back to box mid if no live price

    plan = build_setup(box, side, price, mode)
    plan.update({"symbol": symbol, "window": win["window"],
                 "window_label": win["label"], "session": win["session"]})

    # Order-flow confluence (dormant/neutral until an L2 book feed populates Redis).
    fp = await flow_service.pressure(symbol)
    plan["flow_confluence"] = flow_service.confluence(fp, side)

    if balance:
        lim = risk_limits(balance, tier)
        plan["sizing"] = {
            "balance": balance,
            "tier": lim["tier"],
            "max_lot": max_lot(balance),
            "daily_risk_budget_usd": lim["daily_loss_cap"],
        }
    return plan
