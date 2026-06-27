"""Intraday mean-reversion breach detector — pure, deterministic, no I/O.

Closes the "no intraday tick reaction" gap: the daily USD/JPY scan and the 6h
confluence recompute are too coarse to catch a pair that stretches mid-session.
This runs on 1h bars and flags an instrument the moment its short-window z-score
crosses a threshold — *once* per move, not every hour it stays stretched.

Same statistic as the daily engine (z = (close - mean) / sample-SD over a
rolling window) but on an intraday timeframe. A positive z is overbought
(mean-reversion lean = SELL); negative is oversold (lean = BUY).

The "fresh breach" debounce is the whole point: `fired` is true whenever the
latest bar is beyond the threshold, but `fresh` is true only when the latest bar
breached AND the prior bar did not — i.e. the move just happened. Alerting on
`fresh` means one ping per stretch, no hourly spam.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

DEFAULT_WINDOW = 20
DEFAULT_Z = 2.0


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs)


def _sample_sd(xs: List[float]) -> float:
    """Sample (n-1) standard deviation — matches the daily SD20 convention."""
    n = len(xs)
    if n < 2:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return var ** 0.5


def z_at(closes: List[float], idx: int, window: int) -> Optional[float]:
    """z-score of closes[idx] vs the `window` closes ending at idx (inclusive).

    Returns None if there aren't `window` bars up to and including idx, or if the
    window has zero variance (flat market — z undefined).
    """
    if idx < 0:
        idx += len(closes)
    start = idx - window + 1
    if start < 0 or idx >= len(closes):
        return None
    win = closes[start: idx + 1]
    sd = _sample_sd(win)
    if sd == 0:
        return None
    return (closes[idx] - _mean(win)) / sd


@dataclass
class Breach:
    fired: bool = False           # latest bar is beyond the threshold
    fresh: bool = False           # latest breached and the prior bar did not
    z: Optional[float] = None     # latest z-score
    z_prev: Optional[float] = None
    direction: Optional[str] = None   # "overbought" | "oversold" | None
    lean: Optional[str] = None        # mean-reversion bias: "SELL" | "BUY" | None
    last_close: Optional[float] = None

    def to_dict(self) -> dict:
        def r(x):
            return round(x, 4) if isinstance(x, (int, float)) else x
        return {
            "fired": self.fired,
            "fresh": self.fresh,
            "z": r(self.z),
            "z_prev": r(self.z_prev),
            "direction": self.direction,
            "lean": self.lean,
            "last_close": self.last_close,
        }


def scan_closes(closes: List[float], window: int = DEFAULT_WINDOW,
                z_threshold: float = DEFAULT_Z) -> Breach:
    """Detect a fresh intraday z-score breach from oldest-first closes.

    Needs at least window+1 bars (to have a current and a prior z). Fewer bars,
    or a flat window, yields a non-fired Breach.
    """
    b = Breach()
    if len(closes) < window + 1:
        return b
    b.last_close = closes[-1]
    z_now = z_at(closes, -1, window)
    z_prev = z_at(closes, -2, window)
    b.z = z_now
    b.z_prev = z_prev
    if z_now is None:
        return b

    b.fired = abs(z_now) >= z_threshold
    prev_fired = (z_prev is not None) and (abs(z_prev) >= z_threshold)
    b.fresh = b.fired and not prev_fired

    if b.fired:
        if z_now > 0:
            b.direction = "overbought"
            b.lean = "SELL"
        else:
            b.direction = "oversold"
            b.lean = "BUY"
    return b
