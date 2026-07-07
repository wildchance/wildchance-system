"""Gold (XAU/USD) prop-firm endpoints — risk sheet + ICT profiles + live scan.

  POST /gold/scan?balance=5000&notify=true   real-time signal (ICT→entry/SL/TP→gate→Telegram)
  GET  /gold/plan?balance=5000&tier=6        full computed sheet for a balance
  GET  /gold/scaling                          the 5k→1M lot ladder table
  GET  /gold/phases?balance=5000              6%→12%→18% (Payout) trade counts
  GET  /gold/profile                          active ICT weekly profile (1 of 12)
  GET  /gold/bias                             quick Monday-sweep bias
  GET  /gold/size?entry=&stop=&risk_usd=      money-first lot + TP/BE prices
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from services import trade_executor as te
from services import gold_positions as gp
from services import scorecard_service as sc
from gold import risk_engine as gr
from gold import compounding as gc
from gold import macro as gmacro
from gold import macro_cycle as gcycle
from gold import dxy as gdxy
from gold import purchases_audit as gpa
from gold.trade_types import seek_destroy_plan, SEEK_DESTROY
from gold.limit_orders import size_limit
from gold.weekly import weekly_bias
from gold.ict import classify_week
import datetime as _dt
from utils.price_fetcher import get_forex_price
from services.ohlc_service import fetch_ohlc
from services import gold_scan
from services import gold_intraday

router = APIRouter(prefix="/gold", tags=["gold"])


def _ny_close() -> _dt.datetime:
    """Today's 21:00 UTC (≈ NY close) — the pre-London/CRT limit expiry."""
    now = _dt.datetime.now(_dt.timezone.utc)
    d = now.replace(hour=21, minute=0, second=0, microsecond=0)
    return d if d > now else d + _dt.timedelta(days=1)


def _week_end() -> _dt.datetime:
    """Friday 21:00 UTC of the current week — the Seek & Destroy limit expiry."""
    now = _dt.datetime.now(_dt.timezone.utc)
    fri = now.replace(hour=21, minute=0, second=0, microsecond=0) + \
        _dt.timedelta(days=(4 - now.weekday()))
    return fri if fri > now else fri + _dt.timedelta(days=7)


async def _arm_limits(db, orders, source, balance, risk_usd, expires_at,
                      execute: bool = False):
    """Size limit specs, persist each as a PENDING tracked position, and — when
    ``execute`` — enqueue the matching MT5 LIMIT order for the bridge to place."""
    out = []
    for o in orders:
        card = size_limit(o, balance, risk_usd)
        opened = await gp.open_limit(db, card, source, expires_at)
        entry = {"tracked": opened or {"skipped": "not sizeable", "order": o}}
        if execute and card.get("signal") in ("LONG", "SHORT"):
            order = te.build_order(card, source=source)
            if order:
                entry["queued_order"] = await te.enqueue(db, order)
        out.append(entry)
    return out


@router.post("/scan")
async def scan(balance: float = Query(5000, gt=0),
               tier: str = Query("6"),
               risk_usd: float = Query(20.0, gt=0),
               sl_pips: float = Query(200.0, gt=0),
               require_confluence: bool = Query(False,
                   description="suppress when wildchance retail+COT opposes the profile"),
               require_macro: bool = Query(True,
                   description="require alignment with the standing macro bias"),
               require_location: bool = Query(True,
                   description="only enter in discount (long) / premium (short)"),
               require_regime: bool = Query(True,
                   description="require dollar (DXY inverse) + COT positioning confluence"),
               track: bool = Query(True,
                   description="persist a fired signal as a monitored swing position"),
               notify: bool = Query(False),
               db: AsyncSession = Depends(get_db)):
    """Real-time gold signal: ICT profile → macro/location/regime gate → entry/SL/TP/lot → prop gate → Telegram."""
    sig = await gold_scan.scan(balance=balance, tier=tier, risk_usd=risk_usd,
                               sl_pips=sl_pips, require_confluence=require_confluence,
                               require_macro=require_macro, require_location=require_location,
                               require_regime=require_regime, notify=notify)
    if track and sig.get("signal") in ("LONG", "SHORT"):
        opened = await gp.open_from_signal(db, sig, source="gold_scan")
        if opened:
            sig["tracked_position"] = opened
    return sig


@router.post("/intraday")
async def intraday(balance: float = Query(5000, gt=0),
                   tier: str = Query("6"),
                   risk_usd: float = Query(20.0, gt=0),
                   sl_pips: float = Query(200.0, gt=0),
                   cycle_len: int = Query(20, ge=4, le=200),
                   require_fld: bool = Query(True),
                   require_distribution: bool = Query(False,
                       description="only fire in the NY-AM distribution quarter (Q3)"),
                   track: bool = Query(True,
                       description="persist a fired signal as a monitored swing position"),
                   notify: bool = Query(False),
                   execute: bool = Query(False,
                       description="enqueue the order for the MT5 bridge to place"),
                   db: AsyncSession = Depends(get_db)):
    """Intraday signal: weekly profile × QT session quarter × Hurst FLD → macro+location gate → prop gate → Telegram."""
    sig = await gold_intraday.scan(balance=balance, tier=tier, risk_usd=risk_usd,
                                   sl_pips=sl_pips, cycle_len=cycle_len,
                                   require_fld=require_fld,
                                   require_distribution=require_distribution,
                                   notify=notify)
    if track and sig.get("signal") in ("LONG", "SHORT"):
        opened = await gp.open_from_signal(db, sig, source="gold_intraday")
        if opened:
            sig["tracked_position"] = opened
    if execute:
        order = te.build_order(sig, source="gold_intraday")
        if order:
            sig["queued_order"] = await te.enqueue(db, order)
    return sig

