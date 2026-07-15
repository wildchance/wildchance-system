"""Backtest the gold–silver ratio mean-reversion — event-driven, train/test split.

Walks an aligned daily ratio series: opens a position when |z| ≥ entry_z (and the
trend guard allows), closes it when the ratio reverts (|z| ≤ exit_z, a WIN) or the
relationship breaks (|z| ≥ stop_z, a LOSS). Trade P&L is the % change in the ratio
captured in the trade's favour — the return a dollar-neutral gold/silver pair earns.

Reports hit-rate, avg win/loss %, expectancy, and a chronological TRAIN/TEST split
(first half vs unseen second half) — the same out-of-sample gate as every other
strategy here. Pure & deterministic: the caller supplies the aligned closes.
"""

from __future__ import annotations

import statistics
from typing import Dict, List, Optional, Sequence, Tuple


def _z_at(window: Sequence[float]) -> Optional[float]:
    sd = statistics.stdev(window)
    return (window[-1] - statistics.fmean(window)) / sd if sd > 0 else None


def backtest(dates: Sequence[str], gold: Sequence[float], silver: Sequence[float],
             lookback: int = 20, entry_z: float = 2.0, exit_z: float = 0.5,
             stop_z: float = 3.5, trend_guard: bool = True,
             trend_window: int = 100, max_drift: float = 0.15,
             max_hold: int = 60) -> dict:
    """Replay the ratio mean-reversion over aligned daily closes → stats + train/test.

    ``dates``/``gold``/``silver`` are equal-length, date-sorted. A trade is one
    open→close round trip; ``max_hold`` force-closes a stale position (counts as the
    return at exit). Returns overall + train/test + by_side aggregates.
    """
    # Clean the aligned triples together — drop any bar with a bad/zero close so the
    # three arrays stay in lockstep (a single bad XAG tick otherwise desyncs ``ratio``
    # from ``n`` and IndexErrors the loop → the live 500).
    m = min(len(dates), len(gold), len(silver))
    clean = [(dates[i], gold[i], silver[i]) for i in range(m)
             if gold[i] and silver[i] and silver[i] > 0]
    dates = [c[0] for c in clean]
    gold = [c[1] for c in clean]
    silver = [c[2] for c in clean]
    n = len(dates)
    ratio = [gold[i] / silver[i] for i in range(n)]      # guaranteed length n
    trades: List[dict] = []

    pos = None            # {"side","entry_ratio","entry_date","entry_i"}
    for i in range(n):
        if i + 1 < lookback:
            continue
        window = ratio[i + 1 - lookback: i + 1]
        z = _z_at(window)
        if z is None:
            continue
        r = ratio[i]

        if pos is None:
            # entry — trend guard blocks structural drifts
            dr = None
            if i > trend_window and ratio[i - trend_window]:
                dr = (r - ratio[i - trend_window]) / ratio[i - trend_window]
            regime_ok = (not trend_guard) or dr is None or abs(dr) <= max_drift
            if regime_ok and z >= entry_z:
                pos = {"side": "short_ratio", "entry_ratio": r, "entry_date": dates[i], "entry_i": i}
            elif regime_ok and z <= -entry_z:
                pos = {"side": "long_ratio", "entry_ratio": r, "entry_date": dates[i], "entry_i": i}
        else:
            reverted = abs(z) <= exit_z
            broke = abs(z) >= stop_z
            stale = (i - pos["entry_i"]) >= max_hold
            last = i == n - 1
            if reverted or broke or stale or last:
                e = pos["entry_ratio"]
                ret = ((e - r) / e if pos["side"] == "short_ratio" else (r - e) / e) * 100.0
                trades.append({"side": pos["side"], "entry_date": pos["entry_date"],
                               "exit_date": dates[i], "return_pct": round(ret, 3),
                               "outcome": "win" if ret > 0 else "loss",
                               "exit_reason": ("revert" if reverted else "stop" if broke
                                               else "time" if stale else "end")})
                pos = None

    dated = sorted({t["entry_date"] for t in trades})
    cut = dated[len(dated) // 2] if dated else None
    train = [t for t in trades if cut and t["entry_date"] < cut]
    test = [t for t in trades if cut and t["entry_date"] >= cut]

    return {
        "params": {"lookback": lookback, "entry_z": entry_z, "exit_z": exit_z,
                   "stop_z": stop_z, "trend_guard": trend_guard, "max_drift": max_drift,
                   "days": n},
        "overall": _agg(trades),
        "train": {"until": cut, **_agg(train)},
        "test": {"from": cut, **_agg(test)},
        "by_side": {s: _agg([t for t in trades if t["side"] == s])
                    for s in ("long_ratio", "short_ratio")},
        "trades": trades,
    }


def _agg(trades: List[dict]) -> dict:
    wins = [t for t in trades if t["outcome"] == "win"]
    losses = [t for t in trades if t["outcome"] == "loss"]
    n = len(trades)
    total = round(sum(t["return_pct"] for t in trades), 2)
    return {
        "trades": n, "wins": len(wins), "losses": len(losses),
        "hit_rate": round(len(wins) / n, 3) if n else None,
        "avg_win_pct": round(sum(t["return_pct"] for t in wins) / len(wins), 3) if wins else None,
        "avg_loss_pct": round(sum(t["return_pct"] for t in losses) / len(losses), 3) if losses else None,
        "total_return_pct": total,
        "expectancy_pct": round(total / n, 3) if n else None,
    }
