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
                   require_protraction: bool = Query(True,
                       description="require a session sweep+reversal of the 8-hour range"),
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
                                   require_protraction=require_protraction,
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
                  notify: bool = Query(True, description="push lifecycle alerts to Telegram"),
                  db: AsyncSession = Depends(get_db)):
    """Advance every OPEN gold swing: trail to BE after TP1, close on TP/SL/time-stop.

    Cron-friendly — call frequently through Mon/Tue so the weekend swing is closed
    at target or at the Monday-close/Tuesday-open time-stop. Pushes a lifecycle
    alert (armed→filled→TP/BE→closed, with running R) on every transition.
    """
    if price is None:
        try:
            price = await get_forex_price("XAU/USD")
        except Exception:
            price = None
    if price is None:
        raise HTTPException(status_code=502, detail="could not fetch XAU/USD price")
    # Live pre-London CBDR levels — the hands-off session-hold exit rail.
    pl_levels = None
    try:
        from services.cbdr_service import fetch_cbdr_window
        from cbdr.engine import build_cbdr
        w = await fetch_cbdr_window("XAU/USD", window="prelondon")
        if w and w.get("high") is not None and w.get("low") is not None:
            pl_levels = build_cbdr(w["high"], w["low"]).levels
    except Exception:
        pl_levels = None
    result = await gp.monitor(db, float(price), prelondon_levels=pl_levels)
    if notify and result.get("events"):
        from gold.position import format_lifecycle_events
        text = format_lifecycle_events(result["events"])
        if text:
            result["alert_sent"] = await gold_scan._tg(text)
    return result


@router.get("/options")
async def options_get(side: str = Query(None, description="check confluence for long/short"),
                      level: float = Query(None, description="entry level to check")):
    """Options-flow read — put/call walls + the expected-move envelope + skew. If
    side+level given, also the confluence for that entry."""
    from gold import options_flow as ofl
    out = ofl.snapshot()
    if side and level is not None:
        out["confluence"] = ofl.confluence(side, level)
    return out


@router.post("/options")
async def options_set(future: float = Query(..., description="reference future price"),
                      put_wall: float = Query(None), call_wall: float = Query(None),
                      sigma1: float = Query(None), sigma2: float = Query(None),
                      sigma3: float = Query(None),
                      put_vol: float = Query(None), call_vol: float = Query(None)):
    """Feed the options snapshot (operator-fed, like WGC): the future, put/call
    walls, the 1σ/2σ/3σ expected-move half-widths, and put/call volume."""
    from gold import options_flow as ofl
    return ofl.set_inputs(future=future, put_wall=put_wall, call_wall=call_wall,
                          sigma1=sigma1, sigma2=sigma2, sigma3=sigma3,
                          put_vol=put_vol, call_vol=call_vol)


@router.post("/options/refresh")
async def options_refresh(url: str = Query(None, description="override OPTIONS_FEED_URL")):
    """Pull the options snapshot from the configured live feed (OPTIONS_FEED_URL)
    and ingest it. No-op if no feed is set — the operator POST /gold/options path
    still works. Cron-friendly."""
    from services import options_service as osvc
    return await osvc.refresh(url=url)


@router.get("/options/feed-status")
async def options_feed_status():
    """Is a live options feed wired, and is a snapshot loaded?"""
    from services import options_service as osvc
    return osvc.feed_status()


@router.get("/accounts")
async def accounts(denom: str = Query("USD", description="cent | USD | KES | KWD"),
                   anchor: float = Query(4000.0, description="leg anchor for acc5")):
    """The 5-account fleet plan — each account's growth/layer ladder."""
    from gold import accounts as ga
    return ga.fleet_plan(denom=denom, anchor=anchor)


@router.get("/accounts/{acc_id}")
async def account_one(acc_id: str, deposit: float = Query(None),
                      denom: str = Query("USD"), anchor: float = Query(4000.0)):
    """One account's plan (acc1..acc5)."""
    from gold import accounts as ga
    return ga.account_plan(acc_id, deposit=deposit, denom=denom, anchor=anchor)


@router.post("/accounts/fanout")
async def accounts_fanout(entry: float = Query(...), stop: float = Query(...),
                          side: str = Query("short", description="long | short")):
    """Copy-trade fan-out: size one signal to the default 5-account fleet.
    (Balances/denoms are the fleet defaults; POST your own via the body later.)"""
    from gold import accounts as ga
    fleet = [{"id": aid, "balance": m["default_deposit"], "denom": "USD", "risk_pct": 1.0}
             for aid, m in ga.FLEET.items()]
    return ga.copy_fanout({"side": side, "entry": entry, "stop": stop}, fleet)


