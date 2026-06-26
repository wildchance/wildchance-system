"""PD Arrays — ICT Premium/Discount arrays from intraday candles.

Pure, stdlib-only. Detects the two most-used institutional reference zones:

  • Fair Value Gap (FVG) — a 3-candle imbalance. Bullish when the 1st candle's
    high is below the 3rd candle's low (a gap the displacement left unfilled);
    bearish is the mirror.
  • Order Block (OB) — the last opposite-colour candle before a displacement
    that breaks its range. Bullish OB = last down candle before an up move that
    closes above its high; bearish OB is the mirror.

Each array is a price *zone* [bottom, top]. `arrays_near` finds the zones that
sit at a given level (e.g. a CBDR standard-deviation projection) — that overlap
is the "PD array aligned with an SD level" confluence.

Candles are dicts: {"datetime","open","high","low","close"}, oldest first.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Optional, Sequence


@dataclass
class PDArray:
    kind: str          # "FVG" | "OB"
    direction: str     # "bullish" | "bearish"
    top: float
    bottom: float
    index: int         # bar index the array is anchored to
    datetime: Optional[str]

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["mid"] = round(self.mid, 6)
        return d


def _f(c: dict, k: str) -> float:
    return float(c[k])


def detect_fvgs(candles: Sequence[dict]) -> List[PDArray]:
    """3-candle Fair Value Gaps. The gap is anchored to the middle candle."""
    out: List[PDArray] = []
    for i in range(1, len(candles) - 1):
        c0, c1, c2 = candles[i - 1], candles[i], candles[i + 1]
        h0, l0 = _f(c0, "high"), _f(c0, "low")
        h2, l2 = _f(c2, "high"), _f(c2, "low")
        if h0 < l2:                      # bullish FVG: gap [h0, l2]
            out.append(PDArray("FVG", "bullish", top=l2, bottom=h0,
                               index=i, datetime=c1.get("datetime")))
        elif l0 > h2:                    # bearish FVG: gap [h2, l0]
            out.append(PDArray("FVG", "bearish", top=l0, bottom=h2,
                               index=i, datetime=c1.get("datetime")))
    return out


def detect_order_blocks(candles: Sequence[dict]) -> List[PDArray]:
    """Order blocks: last opposite-colour candle before a displacement that
    closes beyond its range."""
    out: List[PDArray] = []
    for i in range(len(candles) - 1):
        c, n = candles[i], candles[i + 1]
        o, h, l, cl = _f(c, "open"), _f(c, "high"), _f(c, "low"), _f(c, "close")
        n_close = _f(n, "close")
        if cl < o and n_close > h:        # down candle, next closes above its high
            out.append(PDArray("OB", "bullish", top=h, bottom=l,
                               index=i, datetime=c.get("datetime")))
        elif cl > o and n_close < l:      # up candle, next closes below its low
            out.append(PDArray("OB", "bearish", top=h, bottom=l,
                               index=i, datetime=c.get("datetime")))
    return out


def detect_pd_arrays(candles: Sequence[dict]) -> List[PDArray]:
    """All PD arrays (FVGs + order blocks), most recent first."""
    arrays = detect_fvgs(candles) + detect_order_blocks(candles)
    arrays.sort(key=lambda a: a.index, reverse=True)
    return arrays


def arrays_near(arrays: Sequence[PDArray], level: float, tol: float) -> List[PDArray]:
    """PD arrays whose zone sits within `tol` of `level` (zone overlap with
    [level-tol, level+tol], or midpoint within tol)."""
    out: List[PDArray] = []
    for a in arrays:
        if (a.bottom - tol) <= level <= (a.top + tol) or abs(a.mid - level) <= tol:
            out.append(a)
    return out
