"""Backtest the AMD hourly-triad (range→sweep→reaction) — event-driven, train/test.

For every date and trigger hour H (14/7/0), build the triad from the H, H+1, H+2
UTC candles, and if a fade-the-sweep signal fires, hold it forward (crossing
midnight toward pre-London) until the target or stop is hit — or a ``max_hold``
deadline. P&L is measured in R (target reached = +R, stop = −1R, deadline = signed
distance/risk). Reports hit-rate, expectancy_R, a chronological TRAIN/TEST split,
and a BY-TRIGGER split (does the 14:00 triad work but not the 07:00?).

Bars are dicts ``{date, hour, open, high, low, close}`` in UTC (fetch_hourly_raw).
Pure & deterministic: the caller supplies the bars.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from indicators.amd_triad import TRIGGERS, triad_signal


def _simulate(sig: dict, forward: Sequence[dict]) -> dict:
    """Walk ``forward`` bars from the reaction close → win / loss / deadline (in R)."""
    entry, stop, target = sig["entry"], sig["stop"], sig["target"]
    risk = abs(entry - stop)
    if risk <= 0:
        return {"outcome": "skip", "r": 0.0}
    long = sig["side"] == "long"
    for b in forward:
        hi, lo = b["high"], b["low"]
        hit_stop = lo <= stop if long else hi >= stop
        hit_tgt = hi >= target if long else lo <= target
        if hit_stop and hit_tgt:            # both in one bar → assume stop first (conservative)
            return {"outcome": "loss", "r": -1.0}
        if hit_stop:
            return {"outcome": "loss", "r": -1.0}
        if hit_tgt:
            reward = (target - entry) if long else (entry - target)
            return {"outcome": "win", "r": round(reward / risk, 3)}
    if forward:                             # deadline — close at the last bar
        c = forward[-1]["close"]
        pnl = (c - entry) if long else (entry - c)
        return {"outcome": "win" if pnl > 0 else "loss", "r": round(pnl / risk, 3)}
    return {"outcome": "open", "r": 0.0}


def backtest(bars: Sequence[dict], triggers=TRIGGERS, buffer: float = 0.0,
             max_hold: int = 12) -> dict:
    """Replay the triad over a flat UTC-hourly ``bars`` list → stats + splits.

    ``max_hold`` = how many hours after the reaction to hold before force-closing
    (12h from the 14:00 triad reaches ~04:00 next day, past pre-London). Returns
    overall + train/test + by_trigger aggregates (R units).
    """
    flat = sorted(bars, key=lambda b: (b["date"], b["hour"]))
    pos = {(b["date"], b["hour"]): i for i, b in enumerate(flat)}
    by_date: Dict[str, set] = {}
    for b in flat:
        by_date.setdefault(b["date"], set()).add(b["hour"])

    trades: List[dict] = []
    for date in sorted(by_date):
        hours = by_date[date]
        for h in triggers:
            if not ({h, h + 1, h + 2} <= hours):
                continue
            r_bar = flat[pos[(date, h)]]
            m_bar = flat[pos[(date, h + 1)]]
            x_bar = flat[pos[(date, h + 2)]]
            sig = triad_signal(r_bar, m_bar, x_bar, buffer=buffer)
            if sig["signal"] not in ("LONG", "SHORT"):
                continue
            start = pos[(date, h + 2)] + 1
            res = _simulate(sig, flat[start: start + max_hold])
            if res["outcome"] == "skip":
                continue
            trades.append({"date": date, "trigger_hour": h, "side": sig["side"],
                           "outcome": res["outcome"], "r": res["r"]})

    dated = sorted({t["date"] for t in trades})
    cut = dated[len(dated) // 2] if dated else None
    train = [t for t in trades if cut and t["date"] < cut]
    test = [t for t in trades if cut and t["date"] >= cut]

    return {
        "params": {"triggers": list(triggers), "buffer": buffer, "max_hold": max_hold,
                   "days": len(by_date)},
        "overall": _agg(trades),
        "train": {"until": cut, **_agg(train)},
        "test": {"from": cut, **_agg(test)},
        "by_trigger": {str(h): _agg([t for t in trades if t["trigger_hour"] == h])
                       for h in triggers},
        "trades": trades,
    }


def _agg(trades: List[dict]) -> dict:
    wins = [t for t in trades if t["outcome"] == "win"]
    losses = [t for t in trades if t["outcome"] == "loss"]
    n = len(trades)
    total_r = round(sum(t["r"] for t in trades), 3)
    return {
        "trades": n, "wins": len(wins), "losses": len(losses),
        "hit_rate": round(len(wins) / n, 3) if n else None,
        "avg_win_r": round(sum(t["r"] for t in wins) / len(wins), 3) if wins else None,
        "avg_loss_r": round(sum(t["r"] for t in losses) / len(losses), 3) if losses else None,
        "total_r": total_r,
        "expectancy_r": round(total_r / n, 3) if n else None,
    }
