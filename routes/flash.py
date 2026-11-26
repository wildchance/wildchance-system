"""Profit flash cards.

  GET  /flash/card.png?symbol=US30&side=BUY&open=42512&close=42818.43   preview the PNG
  POST /flash/post?symbol=…&side=…&open=…&close=…                        post to the group
Sniper cases that resolve 'held' (TP1 before stop) are posted automatically.
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from services import flash_card as fc

router = APIRouter(prefix="/flash", tags=["flash"])


def _check(side: str, open_: float, close: float, symbol: str):
    if side.upper() not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="side must be BUY or SELL")
    if fc.pips(symbol, side, open_, close) <= 0:
        raise HTTPException(status_code=400, detail="flash cards are for winning trades only")


@router.get("/card.png")
async def card(symbol: str = Query(...), side: str = Query(...), open: float = Query(..., gt=0),
               close: float = Query(..., gt=0)):
    _check(side, open, close, symbol)
    return Response(fc.render(symbol, side, open, close), media_type="image/png")


@router.post("/test")
async def test(symbol: str = Query("EUR/USD"), side: str = Query(None), open: float = Query(None, gt=0),
               close: float = Query(None, gt=0)):
    """Owner TEST: the latest TP-hit card for ``symbol`` (or your own numbers) → owner's bot chat only."""
    return await fc.send_test(symbol, side, open, close)


@router.post("/post")
async def post(symbol: str = Query(...), side: str = Query(...), open: float = Query(..., gt=0),
               close: float = Query(..., gt=0)):
    _check(side, open, close, symbol)
    return await fc.post(symbol, side, open, close)
