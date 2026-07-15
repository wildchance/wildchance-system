"""Gold–silver ratio mean-reversion scan — fetch, align, signal, notify.

Pulls XAU/USD and XAG/USD daily closes, aligns them by date, and runs the pure
ratio z-score engine. Signal-only for now (a pairs trade is two dollar-neutral
legs — sizing/execution come after the edge is proven out-of-sample). Boot-safe.

Pairs with backtest.pairs_backtest — validate on history (train/test) before any
live use, exactly like the CBDR confluence.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

from services.ohlc_service import fetch_ohlc
from services.gold_scan import _tg
from indicators.pairs import pair_signal


def _day(d) -> str:
    return d.isoformat() if hasattr(d, "isoformat") else str(d)[:10]


async def _aligned(outputsize: int = 300) -> Tuple[List[str], List[float], List[float]]:
    """Fetch XAU + XAG daily, intersect dates, return (dates, gold, silver) sorted."""
    g = await fetch_ohlc("XAU/USD", "1day", outputsize)
    s = await fetch_ohlc("XAG/USD", "1day", outputsize)
    if not g or not s:
        return [], [], []
    gmap = {_day(r[0]): r[4] for r in g}
    smap = {_day(r[0]): r[4] for r in s}
    dates = sorted(set(gmap) & set(smap))
    return dates, [gmap[d] for d in dates], [smap[d] for d in dates]


async def scan(lookback: int = 20, entry_z: float = 2.0, exit_z: float = 0.5,
               stop_z: float = 3.5, trend_guard: bool = True,
               trend_window: int = 100, max_drift: float = 0.15,
               notify: bool = False) -> dict:
    dates, gold, silver = await _aligned(max(300, lookback + trend_window + 10))
    if len(dates) < lookback + 1:
        return {"signal": "FLAT", "reason": "no aligned XAU/XAG history"}
    sig = pair_signal(gold, silver, lookback=lookback, entry_z=entry_z, exit_z=exit_z,
                      stop_z=stop_z, trend_guard=trend_guard,
                      trend_window=trend_window, max_drift=max_drift)
    sig["as_of"] = dates[-1]
    sig["gold"] = gold[-1]
    sig["silver"] = silver[-1]
    if notify and sig.get("signal") in ("LONG_RATIO", "SHORT_RATIO"):
        sig["sent"] = await _tg(_format(sig))
    return sig


async def history_for_backtest(outputsize: int = 300):
    """(dates, gold, silver) aligned daily closes for the pairs backtest."""
    return await _aligned(outputsize)


def _format(sig: dict) -> str:
    arrow = "🟢 LONG ratio" if sig["signal"] == "LONG_RATIO" else "🔴 SHORT ratio"
    legs = sig.get("legs", {})
    leg_txt = "  ·  ".join(f"{'🟢 BUY' if v == 'buy' else '🔴 SELL'} {k}" for k, v in legs.items())
    return (f"⚖️ *GOLD–SILVER Pair* {arrow}  (z {sig['z']:+.2f})\n"
            f"_{sig['reason']}_\n"
            f"ratio {sig['ratio']}  ·  mean {sig['mean']}  ·  as of {sig.get('as_of')}\n"
            f"{leg_txt}\n"
            f"_exit at |z| ≤ {sig['exit_z']} (revert) · cut at |z| ≥ {sig['stop_z']}_")
