"""Fetch multi-year daily closes and build the yearly Quarterly-Theory read."""
from __future__ import annotations
import datetime as _dt
from typing import List, Optional
import httpx
from decouple import config
from quarterly.engine import yearly_read

TWELVEDATA_KEY = config("TWELVEDATA_API_KEY", default=None) or config("TWELVEDATA_KEY", default=None)
_OUTPUTSIZE = 1600


async def _daily_rows(symbol: str) -> Optional[List[dict]]:
    if not TWELVEDATA_KEY:
        return None
    url = "https://api.twelvedata.com/time_series"
    params = {"symbol": symbol, "interval": "1day",
              "outputsize": _OUTPUTSIZE, "apikey": TWELVEDATA_KEY}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            data = (await c.get(url, params=params)).json()
    except Exception:
        return None
    values = data.get("values")
    if not values:
        return None
    rows = []
    for v in values:
        ts = v.get("datetime", "")
        try:
            rows.append({"date": ts[:10], "close": float(v["close"])})
        except (KeyError, ValueError, TypeError):
            continue
    return rows or None


async def build_yearly(symbol: str, price: Optional[float] = None) -> Optional[dict]:
    rows = await _daily_rows(symbol)
    if not rows:
        return None
    if price is None:
        price = rows[0]["close"]
    today = _dt.datetime.now(_dt.timezone.utc).date().isoformat()
    out = yearly_read(rows, price, today)
    out["symbol"] = symbol
    return out


def format_alert(read: dict) -> str:
    sym = read.get("symbol", "?")
    lines = [f"🗓️ *Yearly CBDR — {sym} {read['year']}*",
             f"Q{read['quarter']} · *{read['phase'].replace('_', '/').upper()}*  @ {read['price']}"]
    box = read.get("box")
    if box:
        lvl = box["levels"]
        lines += [
            f"Range (Q1 closes): {box['low']} – {box['high']}",
            "Up ext:  " + "  ".join(f"+{n}SD {lvl[f'+{n}SD']}" for n in (2, 2.5, 4) if f"+{n}SD" in lvl),
            "Dn ext:  " + "  ".join(f"-{n}SD {lvl[f'-{n}SD']}" for n in (2, 2.5, 4) if f"-{n}SD" in lvl),
        ]
        ext = read.get("extension") or {}
        if ext.get("extreme"):
            lines.append(f"⚠️ {ext['note']}")
    else:
        lines.append(read.get("note", "no Q1 range yet"))
    streak = (read.get("lookback") or {}).get("streak")
    if streak:
        lines.append(f"Control: *{streak['control']}* for {streak['years']}y (since {streak['since']})")
    return "\n".join(lines)
