"""Backtest the AMD hourly-triad (range→sweep→reaction) — event-driven, train/test.

For every date and trigger hour H (14/7/0), build the triad from the H, H+1, H+2
UTC candles, and if a fade-the-sweep signal fires, hold it forward (crossing
midnight toward the session close) until the target or stop is hit — or a deadline.
P&L is measured in R (target reached = +R, stop = −1R, deadline = signed
distance/risk). Reports hit-rate, expectancy_R, a chronological TRAIN/TEST split,
a BY-TRIGGER split (does the 14:00 triad work but not the 07:00?), and a BY-SIDE
split (long-fades vs short-fades).

Two exit models for the "hold the reaction toward a session move" playbook:
  target_pips      project the target a fixed distance (250/500) instead of the
                   tight opposite-range-side — the higher-R hold-for-a-move target.
  hold_to_session  deadline = the per-trigger SESSION-CLOSE hour (Asian 14:00 →
                   pre-London 06:00, …) rather than a flat max_hold bar count.

An optional daily-bias filter (require_bias): don't fade WITH the longer trend —
long only if the reaction close is above the trailing SMA, short only if below.

Bars are dicts ``{date, hour, open, high, low, close}`` in UTC (fetch_hourly_raw).
Pure & deterministic: the caller supplies the bars.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from indicators.amd_triad import TRIGGERS, triad_signal

# When holding to the SESSION boundary (not a flat bar count): the UTC hour to
# force-close each trigger's triad. Asian 14:00 triad carries to pre-London 06:00;
# the 07:00 triad to the NY close (~14:00); the 00:00 triad to the London close (~07:00).
SESSION_CLOSE = {14: 6, 7: 14, 0: 7}


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


def _forward_slice(flat: Sequence[dict], start: int, trigger: int,
                   max_hold: int, hold_to_session: bool) -> List[dict]:
    """The bars to hold across: to the per-trigger session-close hour, or max_hold bars."""
    if hold_to_session:
        close_h = SESSION_CLOSE.get(trigger)
        if close_h is not None:
            fwd: List[dict] = []
            for j in range(start, min(len(flat), start + 48)):   # 48h safety cap
                fwd.append(flat[j])
                if flat[j]["hour"] == close_h:
                    break
            return fwd
    return list(flat[start: start + max_hold])


def backtest(bars: Sequence[dict], triggers=TRIGGERS, buffer: float = 0.0,
             max_hold: int = 12, require_bias: bool = False, bias_window: int = 50,
             target_pips: Optional[float] = None, pip: float = 0.1,
             hold_to_session: bool = False) -> dict:
    """Replay the triad over a flat UTC-hourly ``bars`` list → stats + splits.

    Exit is whichever of {stop, target, deadline} comes first.

    ``target_pips`` — project the target a fixed distance from entry (e.g. 250/500
    pips = 25.0/50.0 at ``pip=0.1`` for gold) instead of the tight opposite-range
    side. The hold-for-a-move playbook: the reaction only confirms the reversal;
    the trade carries toward a 250–500 pip session target for a higher R multiple.

    ``hold_to_session`` — deadline is the per-trigger SESSION-CLOSE hour (Asian
    14:00 → pre-London 06:00, etc.), not ``max_hold`` bars. "Hold till nearing the
    close of each session." When False, the deadline is ``max_hold`` hours out.

    ``require_bias`` — only take the fade if it aligns with the trailing SMA
    (``bias_window`` closes ending at the reaction): long only above the SMA,
    short only below. Don't fade with the longer trend.

    Returns overall + train/test + by_trigger + by_side aggregates (R units).
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
            sig = triad_signal(r_bar, m_bar, x_bar, buffer=buffer,
                               target_pips=target_pips, pip=pip)
            if sig["signal"] not in ("LONG", "SHORT"):
                continue
            if require_bias:                       # don't fade WITH the longer trend
                react_i = pos[(date, h + 2)]
                lo = max(0, react_i - bias_window + 1)
                closes = [flat[j]["close"] for j in range(lo, react_i + 1)]
                sma = sum(closes) / len(closes) if closes else None
                if sma is not None:
                    if sig["side"] == "long" and x_bar["close"] <= sma:
                        continue
                    if sig["side"] == "short" and x_bar["close"] >= sma:
                        continue
            start = pos[(date, h + 2)] + 1
            forward = _forward_slice(flat, start, h, max_hold, hold_to_session)
            res = _simulate(sig, forward)
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
                   "require_bias": require_bias, "bias_window": bias_window,
                   "target_pips": target_pips, "pip": pip,
                   "hold_to_session": hold_to_session, "days": len(by_date)},
        "overall": _agg(trades),
        "train": {"until": cut, **_agg(train)},
        "test": {"from": cut, **_agg(test)},
        "by_trigger": {str(h): _agg([t for t in trades if t["trigger_hour"] == h])
                       for h in triggers},
        "by_side": {"long": _agg([t for t in trades if t["side"] == "long"]),
                    "short": _agg([t for t in trades if t["side"] == "short"])},
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