@router.get("/bumblebee")
async def bumblebee(session: str = Query(None, description="london | newyork (else by clock)"),
                    now_hour: int = Query(None, description="override UTC-4 hour"),
                    price: float = Query(None)):
    """Bumblebee — intra-session sweep-and-continuity scalper. The session-open 1H
    range → the sweep of one side → the continuity call toward the HTF order block
    (cheetah scalps). Asian 2-5 sets the daily bias; London 00/01/02, NY 07/08/09."""
    import datetime as _dt
    from gold import bumblebee as gbb
    from gold import radar as grd
    # 1H bars tagged with UTC-4 hour
    raw = await fetch_ohlc("XAU/USD", "1h", 60)
    if len(raw) < 8:
        raise HTTPException(status_code=502, detail="not enough XAU/USD 1H bars")
    bars = []
    for b in raw:
        # DatedOHLC is (date,o,h,l,c) at day granularity — fall back to index hour when
        # intraday timestamps aren't available; the operator can pass now_hour.
        bars.append({"hour": None, "open": b[1], "high": b[2], "low": b[3], "close": b[4]})
    # Prefer raw hourly with timestamps if available.
    try:
        from services.ohlc_service import fetch_hourly_raw
        hraw = await fetch_hourly_raw("XAU/USD", timezone="America/New_York", outputsize=60)
        if hraw:
            bars = [{"hour": int(str(x.get("datetime", ""))[11:13] or -1),
                     "open": float(x["open"]), "high": float(x["high"]),
                     "low": float(x["low"]), "close": float(x["close"])}
                    for x in hraw if x.get("datetime")]
    except Exception:
        pass
    nh = now_hour if now_hour is not None else int(_dt.datetime.utcnow().hour - 4) % 24
    # HTF OB bias + nearest OB target from Optimus/radar
    htf_bias = ob_target = None
    try:
        daily = await fetch_ohlc("XAU/USD", "1day", 90)
        htf_bias = grd.combine_htf(daily=grd.order_blocks(daily, timeframe="1D")
                                   if len(daily) >= 8 else []).get("htf_bias")
    except Exception:
        pass
    return gbb.bumblebee_scan(bars, nh, htf_bias=htf_bias, session=session, ob_target=ob_target)


@router.get("/optimus")
async def optimus(interval: str = Query("4h"), bars: int = Query(60, ge=8, le=300),
                  price: float = Query(None)):
    """Optimus Prime — zone-precision locator. Finds the exact up/down-close OB at
    each live reaction zone, gates entry on the reject (no early 250-usd stop),
    grades the 2500-pip / 250-usd capture, and anticipates the next zone."""
    from gold import optimus as gop
    ohlc = await fetch_ohlc("XAU/USD", interval, bars)
    if len(ohlc) < 8:
        raise HTTPException(status_code=502, detail="not enough XAU/USD HTF bars")
    if price is None:
        try:
            price = await get_forex_price("XAU/USD")
        except Exception:
            price = ohlc[-1][4]
    scan = gop.optimus_scan(ohlc, float(price))
    scan["display"] = gop.format_optimus(scan)
    return scan


@router.get("/optimus/sell-limits")
async def optimus_sell_limits(price: float = Query(None), window: str = Query("prelondon")):
    """Pinpoint the SELL-LIMIT ladder (break-retest structure) — each premium level as
    a sell-limit with entry/stop/target, tagged with pre-London CBDR confluence."""
    from gold import optimus as gop
    if price is None:
        try:
            price = await get_forex_price("XAU/USD")
        except Exception:
            raise HTTPException(status_code=502, detail="no price")
    box = None
    try:
        from services.recon_service import _live_box
        box = await _live_box(window)
    except Exception:
        pass
    return gop.sell_limit_ladder(float(price), box)


@router.get("/optimus/path")
async def optimus_path(price: float = Query(None), tp: float = Query(None)):
    """Sell-anticipation PATH — the sequenced lower-high/lower-low staircase (retrace
    → impulse → retrace) projected down to TP. The roadmap, not just the levels."""
    from gold import optimus as gop
    if price is None:
        try:
            price = await get_forex_price("XAU/USD")
        except Exception:
            raise HTTPException(status_code=502, detail="no price")
    return gop.sell_path(float(price), tp)


@router.post("/optimus/path")
async def optimus_path_set(sells: str = Query(None, description="JSON list of sell OBs"),
                           floors: str = Query(None, description="JSON list of demand floors"),
                           tp: float = Query(None)):
    """Feed today's H4 staircase (sell OBs, demand floors, TP) into the path projector."""
    import json
    from gold import optimus as gop
    s = json.loads(sells) if sells else None
    f = json.loads(floors) if floors else None
    return gop.set_sell_path(sells=s, floors=f, tp=tp)


@router.get("/optimus/bounce")
async def optimus_bounce(price: float = Query(None), window: str = Query("prelondon")):
    """Counter-trend bounce map — the premium OBs above price (buy targets) that are
    also where the primary SELL re-arms (daily 4074 / 4H 4135), with CBDR confluence."""
    from gold import optimus as gop
    if price is None:
        try:
            price = await get_forex_price("XAU/USD")
        except Exception:
            raise HTTPException(status_code=502, detail="no price")
    box = None
    try:
        from services.recon_service import _live_box
        box = await _live_box(window)
    except Exception:
        pass
    return gop.bounce_plan(float(price), box)


@router.get("/optimus/campaign")
async def optimus_campaign(price: float = Query(None)):
    """The real-time journaling campaign — expected trades per $-tier to 3130 + progress."""
    from gold import optimus as gop
    if price is None:
        try:
            price = await get_forex_price("XAU/USD")
        except Exception:
            price = None
    return gop.campaign_projection(float(price) if price else None)


