"""Reference-candle range model — continuation vs reversal, pure & deterministic.

Some intraday models anchor off specific hourly candles. Here the anchors are the
**01:00 UTC** and **13:00 UTC** 1-hour candles (London-prep and NY-prep hours).
Each candle's *body* (open→close) is a reference range; where price goes relative
to that body classifies the move:

  • break beyond the body **in the candle's own direction**  → CONTINUATION
  • break beyond the body **against** the candle's direction → REVERSAL
  • still inside the body                                    → INSIDE (no signal)

A bullish reference candle (close ≥ open) broken to the upside is a continuation;
broken to the downside it is a reversal. A bearish candle is the mirror.

The combined range spans both candles' open/close levels — a wider reference used
the same way. All functions are stdlib-only so they unit-test without a feed.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple


def candle_body(open_: float, close: float) -> Tuple[float, float]:
    """(body_low, body_high) of a candle's open→close body."""
    return (min(open_, close), max(open_, close))


def is_bullish(open_: float, close: float) -> bool:
    return close >= open_


def classify(open_: float, close: float, price: float) -> dict:
    """Classify `price` relative to one reference candle's body."""
    lo, hi = candle_body(open_, close)
    bullish = is_bullish(open_, close)
    if lo <= price <= hi:
        state, break_dir = "inside", "none"
    elif price > hi:
        state = "continuation" if bullish else "reversal"
        break_dir = "up"
    else:  # price < lo
        state = "continuation" if not bullish else "reversal"
        break_dir = "down"
    return {
        "candle_dir": "bullish" if bullish else "bearish",
        "body_low": lo,
        "body_high": hi,
        "price": price,
        "state": state,            # continuation | reversal | inside
        "break_dir": break_dir,    # up | down | none
    }


def combined_range(candles: Sequence[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    """(low, high) spanning the open/close levels of every (open, close) given."""
    pts: List[float] = []
    for o, c in candles:
        pts.extend((o, c))
    if not pts:
        return None
    return (min(pts), max(pts))


def analyze(ref_0100: Optional[dict], ref_1300: Optional[dict],
            price: float) -> dict:
    """Combine the two anchor candles into one read.

    ref_* are {"open","close"} dicts (extra keys ignored) or None. Returns a
    per-candle classification plus a combined-range classification.
    """
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
        out["combined_range"] = {"low": lo, "high": hi,
                                 "state": cstate, "break_dir": cdir}
    else:
        out["combined_range"] = None
    return out
