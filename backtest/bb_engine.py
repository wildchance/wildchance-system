"""Bollinger ±2σ re-entry fade — backtest + parameter sweep (pure Python, no deps).

Rules simulated on daily bars (same logic as indicators/bollinger.swing_setup's D1
trigger; the W1/H4 confluence is NOT simulated, so live results should be a bit
better-filtered than this baseline):
  signal  close[i-1] beyond ±k·σ and close[i] back inside  → fade toward the mean
  filter  optional band-walk filter: skip if ≥4 of the last 5 closes were beyond ±1σ
  entry   next bar's OPEN (no look-ahead)
  stop    beyond the last 3 bars' extreme + 0.25·ATR14
  target  the 20-period mean at signal time
  exit    stop first if both hit in one bar (conservative); time exit after max_hold

``sweep`` splits history 70/30: parameters are chosen on the first 70% (in-sample)
and judged on the last 30% (out-of-sample) — pick settings by OOS, never IS alone.
"""

from __future__ import annotations

import itertools
import math
from typing import Dict, List, Sequence, Tuple

Bar = Tuple  # (date, open, high, low, close)

GRID = {"period": [14, 20, 30], "k": [1.8, 2.0, 2.5], "max_hold": [5, 10, 20],
        "walk_filter": [False, True]}


def _bands(closes: Sequence[float], i: int, period: int):
    w = closes[i - period + 1: i + 1]
    m = sum(w) / period
    sd = math.sqrt(sum((x - m) ** 2 for x in w) / period)
    return m, sd


def _atr(bars: Sequence[Bar], i: int, n: int = 14) -> float:
    trs = [max(bars[j][2] - bars[j][3], abs(bars[j][2] - bars[j - 1][4]),
               abs(bars[j][3] - bars[j - 1][4])) for j in range(max(1, i - n + 1), i + 1)]
    return sum(trs) / len(trs) if trs else 0.0


def simulate(bars: Sequence[Bar], period: int = 20, k: float = 2.0, max_hold: int = 10,
             walk_filter: bool = False, cost_r: float = 0.05) -> Dict:
    """Run the fade over bars (oldest-first). Returns trades + summary metrics."""
    closes = [float(b[4]) for b in bars]
    trades: List[Dict] = []
    i = period + 6
    while i < len(bars) - 1:
        m0, s0 = _bands(closes, i - 1, period)
        m1, s1 = _bands(closes, i, period)
        side = 0
        if s0 > 0 and closes[i - 1] >= m0 + k * s0 and closes[i] < m1 + k * s1:
            side = -1                                     # fade a high extreme
        elif s0 > 0 and closes[i - 1] <= m0 - k * s0 and closes[i] > m1 - k * s1:
            side = 1
        if side and walk_filter:
            zs = []
            for j in range(i - 4, i + 1):
                mj, sj = _bands(closes, j, period)
                zs.append((closes[j] - mj) / sj if sj else 0)
            if sum((z >= 1) if side < 0 else (z <= -1) for z in zs) >= 4:
                side = 0
        if not side:
            i += 1
            continue
        a = _atr(bars, i)
        entry = float(bars[i + 1][1])
        if side < 0:
            stop = max(b[2] for b in bars[i - 2: i + 1]) + 0.25 * a
        else:
            stop = min(b[3] for b in bars[i - 2: i + 1]) - 0.25 * a
        target = m1
        risk = abs(entry - stop)
        if risk <= 0 or (target - entry) * side <= 0:   # gapped past the mean / bad geometry
            i += 1
            continue
        exit_px, exit_i, why = None, None, "time"
        for j in range(i + 1, min(len(bars), i + 1 + max_hold)):
            hi, lo = float(bars[j][2]), float(bars[j][3])
            hit_stop = hi >= stop if side < 0 else lo <= stop
            hit_tp = lo <= target if side < 0 else hi >= target
            if hit_stop:
                exit_px, exit_i, why = stop, j, "stop"
                break
            if hit_tp:
                exit_px, exit_i, why = target, j, "target"
                break
        if exit_px is None:
            exit_i = min(len(bars) - 1, i + max_hold)
            exit_px = float(bars[exit_i][4])
        r = (exit_px - entry) * side / risk - cost_r
        trades.append({"date": str(bars[i + 1][0]), "side": "BUY" if side > 0 else "SELL",
                       "entry": round(entry, 5), "exit": round(exit_px, 5), "why": why,
                       "r": round(r, 3)})
        i = exit_i + 1                                    # one position at a time
    return {"params": {"period": period, "k": k, "max_hold": max_hold,
                       "walk_filter": walk_filter, "cost_r": cost_r},
            **metrics([t["r"] for t in trades]), "trades": trades}


def metrics(rs: List[float]) -> Dict:
    if not rs:
        return {"n": 0, "win_rate": None, "expectancy_r": None, "total_r": 0.0,
                "profit_factor": None, "max_dd_r": 0.0}
    wins = [r for r in rs if r > 0]
    losses = [-r for r in rs if r <= 0]
    eq = peak = dd = 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {"n": len(rs), "win_rate": round(len(wins) / len(rs), 3),
            "expectancy_r": round(sum(rs) / len(rs), 3), "total_r": round(sum(rs), 2),
            "profit_factor": round(sum(wins) / sum(losses), 2) if losses and sum(losses) else None,
            "max_dd_r": round(dd, 2)}


def sweep(bars: Sequence[Bar], grid: Dict = None, split: float = 0.7, min_trades: int = 5) -> Dict:
    """Grid search on the in-sample part, report every config's OOS result too.
    Ranked by OOS expectancy among configs with ≥ min_trades in BOTH halves."""
    grid = grid or GRID
    cut = int(len(bars) * split)
    is_bars, oos_bars = bars[:cut], bars[max(0, cut - 40):]   # 40-bar warm-up overlap
    rows = []
    for combo in itertools.product(*grid.values()):
        p = dict(zip(grid.keys(), combo))
        a = simulate(is_bars, **p)
        b = simulate(oos_bars, **p)
        rows.append({"params": p,
                     "in_sample": {k: a[k] for k in ("n", "win_rate", "expectancy_r", "total_r", "profit_factor", "max_dd_r")},
                     "out_of_sample": {k: b[k] for k in ("n", "win_rate", "expectancy_r", "total_r", "profit_factor", "max_dd_r")}})
    ok = [r for r in rows if r["in_sample"]["n"] >= min_trades and r["out_of_sample"]["n"] >= min_trades]
    ok.sort(key=lambda r: (r["out_of_sample"]["expectancy_r"] or -9), reverse=True)
    best = ok[0] if ok else None
    verdict = ("NO ROBUST CONFIG — not enough trades or no positive OOS expectancy"
               if not best or (best["out_of_sample"]["expectancy_r"] or 0) <= 0 else
               "POSITIVE OOS — forward-test on demo before trusting it")
    return {"bars": len(bars), "configs": len(rows), "eligible": len(ok),
            "best": best, "top5": ok[:5], "verdict": verdict}
