"""Gold–silver ratio mean-reversion — the simple, robust base (pure, stdlib-only).

Trades the RATIO gold/silver: when it stretches ±``entry_z`` standard deviations
from its rolling mean it is statistically over/under-valued and tends to revert.

  LONG the ratio  (z ≤ −entry_z, ratio cheap)  →  LONG gold / SHORT silver
  SHORT the ratio (z ≥ +entry_z, ratio rich)   →  SHORT gold / LONG silver
  exit when |z| ≤ ``exit_z`` (reverted); cut when |z| ≥ ``stop_z`` (relationship broke)

A TREND GUARD skips entries while the ratio is structurally trending (a % drift over
a long window) — that's the regime where mean reversion dies (silver squeezes, risk
regimes). No Kalman, no ML: those are added ONLY if they beat this out-of-sample.

  ratio_series(gold, silver)                element-wise gold/silver
  rolling_z(series, lookback)               trailing z of each point (no lookahead)
  drift(series, window)                     % change over the window (trend gauge)
  pair_signal(gold, silver, …)              the current LONG/SHORT/FLAT ratio call
"""

from __future__ import annotations

import statistics
from typing import List, Optional, Sequence


def ratio_series(gold: Sequence[float], silver: Sequence[float]) -> List[float]:
    """Element-wise gold/silver ratio (skips any bar with zero/None silver)."""
    n = min(len(gold), len(silver))
    return [gold[i] / silver[i] for i in range(n) if silver[i]]


def rolling_z(series: Sequence[float], lookback: int) -> List[Optional[float]]:
    """z-score of each point vs its trailing ``lookback`` window (inclusive of the
    point, exclusive of the future — no lookahead). None during the warm-up."""
    out: List[Optional[float]] = []
    for i in range(len(series)):
        if i + 1 < lookback:
            out.append(None)
            continue
        window = series[i + 1 - lookback: i + 1]
        sd = statistics.stdev(window)
        out.append((series[i] - statistics.fmean(window)) / sd if sd > 0 else None)
    return out


def drift(series: Sequence[float], window: int) -> Optional[float]:
    """Fractional change of the last point vs ``window`` bars ago — the trend gauge.

    |drift| large ⇒ the ratio has structurally moved (trending), so mean reversion
    is unsafe. None if there isn't enough history.
    """
    if len(series) <= window or series[-1 - window] == 0:
        return None
    return (series[-1] - series[-1 - window]) / series[-1 - window]


def pair_signal(gold: Sequence[float], silver: Sequence[float],
                lookback: int = 20, entry_z: float = 2.0, exit_z: float = 0.5,
                stop_z: float = 3.5, trend_guard: bool = True,
                trend_window: int = 100, max_drift: float = 0.15) -> dict:
    """The current ratio mean-reversion call from aligned gold & silver closes.

    Returns {signal: LONG_RATIO|SHORT_RATIO|FLAT, z, ratio, legs, regime_ok, reason}.
    ``legs`` maps the two instruments to buy/sell for the pairs (dollar-neutral) trade.
    """
    rs = ratio_series(gold, silver)
    if len(rs) < lookback + 1:
        return {"signal": "FLAT", "reason": "not enough history for the z-score"}

    window = rs[-lookback:]
    sd = statistics.stdev(window)
    mean = statistics.fmean(window)
    if sd <= 0:
        return {"signal": "FLAT", "reason": "zero variance in the ratio window"}
    z = (rs[-1] - mean) / sd
    dr = drift(rs, trend_window)
    regime_ok = (not trend_guard) or (dr is None) or (abs(dr) <= max_drift)

    base = {"ratio": round(rs[-1], 5), "z": round(z, 3), "mean": round(mean, 5),
            "sd": round(sd, 6), "drift": round(dr, 4) if dr is not None else None,
            "regime_ok": regime_ok, "lookback": lookback,
            "entry_z": entry_z, "exit_z": exit_z, "stop_z": stop_z}

    if not regime_ok:
        return {**base, "signal": "FLAT",
                "reason": f"trend guard — ratio drifting {dr:+.1%} over {trend_window}d "
                          "(structural move, no mean-reversion)"}
    if z >= entry_z:
        return {**base, "signal": "SHORT_RATIO",
                "legs": {"XAU/USD": "sell", "XAG/USD": "buy"},
                "reason": f"ratio rich (z {z:+.2f} ≥ {entry_z}) — short gold / long silver, revert to mean"}
    if z <= -entry_z:
        return {**base, "signal": "LONG_RATIO",
                "legs": {"XAU/USD": "buy", "XAG/USD": "sell"},
                "reason": f"ratio cheap (z {z:+.2f} ≤ −{entry_z}) — long gold / short silver, revert to mean"}
    return {**base, "signal": "FLAT",
            "reason": f"ratio near fair value (z {z:+.2f}, |z| < {entry_z})"}
