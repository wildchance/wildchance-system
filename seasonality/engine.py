"""Seasonality — the calendar-month historical bias of an instrument (pure).

The EdgeFinder 4th factor (like a1trading's seasonality row): for the target
calendar month, what has this market done historically? Computed from monthly
closes as the average month-over-month return for that month across years, plus
how consistent it was (share of positive years).

  monthly_returns_by_month(dated_closes) -> {1..12: [returns]}
  seasonal_bias(dated_closes, month)      -> {dir, avg_pct, pos_rate, strength, …}
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Tuple

# (date, close) monthly bars, oldest-first
DatedClose = Tuple[dt.date, float]

STRONG_AVG_PCT = 0.005     # |avg monthly return| ≥ 0.5% to count as "strong"
STRONG_CONSISTENCY = 0.6   # ≥60% of years agree with the direction


def monthly_returns_by_month(dated_closes: List[DatedClose]) -> Dict[int, List[float]]:
    """Month-over-month returns bucketed by the calendar month they landed in."""
    by: Dict[int, List[float]] = {m: [] for m in range(1, 13)}
    for i in range(1, len(dated_closes)):
        (_d0, c0), (d1, c1) = dated_closes[i - 1], dated_closes[i]
        if c0:
            by[d1.month].append((c1 - c0) / c0)
    return by


def seasonal_bias(dated_closes: List[DatedClose], month: int) -> dict:
    """Seasonal read for ``month`` (1–12). Neutral when history is too thin."""
    rets = monthly_returns_by_month(dated_closes).get(month, [])
    if len(rets) < 2:
        return {"month": month, "samples": len(rets), "dir": 0, "avg_pct": None,
                "pos_rate": None, "strength": "none",
                "note": f"insufficient seasonal history for month {month:02d}"}
    avg = sum(rets) / len(rets)
    pos_rate = sum(1 for r in rets if r > 0) / len(rets)
    d = 1 if avg > 0 else -1 if avg < 0 else 0
    consistency = pos_rate if d > 0 else (1 - pos_rate)
    strong = abs(avg) >= STRONG_AVG_PCT and consistency >= STRONG_CONSISTENCY
    lbl = "long" if d > 0 else "short" if d < 0 else "flat"
    return {
        "month": month, "samples": len(rets), "dir": d,
        "avg_pct": round(avg * 100, 2), "pos_rate": round(pos_rate, 2),
        "strength": "strong" if strong else ("mild" if d else "none"),
        "note": (f"month {month:02d} historically {lbl}: avg "
                 f"{round(avg * 100, 2)}% over {len(rets)}y, "
                 f"{round(pos_rate * 100)}% positive"),
    }
