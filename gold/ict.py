"""ICT Weekly Profiles — classify the current week into 1 of 12 profiles (pure).

From the ICT Weekly Profiles doc. Each profile is a typical weekly price path;
naming the active one gives an auditable trend justification + a directional bias
that the gold signal engine folds into confluence.

  I   Classic Tuesday Low            (bullish)   Tue drops into HTF discount → LONG
  II  Classic Tuesday High           (bearish)   Tue rises into HTF premium  → SHORT
  III Wednesday Low                  (bullish)   Wed drops into discount     → LONG
  IV  Wednesday High                 (bearish)   Wed rises into premium      → SHORT
  V   Consolidation Thu Bull Reversal(bullish)   Mon-Wed range, Thu runs low & rejects → LONG
  VI  Consolidation Thu Bear Reversal(bearish)   Mon-Wed range, Thu runs high & rejects → SHORT
  VII Midweek Rally                  (bullish)   range then expand higher into Fri → LONG cont.
  VIII Midweek Decline               (bearish)   range then expand lower into Fri → SHORT cont.
  IX  Seek & Destroy Bull Friday     (avoid)     both-side stop runs, low prob
  X   Seek & Destroy Bear Friday     (avoid)     both-side stop runs, low prob
  XI  Wednesday Weekly Bull Reversal (bullish)   Wed drive into discount, sweep sells, reverse up → LONG
  XII Wednesday Weekly Bear Reversal (bearish)   Wed drive into premium, sweep buys, reverse down → SHORT

Bars are (date, open, high, low, close), oldest-first. Needs ~20 daily bars so
the higher-timeframe premium/discount (equilibrium) can be estimated.
"""

from __future__ import annotations

import datetime as dt
from typing import List, Optional, Tuple

DatedOHLC = Tuple[dt.date, float, float, float, float]

PROFILES = {
    1: ("Classic Tuesday Low", "long"),
    2: ("Classic Tuesday High", "short"),
    3: ("Wednesday Low", "long"),
    4: ("Wednesday High", "short"),
    5: ("Consolidation Thursday Bullish Reversal", "long"),
    6: ("Consolidation Thursday Bearish Reversal", "short"),
    7: ("Consolidation Midweek Rally", "long"),
    8: ("Consolidation Midweek Decline", "short"),
    9: ("Seek and Destroy Bullish Friday", "neutral"),
    10: ("Seek and Destroy Bearish Friday", "neutral"),
    11: ("Wednesday Weekly Bullish Reversal", "long"),
    12: ("Wednesday Weekly Bearish Reversal", "short"),
}


