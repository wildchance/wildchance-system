"""Backtest the cross-session CBDR confluence — Asian premium → London discount.

Replays history day-by-day: build each day's Asian CBDR box, arm the confluence
limit(s), then walk the rest of the day's bars to see whether price (1) filled the
limit and (2) reached the target before the stop. Reports hit-rate, average pips,
and — critically — the split BY WEEKLY BIAS, so we learn which regime the edge
actually works in before risking a live account.

Pure & deterministic: feed it hourly bars grouped per day plus a per-day weekly
bias. Bars are dicts ``{date, hour, open, high, low, close}`` in UTC — the shape
``services.ohlc_service.fetch_hourly_raw`` returns (hour PRESERVED, not collapsed
to a date the way fetch_ohlc does — that difference is what this engine needs).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from cbdr.engine import build_cbdr, cbdr_box
from cbdr.confluence import cross_session_confluence
from gold.risk_engine import GOLD_PIP

# UTC session spans for building each box (start inclusive, end exclusive).
ASIA = (0, 8)
LONDON = (8, 13)


def _box(bars: Sequence[dict], span):
    """A CBDR box from the hourly bars whose UTC hour is in ``span``, or None."""
    sel = [b for b in bars if span[0] <= b["hour"] < span[1]]
    if len(sel) < 2:
        return None
    hi, lo = cbdr_box([b["high"] for b in sel], [b["low"] for b in sel])
    return build_cbdr(hi, lo) if hi > lo else None


def _simulate(order: dict, forward: Sequence[dict]) -> dict:
    """Walk ``forward`` bars: did the limit FILL, then hit target-1 or the stop?

    Returns {filled, outcome: win|loss|open, pips, r}. 'win' = first target reached
    before the stop; one partial target (targets[0]) defines the win for a clean
    hit-rate.
    """
    side = order["side"]
    entry, stop = order["entry"], order["stop"]
    target = order["targets"][0] if order.get("targets") else None
    risk = abs(entry - stop)
    filled = False
    for b in forward:
        hi, lo = b["high"], b["low"]
        if not filled:
            if (side == "short" and hi >= entry) or (side == "long" and lo <= entry):
                filled = True
            else:
                continue
        if side == "short":
            if hi >= stop:
                return {"filled": True, "outcome": "loss",
                        "pips": -round(risk / GOLD_PIP, 1), "r": -1.0}
            if target is not None and lo <= target:
                return {"filled": True, "outcome": "win",
                        "pips": round((entry - target) / GOLD_PIP, 1),
                        "r": round((entry - target) / risk, 2) if risk else 0.0}
        else:
            if lo <= stop:
                return {"filled": True, "outcome": "loss",
                        "pips": -round(risk / GOLD_PIP, 1), "r": -1.0}
            if target is not None and hi >= target:
                return {"filled": True, "outcome": "win",
                        "pips": round((target - entry) / GOLD_PIP, 1),
                        "r": round((target - entry) / risk, 2) if risk else 0.0}
    return {"filled": filled, "outcome": "open", "pips": 0.0, "r": 0.0}


def backtest(days: Dict[str, Sequence[dict]], weekly_bias: Dict[str, str],
             macro_bias: Optional[Dict[str, str]] = None,
             min_score: int = 50, use_london_target: bool = True,
             regime_gated: bool = True, stop_sd: float = 2.0) -> dict:
    """Replay the confluence over ``days`` (date → hourly bars) → aggregate stats.

    ``weekly_bias``/``macro_bias`` map date → 'long'|'short'|'neutral'. For each
    day: build the Asian box (and London box for the target/geometry), arm the
    scored limits, and simulate each against the bars AFTER the Asian session.

    ``stop_sd`` sets the stop distance (2.0 = default −2SD; try 1.5/1.0 to shrink
    the losses). Results include a chronological **train/test split** (first half
    vs unseen second half) — a fix is only real if it holds on the TEST half.
    """
    macro_bias = macro_bias or {}
    trades: List[dict] = []
    for date in sorted(days):
        bars = sorted(days[date], key=lambda b: b["hour"])
        asian = _box(bars, ASIA)
        if asian is None:
            continue
        london = _box(bars, LONDON) if use_london_target else None
        wk = weekly_bias.get(date, "neutral")
        mc = macro_bias.get(date, "neutral")
        # regime_gated=True → default engine policy (premium-sell only in a confirmed
        # downtrend); False → force both sides on to reproduce the pre-rule numbers.
        conf = cross_session_confluence(asian, london, weekly_bias=wk, macro_bias=mc,
                                        min_score=min_score, stop_sd=stop_sd,
                                        enable_sell=None if regime_gated else True)
        forward = [b for b in bars if b["hour"] >= ASIA[1]]
        for o in conf["orders"]:
            res = _simulate(o, forward)
            trades.append({"date": date, "side": o["side"], "score": o["score"],
                           "conviction": o["conviction"], "weekly_bias": wk, **res})

    # Chronological train/test split at the median trade DATE (out-of-sample check).
    dated = sorted({t["date"] for t in trades})
    cut = dated[len(dated) // 2] if dated else None
    train = [t for t in trades if cut and t["date"] < cut]
    test = [t for t in trades if cut and t["date"] >= cut]

    return {
        "params": {"min_score": min_score, "use_london_target": use_london_target,
                   "regime_gated": regime_gated, "stop_sd": stop_sd, "days": len(days)},
        "overall": _agg(trades),
        "train": {"until": cut, **_agg(train)},        # first half (in-sample)
        "test": {"from": cut, **_agg(test)},           # second half (OUT-of-sample)
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
