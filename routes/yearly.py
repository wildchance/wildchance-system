"""Yearly CBDR (Q1 range deviations).

  GET  /yearly/cbdr/{base}/{quote}?years=4     levels, peaks, range in control, clusters, OTE, plan
  POST /yearly/alert?symbols=EUR/USD,XAU/USD   Telegram card per symbol (once a week)
"""

from fastapi import APIRouter, Query

from services import yearly_cbdr as yc

router = APIRouter(prefix="/yearly", tags=["yearly"])
DEFAULT = "EUR/USD,GBP/USD,USD/JPY,XAU/USD"


@router.get("/cbdr/{base}/{quote}")
async def cbdr(base: str, quote: str, years: int = Query(yc.YEARS_BACK, ge=1, le=10)):
    return await yc.run(f"{base.upper()}/{quote.upper()}", years)


@router.post("/alert")
async def alert(symbols: str = Query(DEFAULT), years: int = Query(yc.YEARS_BACK, ge=1, le=10),
                notify: bool = Query(True)):
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    return await yc.alert(syms, years, notify)
