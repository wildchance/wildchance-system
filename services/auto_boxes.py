"""Automatic event boxes — FOMC-day CBDR boxes and chained boxes, from price bars.

FOMC   On each Fed decision day the box is that day's CBDR (14:00–20:00 New York,
       hourly highs/lows). Matches the owner's chart: the Sep-16-26 'rate hike' box
       4,252.93–4,359.77 vs the same day's CBDR 4,253.25–4,359.69.
CHAIN  After an exhaustion move through a parent box's ±3.5 / ±4, the trader draws a
       new box whose edge sits ON that level (Nov-11-26: new 0 = 3,972.63 on the
       old −4 = 3,971.24). Here: any later daily CBDR box whose low (down-move) or
       high (up-move) lands within tolerance of the exhausted level becomes a child.

Both feed the yearly engine's cluster map as event boxes (labelled 'auto').
Bars: one hourly request per symbol (America/New_York), cached with the yearly run.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Sequence, Tuple

from decouple import config

# Fed statement days (federalreserve.gov FOMC calendar). Extend when the Fed publishes.
FOMC_DATES = [dt.date.fromisoformat(d) for d in (
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17",
    "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16",
    "2026-10-28", "2026-12-09")]
EXTRA = str(config("FOMC_EXTRA_DATES", default=""))          # "2027-01-27,2027-03-17"
CHAIN_LEVELS = (3.5, 4.0)


def fomc_dates() -> List[dt.date]:
    extra = []
    for x in EXTRA.split(","):
        try:
            extra.append(dt.date.fromisoformat(x.strip()))
        except ValueError:
            continue
    return sorted(set(FOMC_DATES + extra))


def cbdr_by_day(bars: Sequence[Dict]) -> Dict[dt.date, Tuple[float, float]]:
    """{NY date: (low, high)} of the 14:00–20:00 bars. Bars carry NY-local 'time'."""
    out: Dict[dt.date, List[float]] = {}
    for b in bars:
        try:
            t = dt.datetime.fromisoformat(str(b["time"])[:19])
        except (KeyError, ValueError):
            continue
        if 14 <= t.hour < 20:
            lo_hi = out.setdefault(t.date(), [b["low"], b["high"]])
            lo_hi[0], lo_hi[1] = min(lo_hi[0], b["low"]), max(lo_hi[1], b["high"])
    return {d: (v[0], v[1]) for d, v in out.items() if v[1] > v[0]}


def _box(label: str, day: dt.date, lo: float, hi: float, source: str) -> Dict:
    return {"year": f"{label} {day:%b-%d}", "date": day.isoformat(), "low": lo, "high": hi,
            "range": hi - lo, "source": source}


def fomc_boxes(by_day: Dict[dt.date, Tuple[float, float]], today: dt.date, max_days: int,
               skip: Sequence[dt.date] = ()) -> List[Dict]:
    out = []
    for d in fomc_dates():
        if d in skip or not (0 <= (today - d).days <= max_days) or d not in by_day:
            continue
        lo, hi = by_day[d]
        out.append(_box("FOMC auto", d, lo, hi, "auto-fomc"))
    return out


def chained(parents: Sequence[Dict], by_day: Dict[dt.date, Tuple[float, float]],
            tol: float) -> List[Dict]:
    """Child boxes: a later daily CBDR whose edge sits on a parent's exhausted ±3.5/±4."""
    out = []
    days = sorted(by_day)
    for p in parents:
        try:
            pday = dt.date.fromisoformat(p["date"])
        except (KeyError, ValueError):
            continue
        rng = p["high"] - p["low"]
        if rng <= 0:
            continue
        for k in CHAIN_LEVELS:
            # down-move: child LOW on the fib's −k (low − k·range); up-move: child HIGH on +k
            for sign, lvl in ((-1, p["low"] - k * rng), (1, p["low"] + k * rng)):
                for d in days:
                    if d <= pday:
                        continue
                    lo, hi = by_day[d]
                    if abs((lo if sign < 0 else hi) - lvl) <= tol:
                        out.append(_box(f"chain {p['year']} {'-' if sign < 0 else '+'}{k:g}", d, lo, hi,
                                        "auto-chain"))
                        break
    return out


async def load(symbol: str, today: dt.date, max_days: int, tol: float,
               manual_dates: Sequence[dt.date] = (), manual_parents: Sequence[Dict] = ()) -> List[Dict]:
    """FOMC + chained boxes for ``symbol`` (empty on any data problem)."""
    from services.ohlc_service import fetch_ohlc_raw
    try:
        bars = await fetch_ohlc_raw(symbol, "1h", min(5000, 24 * (max_days + 5)),
                                    timezone="America/New_York")
    except Exception:
        return []
    by_day = cbdr_by_day(bars)
    if not by_day:
        return []
    fomc = fomc_boxes(by_day, today, max_days, skip=manual_dates)
    return fomc + chained(list(fomc) + list(manual_parents), by_day, tol)
