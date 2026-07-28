"""Gold trade-type tiers — intraday / intrasession / swing (pure, stdlib-only).

"Profile drives type": the active ICT weekly profile decides which tier a gold
trade is, and each tier carries its own stop source, reward:risk band, and
holding horizon.

  TIER          profiles                      SL source        R:R band   horizon
  swing         reversals 1,2,5,6,11,12       weekly hi/lo     1:5–1:8    Mon close / Tue open
  intraday      cont. 3,4,7,8 in NY dist (Q3) day hi/lo        1:2–1:3    same-day (NY close)
  intrasession  cont. 3,4,7,8 in Asia (Q1)    session hi/lo    1:3–1:5    session end
  (none)        cont. in London manip (Q2)    — no entry (setup); pre-London limits cover it
  (none)        Seek & Destroy 9,10 / neutral — see seek_destroy_plan (extreme fades)

The R:R band becomes the laddered scale-out targets for that tier (e.g. a swing
carries TP at 5R/6R/7R/8R). Stops come from the *structure* of the tier's own
range — a swing stops beyond the week's extreme, an intrasession beyond the
session's — so tighter tiers get tighter stops and larger multiples fit inside
the same weekly move.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

# 10 pips of buffer at the default 0.10 gold pip = 1.00 in price.
DEFAULT_BUFFER = 1.0

REVERSAL = {1, 2, 5, 6, 11, 12}          # weekly-bias reversals → swing
CONTINUATION = {3, 4, 7, 8}              # directional/continuation → intraday/intrasession
SEEK_DESTROY = {9, 10}                    # both-side stop runs → no trade

TIERS = {
    "swing":        {"rr": (5, 6, 7, 8), "sl_source": "weekly",  "horizon": "weekly"},
    "intraday":     {"rr": (2, 3),       "sl_source": "day",     "horizon": "day"},
    "intrasession": {"rr": (3, 4, 5),    "sl_source": "session", "horizon": "session"},
}


def classify_tier(profile: dict, session_q: Optional[dict] = None) -> Optional[dict]:
    """Which trade-type tier does this profile+session imply? None = stand aside.

    Reversal profiles are swings (any session). Continuation profiles are intraday
    in the NY distribution (Q3) and intrasession in the Asia accumulation (Q1);
    during London manipulation (Q2) or rollover (Q4) they stand aside — London is
    the Judas/setup covered by the pre-London CBDR limits. Seek & Destroy and
    non-directional profiles return None (see seek_destroy_plan).
    """
    bias = (profile or {}).get("bias")
    if bias not in ("long", "short"):
        return None
    pid = (profile or {}).get("profile_id")
    if pid in SEEK_DESTROY or pid is None:
        return None
    if pid in REVERSAL:
        tt = "swing"
    elif pid in CONTINUATION:
        q = (session_q or {}).get("quarter")
        if q == 3:
            tt = "intraday"          # NY distribution
        elif q == 1:
            tt = "intrasession"      # Asia accumulation
        else:
            return None              # London manipulation / rollover — no entry
    else:
        return None
    cfg = TIERS[tt]
    return {"trade_type": tt, "rr": cfg["rr"], "sl_source": cfg["sl_source"],
            "horizon": cfg["horizon"], "bias": bias, "profile_id": pid}


def tier_stop(sl_source: str, bias: str, ranges: dict,
              buffer: float = DEFAULT_BUFFER) -> Optional[float]:
    """Structural stop for a tier: beyond the tier range's extreme.

    ``ranges`` maps 'weekly'/'day'/'session' -> (high, low). A long stops below the
    low, a short above the high, with a small buffer. None if the range is missing.
    """
    rng = (ranges or {}).get(sl_source)
    if not rng or rng[0] is None or rng[1] is None:
        return None
    high, low = rng
    if high <= low:
        return None
    return round(low - buffer, 2) if bias == "long" else round(high + buffer, 2)


def tier_ref_range(sl_source: str, ranges: dict) -> Tuple[Optional[float], Optional[float]]:
    """The (high, low) the location gate should use for this tier."""
    rng = (ranges or {}).get(sl_source)
    if not rng:
        return None, None
    return rng[0], rng[1]


def seek_destroy_plan(high: float, low: float, htf_bias: str,
                      extreme_sd: float = 3.0, buffer: float = DEFAULT_BUFFER) -> dict:
    """Ranging-week (Seek & Destroy) fade plan — extreme limits OUTSIDE the range.

    S&D weeks run both sides of the range for liquidity, so instead of chasing we
    arm limit orders at the ±extreme_sd projection beyond the range and fade the
    sweep back inside — but ONLY on the side aligned with the monthly/quarterly
    HTF trend (``htf_bias``). Neutral HTF arms both sides (react to whichever
    sweeps). Targets ladder back through the range; the stop sits beyond the
    extreme (half a range further).

    Returns {orders:[...], range, extreme_sd, htf_bias}. Each order is a LIMIT
    spec: {side, entry, stop, targets, reason}.
    """
    rng = high - low
    if rng <= 0:
        return {"orders": [], "reason": "no valid range", "range": rng}
    mid = (high + low) / 2.0
    up = round(high + extreme_sd * rng, 2)      # extreme above → sell-fade zone
    dn = round(low - extreme_sd * rng, 2)       # extreme below → buy-fade zone

    buy = {"side": "long", "kind": "limit", "entry": dn, "trade_type": "sd_fade",
           "stop": round(dn - 0.5 * rng - buffer, 2),
           "targets": [round(low, 2), round(mid, 2), round(high, 2)],
           "reason": f"buy the {extreme_sd:g}SD downside sweep, fade back into range (HTF long)"}
    sell = {"side": "short", "kind": "limit", "entry": up, "trade_type": "sd_fade",
            "stop": round(up + 0.5 * rng + buffer, 2),
            "targets": [round(high, 2), round(mid, 2), round(low, 2)],
            "reason": f"sell the {extreme_sd:g}SD upside sweep, fade back into range (HTF short)"}

    want = (htf_bias or "neutral").lower()
    if want in ("long", "buy"):
        orders = [buy]
    elif want in ("short", "sell"):
        orders = [sell]
    else:
        orders = [buy, sell]                     # neutral HTF → both extremes
    return {"orders": orders, "range": round(rng, 2), "extreme_sd": extreme_sd,
            "htf_bias": want, "note": "S&D liquidity-sweep fade in anticipation of the HTF trend"}


# Opening-Range session-breakout tier plan (composes indicators.opening_range +
# indicators.profile). Tier by session: Asia OR = intrasession, London/NY = intraday,
# composite (multi-session) = swing. Gated by the weekly bias + the TPO confirmation.
_SESSION_TIER = {"asia": "intrasession", "tokyo": "intrasession",
                 "london": "intraday", "ny": "intraday", "newyork": "intraday",
                 "composite": "swing"}


def session_breakout_plan(orr: dict, profile: dict, bias: str = "neutral",
                          session: str = "ny") -> dict:
    """Compose an Opening-Range breakout into a fired, tiered plan or NO TRADE.

    orr = indicators.opening_range result; profile = TPO confirmation {ok, reason};
    bias = weekly bias. Blocks when there's no breakout, the profile denies it, or the
    breakout opposes the weekly bias."""
    if not orr.get("ok"):
        return {"signal": "NO TRADE", "reason": orr.get("reason", "no OR breakout")}
    if not profile.get("ok"):
        return {"signal": "NO TRADE",
                "reason": f"profile: {profile.get('reason', 'not confirmed (value area)')}"}
    side = orr.get("side")
    b = (bias or "neutral").lower()
    if b in ("long", "short") and side != b:
        return {"signal": "NO TRADE",
                "reason": f"{side} OR breakout opposes weekly {b} bias"}
    return {
        "signal": "LONG" if side == "long" else "SHORT",
        "side": side, "trade_type": _SESSION_TIER.get(session, "intraday"),
        "entry": orr.get("entry"), "stop": orr.get("stop"),
        "kind": "limit", "session": session,
        "or_high": orr.get("or_high"), "or_low": orr.get("or_low"),
        "reason": (f"{session} OR {side} breakout, TPO-confirmed, "
                   f"aligns weekly {b}"),
    }
