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
               min_target_pips: float = 0.0, require_room: bool = True,
               adr_exhaustion: float = 0.85,
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

    # Scope to TODAY's bars — opening_range filters by hour only, so multi-day M15
    # would merge yesterday's opening window into today's OR. None of the sessions
    # wrap midnight (Asia 0-8, London 8-13, NY 13-21), so a UTC-date filter is exact.
    def _bar_date(b):
        ts = b[0]
        return ts.date() if hasattr(ts, "date") else str(ts)[:10]
    today = now.date()
    day_bars = [b for b in m15 if str(_bar_date(b)) == str(today)] or m15

    orr = opening_range(day_bars, session=sess, or_hours=or_hours, buffer=buffer,
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
    sess_bars = [b for b in day_bars if _in_session(b, sess)]
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

    # ---- QUALITY GATE: projected target size + ADR room-to-run ----------------
    # 500 pips = $50 (GOLD_PIP 0.10). Kill setups that can't PROJECT the target you
    # want, or whose day has no ROOM left to travel there (exhausted / capped).
    from indicators.atr import adr as _adr, percent_of_adr, room_to_run
    from gold.risk_engine import GOLD_PIP
    entry = card.get("entry", plan["entry"])
    side = "long" if plan["signal"] == "LONG" else "short"
    target_dist = min_target_pips * GOLD_PIP
    day_high, day_low = daily[-1][2], daily[-1][3]
    adr_val = _adr([h - l for (_d, _o, h, l, _c) in daily], n=5)
    pct = percent_of_adr(day_high - day_low, adr_val)
    room = room_to_run(entry, side, day_high, day_low, adr_val)
    # furthest projected target from the fib ladder (the runner)
    tl = card.get("trend_targets") or {}
    proj = max((abs(t["price"] - entry) for t in tl.get("targets", [])), default=None)
    card["room"] = {"adr": round(adr_val, 2) if adr_val else None,
                    "pct_adr_used": round(pct, 2) if pct is not None else None,
                    "room_to_run": room, "projection": round(proj, 2) if proj else None,
                    "target_needed": round(target_dist, 2) if target_dist else None}

    if min_target_pips > 0:
        have = proj if proj is not None else room
        if have is None or have < target_dist:
            card["signal"] = "NO TRADE"
            card["reason"] = (f"projects only ~{round(have, 1) if have else '?'} "
                              f"< {round(target_dist, 1)} target ({min_target_pips:g} pips)")
            return card

    if require_room and adr_val:
        # 1) Exhaustion — the day already travelled most of its average range, so a
        #    fresh continuation is low-probability regardless of target size.
        if pct is not None and pct > adr_exhaustion:
            card["signal"] = "NO TRADE"
            card["reason"] = f"day exhausted — {pct:.0%} of ADR (${adr_val:.0f}) already used"
            return card
        # 2) Room — only DEMAND full room-to-run for INTRADAY-sized targets (comfortably
        #    within one ADR). A ~500-pip ($50) target is a swing/runner that expands
        #    beyond a single day, so it's gated by projection + exhaustion, not room.
        intraday_sized = 0 < target_dist <= adr_val * 0.8
        if intraday_sized and room is not None and room < target_dist:
            card["signal"] = "NO TRADE"
            card["reason"] = (f"no room — only ${room:.0f} left in the day's ADR "
                              f"< ${target_dist:.0f} target")
            return card

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
