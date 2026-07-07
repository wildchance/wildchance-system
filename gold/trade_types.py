"""Gold trade-type tiers — intraday / intrasession / swing (pure, stdlib-only).

"Profile drives type": the active ICT weekly profile decides which tier a gold
trade is, and each tier carries its own stop source, reward:risk band, and
holding horizon.

  TIER          profiles                     SL source        R:R band   horizon
  swing         reversals 1,2,5,6,11,12      weekly hi/lo     1:5–1:8    Mon close / Tue open
  intraday      cont. 3,4,7,8 in NY dist Q3  day hi/lo        1:2–1:3    same-day (NY close)
  intrasession  cont. 3,4,7,8 single session session hi/lo    1:3–1:5    session end
  (none)        Seek & Destroy 9,10 / neutral — stand aside

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

    Reversal profiles are swings. Continuation profiles are intraday when the live
    session quarter is the NY distribution (Q3), otherwise intrasession. Seek &
    Destroy and non-directional profiles return None.
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
        tt = "intraday" if q == 3 else "intrasession"
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
