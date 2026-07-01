"""Order-flow endpoints — book imbalance + footprint delta.

  POST /flow/imbalance   body {"bids":[...], "asks":[...], "levels":5}
                         → imbalance value + bid_heavy/ask_heavy/balanced read
  POST /flow/footprint   body {"rows":[{"price":..,"buy":..,"sell":..}, ...]}
                         → per-price delta, total delta, POC, bias

Feed it a book/footprint snapshot from any source (Polygon L2, broker, manual).
No live L2 feed is required — the engine is pure math.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Body

from flow import engine as fe

router = APIRouter(prefix="/flow", tags=["flow"])


@router.post("/imbalance")
async def imbalance(bids: List = Body(..., embed=True),
                    asks: List = Body(..., embed=True),
                    levels: Optional[int] = Body(None, embed=True),
                    strong: float = Body(0.3, embed=True)):
    imb = fe.depth_imbalance(bids, asks, levels)
    return {"levels_used": levels or "all", **fe.classify(imb, strong)}


@router.post("/footprint")
async def footprint(rows: List[dict] = Body(..., embed=True)):
    return fe.footprint(rows)
