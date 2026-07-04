"""Gold (XAU/USD) prop-firm endpoints — the computed risk/lot/target sheet.

  GET /gold/plan?balance=5000&tier=6      full computed sheet for a balance
  GET /gold/scaling                        the 5k→1M lot ladder table
  GET /gold/size?entry=4325&stop=4310&risk_usd=20   money-first lot + TP prices
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from gold import risk_engine as gr
from gold.weekly import weekly_bias
from gold.ict import classify_week
from services.ohlc_service import fetch_ohlc

router = APIRouter(prefix="/gold", tags=["gold"])

_LADDER = [5000, 10000, 25000, 50000, 100000, 200000, 500000, 1000000]


@router.get("/plan")
async def plan(balance: float = Query(5000, gt=0),
               tier: str = Query("6"),
               risk_usd: float = Query(20.0, gt=0)):
    return gr.plan(balance, tier, risk_usd)


@router.get("/scaling")
async def scaling():
    return {"instrument": "XAU/USD",
            "ladder": [{"balance": b, **gr.lot_ladder(b),
                        "prop_6pct": gr.prop_pass_math(b, "6")} for b in _LADDER]}


@router.get("/phases")
async def phases(balance: float = Query(5000, gt=0)):
    """6% → 12% → 18% (Payout) trade-count ladder (the handwritten phase plan)."""
    return {"instrument": "XAU/USD", "balance": balance, **gr.phase_plan(balance)}


@router.get("/profile")
async def profile():
    """Active ICT Weekly Profile for XAU/USD (1 of 12) — full trend justification."""
    daily = await fetch_ohlc("XAU/USD", "1day", 25)
    if len(daily) < 3:
        raise HTTPException(status_code=502, detail="could not fetch XAU/USD daily bars")
    read = classify_week(daily)
    return {"instrument": "XAU/USD", **(read or {})}


@router.get("/bias")
async def bias():
    """Monday-sweep weekly bias for XAU/USD — the trend justification for signals."""
    daily = await fetch_ohlc("XAU/USD", "1day", 15)
    if len(daily) < 2:
        raise HTTPException(status_code=502, detail="could not fetch XAU/USD daily bars")
    read = weekly_bias(daily)
    return {"instrument": "XAU/USD", **(read or {})}


@router.get("/size")
async def size(entry: float = Query(..., gt=0),
               stop: float = Query(..., gt=0),
               side: str = Query("long", pattern="^(long|short|buy|sell)$"),
               risk_usd: float = Query(20.0, gt=0)):
    lot = gr.size_for_risk(entry, stop, risk_usd)
    tps = gr.targets(entry, stop, side)
    return {
        "instrument": "XAU/USD", "side": side, "entry": entry, "stop": stop,
        "risk_usd": risk_usd, "lot": lot,
        "stop_distance": round(abs(entry - stop), 2),
        "targets": tps,
        "breakeven_trigger": gr.breakeven_price(entry, tps[0]["price"]) if tps else None,
    }
