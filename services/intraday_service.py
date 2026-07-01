"""Intraday breach scanner — fetch 1h bars and detect fresh z-score stretches.

Glue between the pure intraday/engine.py and live data. Primary source is
TwelveData (dxy_service); Finnhub is a fallback so a symbol TwelveData can't
serve (or rate-limits) still gets scanned. The wider watch list is affordable
because two sources share the load.

The scan returns every fired breach; the route alerts only on `fresh` ones so a
pair that stays stretched for hours pings once, not every hour.
"""

from __future__ import annotations

from typing import List, Optional

from instruments.catalog import get as get_instrument
from intraday.engine import scan_closes, DEFAULT_WINDOW, DEFAULT_Z
from services.dxy_service import instrument_closes
from services import finnhub_service as fh

# Wider watch set now that Finnhub backs up TwelveData. FX + metals + indices.
DEFAULT_WATCH = [
    "USD/JPY", "EUR/USD", "GBP/USD", "USD/CAD", "AUD/USD", "USD/CHF",
    "EUR/JPY", "GBP/JPY", "XAU/USD", "XAG/USD", "NAS100", "US30",
]


async def _closes(ins, interval: str, outputsize: int):
    """TwelveData first, then Finnhub 1h candles, else None."""
    closes = await instrument_closes(ins.td_symbol, interval, outputsize)
    if closes:
        return closes, "twelvedata"
    # Finnhub fallback (FX pairs → OANDA candles). resolution "60" == 1h.
    if fh.configured():
        fc = await fh.forex_candles(ins.symbol, resolution="60", count=outputsize)
        if fc:
            return fc, "finnhub"
    return None, None


async def scan(symbols: Optional[List[str]] = None,
               window: int = DEFAULT_WINDOW,
               z_threshold: float = DEFAULT_Z,
               interval: str = "1h",
               outputsize: int = 60) -> List[dict]:
    """Scan each watched instrument's recent 1h closes for a z-score breach."""
    symbols = symbols or DEFAULT_WATCH
    results: List[dict] = []
    for sym in symbols:
        ins = get_instrument(sym)
        if ins is None:
            continue
        closes, source = await _closes(ins, interval, outputsize)
        if not closes:
            results.append({"symbol": ins.symbol, "ok": False, "reason": "no data"})
            continue
        b = scan_closes(closes, window=window, z_threshold=z_threshold).to_dict()
        b["symbol"] = ins.symbol
        b["source"] = source
        b["ok"] = True
        results.append(b)
    return results


def fresh_breaches(results: List[dict]) -> List[dict]:
    return [r for r in results if r.get("ok") and r.get("fresh")]


def format_alert(breaches: List[dict]) -> str:
    lines = ["⚡ *Intraday breach* (1h z-score)"]
    for b in breaches:
        arrow = "🔴 SELL lean" if b["lean"] == "SELL" else "🟢 BUY lean"
        lines.append(
            f"• {b['symbol']}  z {b['z']:+.2f}  {b['direction']}  →  {arrow}  "
            f"@ {b['last_close']}"
        )
    return "\n".join(lines)
