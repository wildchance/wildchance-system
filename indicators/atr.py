"""ATR (Average True Range) — volatility unit for entries, stops, targets & spikes.

Pure and asset-agnostic: feed it OHLC bars (daily or intraday) and it returns the
true range, ATR, ATR-based stop/targets, and a spike flag. Used across every
instrument so stop distance and take-profits scale to each market's own
volatility instead of a fixed pip count.

  true_range(high, low, prev_close)      one bar's TR (Wilder)
  atr(bars, period=14)                   Wilder-smoothed ATR over OHLC bars
  atr_stop(entry, atr, side, mult)       volatility stop, N*ATR beyond entry
  atr_targets(entry, atr, side, mults)   scale-out targets at multiples of ATR
  spike_ratio(bar_range, atr)            how stretched the current bar is
  is_spike(bar, atr, k)                  True if this bar is a > k*ATR expansion
  adr(daily_ranges, n)                   average daily range (last n days)
  percent_of_adr(range_used, adr)        how much of the day's range is spent
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

# A bar is (high, low, close).
Bar = Tuple[float, float, float]


def true_range(high: float, low: float, prev_close: Optional[float]) -> float:
    """Wilder's true range. First bar (no prev_close) falls back to high-low."""
    hl = high - low
    if prev_close is None:
        return hl
    return max(hl, abs(high - prev_close), abs(low - prev_close))


def true_ranges(bars: Sequence[Bar]) -> List[float]:
    out: List[float] = []
    prev_close: Optional[float] = None
    for h, l, c in bars:
        out.append(true_range(h, l, prev_close))
        prev_close = c
    return out


def atr(bars: Sequence[Bar], period: int = 14) -> Optional[float]:
    """Wilder-smoothed ATR over OHLC bars, or None if fewer than ``period`` bars.

    Seeds with the simple mean of the first ``period`` true ranges, then applies
    Wilder smoothing:  ATR_t = (ATR_{t-1}*(period-1) + TR_t) / period.
    """
    trs = true_ranges(bars)
    if len(trs) < period or period < 1:
        return None
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return a


def atr_stop(entry: float, atr_value: float, side: str, mult: float = 1.5) -> float:
    """Volatility stop: ``mult`` ATRs beyond entry (below for long, above short)."""
    dist = mult * atr_value
    s = side.upper()
    if s in ("BUY", "LONG"):
        return entry - dist
    if s in ("SELL", "SHORT"):
        return entry + dist
    raise ValueError(f"side must be BUY/LONG or SELL/SHORT, got {side!r}")


def atr_targets(entry: float, atr_value: float, side: str,
                mults: Sequence[float] = (1.0, 2.0, 3.0)) -> List[float]:
    """Scale-out targets at increasing ATR multiples in the trade's direction."""
    s = side.upper()
    sign = 1.0 if s in ("BUY", "LONG") else -1.0 if s in ("SELL", "SHORT") else None
    if sign is None:
        raise ValueError(f"side must be BUY/LONG or SELL/SHORT, got {side!r}")
    return [round(entry + sign * m * atr_value, 6) for m in mults]


def spike_ratio(bar_range: float, atr_value: Optional[float]) -> Optional[float]:
    """Current bar range as a multiple of ATR. None if ATR is unavailable/zero."""
    if not atr_value:
        return None
    return bar_range / atr_value


def is_spike(bar: Bar, atr_value: Optional[float], k: float = 2.0) -> bool:
    """True if this bar's range exceeds ``k`` * ATR — a volatility expansion.

    Chasing entries into a > k*ATR bar is where mean-reversion fades get run over
    (news spikes). The signal layer can use this to widen the stop, skip, or wait
    for a pullback.
    """
    r = spike_ratio(bar[0] - bar[1], atr_value)
    return r is not None and r >= k


def adr(daily_ranges: Sequence[float], n: int = 5) -> Optional[float]:
    """Average Daily Range over the last ``n`` days (high-low per day)."""
    vals = [r for r in daily_ranges if r is not None]
    if not vals:
        return None
    window = vals[-n:]
    return sum(window) / len(window)


def percent_of_adr(range_used: float, adr_value: Optional[float]) -> Optional[float]:
    """Fraction of ADR already travelled today (1.0 == a full average day).

    High values mean the move is stretched/late — separates continuation from
    exhausted, chase-y entries.
    """
    if not adr_value:
        return None
    return range_used / adr_value
