"""Sweeper drones — multi-asset confluence recon.

  POST /sweep/run?notify=true&symbols=…   sweep the universe (default SWEEP_UNIVERSE)
  GET  /sweep/latest                       the last report (cached)
  GET  /sweep/asset/{base}/{quote}         one asset's maps + zones
"""

from typing import Optional

from fastapi import APIRouter, Query

from services import sweeper

router = APIRouter(prefix="/sweep", tags=["sweep"])


@router.post("/run")
async def run(notify: bool = Query(False), symbols: Optional[str] = Query(None)):
    syms = [s.strip().upper() for s in symbols.split(",")] if symbols else None
    rep = await sweeper.sweep(syms, notify)
    return {k: v for k, v in rep.items() if k != "results"}


@router.get("/latest")
async def latest():
    return sweeper._last.get("report") or {"note": "no sweep yet — runs 01:30 / 09:30 / 15:30 UTC on weekdays"}


@router.get("/asset/{base}/{quote}")
async def asset(base: str, quote: str):
    return await sweeper.sweep_one(f"{base.upper()}/{quote.upper()}")


@router.get("/asset/{symbol}")
async def asset_one(symbol: str):
    return await sweeper.sweep_one(symbol.upper())
