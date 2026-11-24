"""Intraday reference levels the journal trades from — all named, all from NY-time bars.

SESSION  midnight open + midnight high/low (00:00 NY bar), 1am high/low, 7am high/low,
         8-hour high/low (00:00–08:00 NY), 14-hour high/low (yesterday 14:00–24:00 NY),
         previous-day high/low
GRID     fixed steps around the midnight open (the day's mean): gold $15, EUR/USD
         60 pips, GBP/USD 70 pips (GRID_STEPS JSON overrides) → R1..R8 / S1..S8
OPTIONS  25-delta put/call strikes per expiry from data/options_strikes.csv (read off
         the investing.com options screen; expired rows ignored) + the gold put/call
         walls the options feed already holds
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
from typing import Dict, List, Optional, Sequence

from decouple import config

GRID_STEPS = {"XAUUSD": 15.0, "EURUSD": 0.0060, "GBPUSD": 0.0070}
try:
    GRID_STEPS.update({k.upper().replace("/", ""): float(v)
                       for k, v in json.loads(config("GRID_STEPS", default="{}") or "{}").items()})
except (ValueError, TypeError):
    pass
GRID_N = 8
STRIKES_CSV = str(config("OPTIONS_STRIKES_CSV", default="data/options_strikes.csv"))


def _t(b: Dict) -> Optional[dt.datetime]:
    try:
        return dt.datetime.fromisoformat(str(b["time"])[:19])
    except (KeyError, ValueError):
        return None


def session(bars: Sequence[Dict]) -> List[Dict]:
    """Named levels for the latest NY day in ``bars`` (oldest-first, NY-local times)."""
    tb = [(t, b) for b in bars if (t := _t(b))]
    if not tb:
        return []
    day = tb[-1][0].date()
    prev = max((t.date() for t, _ in tb if t.date() < day), default=None)
    out: List[Dict] = []

    def hl(name: str, sel: List[Dict]) -> None:
        if sel:
            out.append({"label": f"{name} high", "price": max(b["high"] for b in sel)})
            out.append({"label": f"{name} low", "price": min(b["low"] for b in sel)})

    today = [(t, b) for t, b in tb if t.date() == day]
    mid = [b for t, b in today if t.hour == 0]
    if mid:
        out.append({"label": "midnight open", "price": mid[0]["open"]})
    hl("midnight", mid)
    hl("1am", [b for t, b in today if t.hour == 1])
    hl("7am", [b for t, b in today if t.hour == 7])
    hl("8-hour", [b for t, b in today if t.hour < 8])
    if prev:
        hl("14-hour", [b for t, b in tb if t.date() == prev and t.hour >= 14])
        hl("prev day", [b for t, b in tb if t.date() == prev])
    return out


def grid(symbol: str, mean: Optional[float], n: int = GRID_N) -> List[Dict]:
    step = GRID_STEPS.get(symbol.upper().replace("/", ""))
    if not step or mean is None:
        return []
    return ([{"label": f"R{i}", "price": mean + i * step} for i in range(1, n + 1)]
            + [{"label": f"S{i}", "price": mean - i * step} for i in range(1, n + 1)])


def option_strikes(symbol: str, today: Optional[dt.date] = None, path: str = STRIKES_CSV,
                   price: Optional[float] = None) -> List[Dict]:
    today = today or dt.date.today()
    out: List[Dict] = []
    if os.path.exists(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                try:
                    if r["symbol"].upper().replace("/", "") != symbol.upper().replace("/", ""):
                        continue
                    exp = dt.date.fromisoformat(r["expiry"])
                    if exp < today:
                        continue
                    for col, name in (("put25", "25Δ put"), ("call25", "25Δ call"),
                                      ("put10", "10Δ put"), ("call10", "10Δ call")):
                        if (r.get(col) or "").strip():
                            out.append({"label": f"{name} {exp:%d-%b}", "price": float(r[col])})
                except (KeyError, ValueError):
                    continue
    try:                                   # free CBOE dealer levels (cached by the 6-hourly refresh)
        from services.options_levels import cached_levels
        out += [{"label": x["label"], "price": x["price"]} for x in cached_levels(symbol, price)]
    except Exception:
        pass
    if symbol.upper().replace("/", "").startswith("XAU"):
        try:
            from gold import options_flow as of
            for k, name in (("put_wall", "put wall"), ("call_wall", "call wall")):
                if of.INPUTS.get(k):
                    out.append({"label": name, "price": float(of.INPUTS[k])})
        except Exception:
            pass
    return out


def all_levels(symbol: str, bars_ny: Sequence[Dict], today: Optional[dt.date] = None) -> List[Dict]:
    s = session(bars_ny)
    mean = next((x["price"] for x in s if x["label"] == "midnight open"), None)
    last = bars_ny[-1]["close"] if bars_ny else None
    return s + grid(symbol, mean) + option_strikes(symbol, today, price=last)


def near(levels: Sequence[Dict], lo: float, hi: float, tol: float) -> List[str]:
    return [x["label"] for x in levels if lo - tol <= x["price"] <= hi + tol]
