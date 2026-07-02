"""EdgeFinder endpoints — per-pair macro bias scoreboard.

  GET /edgefinder            ranked bias board (retail + COT + confluence)
  GET /edgefinder?mmm=true   also fold in the MMM weekly-cycle bias (slower)
  GET /edgefinder/{symbol}   deep single-pair read (always includes MMM + news)

Aggregates the layers the system already computes into one signed bias score per
pair, most-conviction first.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from services import edgefinder_service

router = APIRouter(prefix="/edgefinder", tags=["edgefinder"])


@router.get("")
async def board(mmm: bool = Query(False, description="fold in MMM weekly cycle (slower)")):
    return await edgefinder_service.scoreboard(with_mmm=mmm)


@router.get("/{symbol:path}")
async def pair(symbol: str):
    row = await edgefinder_service.pair_read(symbol)
    if row is None:
        raise HTTPException(status_code=404,
                            detail=f"{symbol} not in the wildchance watchlist / feed")
    return row
