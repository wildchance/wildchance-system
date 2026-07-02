"""Trade-setup endpoints — Entry/SL/TP1-3 zones off any CBDR window.

  GET /setups/{symbol}?window=cbdr&side=long&mode=continuation
        Build a full plan (entry, stop, TP1/TP2/TP3 with R) from the latest box
        of `window` (cbdr | prelondon | trade1..6). entry defaults to live price.
        Add &balance=2500&tier=6 to also size the position (lot + USD risk).

window picks the session: 'cbdr' = Asian/NY CBDR (2pm-8pm NY), 'prelondon' =
18:00-02:45 UTC London box, 'trade1'..'trade6' = the NY session grid.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from cbdr.engine import build_cbdr
from services.cbdr_service import fetch_cbdr_window
from setups.engine import build_setup
from utils.price_fetcher import get_forex_price
from propfirm.engine import max_lot, risk_limits
from services import flow_service
from services import mmm_service
from services.ohlc_service import fetch_ohlc, to_hlc
from services.news_guard import news_flag
from indicators.atr import atr as compute_atr, is_spike

router = APIRouter(prefix="/setups", tags=["setups"])


@router.get("/{symbol:path}")
async def setup(symbol: str,
                window: str = Query("cbdr"),
                side: str = Query("long", pattern="^(long|short)$"),
                mode: str = Query("continuation", pattern="^(continuation|reversal)$"),
                entry: Optional[float] = Query(None),
                balance: Optional[float] = Query(None),
                tier: str = Query("6"),
                atr_period: int = Query(14, ge=2, le=100),
                atr_mult: float = Query(1.5, gt=0)):
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

    # Volatility overlay: ATR-floored stop + spike filter from daily OHLC.
    atr_val = None
    spike = None
    bars = await fetch_ohlc(symbol, "1day", max(atr_period + 5, 30))
    if len(bars) >= atr_period + 1:
        hlc = to_hlc(bars)
        atr_val = compute_atr(hlc, atr_period)
        spike = is_spike(hlc[-1], atr_val)

    plan = build_setup(box, side, price, mode,
                       atr=atr_val, atr_mult=atr_mult, spike=spike)
    plan.update({"symbol": symbol, "window": win["window"],
                 "window_label": win["label"], "session": win["session"]})

    # News guard for the pair's currencies (flag-only).
    plan["news_warning"] = await news_flag(_dt.date.today(), symbol)

    # Market Maker Methods confluence (weekly cycle / peak formation) for the side.
    mmm_read = await mmm_service.read_with_daily(symbol, bars)
    plan["mmm_confluence"] = mmm_service.confluence(mmm_read, side)

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
