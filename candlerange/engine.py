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
