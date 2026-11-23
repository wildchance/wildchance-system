"""Free data feeds — health, cross-checks, macro, sentiment.

  GET /data/sources                       per-source health score + which keys are set
  GET /data/check/{base}/{quote}           latest close across Stooq/Tiingo/Binance (+live), outliers
  GET /data/history/{base}/{quote}         base rates from free long history (auto)
  GET /data/macro?force=true               ECB · US yields + debt · BLS · VIX · crypto dominance
  GET /data/refresh?part=sentiment|macro   scheduled refreshes (6-hourly / daily)
  GET /data/sentiment/{base}/{quote}       cached news sentiment (Alpha Vantage + MarketAux)
"""

from typing import Optional

from fastapi import APIRouter, Query

from services import data_feeds as df

router = APIRouter(prefix="/data", tags=["data"])


@router.get("/sources")
async def sources():
    return df.status()


@router.get("/check/{base}/{quote}")
async def check(base: str, quote: str, reference: Optional[float] = Query(None)):
    return await df.cross_check(f"{base.upper()}/{quote.upper()}", reference)


@router.get("/history/{base}/{quote}")
async def history(base: str, quote: str):
    from services.yearly_cbdr import auto_history
    return {"symbol": f"{base.upper()}/{quote.upper()}",
            "base_rates": await auto_history(f"{base.upper()}/{quote.upper()}")}


@router.get("/macro")
async def macro(force: bool = Query(False)):
    return await df.refresh_macro() if force or not df.macro_cached() else df.macro_cached()


@router.get("/refresh")
async def refresh(part: str = Query("sentiment", pattern="^(sentiment|macro)$")):
    if part == "macro":
        return await df.refresh_macro()
    from services.sniper import DEFAULT_SYMBOLS, STOCKS
    syms = [s.strip() for s in (DEFAULT_SYMBOLS + "," + STOCKS).split(",") if s.strip()]
    return await df.refresh_sentiment(syms)


@router.get("/sentiment/{base}/{quote}")
async def sentiment(base: str, quote: str):
    return df.news_sentiment(f"{base}{quote}") or {"score": None, "note": "no recent read — refresh runs 6-hourly"}
