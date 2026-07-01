"""Commodities data via FRED (gold/oil/silver/copper) + EIA (oil) — free, guarded.

Uses FRED_KEY (St. Louis Fed) and EIA_KEY (US Energy Info). Both free. Daily
official series — perfect for CBDR / quarterly on commodities. No-op (None) when
the relevant key is unset.

  latest(key)        newest {value, date, source} for a commodity
  series(key, n)     last n {date, value} points (oldest-first)

Commodity keys: gold, wti, brent, natgas, copper, silver
"""

from __future__ import annotations

from typing import List, Optional

import httpx
from decouple import config

FRED_KEY = config("FRED_KEY", default=None)
EIA_KEY = config("EIA_KEY", default=None)

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
EIA_URL = "https://api.eia.gov/v2/seriesid/{series}"

# key -> data sources. FRED series ids; EIA series ids for oil (fresher).
COMMODITIES = {
    "gold":   {"fred": "GOLDAMGBD228NLBM", "unit": "USD/oz"},
    "wti":    {"fred": "DCOILWTICO", "eia": "PET.RWTC.D", "unit": "USD/bbl"},
    "brent":  {"fred": "DCOILBRENTEU", "eia": "PET.RBRTE.D", "unit": "USD/bbl"},
    "natgas": {"fred": "DHHNGSP", "unit": "USD/MMBtu"},
    "copper": {"fred": "PCOPPUSDM", "unit": "USD/mt"},
    "silver": {"fred": "SLVPRUSD", "unit": "USD/oz"},
}

# Trading symbols → commodity key, so /quarterly/XAU/USD, /quarterly/USOIL etc.
# route to the FRED/EIA daily series instead of TwelveData.
_ALIASES = {
    "GOLD": "gold", "XAU": "gold", "XAUUSD": "gold",
    "WTI": "wti", "USOIL": "wti", "OIL": "wti", "CL": "wti", "CRUDE": "wti",
    "BRENT": "brent", "UKOIL": "brent",
    "SILVER": "silver", "XAG": "silver", "XAGUSD": "silver",
    "COPPER": "copper", "XCU": "copper",
    "NATGAS": "natgas", "NG": "natgas", "NATURALGAS": "natgas",
}


def resolve_commodity(symbol: str) -> Optional[str]:
    """Map a trading symbol to a commodity key, or None if not a commodity.

    Accepts 'gold', 'XAU/USD', 'xauusd', 'USOIL', 'WTI', etc.
    """
    if not symbol:
        return None
    s = symbol.strip().upper().replace("/", "").replace(" ", "")
    if s.lower() in COMMODITIES:
        return s.lower()
    return _ALIASES.get(s)


async def daily_closes(key: str, n: int = 1600) -> Optional[List[dict]]:
    """Commodity daily history as [{date, close}] (for the quarterly engine)."""
    rows = await series(key, n)
    if not rows:
        return None
    return [{"date": r["date"], "close": r["value"]} for r in rows]


def parse_fred(observations: List[dict]) -> List[dict]:
    """FRED observations -> [{date, value}] oldest-first, skipping '.' gaps."""
    out = []
    for o in observations or []:
        v = o.get("value")
        if v in (None, ".", ""):
            continue
        try:
            out.append({"date": o.get("date"), "value": float(v)})
        except (ValueError, TypeError):
            continue
    out.sort(key=lambda r: r["date"])
    return out


async def _fred_series(series_id: str, n: int) -> Optional[List[dict]]:
    if not FRED_KEY:
        return None
    params = {"series_id": series_id, "api_key": FRED_KEY, "file_type": "json",
              "sort_order": "desc", "limit": n}
    try:
        async with httpx.AsyncClient(timeout=12) as c:
            data = (await c.get(FRED_URL, params=params)).json()
    except Exception:
        return None
    rows = parse_fred(data.get("observations"))
    return rows or None


async def _eia_latest(series_id: str) -> Optional[dict]:
    if not EIA_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=12) as c:
            data = (await c.get(EIA_URL.format(series=series_id),
                                params={"api_key": EIA_KEY})).json()
        pts = (data.get("response") or {}).get("data") or []
        if not pts:
            return None
        p = pts[0]                                  # EIA returns newest-first
        return {"value": float(p.get("value")), "date": p.get("period"), "source": "eia"}
    except Exception:
        return None


async def series(key: str, n: int = 30) -> Optional[List[dict]]:
    meta = COMMODITIES.get(key)
    if not meta:
        return None
    rows = await _fred_series(meta["fred"], n) if meta.get("fred") else None
    return rows


async def latest(key: str) -> Optional[dict]:
    meta = COMMODITIES.get(key)
    if not meta:
        return None
    rows = await _fred_series(meta["fred"], 1) if meta.get("fred") else None
    if rows:
        last = rows[-1]
        return {"key": key, "value": last["value"], "date": last["date"],
                "unit": meta.get("unit"), "source": "fred"}
    # Oil fallback to EIA.
    if meta.get("eia"):
        e = await _eia_latest(meta["eia"])
        if e:
            e.update({"key": key, "unit": meta.get("unit")})
            return e
    return None
