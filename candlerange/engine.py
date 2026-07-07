"""Reference-candle range model — continuation vs reversal, pure & deterministic.

... (full content from previous extraction) ...
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple


def candle_body(open_: float, close: float) -> Tuple[float, float]:
    return (min(open_, close), max(open_, close))


def is_bullish(open_: float, close: float) -> bool:
    return close >= open_


def classify(open_: float, close: float, price: float) -> dict:
    lo, hi = candle_body(open_, close)
    bullish = is_bullish(open_, close)
    if lo <= price <= hi:
        state, break_dir = "inside", "none"
    elif price > hi:
        state = "continuation" if bullish else "reversal"
        break_dir = "up"
    else:
        state = "continuation" if not bullish else "reversal"
        break_dir = "down"
    return {
        "candle_dir": "bullish" if bullish else "bearish",
        "body_low": lo,
        "body_high": hi,
        "price": price,
        "state": state,
        "break_dir": break_dir,
    }


def combined_range(candles: Sequence[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    pts: List[float] = []
    for o, c in candles:
        pts.extend((o, c))
    if not pts:
        return None
    return (min(pts), max(pts))


def close_position(open_: float, high: float, low: float, close: float) -> float:
    """Where the close sits in the candle's range: 0 = at the low, 1 = at the high."""
    rng = high - low
    return round((close - low) / rng, 3) if rng > 0 else 0.5


def friday_gap_read(candle: dict) -> dict:
    """Anticipate Monday's gap from the Friday pre-close 1h candle.

    A strong bullish close into the top of its range → gap-up bias; a weak bearish
    close into the bottom → gap-down; otherwise flat. (The Friday-close read is the
    reliable one; mid-week the same signal is choppy — see weekday_confidence.)
    """
    o, h = float(candle["open"]), float(candle["high"])
    l, c = float(candle["low"]), float(candle["close"])
    pos = close_position(o, h, l, c)
    bull = c >= o
    if pos >= 0.66 and bull:
        gap = "up"
    elif pos <= 0.34 and not bull:
        gap = "down"
    else:
        gap = "flat"
    if gap == "flat":
        strength = "weak"
    elif pos >= 0.8 or pos <= 0.2:
        strength = "strong"
    else:
        strength = "moderate"
    return {"gap_bias": gap, "strength": strength, "close_position": pos,
            "candle_dir": "bullish" if bull else "bearish",
            "body_low": round(min(o, c), 2), "body_high": round(max(o, c), 2),
            "note": f"Friday close {pos:.0%} up its range ({'bullish' if bull else 'bearish'}) "
                    f"→ Monday gap {gap}"}


def weekday_confidence(weekday: int) -> str:
    """Confidence in the candle-range read by weekday: Friday (Monday-gap) is the
    reliable one; Monday medium; Tue–Thu choppy / low-confidence."""
    if weekday == 4:
        return "high"
    if weekday == 0:
        return "medium"
    return "low"


def analyze(ref_0100: Optional[dict], ref_1300: Optional[dict], price: float) -> dict:
    out: dict = {"price": price, "anchors": {}}
    bodies: List[Tuple[float, float]] = []
    for name, ref in (("0100", ref_0100), ("1300", ref_1300)):
        if ref is None:
            out["anchors"][name] = None
            continue
        o, c = float(ref["open"]), float(ref["close"])
        out["anchors"][name] = classify(o, c, price)
        bodies.append((o, c))

    rng = combined_range(bodies)
    if rng:
        lo, hi = rng
        if lo <= price <= hi:
            cstate, cdir = "inside", "none"
        elif price > hi:
            cstate, cdir = "break_up", "up"
        else:
            cstate, cdir = "break_down", "down"
        out["combined_range"] = {"low": lo, "high": hi, "state": cstate, "break_dir": cdir}
    else:
        out["combined_range"] = None
    return out
