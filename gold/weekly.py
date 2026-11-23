"""ICT weekly profile — Monday sweep → directional bias for the week (pure).

The 'hack' for accountable trend justification: the week's direction is set by how
price interacts with MONDAY's range. The four states (your "1 of 4 conditions"):

  swept Monday HIGH then trades back below  → SHORT bias (liquidity grab up → sell)
  swept Monday LOW  then trades back above  → LONG  bias (liquidity grab down → buy)
  broke & HOLDING above Monday high         → LONG  continuation
  broke & HOLDING below Monday low          → SHORT continuation
  (still inside Monday range)               → NEUTRAL — wait

Every gold signal logs the active state + reason, so the trend call is auditable.
Bars are (date, open, high, low, close), oldest-first.
"""

from __future__ import annotations

import datetime as dt
from typing import List, Optional, Tuple

DatedOHLC = Tuple[dt.date, float, float, float, float]


def _monday_of(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=d.weekday())


def weekly_bias(daily: List[DatedOHLC], prior_fallback: bool = False) -> Optional[dict]:
    """Monday-sweep bias for the CURRENT week (relative to the last bar).

    ``prior_fallback``: while this week is still inside (or before) Monday's range,
    carry LAST week's directional profile instead of 'neutral' — labelled
    ``prior_week`` so every card shows it is last week's read, not this week's.
    """
    if not daily:
        return None
    read = _bias(daily)
    if prior_fallback and read and read["bias"] == "neutral":
        monday = _monday_of(daily[-1][0])
        prev = _bias([b for b in daily if b[0] < monday])
        if prev and prev["bias"] in ("long", "short"):
            return {**read, "bias": prev["bias"], "prior_week": prev,
                    "profile": f"prior_week_{prev['profile']}",
                    "reason": f"{read['reason']} — using last week's profile "
                              f"({prev['week_of']}: {prev['reason']})"}
    return read


def _bias(daily: List[DatedOHLC]) -> Optional[dict]:
    if not daily:
        return None
    last_date = daily[-1][0]
    monday = _monday_of(last_date)
    week = [b for b in daily if monday <= b[0] <= last_date]
    mon = [b for b in week if b[0] == monday]
    if not mon:
        return {"bias": "neutral", "reason": "no Monday bar yet this week",
                "monday_high": None, "monday_low": None}

    mh = max(b[2] for b in mon)       # high = idx 2
    ml = min(b[3] for b in mon)       # low  = idx 3
    post = [b for b in week if b[0] > monday]
    swept_high = any(b[2] > mh for b in post)
    swept_low = any(b[3] < ml for b in post)
    last_close = week[-1][4]          # close = idx 4

    if swept_high and last_close < mh:
        bias, reason, profile = "short", "swept Monday high then rejected — liquidity grab up, sell", "reversal_high_sweep"
    elif swept_low and last_close > ml:
        bias, reason, profile = "long", "swept Monday low then reclaimed — liquidity grab down, buy", "reversal_low_sweep"
    elif swept_high:
        bias, reason, profile = "long", "broke and holding above Monday high — bullish continuation", "continuation_up"
    elif swept_low:
        bias, reason, profile = "short", "broke and holding below Monday low — bearish continuation", "continuation_down"
    else:
        bias, reason, profile = "neutral", "still inside Monday range — no sweep yet", "inside_range"

    return {
        "week_of": monday.isoformat(),
        "monday_high": round(mh, 5),
        "monday_low": round(ml, 5),
        "swept_high": swept_high,
        "swept_low": swept_low,
        "last_close": round(last_close, 5),
        "bias": bias,
        "profile": profile,
        "reason": reason,
    }


def confluence(bias_read: Optional[dict], side: str) -> str:
    """Does the weekly bias confirm / diverge from a proposed side?"""
    if not bias_read or bias_read.get("bias") in (None, "neutral"):
        return "neutral"
    want = "long" if side.lower() in ("long", "buy") else "short"
    return "confirms" if bias_read["bias"] == want else "diverges"
