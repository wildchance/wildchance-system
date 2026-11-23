"""Stock data (optional keys): GET /stocks/status · /stocks/quote/{sym} · /stocks/daily/{sym}"""

from fastapi import APIRouter, HTTPException, Query

from services import stocks_service as ss

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("/status")
async def status():
    return ss.status()


@router.get("/quote/{symbol}")
async def quote(symbol: str):
    q = await ss.quote(symbol)
    if not q:
        raise HTTPException(status_code=404, detail="no quote (FINNHUB_API_KEY unset or unknown symbol)")
    return q


@router.get("/daily/{symbol}")
async def daily(symbol: str, days: int = Query(730, ge=30, le=730)):
    bars = await ss.daily(symbol, days)
    return {"symbol": symbol.upper(), "bars": len(bars),
            "last": bars[-1] if bars else None, "first": bars[0] if bars else None}
