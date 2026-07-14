"""Backtest the cross-session CBDR confluence — Asian premium → London discount.

Replays history day-by-day: build each day's Asian CBDR box, arm the confluence
limit(s), then walk the rest of the day's bars to see whether price (1) filled the
limit and (2) reached the target before the stop. Reports hit-rate, average pips,
and — critically — the split BY WEEKLY BIAS, so we learn which regime the edge
actually works in before risking a live account.

Pure & deterministic: feed it timestamped intraday bars grouped per day plus a
per-day weekly bias. No network, no clock — the caller supplies the data.

Bars are (datetime, open, high, low, close), oldest-first. Sessions are UTC.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

from cbdr.engine import build_cbdr, cbdr_box
from cbdr.confluence import cross_session_confluence
from gold.risk_engine import GOLD_PIP

Bar = Tuple[object, float, float, float, float]

# UTC session spans for building each box (start inclusive, end exclusive).
ASIA = (0, 8)
LONDON = (8, 13)


def _hour(ts) -> Optional[int]:
    if hasattr(ts, "hour"):
        return ts.hour
    try:
        return int(str(ts).replace("T", " ").split(" ")[1][:2])
    except (IndexError, ValueError):
        return None


def _day(ts) -> str:
    return ts.date().isoformat() if hasattr(ts, "date") else str(ts)[:10]


def _box(bars: Sequence[Bar], span: Tuple[int, int]):
    """A CBDR box from the bars whose UTC hour is in ``span``, or None."""
    sel = [b for b in bars if (_hour(b[0]) is not None and span[0] <= _hour(b[0]) < span[1])]
    if len(sel) < 2:
        return None
    hi, lo = cbdr_box([b[2] for b in sel], [b[3] for b in sel])
    if hi <= lo:
        return None
    return build_cbdr(hi, lo)


def _simulate(order: dict, forward: Sequence[Bar]) -> dict:
    """Walk ``forward`` bars: did the limit FILL, then hit target-1 or the stop?

    Returns {filled, outcome: win|loss|open, pips, r}. 'win' = first target reached
    before stop; pips is signed favourable excursion to the target (or to stop on a
    loss). One partial target (targets[0]) defines the win for a clean hit-rate.
    """
    side = order["side"]
    entry, stop = order["entry"], order["stop"]
    target = order["targets"][0] if order.get("targets") else None
    risk = abs(entry - stop)
    filled = False
    for b in forward:
        hi, lo = b[2], b[3]
        if not filled:
            # limit fills when price trades to it
            if (side == "short" and hi >= entry) or (side == "long" and lo <= entry):
                filled = True
            else:
                continue
        # once filled, check stop / target on the same or later bars
        if side == "short":
            if hi >= stop:
                return {"filled": True, "outcome": "loss",
                        "pips": -round(risk / GOLD_PIP, 1), "r": -1.0}
            if target is not None and lo <= target:
                pips = round((entry - target) / GOLD_PIP, 1)
                return {"filled": True, "outcome": "win", "pips": pips,
                        "r": round((entry - target) / risk, 2) if risk else 0.0}
        else:  # long
            if lo <= stop:
                return {"filled": True, "outcome": "loss",
                        "pips": -round(risk / GOLD_PIP, 1), "r": -1.0}
            if target is not None and hi >= target:
                pips = round((target - entry) / GOLD_PIP, 1)
                return {"filled": True, "outcome": "win", "pips": pips,
                        "r": round((target - entry) / risk, 2) if risk else 0.0}
    return {"filled": filled, "outcome": "open", "pips": 0.0, "r": 0.0}


def backtest(days: Dict[str, Sequence[Bar]], weekly_bias: Dict[str, str],
             macro_bias: Optional[Dict[str, str]] = None,
             min_score: int = 50, use_london_target: bool = True) -> dict:
    """Replay the confluence over ``days`` (date → bars) → aggregate stats.

    ``weekly_bias``/``macro_bias`` map date → 'long'|'short'|'neutral'. For each
    day: build the Asian box (and London box for the target/geometry), arm the
    scored limits, and simulate each against the bars AFTER the Asian session.

    Returns overall {trades, filled, wins, losses, open, hit_rate, avg_pips,
    total_pips, expectancy_pips} plus the SAME broken out ``by_bias`` and
    ``by_side``. The by_bias split is the deliverable: it tells you which weekly
    regime to trade this in.
    """
    macro_bias = macro_bias or {}
    trades: List[dict] = []
    for date in sorted(days):
        bars = sorted(days[date], key=lambda b: str(b[0]))
        asian = _box(bars, ASIA)
        if asian is None:
            continue
        london = _box(bars, LONDON) if use_london_target else None
        wk = weekly_bias.get(date, "neutral")
        mc = macro_bias.get(date, "neutral")
        conf = cross_session_confluence(asian, london, weekly_bias=wk, macro_bias=mc,
                                        min_score=min_score)
        # simulate each armed limit against bars AFTER the Asian session closes
        forward = [b for b in bars if (_hour(b[0]) is not None and _hour(b[0]) >= ASIA[1])]
        for o in conf["orders"]:
            res = _simulate(o, forward)
            trades.append({"date": date, "side": o["side"], "score": o["score"],
                           "conviction": o["conviction"], "weekly_bias": wk, **res})

    return {
        "params": {"min_score": min_score, "use_london_target": use_london_target,
                   "days": len(days)},
        "overall": _agg(trades),
        "by_bias": {b: _agg([t for t in trades if t["weekly_bias"] == b])
                    for b in ("long", "short", "neutral")},
        "by_side": {s: _agg([t for t in trades if t["side"] == s])
                    for s in ("long", "short")},
        "trades": trades,
    }


def _agg(trades: List[dict]) -> dict:
    """Aggregate a trade list → hit-rate / avg-pips / expectancy."""
    filled = [t for t in trades if t["filled"]]
    settled = [t for t in filled if t["outcome"] in ("win", "loss")]
    wins = [t for t in settled if t["outcome"] == "win"]
    losses = [t for t in settled if t["outcome"] == "loss"]
    n = len(settled)
    total_pips = round(sum(t["pips"] for t in settled), 1)
    return {
        "trades": len(trades), "filled": len(filled), "settled": n,
        "wins": len(wins), "losses": len(losses),
        "open": len([t for t in filled if t["outcome"] == "open"]),
        "hit_rate": round(len(wins) / n, 3) if n else None,
        "avg_win_pips": round(sum(t["pips"] for t in wins) / len(wins), 1) if wins else None,
        "avg_loss_pips": round(sum(t["pips"] for t in losses) / len(losses), 1) if losses else None,
        "total_pips": total_pips,
        "expectancy_pips": round(total_pips / n, 1) if n else None,
    }
