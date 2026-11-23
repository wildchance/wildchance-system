"""SNIPER entries + the case library.

  POST /sniper/scan?notify=true&symbols=EUR/USD,XAU/USD   CISD at golden clusters
  GET  /sniper/cases                                        role hit-rates (csv + live)
"""

from typing import Optional

from fastapi import APIRouter, Query

from services import case_library as cl, sniper

router = APIRouter(prefix="/sniper", tags=["sniper"])


@router.post("/scan")
async def scan(notify: bool = Query(False), symbols: Optional[str] = Query(None)):
    syms = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    return await sniper.scan(syms, notify)


@router.get("/cases")
async def cases(symbol: Optional[str] = Query(None)):
    allc = await cl.all_cases()
    return {"cases": len(allc), "live": sum(1 for c in allc if c["source"] == "live"),
            "by_role": cl.score(allc, symbol=symbol)}


@router.get("/council/{base}/{quote}")
async def council(base: str, quote: str, side: str = Query("BUY", pattern="^(BUY|SELL)$")):
    from services import council as cn
    return await cn.convene(f"{base.upper()}/{quote.upper()}", side)


@router.get("/levels/{base}/{quote}")
async def levels(base: str, quote: str):
    """Session time levels, the fixed grid and option strikes for today (NY time)."""
    from services import session_levels as sl
    from services.sniper import _bars_1h
    sym = f"{base.upper()}/{quote.upper()}"
    bars = await _bars_1h(sym, 48, tz="America/New_York")
    return {"symbol": sym, "levels": sorted(sl.all_levels(sym, bars), key=lambda x: -x["price"])}
