"""GET /data/tp-study/{base}/{quote}?horizon=5 — hit-rate + expectancy of 0.5σ…3σ targets
vs a 1σ stop over the market's history; best_k = the sweet spot inside 1σ–2σ."""

from fastapi import APIRouter, Query

from services import tp_study

router = APIRouter(prefix="/data", tags=["data"])


@router.get("/tp-study/{base}/{quote}")
async def tp(base: str, quote: str, horizon: int = Query(5, ge=1, le=30)):
    return await tp_study.study(f"{base.upper()}/{quote.upper()}", horizon) or {"note": "no history available"}


@router.get("/tp-study/{symbol}")
async def tp_single(symbol: str, horizon: int = Query(5, ge=1, le=30)):
    return await tp_study.study(symbol.upper(), horizon) or {"note": "no history available"}
