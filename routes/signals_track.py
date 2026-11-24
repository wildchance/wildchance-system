"""Signal tracker routes (owner / scheduler — ops token).

  GET /signals/track?symbol=USDJPY&side=SELL&entry=159.3&stop=159.467&tp=159.064,158.746,158.11&pending=true
        add a manual signal (e.g. one posted on another platform) — followed like our own
  GET /signals/check      walk open signals to TP / SL, post flash cards (every 5 min)
  GET /signals/stats      wins / losses / breakevens / win rate / pips, by engine
  GET /signals/open       open signals
"""

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from services import signal_tracker as st

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("/track")
async def track(symbol: str, side: str, entry: float, stop: float, tp: str,
                pending: bool = Query(False), source: Optional[str] = Query("manual")):
    tps = [float(x) for x in tp.replace(" ", "").split(",") if x]
    s = await st.record(symbol, side, entry, stop, tps, source or "manual", pending)
    if not s:
        raise HTTPException(status_code=400, detail="stop must be on the losing side and a target ahead")
    return {"tracking": s}


@router.get("/check")
async def check():
    return await st.check()


@router.get("/stats")
async def stats():
    return await st.stats()


@router.get("/open")
async def open_signals():
    rows = await st._load()
    return {"open": [r for r in rows if r["result"] == "open"]}
