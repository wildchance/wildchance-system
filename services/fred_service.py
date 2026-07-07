"""FRED macro series for the gold regime filter — guarded, additive.

Pulls the higher-timeframe macro inputs the regime stack needs (real rate, Fed
funds, real broad dollar) from the St. Louis Fed. No-op (returns None) when
FRED_KEY is unset, so the regime filter falls back to the encoded snapshot in
gold.macro_cycle.

  real_rate_read()   DFII10 (10Y TIPS) direction — the gold real-rate headwind
  fed_funds_read()   DFF level + 3-month change — hiking / holding / cutting
  dollar_read()      RBUSBIS real broad USD direction — dollar cycle (inverse gold)

Direction is a simple slope: latest vs the mean of the prior points.
"""

from __future__ import annotations

from typing import List, Optional

import httpx
from decouple import config

FRED_KEY = config("FRED_KEY", default=None)
FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


def configured() -> bool:
    return bool(FRED_KEY)


def parse_series(observations: List[dict]) -> List[float]:
    """FRED observations → [value] oldest-first, skipping '.' gaps."""
    out = []
    for o in observations or []:
        v = o.get("value")
        if v in (None, ".", ""):
            continue
        try:
            out.append(float(v))
        except (ValueError, TypeError):
            continue
    return out


def direction(values: List[float], flat_eps: float = 1e-9) -> str:
    """'rising' / 'falling' / 'flat' — latest vs the mean of the prior values."""
    if len(values) < 2:
        return "flat"
    latest, prior = values[-1], values[:-1]
    base = sum(prior) / len(prior)
    if latest > base + flat_eps:
        return "rising"
    if latest < base - flat_eps:
        return "falling"
    return "flat"


async def _series(series_id: str, n: int = 8) -> Optional[List[float]]:
    if not FRED_KEY:
        return None
    params = {"series_id": series_id, "api_key": FRED_KEY, "file_type": "json",
              "sort_order": "desc", "limit": n}
    try:
        async with httpx.AsyncClient(timeout=12) as c:
            data = (await c.get(FRED_URL, params=params)).json()
    except Exception:
        return None
    obs = list(reversed(data.get("observations") or []))     # → oldest-first
    vals = parse_series(obs)
    return vals or None


async def real_rate_read(n: int = 8) -> Optional[dict]:
    """DFII10 (10Y TIPS = real-rate proxy) latest + direction."""
    vals = await _series("DFII10", n)
    if not vals:
        return None
    d = direction(vals)
    return {"series": "DFII10", "latest": vals[-1], "direction": d,
            "gold": "short" if d == "rising" else "long" if d == "falling" else "neutral"}


async def fed_funds_read(n: int = 4) -> Optional[dict]:
    """DFF (Fed funds effective) — level and hold/hike/cut read over the window."""
    vals = await _series("DFF", n)
    if not vals:
        return None
    d = direction(vals)
    cycle = {"rising": "hiking", "falling": "cutting", "flat": "hold"}[d]
    return {"series": "DFF", "latest": vals[-1], "cycle": cycle}


async def dollar_read(n: int = 8) -> Optional[dict]:
    """RBUSBIS (real broad effective USD) latest + direction (inverse gold)."""
    vals = await _series("RBUSBIS", n)
    if not vals:
        return None
    d = direction(vals)
    return {"series": "RBUSBIS", "latest": vals[-1], "direction": d,
            "gold": "long" if d == "falling" else "short" if d == "rising" else "neutral"}
