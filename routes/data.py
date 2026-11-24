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
    await df.ensure_loaded()                       # macro is persisted — restore after a restart
    macro = df.macro_cached()
    if macro.get("vix") is None:
        try:
            await df.refresh_macro()
            macro = df.macro_cached()
        except Exception:
            pass
    return {"as_of_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"), "prices": out,
            "vix": macro.get("vix"), "us_yields": macro.get("us_yields")}


@router.get("/options-levels/{base}/{quote}")
async def options_levels_pair(base: str, quote: str, price: Optional[float] = Query(None)):
    return await _opt(f"{base.upper()}/{quote.upper()}", price)


@router.get("/options-levels/{symbol}")
async def options_levels_one(symbol: str, price: Optional[float] = Query(None)):
    return await _opt(symbol.upper(), price)


async def _opt(sym: str, price: Optional[float]):
    """Dealer levels from CBOE's free delayed chains (GLD→gold, NDX→NAS100, DIA→US30)."""
    from services import options_levels as ol
    if price is None:
        try:
            from services.ohlc_service import fetch_ohlc_raw
            bars = await fetch_ohlc_raw(sym, "1min", 2)
            price = bars[-1]["close"] if bars else None
        except Exception:
            price = None
    return await ol.run(sym, price)


# ── economic calendar (automatic blackouts, learns each release's move) ──────────────
@router.get("/calendar")
async def calendar(days: int = Query(7, ge=1, le=30), high_only: bool = Query(True)):
    from services import econ_calendar as ec
    await ec.ensure_loaded()
    return {"events": ec.upcoming(days, high_only)}


@router.get("/calendar/refresh")
async def calendar_refresh(force: bool = Query(False)):
    from services import econ_calendar as ec
    return await ec.refresh(force)


@router.get("/calendar/status")
async def calendar_status():
    from services import econ_calendar as ec
    await ec.ensure_loaded()
    return ec.status()


@router.get("/calendar/learn")
async def calendar_learn():
    from services import econ_calendar as ec
    return await ec.learn()


@router.get("/calendar/moves")
async def calendar_moves():
    from services import econ_calendar as ec
    await ec.ensure_loaded()
    return dict(sorted(ec._moves.items(), key=lambda kv: -kv[1]["avg_pips"]))


@router.get("/calendar/week")
async def calendar_week(notify: bool = Query(False)):
    import datetime as dt
    from services import econ_calendar as ec
    from services.realtime import alert_once
    await ec.ensure_loaded()
    ev = ec.upcoming(7, high_only=True)
    out = {"events": ev}
    if notify:
        out["sent"] = await alert_once(f"calweek:{dt.date.today():%G-%V}", ec.week_card(ev), ttl_hours=24 * 6)
    return out


# ── CME Daily Bulletin (real COMEX gold options open interest) ─────────────────────
@router.get("/cme/refresh")
async def cme_refresh():
    from services import cme_bulletin as cb
    return {"gold": await cb.refresh(), "fx": await cb.refresh_fx()}


@router.get("/cme/fx/{base}/{quote}")
async def cme_fx(base: str, quote: str):
    from services import cme_bulletin as cb
    c = await cb.cached_fx_chain(f"{base}{quote}", max_age_days=30)
    if not c:
        return {"ok": False, "reason": "no CME chain for this pair (USD/JPY, USD/CHF, USD/CAD, GBP/USD)"}
    top_c = sorted(c["strikes"].items(), key=lambda kv: -kv[1]["call_oi"])[:5]
    top_p = sorted(c["strikes"].items(), key=lambda kv: -kv[1]["put_oi"])[:5]
    return {"ok": True, "pair": c.get("pair"), "asof": c["asof"], "front_future_in_pair_terms": c["spot"],
            "expiries": c.get("expiries"), "put_call_ratio": c.get("put_call_ratio"),
            "top_call_strikes": [{"strike": k, "oi": int(v["call_oi"])} for k, v in top_c],
            "top_put_strikes": [{"strike": k, "oi": int(v["put_oi"])} for k, v in top_p]}


@router.get("/cme/gold")
async def cme_gold():
    from services import cme_bulletin as cb
    c = await cb.cached_chain(max_age_days=30)
    if not c:
        return {"ok": False, "reason": "no bulletin parsed yet"}
    top_c = sorted(c["strikes"].items(), key=lambda kv: -kv[1]["call_oi"])[:5]
    top_p = sorted(c["strikes"].items(), key=lambda kv: -kv[1]["put_oi"])[:5]
    return {"ok": True, "asof": c["asof"], "front_future": c["spot"], "expiries": c.get("expiries"),
            "put_call_ratio": c.get("put_call_ratio"), "call_oi": c.get("call_oi"), "put_oi": c.get("put_oi"),
            "top_call_strikes": [{"strike": k, "oi": int(v["call_oi"])} for k, v in top_c],
            "top_put_strikes": [{"strike": k, "oi": int(v["put_oi"])} for k, v in top_p],
            "note": "strikes in front-future terms; /data/options-levels/XAU/USD maps them to live spot"}


@router.get("/cme/reminder")
async def cme_reminder(notify: bool = Query(False)):
    from services.owner_notify import cme_reminder as rem
    return await rem(notify)
