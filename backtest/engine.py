"""Backtest / validation harness — does the confluence stack actually help? (pure)

Replays the mean-reversion fade on real OHLC and compares variants so the lift
from each addition is measured, not assumed:

  • baseline    — the frozen 0.5*SD stop, every fade                (control)
  • atr         — stop floored at atr_mult*ATR (the live overlay)
  • atr_struct  — atr stop AND only fades a peak/trough reversal confirms
                  (the MMM structure filter)

Exit is the day+hold close, stop-aware (a stop breached intra-hold realizes -1R),
mirroring the USD/JPY scoreboard. Honest scope: EdgeFinder's retail/COT layers
can't be replayed (no historical retail/COT feed), so this validates the
PRICE-derived layers — the ATR stop and the MMM structure filter.

Bars are (open, high, low, close) tuples, oldest-first.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from usdjpy.engine import rolling_stats, Z_BUY, Z_SELL, result_r
from indicators.atr import atr as compute_atr
from mmm.engine import peak_formation

OHLC = Tuple[float, float, float, float]
VARIANTS = ("baseline", "atr", "atr_struct")


def _stats(rs: List[float]) -> dict:
    n = len(rs)
    if not n:
        return {"trades": 0, "wins": 0, "losses": 0, "win_rate": None,
                "expectancy_r": None, "total_r": 0.0, "profit_factor": None,
                "max_drawdown_r": 0.0}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    eq = peak = mdd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    return {
        "trades": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n, 3),
        "expectancy_r": round(sum(rs) / n, 3),
        "total_r": round(sum(rs), 2),
        "profit_factor": (round(gross_win / gross_loss, 2) if gross_loss else None),
        "max_drawdown_r": round(mdd, 2),
    }


def simulate(bars: Sequence[OHLC], variant: str = "baseline",
             lookback: int = 20, hold: int = 3,
             sd_mult: float = 0.5, atr_mult: float = 1.5,
             atr_period: int = 14) -> dict:
    """Run one variant over the bars and return its performance stats."""
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}")
    closes = [b[3] for b in bars]
    highs = [b[1] for b in bars]
    lows = [b[2] for b in bars]

    rs: List[float] = []
    i = lookback
    while i < len(bars) - hold:
        ma, sd = rolling_stats(closes[:i + 1], lookback)
        if not ma or not sd:
            i += 1
            continue
        z = (closes[i] - ma) / sd
        action = "BUY" if z <= Z_BUY else "SELL" if z >= Z_SELL else None
        if action is None:
            i += 1
            continue

        entry = closes[i]
        dist = sd_mult * sd
        if variant in ("atr", "atr_struct"):
            a = compute_atr(list(zip(highs[:i + 1], lows[:i + 1], closes[:i + 1])),
                            atr_period) or 0.0
            dist = max(dist, atr_mult * a)
        stop = entry - dist if action == "BUY" else entry + dist

        if variant == "atr_struct":
            pf = peak_formation([b for b in bars[max(0, i - 6):i + 1]])
            rev = pf.get("reversal") or ""
            ok = ("bullish" in rev) if action == "BUY" else ("bearish" in rev)
            if not ok:
                i += 1
                continue

        breached = (any(lows[j] <= stop for j in range(i + 1, i + hold + 1))
                    if action == "BUY"
                    else any(highs[j] >= stop for j in range(i + 1, i + hold + 1)))
        r = -1.0 if breached else result_r(action, entry, stop, closes[i + hold])
        rs.append(r)
        i += hold + 1                      # non-overlapping, like the backtest

    out = _stats(rs)
    out["variant"] = variant
    return out


def compare(bars: Sequence[OHLC], **kw) -> dict:
    """Run all variants and surface the win-rate / expectancy lift vs baseline."""
    results = {v: simulate(bars, v, **kw) for v in VARIANTS}
    base = results["baseline"]
    lift = {}
    for v in ("atr", "atr_struct"):
        r = results[v]
        lift[v] = {
            "win_rate_delta": (round(r["win_rate"] - base["win_rate"], 3)
                               if r["win_rate"] is not None and base["win_rate"] is not None else None),
            "expectancy_delta": (round(r["expectancy_r"] - base["expectancy_r"], 3)
                                 if r["expectancy_r"] is not None and base["expectancy_r"] is not None else None),
        }
    return {"bars": len(bars), "variants": results, "lift_vs_baseline": lift}
