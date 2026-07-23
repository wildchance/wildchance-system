"""Volatility engine (B9) — ATR, realized vol, historical percentile, expected range.

Pure OHLC math (no new data feed). Feeds position sizing and the scenario ranges the
Kingdom report needs:

  • ATR (Wilder-smoothed) — the average true range, the stop/target unit.
  • realized_vol — close-to-close log-return stdev (optionally annualised).
  • atr_percentile — where today's ATR sits vs its own history (0-1); the regime.
  • expected_range — 24-48h contraction / base / expansion bands off the ATR.

Feed (o,h,l,c) tuples / dicts / (t,o,h,l,c) rows oldest-first.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence


def _ohlc(bar):
    if isinstance(bar, dict):
        return (float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"]))
    if len(bar) >= 5:                      # (t,o,h,l,c)
        return (float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]))
    return (float(bar[0]), float(bar[1]), float(bar[2]), float(bar[3]))  # (o,h,l,c)


def true_ranges(bars: Sequence) -> List[float]:
    trs, prev_close = [], None
    for b in bars:
        _o, h, l, c = _ohlc(b)
        tr = (h - l) if prev_close is None else max(h - l, abs(h - prev_close), abs(l - prev_close))
        trs.append(tr)
        prev_close = c
    return trs


def atr(bars: Sequence, period: int = 14) -> Optional[float]:
    """Wilder-smoothed ATR."""
    trs = true_ranges(bars)
    if not trs:
        return None
    if len(trs) < period:
        return round(sum(trs) / len(trs), 4)
    a = sum(trs[:period]) / period
    for tr in trs[period:]:
        a = (a * (period - 1) + tr) / period
    return round(a, 4)


def realized_vol(bars: Sequence, period: int = 20, annualize: bool = False,
                 periods_per_year: int = 252) -> Optional[float]:
    """Close-to-close log-return standard deviation."""
    closes = [_ohlc(b)[3] for b in bars]
    rets = [math.log(closes[i] / closes[i - 1])
            for i in range(1, len(closes)) if closes[i - 1] > 0]
    win = rets[-period:]
    if len(win) < 2:
        return None
    mean = sum(win) / len(win)
    var = sum((r - mean) ** 2 for r in win) / (len(win) - 1)
    sd = math.sqrt(var)
    if annualize:
        sd *= math.sqrt(periods_per_year)
    return round(sd, 6)


def _rolling_atr_series(bars: Sequence, period: int) -> List[float]:
    trs = true_ranges(bars)
    return [sum(trs[i - period:i]) / period for i in range(period, len(trs) + 1)]


def atr_percentile(bars: Sequence, period: int = 14) -> Optional[float]:
    """Percentile rank (0-1) of the current ATR vs its rolling history."""
    series = _rolling_atr_series(bars, period)
    if len(series) < 5:
        return None
    cur = series[-1]
    below = sum(1 for x in series if x <= cur)
    return round(below / len(series), 3)


def vol_regime(bars: Sequence, period: int = 14) -> dict:
    """Low / normal / high volatility regime from the ATR percentile."""
    p = atr_percentile(bars, period)
    if p is None:
        return {"regime": "unknown", "atr_percentile": None}
    regime = "high" if p >= 0.66 else "low" if p <= 0.33 else "normal"
    return {"regime": regime, "atr_percentile": p,
            "note": (f"ATR at {int(p*100)}th pct — "
                     + {"high": "expansion regime: wider stops, bigger targets, size down",
                        "low": "compression regime: coil — expect a break, tighten",
                        "normal": "mid-range volatility"}[regime])}


def expected_range(bars: Sequence, price: Optional[float] = None, period: int = 14,
                   mults=(0.5, 1.0, 1.75)) -> dict:
    """24-48h contraction / base / expansion bands off the ATR."""
    a = atr(bars, period)
    if a is None:
        return {"atr": None, "scenarios": {}}
    if price is None:
        price = _ohlc(bars[-1])[3]
    price = float(price)
    scen = {}
    for name, m in zip(("contraction", "base", "expansion"), mults):
        scen[name] = {"atr_mult": m, "range_usd": round(a * m, 2),
                      "upper": round(price + a * m, 2), "lower": round(price - a * m, 2)}
    return {"price": round(price, 2), "atr": a, "scenarios": scen}


def volatility_read(bars: Sequence, price: Optional[float] = None,
                    atr_period: int = 14, rv_period: int = 20) -> dict:
    """The full B9 read — ATR, realized vol, regime, and the expected-range scenarios."""
    return {
        "atr": atr(bars, atr_period),
        "atr_percentile": atr_percentile(bars, atr_period),
        "realized_vol": realized_vol(bars, rv_period),
        "realized_vol_annualized": realized_vol(bars, rv_period, annualize=True),
        "regime": vol_regime(bars, atr_period),
        "expected_range": expected_range(bars, price, atr_period),
    }


def size_modifier(bars: Sequence, period: int = 14) -> float:
    """A sizing multiplier for the vol regime — smaller in expansion, larger in
    compression (keeps risk-per-trade roughly constant across regimes). ~[0.7, 1.2]."""
    p = atr_percentile(bars, period)
    if p is None:
        return 1.0
    return round(1.2 - 0.5 * p, 3)      # p=0 → 1.2, p=1 → 0.7
