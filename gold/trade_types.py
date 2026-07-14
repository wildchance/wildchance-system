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

# Session Opening-Range → tier, and which session's OR each tier breaks out of.
#   intrasession = Asia OR broken inside Asia   (Q1)
#   intraday     = London / NY-open OR          (Q2→Q3, the classic gold mover)
#   swing        = Asia+London composite broken by NY
_OR_SESSION = {"asia": "intrasession", "tokyo": "intrasession",
               "london": "intraday", "ny": "intraday", "newyork": "intraday",
               "composite": "swing"}


def session_breakout_plan(or_result: dict, profile_confirm: dict, bias: str,
                          session: str = "ny", rr: Optional[Sequence[float]] = None) -> dict:
    """Compose an Opening-Range breakout into a tiered, sized-elsewhere trade plan.

    Inputs are the pure reads: ``or_result`` from indicators.opening_range (OR +
    breakout + retest + stop) and ``profile_confirm`` from
    indicators.profile.breakout_confirmed (the leaving-balance gate). The plan
    fires only when BOTH pass AND the OR breakout direction agrees with the weekly
    ``bias`` (so a session break never trades against the higher-timeframe read).

    Entry/stop come straight from the OR (retest level / opposite extreme); the
    tier's R:R band becomes the scale-out ladder. Money-first sizing and the fib
    trend-TP ladder are applied by the caller — this only decides WHETHER and the
    entry/stop/tier. Returns {signal, ...} — 'NO TRADE' with a reason when gated.
    """
    tier = _OR_SESSION.get((session or "").lower(), "intraday")
    band = rr if rr is not None else TIERS[tier]["rr"]
    layers = {"opening_range": or_result, "profile": profile_confirm}

    if bias not in ("long", "short"):
        return {"signal": "NO TRADE", "trade_type": tier,
                "reason": "no weekly bias for the session break", "layers": layers}
    if not or_result or not or_result.get("ok"):
        return {"signal": "NO TRADE", "trade_type": tier,
                "reason": (or_result or {}).get("reason", "no opening-range breakout"),
                "layers": layers}
    side = or_result.get("side")
    if side != bias:
        return {"signal": "NO TRADE", "trade_type": tier,
                "reason": f"OR {side} breakout opposes weekly {bias} — stand aside",
                "layers": layers}
    if not profile_confirm or not profile_confirm.get("ok"):
        return {"signal": "NO TRADE", "trade_type": tier,
                "reason": (profile_confirm or {}).get("reason", "profile did not confirm"),
                "layers": layers}

    return {
        "signal": side.upper(), "instrument": "XAU/USD", "trade_type": tier,
        "entry": or_result["entry"], "stop": or_result["stop"],
        "entry_mode": "structure", "kind": "limit",     # retest = a limit at the OR boundary
        "rr": tuple(band), "session": session,
        "reason": f"{tier} · {session} OR {side} break + retest, profile leaving balance",
        "layers": layers,
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