@router.post("/optimus/fib")
async def optimus_fib(bullish_mean: float = Query(None), central_limit: float = Query(None),
                      bearish_mean: float = Query(None), buy_sell_limit_1: float = Query(None),
                      buy_sell_limit_15: float = Query(None), equilibrium: float = Query(None)):
    """Feed today's HTF fib/structure levels (means, limits) into Optimus."""
    from gold import optimus as gop
    return gop.set_fib_map(bullish_mean=bullish_mean, central_limit=central_limit,
                           bearish_mean=bearish_mean, buy_sell_limit_1=buy_sell_limit_1,
                           buy_sell_limit_15=buy_sell_limit_15, equilibrium=equilibrium)


@router.post("/optimus/zones")
async def optimus_zones(sell: str = Query(None, description="JSON list of sell zones [{name,lo,hi,note}]"),
                        buy: str = Query(None, description="JSON list of buy zones"),
                        bullish_mean: float = Query(None, description="the mean pivot")):
    """Feed today's live reaction map (up/down-close zones) into Optimus."""
    import json
    from gold import optimus as gop
    s = json.loads(sell) if sell else None
    b = json.loads(buy) if buy else None
    p = {"bullish_mean": bullish_mean} if bullish_mean is not None else None
    return gop.set_live_zones(sell=s, buy=b, pivots=p)


@router.get("/network")
async def network():
    """Copy-trade network structure — the ×10 upscale ladder + the structured
    daily/weekly/monthly %-return bands per account size."""
    from gold import account_network as gan
    return gan.network_structure()


@router.get("/network/report")
async def network_report(base: float = Query(100.0, gt=0),
                         prop_firm: str = Query("fundingpips"),
                         prop_size: float = Query(5000.0, gt=0)):
    """Full network report — upscale ladder + D/W/M targets + prop plan + currency tiers."""
    from gold import account_network as gan
    return gan.network_report(base, prop_firm, prop_size)


@router.get("/network/upscale")
async def network_upscale(base: float = Query(100.0, gt=0),
                          min_copies: int = Query(10, ge=1),
                          denom: str = Query("USD")):
    """The ×10 upscale ladder — each rung needs N cleared copy trades to graduate."""
    from gold import account_network as gan
    return gan.upscale_ladder(base, min_copies, denom)


@router.get("/network/prop")
async def network_prop(firm: str = Query(None, description="fundingpips | ftmo | the5ers | myfundedfx"),
                       size: float = Query(5000.0, gt=0),
                       risk_pct: float = Query(1.0, gt=0)):
    """Prop copy-trader plan — per-firm phase targets / drawdown / split. Omit firm to list firms."""
    from gold import account_network as gan
    if not firm:
        return gan.prop_firms()
    return gan.prop_plan(firm, size, risk_pct)


@router.get("/network/currencies")
async def network_currencies(target_usd: float = Query(5000.0, gt=0),
                             currencies: str = Query(None, description="comma list e.g. USD,KES,KWD,NGN")):
    """Cross-border deposit tiers — a USD size threshold in global deposit currencies."""
    from gold import account_network as gan
    ccys = [c.strip() for c in currencies.split(",")] if currencies else None
    return gan.currency_deposits(target_usd, ccys)


@router.get("/network/targets")
async def network_targets(size: float = Query(..., gt=0)):
    """The structured daily/weekly/monthly %-and-$ targets for one account size."""
    from gold import account_network as gan
    return gan.structured_targets(size)


@router.get("/budget")
async def budget(db: AsyncSession = Depends(get_db)):
    """Weekly trade budget board — this week's per-tier counts vs the cadence caps
    (swing 1 · intraday 5 · intrasession 5 · crt 10 · sniper 5 · prelondon 5 · sd_fade 3)."""
    from gold import trade_budget as tb
    since = tb.week_start()
    positions = await gp.list_positions(db, limit=300)
    return tb.budget_status(tb.count_by_tier(positions, since))


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


@router.get("/backtest")
async def backtest(horizon: int = Query(7, ge=1, le=30),
                   require_discount: bool = Query(True),
                   bars: int = Query(400, ge=60, le=5000)):
    """Backtest the SWING tier on historical daily bars → expectancy + reflection."""
    from backtest.gold_tiers import backtest_swing
    daily = await fetch_ohlc("XAU/USD", "1day", bars)
    if len(daily) < 30:
        raise HTTPException(status_code=502, detail="not enough XAU/USD daily history")
    return backtest_swing(daily, horizon=horizon, require_discount=require_discount)


@router.get("/backtest/retracement")
async def backtest_retracement_ep(interval: str = Query("4h", description="HTF: 4h / 1h / 1day"),
                                  bars: int = Query(300, ge=40, le=2000),
                                  lookahead: int = Query(6, ge=1, le=30),
                                  tp_r: float = Query(2.0, gt=0)):
    """Backtest the 3-state retracement classifier over history — SELL-the-OTE and
    scalp-the-bounce win-rate / avg-R, so the edge is known BEFORE it trades live
    paper. Uses the fused HTF ORB bias as the trend filter."""
    from gold import retracement as gret
    from gold import radar as grd
    ohlc = await fetch_ohlc("XAU/USD", interval, bars)
    if len(ohlc) < 40:
        raise HTTPException(status_code=502, detail="not enough XAU/USD HTF bars")
    htf_bias = None
    try:
        daily = await fetch_ohlc("XAU/USD", "1day", 90)
        htf_bias = grd.combine_htf(
            daily=grd.order_blocks(daily, timeframe="1D") if len(daily) >= 8 else []
        ).get("htf_bias")
    except Exception:
        pass
    return gret.backtest_retracement(ohlc, lookahead=lookahead, htf_bias=htf_bias, tp_r=tp_r)