@router.get("/compound")
async def compound(deposit: float = Query(700, gt=0),
                   currency: str = Query("USD", pattern="^(USD|KES|KWD|usd|kes|kwd)$"),
                   mode: str = Query("", description="swing|low|mixed (blank = full plan)")):
    """Deposit growth ladder (swing ×10 / low-income doubling / mixed) per currency."""
    if mode:
        return gc.ladder(deposit, mode, currency)
    return gc.plan(deposit, currency)


_LADDER = [5000, 10000, 25000, 50000, 100000, 200000, 500000, 1000000]


@router.get("/plan")
async def plan(balance: float = Query(5000, gt=0),
               tier: str = Query("6"),
               risk_usd: float = Query(20.0, gt=0)):
    return gr.plan(balance, tier, risk_usd)


@router.get("/scaling")
async def scaling():
    return {"instrument": "XAU/USD",
            "ladder": [{"balance": b, **gr.lot_ladder(b),
                        "prop_6pct": gr.prop_pass_math(b, "6")} for b in _LADDER]}


@router.get("/phases")
async def phases(balance: float = Query(5000, gt=0)):
    """6% → 12% → 18% (Payout) trade-count ladder (the handwritten phase plan)."""
    return {"instrument": "XAU/USD", "balance": balance, **gr.phase_plan(balance)}


@router.get("/profile")
async def profile():
    """Active ICT Weekly Profile for XAU/USD (1 of 12) — full trend justification."""
    daily = await fetch_ohlc("XAU/USD", "1day", 25)
    if len(daily) < 3:
        raise HTTPException(status_code=502, detail="could not fetch XAU/USD daily bars")
    read = classify_week(daily)
    return {"instrument": "XAU/USD", **(read or {})}


@router.get("/bias")
async def bias():
    """Monday-sweep weekly bias for XAU/USD — the trend justification for signals."""
    daily = await fetch_ohlc("XAU/USD", "1day", 15)
    if len(daily) < 2:
        raise HTTPException(status_code=502, detail="could not fetch XAU/USD daily bars")
    read = weekly_bias(daily)
    return {"instrument": "XAU/USD", **(read or {})}


@router.post("/monitor")
async def monitor(price: float = Query(None, gt=0,
                      description="override live price (else fetched)"),
                  db: AsyncSession = Depends(get_db)):
    """Advance every OPEN gold swing: trail to BE after TP1, close on TP/SL/time-stop.

    Cron-friendly — call frequently through Mon/Tue so the weekend swing is closed
    at target or at the Monday-close/Tuesday-open time-stop.
    """
    if price is None:
        try:
            price = await get_forex_price("XAU/USD")
        except Exception:
            price = None
    if price is None:
        raise HTTPException(status_code=502, detail="could not fetch XAU/USD price")
    return await gp.monitor(db, float(price))


@router.get("/positions")
async def positions(status: str = Query(None, description="OPEN | CLOSED (default all)"),
                    limit: int = Query(50, ge=1, le=200),
                    db: AsyncSession = Depends(get_db)):
    return {"positions": await gp.list_positions(db, status=status, limit=limit)}


@router.get("/scorecard")
async def scorecard(db: AsyncSession = Depends(get_db)):
    """Performance + reflection over CLOSED gold swings (realized R)."""
    return await sc.gold_report(db)


@router.post("/scorecard/digest")
async def scorecard_digest(force: bool = Query(False, description="send even if nothing closed"),
                           db: AsyncSession = Depends(get_db)):
    """Push the gold scorecard to Telegram (cron-friendly)."""
    text = await sc.gold_digest_text(db)
    if not text:
        if not force:
            return {"sent": False, "reason": "no closed gold trades yet"}
        text = "🏆 *GOLD Scorecard*\n\n_No closed gold trades yet._"
    sent = await gold_scan._tg(text)
    return {"sent": sent}


