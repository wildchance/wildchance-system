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
    await df.ensure_loaded()                 # scores survive restarts (saved in Neon)
    return df.status()


@router.get("/check/{base}/{quote}")
async def check(base: str, quote: str, reference: Optional[float] = Query(None)):
    return await df.cross_check(f"{base.upper()}/{quote.upper()}", reference)


@router.get("/history/{base}/{quote}")
async def history(base: str, quote: str):
    from services.yearly_cbdr import auto_history
    return {"symbol": f"{base.upper()}/{quote.upper()}",
            "base_rates": await auto_history(f"{base.upper()}/{quote.upper()}")}


@router.get("/base-rates")
async def base_rates(symbols: Optional[str] = Query(None)):
    """Historical base rates for every scanned market (cached a week, saved in Neon)."""
    from services.sniper import DEFAULT_SYMBOLS, STOCKS
    from services.yearly_cbdr import auto_history
    syms = [s.strip().upper() for s in (symbols or DEFAULT_SYMBOLS + "," + STOCKS + ",NAS100,US30,ETH/USD").split(",")
            if s.strip()]
    out = {}
    for s in syms:
        out[s] = await auto_history(s)
    return out


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


@router.get("/cycles/{base}/{quote}")
async def cycles_read(base: str, quote: str):
    """Presidential-cycle year + Hurst cycle phases from free long history (rough, v1)."""
    from services import cycles
    from services.yearly_cbdr import INDEX_PROXY
    sym = f"{base.upper()}/{quote.upper()}"
    bars, src = await df.daily_chain(sym, min_bars=262 * 5)
    proxy = INDEX_PROXY.get(sym.replace("/", ""))
    if len(bars) < 262 * 5 and proxy:
        pb, ps = await df.daily_chain(proxy, min_bars=262 * 5)
        if len(pb) > len(bars):
            bars, src = pb, f"{ps} ({proxy} proxy)"
    return {"symbol": f"{base.upper()}/{quote.upper()}", "source": src, "years_of_data": round(len(bars) / 262, 1),
            **cycles.read(bars)} if bars else {"note": "no history available"}


@router.get("/cycles/{symbol}")
async def cycles_single(symbol: str):
    """Same for single-word symbols: NAS100, US30, SPX500, NVDA (index → ETF proxy)."""
    from services import cycles
    from services.yearly_cbdr import INDEX_PROXY
    sym = symbol.upper()
    bars, src = await df.daily_chain(sym, min_bars=262 * 5)
    proxy = INDEX_PROXY.get(sym)
    if len(bars) < 262 * 5 and proxy:
        pb, ps = await df.daily_chain(proxy, min_bars=262 * 5)
        if len(pb) > len(bars):
            bars, src = pb, f"{ps} ({proxy} proxy)"
    return {"symbol": sym, "source": src, "years_of_data": round(len(bars) / 262, 1),
            **cycles.read(bars)} if bars else {"note": "no history available"}


@router.get("/snapshot")
async def snapshot(symbols: Optional[str] = Query(None)):
    """Live prices for analysis (EA broker feed first, else TwelveData 1-minute) —
    paste this when asking for a read so it's always on real-time numbers."""
    import datetime as _dt
    from services.ohlc_service import fetch_ohlc_raw
    from services.sniper import DEFAULT_SYMBOLS, STOCKS
    syms = [x.strip().upper() for x in (symbols or DEFAULT_SYMBOLS + "," + STOCKS + ",NAS100,US30,ETH/USD,USD/CHF,USD/CAD")
            .split(",") if x.strip()]
    out = {}
    for s in syms:
        try:
            bars = await fetch_ohlc_raw(s, "1min", 2)
        except Exception:
            bars = []
        out[s] = {"price": bars[-1]["close"], "time": bars[-1]["time"]} if bars else {"price": None,
                  "note": "no live feed (indices need the EA on that chart)"}
    macro = df.macro_cached()
    return {"as_of_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), "prices": out,
            "vix": macro.get("vix"), "us_yields": macro.get("us_yields")}