@router.get("/backtest/intraday")
async def backtest_intraday_ep(horizon: int = Query(8, ge=1, le=48),
                               require_discount: bool = Query(True),
                               h1_bars: int = Query(1500, ge=100, le=5000),
                               daily_bars: int = Query(120, ge=30, le=1000)):
    """Backtest the INTRADAY + INTRASESSION tiers on H1 history → per-tier expectancy
    (P3 — the numbers that fit the STRATOPS weights)."""
    from backtest.gold_tiers import backtest_intraday
    from services.ohlc_service import fetch_hourly_raw
    h1 = await fetch_hourly_raw("XAU/USD", timezone="UTC", outputsize=h1_bars)
    daily = await fetch_ohlc("XAU/USD", "1day", daily_bars)
    if len(h1) < 100 or len(daily) < 30:
        raise HTTPException(status_code=502, detail="not enough XAU/USD H1/daily history")
    return backtest_intraday(h1, daily, horizon=horizon, require_discount=require_discount)


@router.get("/objective")
async def objective(price: float = Query(None, description="price to frame (else live)")):
    """CBDR range-to-range campaign objective — the next-range target + leg."""
    from gold.objective import campaign_objective
    if price is None:
        try:
            price = await get_forex_price("XAU/USD")
        except Exception:
            price = None
    if price is None:
        raise HTTPException(status_code=502, detail="could not fetch XAU/USD price")
    return campaign_objective(price)


@router.get("/zones")
async def zones(price: float = Query(None, description="price to frame (else live)")):
    """Named OB zones + the zone-to-zone pip budget (how many pips to the next zone
    each way, and the round-trip bag)."""
    from gold import zones as gz
    if price is None:
        try:
            price = await get_forex_price("XAU/USD")
        except Exception:
            price = None
    if price is None:
        raise HTTPException(status_code=502, detail="could not fetch XAU/USD price")
    return {"zones": gz.ZONES, "here": gz.zone_for(price), "budget": gz.zone_budget(price)}


@router.get("/zones/stack")
async def zones_stack(zone: str = Query(..., description="named zone, e.g. ob_3840"),
                      balance: float = Query(5000, gt=0),
                      risk_usd: float = Query(20.0, gt=0),
                      layers: int = Query(3, ge=1, le=5),
                      target: float = Query(None, description="optional TP price")):
    """Build a sniper limit stack for a named zone — 2-5 layered limits with one
    shared stop, the whole stack sized to stay inside the risk budget/exposure cap."""
    from gold import zones as gz
    return gz.sniper_stack(zone, balance=balance, risk_usd=risk_usd,
                           layers=layers, target_price=target)


@router.post("/zones/digest")
async def zones_digest(balance: float = Query(5000, gt=0),
                       risk_usd: float = Query(20.0, gt=0),
                       touch_only: bool = Query(True,
                           description="only send when a zone is armed (price touched)"),
                       notify: bool = Query(True, description="push to Telegram"),
                       price: float = Query(None, description="override live price")):
    """Live buy-limit / sell-limit plan (entry ladder, SL, TP, HTF alignment) for
    the flanking OB zones. ``touch_only`` (default) fires the alert only when price
    has come within the touch band of a zone — the real-time 'price hit our point'
    trigger. Cron-friendly: schedule it and it stays quiet until a level is armed."""
    from gold import zones as gz
    if price is None:
        try:
            price = await get_forex_price("XAU/USD")
        except Exception:
            price = None
    if price is None:
        raise HTTPException(status_code=502, detail="could not fetch XAU/USD price")
    plan = gz.zone_plan(price, balance=balance, risk_usd=risk_usd)
    if touch_only and not plan["armed"]:
        return {"sent": False, "armed": False, "price": plan["price"],
                "reason": "no zone armed — price not at a level yet", "plan": plan}
    sent = await gold_scan._tg(gz.format_zone_digest(plan)) if notify else False
    return {"sent": sent, "armed": plan["armed"], "plan": plan}


@router.get("/b2b")
async def b2b(bars: int = Query(30, ge=4, le=200, description="how many 4H candles"),
              mode: str = Query("confirmed", description="confirmed (after 8h) | armed (at sweep)")):
    """4H b2b bomber — 1-5-9 liquidity sweep + 8h back-to-back continuation, the
    swing-trade confluence anchored to the 00:00 / 14:00 (UTC-4) session opens.
    ``mode=armed`` fires the swing the moment candle 1 sweeps (enter early, ride 5&9)."""
    from services.ohlc_service import fetch_ohlc_raw
    from gold.b2b import b2b_bomber, b2b_armed
    ohlc = await fetch_ohlc_raw("XAU/USD", interval="4h", outputsize=bars)
    if len(ohlc) < 4:
        raise HTTPException(status_code=502, detail="not enough XAU/USD 4H bars")
    return b2b_armed(ohlc) if mode == "armed" else b2b_bomber(ohlc)


@router.get("/venom")
async def venom(now_utc4: str = Query(None, description="override, ISO 'YYYY-MM-DD HH:MM' UTC-4")):
    """Venom — the fractal AMD clock (Accumulation/Manipulation/Distribution) across
    intraday sessions / weekday / week-of-month, with the confluence conviction."""
    import datetime as _dt
    from gold import venom as gv
    now = None
    if now_utc4:
        try:
            now = _dt.datetime.fromisoformat(now_utc4)
        except Exception:
            now = None
    return gv.venom_read(now)


