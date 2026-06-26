"""CBDR — Central Bank Dealers Range (ICT) confluence engine.

Pure, deterministic, stdlib-only so it unit-tests without a data feed.

Definition (per the ICT methodology):
  • The CBDR box = the highest high and lowest low inside the 2:00 PM–8:00 PM
    New York time window (DST-aware; the data layer supplies the right bars).
  • One "standard deviation" = the box range (high − low).
  • Projection levels are measured OUTWARD from the box edges by whole multiples
    of the range:  +nSD = high + n·range ,  −nSD = low − n·range.
  • Read for the day's high/low: when price trades in the upper half of the box
    the lower deviations tend to act as the floor (low of day); in the lower
    half the upper deviations tend to cap it (high of day).

Conventions here are explicit constants so they are easy to adjust if your
flavour of CBDR differs.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Sequence, Tuple

# CBDR window in NEW YORK local time (the data layer must convert).
CBDR_START_HOUR = 14   # 2:00 PM
CBDR_END_HOUR = 20     # 8:00 PM (exclusive — hourly bars 14,15,16,17,18,19)
DEFAULT_DEVIATIONS: Tuple[int, ...] = (1, 2, 3)


def cbdr_box(highs: Sequence[float], lows: Sequence[float]) -> Tuple[float, float]:
    """Box high/low from the window's bar highs and lows."""
    if not highs or not lows:
        raise ValueError("need at least one bar to form a CBDR box")
    return max(highs), min(lows)


@dataclass
class CBDR:
    high: float
    low: float
    mid: float
    range: float
    levels: Dict[str, float]   # "+1SD" / "-1SD" ... -> price

    def to_dict(self) -> dict:
        return asdict(self)


def build_cbdr(high: float, low: float,
               deviations: Sequence[int] = DEFAULT_DEVIATIONS) -> CBDR:
    """Box + standard-deviation projection levels."""
    if high < low:
        raise ValueError("high must be >= low")
    rng = high - low
    mid = (high + low) / 2.0
    levels: Dict[str, float] = {}
    for n in deviations:
        levels[f"+{n}SD"] = high + n * rng
        levels[f"-{n}SD"] = low - n * rng
    return CBDR(high=high, low=low, mid=mid, range=rng, levels=levels)


def read_bias(price: float, box: CBDR) -> dict:
    """Where price sits relative to the box, and what it implies for the day.

    Returns the directional read plus the most likely day-extreme level.
    """
    if price > box.high:
        state = "breakout_up"
        note = "above the range — upper SDs are upside targets"
        key_level = box.levels.get("+1SD")
    elif price < box.low:
        state = "breakout_down"
        note = "below the range — lower SDs are downside targets"
        key_level = box.levels.get("-1SD")
    elif price >= box.mid:
        state = "bullish_half"
        note = "upper half — lower SDs likely act as the floor (low of day)"
        key_level = box.levels.get("-1SD")
    else:
        state = "bearish_half"
        note = "lower half — upper SDs likely cap it (high of day)"
        key_level = box.levels.get("+1SD")
    return {"price": price, "state": state, "note": note, "key_level": key_level}


def nearest_levels(price: float, box: CBDR, n: int = 2) -> List[dict]:
    """The n projection/box levels closest to a price (for 'price near a level'
    confluence)."""
    pts = {"high": box.high, "low": box.low, "mid": box.mid, **box.levels}
    ranked = sorted(pts.items(), key=lambda kv: abs(kv[1] - price))
    return [{"level": k, "price": v, "distance": abs(v - price)} for k, v in ranked[:n]]