def _monday_of(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def _result(pid: Optional[int], reason: str, ctx: dict) -> dict:
    name, bias = PROFILES.get(pid, ("No clear profile", "neutral"))
    return {"profile_id": pid, "profile": name, "bias": bias,
            "reason": reason, **ctx}


def classify_week(daily: List[DatedOHLC], htf_lookback: int = 20) -> Optional[dict]:
    """Name the active weekly profile from daily bars (current week vs HTF)."""
    if len(daily) < 3:
        return None
    last_date = daily[-1][0]
    weekday = last_date.weekday()                 # 0=Mon … 4=Fri
    monday = _monday_of(last_date)
    week = [b for b in daily if b[0] >= monday]
    if not week:
        return None

    # Higher-timeframe equilibrium (premium above / discount below).
    htf = daily[-htf_lookback:] if len(daily) >= htf_lookback else daily
    hi = max(b[2] for b in htf)
    lo = min(b[3] for b in htf)
    eq = (hi + lo) / 2.0
    close = week[-1][4]
    zone = "premium" if close > eq else "discount"

    # Where did the week's extremes form?
    wk_high = max(b[2] for b in week)
    wk_low = min(b[3] for b in week)
    high_day = [b[0].weekday() for b in week if b[2] == wk_high][0]
    low_day = [b[0].weekday() for b in week if b[3] == wk_low][0]
    last_open = week[-1][1]
    bull_candle = close >= last_open

    ctx = {
        "week_of": monday.isoformat(), "weekday": weekday,
        "htf_equilibrium": round(eq, 2), "zone": zone,
        "week_high": round(wk_high, 2), "week_low": round(wk_low, 2),
        "high_day": high_day, "low_day": low_day,
    }

    # Consolidation Mon..Wed = narrow range vs the HTF range.
    mon_wed = [b for b in week if b[0].weekday() <= 2]
    cons_range = (max(b[2] for b in mon_wed) - min(b[3] for b in mon_wed)) if mon_wed else 0
    consolidated = cons_range < 0.4 * (hi - lo) if (hi - lo) else False

    # --- rule cascade, most-specific first --------------------------------
    # Seek & destroy: BOTH sides of the week's range have been run for liquidity.
    # Detected on distinct days (a genuine two-sided sweep, not one wide bar) so it
    # fires MID-WEEK — from Wednesday on — not only on Friday.
    hi_days = {b[0].weekday() for b in week if b[2] >= wk_high * 0.999}
    lo_days = {b[0].weekday() for b in week if b[3] <= wk_low * 1.001}
    # Genuine two-sided sweep = the high and the low were run on DIFFERENT days
    # AND price has come back INSIDE the middle of the week's range (a ranging
    # market). A trending week also makes its extremes on different days, but its
    # close sits AT an extreme — that is a trend, not seek & destroy.
    wk_rng = wk_high - wk_low
    back_inside = wk_rng > 0 and abs(close - (wk_high + wk_low) / 2.0) <= 0.25 * wk_rng
    swept_both = back_inside and any(hd != ld for hd in hi_days for ld in lo_days)
    if swept_both and weekday >= 2:
        pid = 9 if close >= eq else 10
        return _result(pid, "both sides of the week swept (seek & destroy) — ranging, fade the extremes", ctx)
    if weekday >= 4 and consolidated:
        pid = 9 if close >= eq else 10
        return _result(pid, "Friday expansion after both-side stop runs — low probability, avoid", ctx)

    # Wednesday weekly reversal (XI/XII): Wed extreme in HTF array then reversed.
    if weekday == 2:
        if low_day == 2 and zone == "discount" and bull_candle:
            return _result(11, "Wed drove into discount, swept sells, reversing up", ctx)
        if high_day == 2 and zone == "premium" and not bull_candle:
            return _result(12, "Wed drove into premium, swept buys, reversing down", ctx)
        if low_day == 2:
            return _result(3, "Wednesday low of week in discount → long", ctx)
        if high_day == 2:
            return _result(4, "Wednesday high of week in premium → short", ctx)

    # Classic Tuesday high/low.
    if weekday == 1:
        if low_day == 1 and bull_candle:
            return _result(1, "Tuesday low of week into discount → long", ctx)
        if high_day == 1 and not bull_candle:
            return _result(2, "Tuesday high of week into premium → short", ctx)

    # Thursday consolidation reversal / midweek expansion.
    if weekday >= 3:
        if consolidated and low_day == weekday and bull_candle:
            return _result(5, "Consolidated Mon-Wed, Thu ran the low and rejected → long", ctx)
        if consolidated and high_day == weekday and not bull_candle:
            return _result(6, "Consolidated Mon-Wed, Thu ran the high and rejected → short", ctx)
        if zone == "premium" and close >= week[0][4]:
            return _result(7, "Midweek rally — expanding higher into Friday → long continuation", ctx)
        if zone == "discount" and close <= week[0][4]:
            return _result(8, "Midweek decline — expanding lower into Friday → short continuation", ctx)

    return _result(None, "no clear weekly profile yet — inside range / early week", ctx)


def confluence(profile_read: Optional[dict], side: str) -> str:
    """confirms / diverges / neutral for a proposed side vs the active profile."""
    if not profile_read or profile_read.get("bias") in (None, "neutral"):
        return "neutral"
    want = "long" if side.lower() in ("long", "buy") else "short"
    return "confirms" if profile_read["bias"] == want else "diverges"
