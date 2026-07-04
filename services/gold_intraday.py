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

    sig = assemble_intraday(profile, sess, fsig, entry, balance, tier=tier,
                            risk_usd=risk_usd, sl_pips=sl_pips, weekday_q=wq,
                            require_fld=require_fld,
                            require_distribution=require_distribution,
                            entry_read=entry_read)

    if notify and sig.get("signal") in ("LONG", "SHORT"):
        sig["sent"] = await _tg(format_card(sig))
    return sig
