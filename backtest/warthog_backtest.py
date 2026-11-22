"""Backtest gold.warthog — HTF liquidity-sweep + OTE catapult (limit fill), train/test.

Warthog is a LIMIT setup, not a market trigger: it frames the HTF impulse leg, places
the entry in the OTE band (0.62–0.79 retrace), stops beyond the swept extreme, and
targets the next liquidity at 2R/3R/5R. So the backtest models a PENDING LIMIT:

  at each HTF bar, read warthog(window); if LONG/SHORT with an OTE entry, place the
  limit → wait up to ``max_wait`` bars for price to pull back and FILL it → from the
  fill, take the ``rr`` target (+rr R) or the stop (−1R), else mark to the deadline.

Event-driven (one position at a time; the cursor jumps past each resolution) so the
same persistent leg isn't counted on every bar. P&L in R. Reports hit-rate,
expectancy_R, TRAIN/TEST, and by-side.

No lookahead: the limit fill and the stop/target are only ever checked on bars AFTER
the signal bar. Input ``raw`` is fetch_ohlc dicts {date,open,high,low,close} (or
[ts,o,h,l,c]) oldest-first — HTF (1day/4h/1h). Pure & deterministic.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence

from gold.warthog import warthog, to_ohlc


def backtest(raw: Sequence, window: int = 60, rr: float = 2.0, max_wait: int = 20,
             max_hold: int = 60, require_catapult: bool = False) -> dict:
    """Replay Warthog as a pending-limit strategy over HTF ``raw`` bars → stats + splits.

    ``window`` — HTF lookback fed to the engine each step (frames the impulse leg).
    ``rr`` — the target in R multiples of the entry→stop risk (2 = the first target).
    ``max_wait`` — bars the OTE limit stays live waiting for a fill before it's voided.
    ``max_hold`` — bars to hold a filled trade before marking to the close.
    ``require_catapult`` — only take setups that have retraced >60% (engine 'catapult').
    """
    tuples = to_ohlc(raw)
    dates = [b.get("date") if isinstance(b, dict) else None for b in raw]
    n = len(tuples)
    trades: List[dict] = []

    c = window
    while c < n - 1:
        read = warthog(tuples[max(0, c - window + 1): c + 1])
        if read.get("signal") not in ("LONG", "SHORT") or read.get("entry") is None:
            c += 1
            continue
        if require_catapult and not read.get("catapult"):
            c += 1
            continue
        side = "long" if read["signal"] == "LONG" else "short"
        entry, stop = read["entry"], read["stop"]
        risk = abs(entry - stop)
        if risk <= 0:
            c += 1
            continue
        target = entry + (rr * risk if side == "long" else -rr * risk)

        # 1) wait for the limit to FILL (price pulls back into the OTE entry)
        fill_idx = None
        for j in range(c + 1, min(n, c + 1 + max_wait)):
            _o, h, l, _c = tuples[j]
            if (side == "long" and l <= entry) or (side == "short" and h >= entry):
                fill_idx = j
                break
        if fill_idx is None:
            c += 1
            continue

        # 2) from the fill, resolve target / stop / deadline
        outcome, r, end = None, 0.0, fill_idx
        for k in range(fill_idx, min(n, fill_idx + max_hold)):
            _o, h, l, cl = tuples[k]
            hit_stop = l <= stop if side == "long" else h >= stop
            hit_tgt = h >= target if side == "long" else l <= target
            end = k
            if hit_stop:                     # stop first = conservative
                outcome, r = "loss", -1.0
                break
            if hit_tgt:
                outcome, r = "win", round(rr, 3)
                break
        if outcome is None:                  # deadline — mark to the last close
            cl = tuples[end][3]
            pnl = (cl - entry) if side == "long" else (entry - cl)
            r = round(pnl / risk, 3)
            outcome = "win" if pnl > 0 else "loss"

        trades.append({"date": dates[c], "side": side, "outcome": outcome, "r": r})
        c = end + 1                          # jump past the resolved trade (no overlap)

    dated = sorted({t["date"] for t in trades if t["date"]})
    cut = dated[len(dated) // 2] if dated else None
    train = [t for t in trades if cut and t["date"] and t["date"] < cut]
    test = [t for t in trades if cut and t["date"] and t["date"] >= cut]

    return {
        "params": {"window": window, "rr": rr, "max_wait": max_wait, "max_hold": max_hold,
                   "require_catapult": require_catapult, "bars": n},
        "overall": _agg(trades),
        "train": {"until": cut, **_agg(train)},
        "test": {"from": cut, **_agg(test)},
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