@router.post("/confirm")
async def confirm(balance: float = Query(5000, gt=0),
                  risk_usd: float = Query(20.0, gt=0),
                  window: str = Query("prelondon"),
                  deploy: bool = Query(False, description="open the confirmed entry (paper)"),
                  notify: bool = Query(True),
                  db: AsyncSession = Depends(get_db)):
    """Take the trade on the sweep-and-reject: watch the CBDR ±1/±1.5SD levels, fire
    only when price sweeps a level and CLOSES BACK INSIDE on the M15 — alert, and
    (deploy) open it with the stop beyond the swept wick."""
    from services.confirm_service import confirm as run_confirm
    return await run_confirm(balance=balance, risk_usd=risk_usd, window=window,
                             notify=notify, deploy=deploy, db=db)


@router.get("/radar")
async def radar(interval: str = Query("1day", description="HTF: 1day / 4h"),
                bars: int = Query(60, ge=8, le=300),
                price: float = Query(None, description="override live price")):
    """OB radar — HTF bullish/bearish order blocks (last up/down-close wick+close),
    which one price is retesting now, the trade bias, and the continuity TP ladder."""
    from gold import radar as gr
    ohlc = await fetch_ohlc("XAU/USD", interval, max(bars, 20))
    if len(ohlc) < 8:
        raise HTTPException(status_code=502, detail="not enough XAU/USD HTF bars")
    if price is None:
        try:
            price = await get_forex_price("XAU/USD")
        except Exception:
            price = ohlc[-1][4]
    return gr.radar_scan(ohlc, float(price))


@router.post("/radar/continuity")
async def radar_continuity(sell: str = Query(None, description="comma prices e.g. 4135,4075,4000,3885"),
                           buy: str = Query(None, description="comma prices e.g. 4195,4275,4380")):
    """Set the continuity TP ladder (the risk book)."""
    from gold import radar as gr
    s = [float(x) for x in sell.split(",")] if sell else None
    b = [float(x) for x in buy.split(",")] if buy else None
    return gr.set_continuity(sell=s, buy=b)


@router.get("/radar/htf")
async def radar_htf():
    """Multi-timeframe OB map — daily / weekly / monthly order blocks fused into one
    HTF bias, then combined with the weekly profile for the conviction read."""
    from gold import radar as gr
    daily = await fetch_ohlc("XAU/USD", "1day", 90)
    weekly = await fetch_ohlc("XAU/USD", "1week", 60)
    monthly = await fetch_ohlc("XAU/USD", "1month", 48)
    d_ob = gr.order_blocks(daily, timeframe="1D") if len(daily) >= 8 else []
    w_ob = gr.order_blocks(weekly, timeframe="1W") if len(weekly) >= 8 else []
    m_ob = gr.order_blocks(monthly, timeframe="1M") if len(monthly) >= 8 else []
    combined = gr.combine_htf(daily=d_ob, weekly=w_ob, monthly=m_ob)
    # weekly profile direction for the fusion
    wk_bias = None
    try:
        from gold.ict import classify_week
        wk = classify_week(daily)
        wk_bias = (wk or {}).get("bias")
    except Exception:
        pass
    fusion = gr.fuse_with_weekly_profile(combined["htf_bias"], wk_bias)
    return {"combined": combined, "weekly_profile_bias": wk_bias, "fusion": fusion,
            "fresh": {"daily": gr.unmitigated(d_ob), "weekly": gr.unmitigated(w_ob),
                      "monthly": gr.unmitigated(m_ob)}}


@router.get("/warthog")
async def warthog(interval: str = Query("1h", description="HTF: 1h / 4h"),
                  bars: int = Query(80, ge=8, le=300),
                  side: str = Query(None, description="force long/short (else BMS trend)")):
    """Warthog — HTF liquidity sweep + OTE catapult: the swept high/low, the BMS
    trend, and the OTE continuation entry (stop beyond the swept extreme) toward the
    next liquidity pool. 1H and above."""
    from services.ohlc_service import fetch_ohlc_raw
    from gold.warthog import warthog as wh, to_ohlc
    raw = await fetch_ohlc_raw("XAU/USD", interval=interval, outputsize=bars)
    if len(raw) < 8:
        raise HTTPException(status_code=502, detail="not enough XAU/USD HTF bars")
    return wh(to_ohlc(raw), side=side)


@router.get("/retracement")
async def retracement(interval: str = Query("4h", description="HTF for the impulse leg: 4h / 1h / 1day"),
                      bars: int = Query(40, ge=8, le=300),
                      price: float = Query(None, description="override live price"),
                      window: str = Query("prelondon", description="CBDR window for the discount extreme")):
    """Live retracement read — which of the THREE states are we in RIGHT NOW?

      SELL-the-OTE     — down-leg retraced into OTE 62–79%, swept a high + rejected,
                         HTF not bullish → sell the top with the trend (full size).
      scalp-the-bounce — swept a low + reclaimed at a −1SD/−1.5SD extreme or fresh
                         buy OB → a small range-fade scalp (never a trend long).
      LEAVE            — the dangerous middle (30–50%, no sweep-reject) → stand down.

    Makes the system's decision visible at a glance so a retracement in a bullish
    leg is never sold (and a bounce is never held as a trend long)."""
    from services import retracement_service as rsvc
    read = await rsvc.live_read(gold_price=price, interval=interval, bars=bars,
                                window=window)
    if read.get("reason", "").startswith("not enough"):
        raise HTTPException(status_code=502, detail="not enough XAU/USD HTF bars")
    return read