@router.post("/prelondon")
async def prelondon(balance: float = Query(5000, gt=0),
                    risk_usd: float = Query(20.0, gt=0),
                    track: bool = Query(True, description="arm the limits as PENDING positions"),
                    execute: bool = Query(False, description="also enqueue MT5 limit orders"),
                    db: AsyncSession = Depends(get_db)):
    """Arm the pre-London CBDR limits (buy −1SD / sell +1,+3SD) as tracked PENDING
    positions that fill on touch and are monitored to TP/SL through the NY session."""
    from services.cbdr_service import fetch_cbdr_window
    from cbdr.engine import build_cbdr, prelondon_limits
    win = await fetch_cbdr_window("XAU/USD", "prelondon")
    if not win:
        raise HTTPException(status_code=502, detail="could not fetch the pre-London box")
    box = build_cbdr(win["high"], win["low"])
    plan = prelondon_limits(box)
    if track:
        plan["tracked"] = await _arm_limits(db, plan["orders"], "gold_prelondon",
                                            balance, risk_usd, _ny_close(), execute=execute)
    plan["session"] = win["session"]
    return plan


@router.get("/macro")
async def macro():
    """The standing Q3/Q4 macro read that gates gold signals."""
    return gmacro.macro_read()


@router.get("/news")
async def news():
    """Current gold news state — same-day block, window flag, and the live tier-1
    USD calendar. Use to confirm the news engine is refreshing."""
    from services import news_guard
    today = _dt.datetime.now(_dt.timezone.utc).date()
    same_day = await news_guard.news_flag(today, "XAU/USD", win=0)
    window = await news_guard.news_flag(today, "XAU/USD")
    cal = await news_guard.high_impact_calendar({"USD"})
    return {"as_of": today.isoformat(), "same_day_block": same_day,
            "window_flag": window, "high_impact_usd": cal}


@router.get("/regime")
async def regime():
    """Fused macro-cycle regime read (BIS/FRED/WGC/COT + DXY) — HTF filter + LTF entry filter."""
    return gcycle.regime_read()


@router.post("/regime/refresh")
async def regime_refresh():
    """Pull live FRED (real rate / Fed) + CFTC (gold COT) into the regime inputs."""
    return await gcycle.refresh_inputs()


@router.get("/dxy")
async def dxy(price: float = Query(None, description="live DXY weekly close (optional)")):
    """DXY regime + the dollar→gold inverse bias (Trump-term anticipation structure)."""
    return {"dollar_regime": gdxy.dollar_regime(price), "gold_implication": gdxy.gold_from_dollar(price)}


@router.get("/audit")
async def audit():
    """Gold purchases & positioning change audit — YoY / QoQ / MoM / WoW."""
    return gpa.audit()


@router.get("/sd-fade")
async def sd_fade(extreme_sd: float = Query(3.0, ge=1.5, le=6.0,
                      description="deviation multiple for the extreme limit"),
                  balance: float = Query(5000, gt=0),
                  risk_usd: float = Query(20.0, gt=0),
                  track: bool = Query(False, description="persist the fade limits as PENDING positions"),
                  execute: bool = Query(False, description="also enqueue MT5 limit orders"),
                  db: AsyncSession = Depends(get_db)):
    """Seek & Destroy fade plan — extreme limits outside the week's range on the
    HTF-trend side (monthly/quarterly), to catch liquidity sweeps in a ranging week."""
    daily = await fetch_ohlc("XAU/USD", "1day", 25)
    if len(daily) < 3:
        raise HTTPException(status_code=502, detail="could not fetch XAU/USD daily bars")
    profile = classify_week(daily)
    pid = (profile or {}).get("profile_id")
    if pid not in SEEK_DESTROY:
        return {"applicable": False, "profile": (profile or {}).get("profile"),
                "reason": "not a Seek & Destroy week — use the normal tiered scan"}
    htf = gcycle.regime_read()["gold_bias"]        # monthly/quarterly fused bias
    # Deviation basis = MONDAY's CBDR (2-8pm NY / "20:00 range") — it sets the
    # week's trend/range; extremes project from it. Fall back to the week hi/lo.
    from services.cbdr_service import fetch_cbdr_window
    mon = await fetch_cbdr_window("XAU/USD", "cbdr", pick="monday")
    if mon:
        high, low, basis = mon["high"], mon["low"], f"Monday CBDR ({mon['session']})"
    else:
        high, low, basis = profile["week_high"], profile["week_low"], "week range (CBDR unavailable)"
    plan = seek_destroy_plan(high, low, htf, extreme_sd=extreme_sd)
    if track:
        plan["tracked"] = await _arm_limits(db, plan["orders"], "gold_sd_fade",
                                            balance, risk_usd, _week_end(), execute=execute)
    return {"applicable": True, "profile": profile["profile"],
            "basis": basis, "range": [low, high], "htf_bias": htf, **plan}


@router.get("/size")
async def size(entry: float = Query(..., gt=0),
               stop: float = Query(..., gt=0),
               side: str = Query("long", pattern="^(long|short|buy|sell)$"),
               risk_usd: float = Query(20.0, gt=0)):
    lot = gr.size_for_risk(entry, stop, risk_usd)
    tps = gr.targets(entry, stop, side)
    return {
        "instrument": "XAU/USD", "side": side, "entry": entry, "stop": stop,
        "risk_usd": risk_usd, "lot": lot,
        "stop_distance": round(abs(entry - stop), 2),
        "targets": tps,
        "breakeven_trigger": gr.breakeven_price(entry, tps[0]["price"]) if tps else None,
    }
