"""Sell-setup backtest — validate the Optimus premium-retest SELL logic on history.

The strategy under test (the sells you run): when price rallies UP into a premium
SELL-retest level (4200 daily OB, 4135 sweep, 4110/4094/4075 …) and REJECTS (bar high
tags the level, close comes back below it), sell at the level with a stop just above and
target the next demand floor below (…4002 → 3885). One position at a time.

Pure + stdlib-only over OHLC bars — runs on fetched history live, and deterministic in
tests. Reports trades, win-rate, avg win/loss pips, total pips, expectancy and profit
factor, plus the worst losing streak — the honest read before pointing size at it.
"""

from __future__ import annotations

from typing import List, Optional, Sequence

from gold.risk_engine import GOLD_PIP


def _ohlc(bar):
    if isinstance(bar, dict):
        return (float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"]))
    return (float(bar[1]), float(bar[2]), float(bar[3]), float(bar[4]))


def _next_floor_below(level: float, floors: Sequence[float]) -> Optional[float]:
    below = [f for f in floors if f < level]
    return max(below) if below else None


def backtest_sells(bars: Sequence, levels: Sequence[float], floors: Sequence[float],
                   stop_buffer: float = 3.0, approach_tol: float = 1.0) -> dict:
    """Walk the bars; sell each premium-level reject; exit at the next floor (win) or
    the stop above the level (loss). ``stop_buffer`` = price above the level for the stop."""
    levels = sorted(set(float(l) for l in levels), reverse=True)
    floors = sorted(set(float(f) for f in floors), reverse=True)
    trades: List[dict] = []
    open_trade = None

    for bar in bars:
        o, h, l, c = _ohlc(bar)
        if open_trade is None:
            # look for a reject at any level this bar tagged (high >= level, close back below)
            for lv in levels:
                if h >= lv - approach_tol and c < lv:
                    target = _next_floor_below(lv, floors)
                    if target is None:
                        continue
                    entry = lv
                    stop = lv + stop_buffer
                    open_trade = {"level": lv, "entry": entry, "stop": stop, "target": target}
                    break
        else:
            # manage the open short. Conservative: if the same bar hits BOTH, count the stop.
            hit_stop = h >= open_trade["stop"]
            hit_tgt = l <= open_trade["target"]
            if hit_stop:
                pips = -(open_trade["stop"] - open_trade["entry"]) / GOLD_PIP
                trades.append({**open_trade, "result": "loss", "pips": round(pips, 1)})
                open_trade = None
            elif hit_tgt:
                pips = (open_trade["entry"] - open_trade["target"]) / GOLD_PIP
                trades.append({**open_trade, "result": "win", "pips": round(pips, 1)})
                open_trade = None

    wins = [t for t in trades if t["result"] == "win"]
    losses = [t for t in trades if t["result"] == "loss"]
    n = len(trades)
    total_pips = round(sum(t["pips"] for t in trades), 1)
    gross_win = sum(t["pips"] for t in wins)
    gross_loss = -sum(t["pips"] for t in losses)
    # worst losing streak
    streak = worst = 0
    for t in trades:
        streak = streak + 1 if t["result"] == "loss" else 0
        worst = max(worst, streak)

    return {
        "trades": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n * 100, 1) if n else 0.0,
        "total_pips": total_pips,
        "avg_win_pips": round(gross_win / len(wins), 1) if wins else 0.0,
        "avg_loss_pips": round(-gross_loss / len(losses), 1) if losses else 0.0,
        "expectancy_pips": round(total_pips / n, 1) if n else 0.0,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "worst_losing_streak": worst,
        "open_at_end": open_trade,
        "levels_tested": levels, "floors": floors,
        "note": (f"{len(wins)}/{n} wins ({round(len(wins)/n*100,1) if n else 0}%), "
                 f"{total_pips:+.0f} pips, expectancy {round(total_pips/n,1) if n else 0}/trade"
                 if n else "no sell setups triggered on this window"),
    }


def backtest_optimus_sells(bars: Sequence, stop_buffer: float = 3.0) -> dict:
    """Backtest using the live Optimus sell map (SELL_RETEST_LEVELS + floors → TP)."""
    from gold import optimus as gop
    floors = list(gop.PATH_FLOORS) + [gop.PATH_TP]
    return backtest_sells(bars, gop.SELL_RETEST_LEVELS, floors, stop_buffer=stop_buffer)
