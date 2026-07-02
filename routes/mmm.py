"""Market Maker Methods read for any instrument.

  GET /mmm/{symbol}   weekly levels + PFH/PFL + ADR + Taylor day + peak formation,
                      with ATR volatility levels, a spike flag, and a news flag.

Pulls daily + weekly OHLC from the feed and runs the pure mmm/ + indicators/
engines. Read-only; safe to call anytime.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from services.ohlc_service import fetch_ohlc, to_ohlc, to_hlc
from services.news_guard import news_flag
from mmm.engine import weekly_setup, confluence as mmm_confluence
from indicators.atr import atr, atr_stop, atr_targets, is_spike, spike_ratio

router = APIRouter(prefix="/mmm", tags=["mmm"])


@router.get("/{symbol:path}")
async def mmm(symbol: str,
              side: str = Query("", description="long|short → include confluence"),
              atr_period: int = Query(14, ge=2, le=100),
              atr_mult: float = Query(1.5, gt=0),
              adr_n: int = Query(5, ge=1, le=30)):
    daily = await fetch_ohlc(symbol, "1day", 60)
    weekly = await fetch_ohlc(symbol, "1week", 4)
    if len(daily) < 3 or len(weekly) < 2:
        raise HTTPException(status_code=502,
                            detail=f"insufficient OHLC for {symbol}")

    prev_week = to_ohlc([weekly[-2]])[0]          # last COMPLETED week
    dbars = to_ohlc(daily)
    last_date = daily[-1][0]

    setup = weekly_setup(symbol, last_date.weekday(), prev_week, dbars, adr_n)

    # Volatility overlay (ATR) — entry/stop/target scaled to this market.
    a = atr(to_hlc(daily), atr_period)
    today_hlc = to_hlc([daily[-1]])[0]
    close = dbars[-1][3]
    setup["atr"] = round(a, 6) if a is not None else None
    setup["spike"] = {
        "is_spike": is_spike(today_hlc, a),
        "range_vs_atr": round(spike_ratio(today_hlc[0] - today_hlc[1], a), 2)
                        if a else None,
        "note": "range > 2*ATR — news/expansion bar; avoid chasing the fade",
    }
    if a:
        setup["atr_levels"] = {
            "entry_ref": close,
            "long": {"stop": round(atr_stop(close, a, "BUY", atr_mult), 6),
                     "targets": atr_targets(close, a, "BUY")},
            "short": {"stop": round(atr_stop(close, a, "SELL", atr_mult), 6),
                      "targets": atr_targets(close, a, "SELL")},
            "mult": atr_mult,
        }
    setup["news_warning"] = await news_flag(last_date, symbol)
    if side.lower() in ("long", "short", "buy", "sell"):
        setup["confluence"] = mmm_confluence(setup, side)
    setup["as_of"] = str(last_date)
    return setup
