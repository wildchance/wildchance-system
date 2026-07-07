"""Real-time gold intraday scan — weekly profile × session quarter × Hurst FLD.

Fetches daily bars (weekly profile), H1 bars (FLD), reads the live session quarter
off the clock, and fires the fused intraday signal to Telegram. Boot-safe: returns
NO TRADE (not an error) when any layer says wait or data is missing.
"""

from __future__ import annotations

import datetime as _dt

from services.ohlc_service import fetch_ohlc
from services.gold_scan import _tg
from utils.price_fetcher import get_forex_price
from gold.ict import classify_week
from gold.quarterly_session import session_quarter, weekday_quarter
from gold.hurst import fld_signal
from gold.entry import refined_entry
from gold.intraday import assemble_intraday, format_card
from gold.risk_engine import GOLD_PIP
from gold.trade_types import classify_tier, tier_stop, tier_ref_range

# QT session quarter → its end hour UTC (Asia→8, London→13, NY→21, rollover→24).
_SESSION_END = {1: 8, 2: 13, 3: 21, 4: 24}


async def scan(balance: float = 5000.0, tier: str = "6", risk_usd: float = 20.0,
               sl_pips: float = 200.0, cycle_len: int = 20,
               require_fld: bool = True, require_distribution: bool = False,
               notify: bool = False) -> dict:
    daily = await fetch_ohlc("XAU/USD", "1day", 25)
    if len(daily) < 3:
        return {"signal": "NO TRADE", "reason": "no XAU/USD daily bars"}

    profile = classify_week(daily)
    now = _dt.datetime.now(_dt.timezone.utc)
    sess = session_quarter(now)
    wq = weekday_quarter(now)

    # FLD on H1 (intraday cycle); fall back to daily if H1 is thin.
    h1 = await fetch_ohlc("XAU/USD", "1h", max(cycle_len * 3, 60))
    closes = [c for (_d, _o, _h, _l, c) in h1] if len(h1) >= cycle_len + 2 \
        else [c for (_d, _o, _h, _l, c) in daily]
    fsig = fld_signal(closes, cycle_len if len(h1) >= cycle_len + 2 else 10)

    entry = None
    try:
        entry = await get_forex_price("XAU/USD")
    except Exception:
        entry = None
    if entry is None:
        entry = daily[-1][4]

    # Wade structure entry (BMS → OTE → OB/FVG) off the H1 bars, if a BMS exists.
    entry_read = None
    bias = (profile or {}).get("bias")
    if bias in ("long", "short") and len(h1) >= 8:
        ohlc = [(o, hh, ll, c) for (_d, o, hh, ll, c) in h1]
        entry_read = refined_entry(ohlc, bias, buffer=10 * GOLD_PIP)   # ~10-pip buffer

    # --- TRADE-TYPE TIER: profile (+ session) → swing / intraday / intrasession.
    # Each tier brings its own structural stop, R:R band, location range, horizon.
    #   weekly  = the week's hi/lo (from the profile)   → swing, 1:5–1:8
    #   day     = today's daily bar hi/lo               → intraday, 1:2–1:3
    #   session = the current session's ~6× H1 hi/lo    → intrasession, 1:3–1:5
    sess_bars = h1[-6:] if len(h1) >= 6 else h1
    ranges = {
        "weekly": ((profile or {}).get("week_high"), (profile or {}).get("week_low")),
        "day": (daily[-1][2] if daily else None, daily[-1][3] if daily else None),
        "session": (max((b[2] for b in sess_bars), default=None),
                    min((b[3] for b in sess_bars), default=None)),
    }
    tt = classify_tier(profile, sess)

    stop_override = None
    rr = (2, 3, 4)
    trade_type = None
    # Default location range = the day's range (fallback when no tier).
    ref_high = ranges["day"][0]
    ref_low = ranges["day"][1]
    if tt:
        trade_type = tt["trade_type"]
        rr = tt["rr"]
        stop_override = tier_stop(tt["sl_source"], bias, ranges)
        rh, rl = tier_ref_range(tt["sl_source"], ranges)
        if rh is not None and rl is not None:
            ref_high, ref_low = rh, rl

    sig = assemble_intraday(profile, sess, fsig, entry, balance, tier=tier,
                            risk_usd=risk_usd, sl_pips=sl_pips, weekday_q=wq,
                            require_fld=require_fld,
                            require_distribution=require_distribution,
                            entry_read=entry_read,
                            ref_high=ref_high, ref_low=ref_low,
                            stop_override=stop_override, rr=rr, trade_type=trade_type)

    # Horizon hint for the tracked-position opener (deadline computed at open time).
    if sig.get("signal") in ("LONG", "SHORT") and trade_type:
        sig["session_end_hour"] = _SESSION_END.get(sess.get("quarter"), 24)

    # NEWS gate — block same-day tier-1 (NFP/CPI/FOMC), flag within the window.
    if sig.get("signal") in ("LONG", "SHORT"):
        from services import news_guard
        today = now.date()
        block = await news_guard.news_flag(today, "XAU/USD", win=0)
        warn = block or await news_guard.news_flag(today, "XAU/USD")
        if warn:
            sig["news"] = warn
        if block:
            sig["signal"] = "NO TRADE"
            sig["reason"] = block
            return sig

    if notify and sig.get("signal") in ("LONG", "SHORT"):
        sig["sent"] = await _tg(format_card(sig))
    return sig
