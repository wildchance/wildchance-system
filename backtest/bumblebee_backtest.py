"""Backtest gold.bumblebee — sweep→CONTINUITY (bias-gated) toward a target, train/test.

Bumblebee reads each session as ANCHOR (1H range) → SWEEP (one side grabbed) →
CONTINUITY (trade toward the HTF OB, in confluence with the daily bias). Unlike the
AMD triad — which FADES the sweep unconditionally and died at −0.4R — Bumblebee trades
the CONTINUATION and GATES it on the higher-timeframe bias: sell a swept high only when
bias isn't bullish, buy a swept low only when bias isn't bearish. This harness is the
direct test of whether that bias gate + continuation target beats the raw fade.

It drives the REAL engine (gold.bumblebee.detect_sweep / continuity_call) over flat
UTC-hourly bars. For each date & session it builds the anchor/sweep/continuity triad
from three consecutive 1H candles, derives the signal, enters at the CONTINUITY OPEN,
stops beyond the swept extreme, targets a fixed 'cheetah' distance (default 250 pips)
toward continuation, and holds to a deadline. P&L in R. Reports hit-rate, expectancy_R,
a chronological TRAIN/TEST split, by-side, and by-session.

No lookahead: the daily-bias SMA is measured only through the SWEEP bar's close (all
data available at the continuity open); entry is the continuity open, never a close
we haven't seen. Bars: {date, hour, open, high, low, close} UTC (fetch_hourly_raw).
Pure & deterministic — the caller supplies the bars.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from gold.bumblebee import SESSIONS, detect_sweep, continuity_call

# Per-anchor session-close hour (UTC) for hold_to_session: carry the continuation
# toward the end of its session window (Venom: Asian 14-22, London 22-06, NY 06-14).
SESSION_CLOSE = {14: 22, 22: 6, 6: 14, 0: 6, 7: 14}


def _simulate(side: str, entry: float, stop: float, target: float,
              forward: Sequence[dict]) -> dict:
    """Walk ``forward`` bars → win / loss / deadline (in R). Stop-first on a tie."""
    risk = abs(entry - stop)
    if risk <= 0:
        return {"outcome": "skip", "r": 0.0}
    long = side == "long"
    for b in forward:
        hi, lo = b["high"], b["low"]
        hit_stop = lo <= stop if long else hi >= stop
        hit_tgt = hi >= target if long else lo <= target
        if hit_stop:                         # stop checked first = conservative
            return {"outcome": "loss", "r": -1.0}
        if hit_tgt:
            reward = (target - entry) if long else (entry - target)
            return {"outcome": "win", "r": round(reward / risk, 3)}
    if forward:                              # deadline — mark to the last close
        c = forward[-1]["close"]
        pnl = (c - entry) if long else (entry - c)
        return {"outcome": "win" if pnl > 0 else "loss", "r": round(pnl / risk, 3)}
    return {"outcome": "open", "r": 0.0}


def _sma_bias(flat: Sequence[dict], idx: int, window: int) -> Optional[str]:
    """Trailing-SMA daily bias through bar ``idx`` (inclusive) — long above, short below."""
    lo = max(0, idx - window + 1)
    closes = [flat[j]["close"] for j in range(lo, idx + 1)]
    if not closes:
        return None
    sma = sum(closes) / len(closes)
    return "long" if flat[idx]["close"] > sma else "short"


def backtest(bars: Sequence[dict], sessions: Optional[Sequence[str]] = None,
             buffer: float = 0.0, target_pips: float = 250.0, pip: float = 0.1,
             max_hold: int = 10, hold_to_session: bool = False,
             require_bias: bool = False, bias_window: int = 50) -> dict:
    """Replay Bumblebee over flat UTC-hourly ``bars`` → stats + splits.

    ``sessions`` — subset of gold.bumblebee.SESSIONS (default: all). Each is an
    (anchor, sweep, continuity) hour triad.
    ``target_pips`` — cheetah continuation target distance (250 = +25.0 at pip 0.1).
    ``require_bias`` — gate on the trailing-SMA bias (the engine's HTF confluence):
    sell swept-high only if bias != long; buy swept-low only if bias != short. When
    False the continuity is ungated (the raw comparison to the AMD fade).
    ``hold_to_session`` — deadline at the per-session close hour, else ``max_hold`` bars.
    """
    names = list(sessions) if sessions else list(SESSIONS)
    flat = sorted(bars, key=lambda b: (b["date"], b["hour"]))
    n = len(flat)
    trades: List[dict] = []

    for i, anchor in enumerate(flat):
        if i + 2 >= n:
            break
        for name in names:
            cfg = SESSIONS.get(name)
            if not cfg or anchor["hour"] != cfg["anchor"]:
                continue
            sweep_bar, cont_bar = flat[i + 1], flat[i + 2]
            # the two following bars must be the real sweep/continuity hours (no gap)
            if sweep_bar["hour"] != (cfg["anchor"] + 1) % 24:
                continue
            if cont_bar["hour"] != (cfg["anchor"] + 2) % 24:
                continue

            swept = detect_sweep(anchor["high"], anchor["low"], [sweep_bar])
            bias = _sma_bias(flat, i + 1, bias_window) if require_bias else None
            call = continuity_call(swept["side"], bias)
            if call["signal"] not in ("BUY", "SELL"):
                continue

            entry = round(cont_bar["open"], 3)
            if call["signal"] == "SELL":
                side = "short"
                stop = round(sweep_bar["high"] + buffer, 3)
                target = round(entry - target_pips * pip, 3)
            else:
                side = "long"
                stop = round(sweep_bar["low"] - buffer, 3)
                target = round(entry + target_pips * pip, 3)

            if hold_to_session:
                close_h = SESSION_CLOSE.get(cfg["anchor"])
                fwd: List[dict] = []
                for j in range(i + 2, min(n, i + 2 + 48)):
                    fwd.append(flat[j])
                    if close_h is not None and flat[j]["hour"] == close_h:
                        break
                forward = fwd
            else:
                forward = list(flat[i + 2: i + 2 + max_hold])

            res = _simulate(side, entry, stop, target, forward)
            if res["outcome"] == "skip":
                continue
            trades.append({"date": anchor["date"], "session": name, "side": side,
                           "confluence": call.get("confluence", False),
                           "outcome": res["outcome"], "r": res["r"]})

    dated = sorted({t["date"] for t in trades})
    cut = dated[len(dated) // 2] if dated else None
    train = [t for t in trades if cut and t["date"] < cut]
    test = [t for t in trades if cut and t["date"] >= cut]

    return {
        "params": {"sessions": names, "buffer": buffer, "target_pips": target_pips,
                   "pip": pip, "max_hold": max_hold, "hold_to_session": hold_to_session,
                   "require_bias": require_bias, "bias_window": bias_window,
                   "days": len({b["date"] for b in flat})},
        "overall": _agg(trades),
        "train": {"until": cut, **_agg(train)},
        "test": {"from": cut, **_agg(test)},
        "by_side": {"long": _agg([t for t in trades if t["side"] == "long"]),
                    "short": _agg([t for t in trades if t["side"] == "short"])},
        "by_session": {s: _agg([t for t in trades if t["session"] == s]) for s in names},
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
