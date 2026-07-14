"""Gold session Opening-Range breakout scan — Asia / London / NY, TPO-confirmed.

Composes the Phase-1 pieces into a fired, sized signal:
  1. weekly ICT profile (direction / bias)                       gold.ict
  2. session Opening Range + breakout + retest                   indicators.opening_range
  3. TPO value-area confirmation (leaving balance)               indicators.profile
  4. session_breakout tier (intrasession / intraday / swing)     gold.trade_types
  5. money-first sizing off the OR entry+stop                    gold.signal.assemble_structured
  6. fib trend-TP ladder + scale-out                             structure_service / trade_executor

Boot-safe: returns NO TRADE (not an error) whenever a layer says wait or data is
missing. Real volume isn't available for spot gold, so confirmation is TPO
(time-at-price); swap in MT5 tick volume later without touching this flow.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from services.ohlc_service import fetch_ohlc
from services.gold_scan import _tg
from utils.price_fetcher import get_forex_price
from gold.ict import classify_week
from gold.signal import assemble_structured, format_card
from gold.trade_types import session_breakout_plan
from indicators.opening_range import opening_range, SESSIONS
from indicators.profile import tpo_profile, value_area, breakout_confirmed

# Session UTC spans (start inclusive, end exclusive) for scoping the profile bars.
_SPAN = {"asia": (0, 8), "london": (8, 13), "ny": (13, 21)}


def _auto_session(now: _dt.datetime) -> str:
    h = now.hour
    if 0 <= h < 8:
        return "asia"
    if 8 <= h < 13:
        return "london"
    return "ny"                       # 13–24: NY session / close


def _in_session(bar, sess: str) -> bool:
    start, end = _SPAN.get(sess, (13, 21))
    ts = bar[0]
    hour = ts.hour if hasattr(ts, "hour") else int(str(ts).replace("T", " ").split(" ")[1][:2])
    return start <= hour < end


async def scan(balance: float = 5000.0, risk_usd: float = 20.0,
               session: Optional[str] = None, tier: str = "6",
               bin_size: float = 0.5, buffer: float = 1.0, or_hours: int = 1,
               require_retest: bool = True, require_profile: bool = True,
               execute: bool = False, notify: bool = False, db=None) -> dict:
    daily = await fetch_ohlc("XAU/USD", "1day", 25)
    if len(daily) < 3:
        return {"signal": "NO TRADE", "reason": "no XAU/USD daily bars"}
    profile = classify_week(daily)
    bias = (profile or {}).get("bias")

    now = _dt.datetime.now(_dt.timezone.utc)
    sess = (session or _auto_session(now)).lower()
    if sess not in SESSIONS:
        return {"signal": "NO TRADE", "reason": f"unknown session {sess!r}"}

    m15 = await fetch_ohlc("XAU/USD", "15min", 120)
    if len(m15) < 4:
        return {"signal": "NO TRADE", "reason": "no XAU/USD 15m bars", "session": sess}

    orr = opening_range(m15, session=sess, or_hours=or_hours, buffer=buffer,
                        require_retest=require_retest)

    # live entry price (for the leaving-balance test); fall back to last close
    try:
        price = await get_forex_price("XAU/USD")
    except Exception:
        price = None
    if price is None:
        price = m15[-1][4]

    # TPO value area from the session's BALANCE — the bars that closed INSIDE the
    # opening range (the coil), NOT the expansion leg. Profiling the balance keeps
    # the POC in the range, so "price left the value area" is a real expansion read.
    sess_bars = [b for b in m15 if _in_session(b, sess)]
    if orr.get("or_high") is not None and orr.get("or_low") is not None:
        balance_bars = [b for b in sess_bars if orr["or_low"] <= b[4] <= orr["or_high"]]
    else:
        balance_bars = sess_bars
    prof = tpo_profile(balance_bars or sess_bars, bin_size=bin_size)
    va = value_area(prof)
    if orr.get("ok"):
        conf = breakout_confirmed(orr.get("side") or "long", price, va,
                                  orr.get("or_high"), orr.get("or_low"))
    else:
        conf = {"ok": False, "reason": "no OR breakout to confirm"}
    if not require_profile:
        conf = {"ok": True, "reason": "profile confirmation skipped"}

    plan = session_breakout_plan(orr, conf, bias, session=sess)
    if plan["signal"] not in ("LONG", "SHORT"):
        return plan

    # money-first sizing from the OR entry + opposite-extreme stop
    card = assemble_structured(profile, plan["entry"], plan["stop"], balance,
                               tier=tier, risk_usd=risk_usd, rr=plan["rr"])
    if card.get("signal") == "NO TRADE":
        card["layers"] = plan["layers"]
        return card
    card["signal"] = plan["signal"]
    card["trade_type"] = plan["trade_type"]
    card["session"] = sess
    card["kind"] = plan["kind"]                    # limit at the retest boundary
    card["layers"] = plan["layers"]
    card["justification"] = plan["reason"]
    card["value_area"] = va

    # fib trend-TP ladder off the OR range → scale-out
    try:
        from services import structure_service as ss
        tl = await ss.trend_targets("XAU/USD", bias, card.get("entry", plan["entry"]),
                                    ref_high=orr.get("or_high"), ref_low=orr.get("or_low"))
        if tl:
            card["trend_targets"] = tl
    except Exception:
        pass

    if execute and db is not None:
        try:
            from services import trade_executor as te
            orders = te.build_orders(card, source="gold_session_breakout")
            if orders:
                card["queued_orders"] = await te.enqueue_all(db, orders)
        except Exception:
            pass

    if notify:
        card["sent"] = await _tg(format_card(card))
    return card