@router.post("/retracement")
async def retracement_alert(interval: str = Query("4h"),
                            window: str = Query("prelondon"),
                            notify: bool = Query(True),
                            force: bool = Query(False, description="send even if unchanged"),
                            deploy: bool = Query(False, description="open the SELL_OTE card (paper) on a fresh transition"),
                            balance: float = Query(5000, gt=0),
                            risk_usd: float = Query(20.0, gt=0),
                            db: AsyncSession = Depends(get_db)):
    """Retracement-state transition alert — fires ONE Telegram when the state flips
    (LEAVE→SELL_OTE, etc). Cron-friendly dedup (quiet until it changes). With
    ``deploy`` it also opens the SELL_OTE entry as a paper position on the flip."""
    from services import retracement_service as rsvc
    return await rsvc.state_alert(interval=interval, window=window, notify=notify,
                                  force=force, deploy=deploy, db=db,
                                  balance=balance, risk_usd=risk_usd)


@router.get("/volatility")
async def volatility(interval: str = Query("4h"), bars: int = Query(120, ge=20, le=500),
                     price: float = Query(None)):
    """Volatility engine (B9) — ATR, realized vol, regime percentile, expected range."""
    from gold import volatility as gv
    ohlc = await fetch_ohlc("XAU/USD", interval, bars)
    if len(ohlc) < 20:
        raise HTTPException(status_code=502, detail="not enough XAU/USD bars")
    return gv.volatility_read(ohlc, price)


@router.get("/intermarket")
async def intermarket(interval: str = Query("1day"), bars: int = Query(60, ge=20, le=300)):
    """Intermarket intelligence (B11) — DXY/yields/oil/SPX/silver correlation matrix
    + one net-correlation score (confirming / diverging / mixed)."""
    from services.intermarket_service import intermarket_matrix
    return await intermarket_matrix("XAU/USD", interval, bars)


@router.get("/trap")
async def trap(level: float = Query(..., description="the level being tested"),
               interval: str = Query("1h"), bars: int = Query(10, ge=3, le=50),
               side: str = Query(None, description="optional: confirm sweep-reject long/short")):
    """Trap detection (B10) — conditional-probability read (clean-breakout / bull-trap
    / bear-trap / capitulation) for the most recent test of a level."""
    from gold import trap_probability as gt
    ohlc = await fetch_ohlc("XAU/USD", interval, max(bars, 4))
    if side:
        return gt.trap_from_sweep(ohlc, level, side)
    return gt.trap_probabilities(ohlc, level)


@router.get("/cot-projection")
async def cot_projection():
    """COT projection (B7) — the official net projected forward past the 3-5 day lag
    using price action + options flow."""
    from services.cot_projection_service import project_cot
    return await project_cot("XAU/USD")


@router.get("/events")
async def events():
    """Event horizon (B14) — the live tier-1 calendar mapped to impact/decay/stack."""
    from gold import event_horizon as eh
    from services.news_guard import high_impact_calendar
    import datetime as _dt
    cal = await high_impact_calendar()
    now = _dt.datetime.now(_dt.timezone.utc)
    evs = []
    for ev in cal or []:
        try:
            d = _dt.datetime.fromisoformat(ev["date"]).replace(tzinfo=_dt.timezone.utc)
            evs.append({"name": ev.get("event") or "", "ccy": ev.get("ccy"),
                        "hours_until": (d - now).total_seconds() / 3600.0})
        except Exception:
            continue
    return eh.event_horizon(evs)


@router.get("/kingdom-report")
async def kingdom_report(price: float = Query(None, description="override live price"),
                         cbdr_high: float = Query(None, description="daily CBDR high (ingestion)"),
                         cbdr_low: float = Query(None, description="daily CBDR low (ingestion)"),
                         interval: str = Query("4h"),
                         db: AsyncSession = Depends(get_db)):
    """The full 14-branch Kingdom intelligence report — assembles every branch
    (macro, CBDR, liquidity, SMC, options, COT+projection, central bank, volatility,
    trap, intermarket, regime checklist, command, event horizon) into one structured
    JSON with the Kingdom Consensus table + Vaultum Directive. Feed daily CBDR
    high/low for the ingestion protocol. Read-only."""
    from services.kingdom_service import kingdom_report as _kr
    return await _kr(db, "XAU/USD", price, cbdr_high, cbdr_low, interval)


@router.post("/kingdom-report")
async def kingdom_report_post(price: float = Query(None),
                              cbdr_high: float = Query(None, description="daily CBDR high"),
                              cbdr_low: float = Query(None, description="daily CBDR low"),
                              interval: str = Query("4h"),
                              notify: bool = Query(True, description="push digest to Telegram"),
                              db: AsyncSession = Depends(get_db)):
    """Generate the 14-branch report and push the headline digest to Telegram —
    the daily automated run. Feed daily CBDR high/low for the ingestion protocol."""
    from services.kingdom_service import kingdom_alert
    return await kingdom_alert(db, "XAU/USD", price, cbdr_high, cbdr_low, interval, notify)


@router.get("/volume-profile")
async def volume_profile(interval: str = Query("1day"), bars: int = Query(60, ge=5, le=500),
                         bins: int = Query(30, ge=10, le=100), price: float = Query(None)):
    """Volume/TPO profile (B2) — POC / VAH / VAL + where price sits vs value."""
    from gold import volume_profile as gvp
    ohlc = await fetch_ohlc("XAU/USD", interval, bars)
    if len(ohlc) < 3:
        raise HTTPException(status_code=502, detail="not enough XAU/USD bars")
    return gvp.profile_read(ohlc, price, bins)


@router.get("/scenario")
async def scenario(price: float = Query(None), window: str = Query("prelondon"),
                   interval: str = Query("1h")):
    """Four-scenario read (B13) — liquidity-sweep / direct-expansion / deep-hunt /
    dead-cat, with the execution lean, off the live CBDR box."""
    from gold import scenarios as gsc
    from gold import radar as grd
    from services.recon_service import _live_box
    box = await _live_box(window)
    if box is None:
        raise HTTPException(status_code=502, detail="no CBDR box available")
    if price is None:
        try:
            price = await get_forex_price("XAU/USD")
        except Exception:
            raise HTTPException(status_code=502, detail="no price")
    htf_bias = None
    try:
        daily = await fetch_ohlc("XAU/USD", "1day", 90)
        htf_bias = grd.combine_htf(daily=grd.order_blocks(daily, timeframe="1D")
                                   if len(daily) >= 8 else []).get("htf_bias")
    except Exception:
        pass
    bars = await fetch_ohlc("XAU/USD", interval, 6)
    return gsc.classify_scenario(box, float(price), htf_bias, bars)


@router.get("/recon")
async def recon_get(gold: float = Query(None, description="override gold price"),
                    dxy: float = Query(None, description="live DXY price (optional)"),
                    window: str = Query("prelondon", description="CBDR window for the ±SD map")):
    """Drone fib-recon sweep — the fused gold + DXY board (HTF ladder, OB zones,
    CBDR deviation extreme, DXY lock, 4H b2b bomber) with any armed setups. Read-only."""
    from services.recon_service import recon
    return await recon(dxy_price=dxy, gold_price=gold, window=window,
                       notify=False, armed_only=False)


@router.post("/recon")
async def recon_post(dxy: float = Query(None, description="live DXY price (optional)"),
                     window: str = Query("prelondon"),
                     armed_only: bool = Query(True, description="alert only on an armed board"),
                     notify: bool = Query(True), force: bool = Query(False)):
    """Run the recon sweep and push the board to Telegram when a setup is armed
    (best-effort dedup — cron-friendly, stays quiet until the board changes)."""
    from services.recon_service import recon
    return await recon(dxy_price=dxy, window=window, notify=notify,
                       armed_only=armed_only, force=force)


@router.get("/flip-ladder")
async def flip_ladder(deposit: float = Query(700, gt=0),
                      denom: str = Query("USD", description="cent | USD | KES | KWD"),
                      runs: int = Query(None, description="override run count")):
    """Account-tier flip ladder for a deposit — cent flipper / middle / flipper,
    with the run cadence, pip targets, and projected balance curve."""
    from gold import flip_ladders as fl
    return fl.plan(deposit, denom=denom, runs=runs)


@router.get("/stratops")
async def stratops(balance: float = Query(5000, gt=0),
                   risk_usd: float = Query(20.0, gt=0),
                   db: AsyncSession = Depends(get_db)):
    """STRATOPS engagement list — muster every live candidate, score by confluence
    toward the campaign objective, and allocate under the exposure cap."""
    from services.stratops_service import muster
    return await muster(db, balance=balance, risk_usd=risk_usd)


@router.post("/stratops/deploy")
async def stratops_deploy(balance: float = Query(5000, gt=0),
                          risk_usd: float = Query(20.0, gt=0),
                          db: AsyncSession = Depends(get_db)):
    """P4 paper-run: muster, allocate, and OPEN each allocated candidate as a
    tracked position (source stratops_paper) — the scorecard then measures STRATOPS
    itself. Schedule at the entry sessions; run until the verdict is GREEN."""
    from services.stratops_service import muster
    return await muster(db, balance=balance, risk_usd=risk_usd, deploy=True)


@router.post("/stratops/fit")
async def stratops_fit(h1_bars: int = Query(3000, ge=100, le=5000),
                       daily_bars: int = Query(250, ge=30, le=1000)):
    """P3 loop: re-run the tier backtests on live history and refit the STRATOPS
    tier factors from the measured reflection confidence."""
    from backtest.gold_tiers import backtest_intraday, backtest_swing
    from services.ohlc_service import fetch_hourly_raw
    from gold.stratops import fit_tier_factors
    h1 = await fetch_hourly_raw("XAU/USD", timezone="UTC", outputsize=h1_bars)
    daily = await fetch_ohlc("XAU/USD", "1day", max(daily_bars, 60))
    if len(h1) < 100 or len(daily) < 30:
        raise HTTPException(status_code=502, detail="not enough XAU/USD history to fit")
    intra = backtest_intraday(h1, daily)
    swing = backtest_swing(daily)
    fit = fit_tier_factors(intra, swing)
    return {**fit, "samples": {"intraday_trades": intra["trades"], "swing_trades": swing["trades"]}}


@router.get("/timeline")
async def timeline(price: float = Query(None, description="price to locate (else live)")):
    """HTF timeline identifier — the daily named-zone ladder + where price sits +
    the smaller-timeframe bias it implies."""
    from gold.timeline import (htf_ladder, locate, fib_levels, weekly_decision,
                               cycle_status, HTF_ANCHOR)
    if price is None:
        try:
            price = await get_forex_price("XAU/USD")
        except Exception:
            price = None
    out = {"anchor": HTF_ANCHOR, "ladder": htf_ladder(), "fibs": fib_levels()}
    if price is not None:
        out["located"] = locate(price)
        out["weekly_decision"] = weekly_decision(price)
        out["cycle"] = cycle_status(price)
    return out


@router.get("/daily-map")
async def daily_map(open_: float = Query(None, alias="open"),
                    close: float = Query(None)):
    """Daily mean-range map: (open+close)/2 of the closed daily candle → the
    $25…$250 target collection framed by the HTF structure. Omit open/close to
    use the last completed daily bar."""
    from gold.timeline import daily_mean_map
    if open_ is None or close is None:
        daily = await fetch_ohlc("XAU/USD", "1day", 3)
        if len(daily) < 2:
            raise HTTPException(status_code=502, detail="could not fetch XAU/USD daily bars")
        d = daily[-2] if close is None else daily[-1]     # last COMPLETED candle
        open_, close = d[1], d[4]
    return daily_mean_map(open_, close)


@router.get("/session-levels")
async def session_levels():
    """8-hour session range + the CBDR SD ladder (0.5-step, with mean) + the current
    protraction (session sweep+reversal) read — the intraday liquidity map."""
    from gold.session_levels import eight_hour_range, detect_protraction
    from services.cbdr_service import fetch_cbdr_window
    from cbdr.engine import sd_ladder
    from services.gold_liquidity import liquidity_map
    h1 = await fetch_ohlc("XAU/USD", "1h", 24)
    bars = [(d, o, h, l, c) for (d, o, h, l, c) in h1]
    rng8 = eight_hour_range(bars)
    protr = detect_protraction(bars, rng8["high"], rng8["low"]) if rng8 else None
    cb = await fetch_cbdr_window("XAU/USD", "cbdr")
    ladder = sd_ladder(cb["high"], cb["low"]) if cb else None
    liquidity = await liquidity_map("XAU/USD")   # 1am/7am/8h/PDH/PDL/PWH/PWL
    return {"instrument": "XAU/USD", "session_8h": rng8, "liquidity": liquidity,
            "protraction": protr, "sd_ladder": ladder}


@router.get("/macro")
async def macro():
    """The standing Q3/Q4 macro read that gates gold signals."""
    return gmacro.macro_read()


@router.post("/macro/feed")
async def macro_feed(
        cot_noncomm_net: float = Query(None), cot_open_interest: float = Query(None),
        etf_holdings_t: float = Query(None), cb_purchases_2026e_t: float = Query(None),
        etf_h1_flows_usd_bn: float = Query(None), gold_price: float = Query(None),
        real_rate_direction: str = Query(None, description="rising | falling | flat"),
        fed_cycle: str = Query(None, description="hiking | cutting | hold_hawkish_risk"),
        cb_survey_conviction: str = Query(None, description="strong | moderate | weak"),
        etf_flow_direction: str = Query(None, description="accumulation | easing_outflows | outflows"),
        as_of: str = Query(None, description="report date e.g. 2026-07-21")):
    """Operator-feed the WGC/macro audit numbers (the manual part of the regime
    stack): CB tonnage, ETF holdings/flows, COT net/OI, real-rate & Fed reads.
    Returns the refreshed fused regime verdict."""
    gpa.feed(as_of=as_of, cot_noncomm_net=cot_noncomm_net,
             cot_open_interest=cot_open_interest, etf_holdings_t=etf_holdings_t,
             cb_purchases_2026e_t=cb_purchases_2026e_t,
             etf_h1_flows_usd_bn=etf_h1_flows_usd_bn, gold_price=gold_price)
    gcycle.feed_inputs(real_rate_direction=real_rate_direction, fed_cycle=fed_cycle,
                       cb_survey_conviction=cb_survey_conviction,
                       etf_flow_direction=etf_flow_direction, as_of=as_of)
    return {"snapshot_as_of": gpa.SNAPSHOT["as_of"], "regime": gcycle.regime_read()}


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
    return {"dollar_regime": gdxy.dollar_regime(price), "gold_implication": gdxy.gold_from_dollar(price),
            "gold_long_lock": gdxy.dxy_flip_status(price)}


@router.post("/dxy/flip")
async def dxy_flip(price: float = Query(None, description="live DXY weekly close (optional)"),
                   notify: bool = Query(True, description="push to Telegram on a state change"),
                   force: bool = Query(False, description="send even if the lock state is unchanged")):
    """DXY-flip alert — the 2026 unlock signal. Fires ONE Telegram when gold longs
    transition locked↔unlocked (dollar flips bearish/bullish). Cron-friendly: stays
    quiet until the state actually changes."""
    from services.dxy_flip_service import flip_alert
    return await flip_alert(dxy_price=price, notify=notify, force=force)


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
