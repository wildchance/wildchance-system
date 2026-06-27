"""Intraday breach scanner — fetch 1h bars and detect fresh z-score stretches.

Glue between the pure intraday/engine.py and live data. Reuses dxy_service's
TwelveData fetcher (1h closes) and the instrument catalog. A small default watch
list keeps free-tier request volume low: ~5 symbols x 24 hourly runs = ~120
calls/day, well under TwelveData's 800/day.

The scan returns every fired breach; the route alerts only on `fresh` ones so a
pair that stays stretched for hours pings once, not every hour.
"""

from __future__ import annotations

from typing import List, Optional

from instruments.catalog import get as get_instrument
from intraday.engine import scan_closes, DEFAULT_WINDOW, DEFAULT_Z
from services.dxy_service import instrument_closes

# High-priority intraday watch set (kept small for free-tier request budget).
DEFAULT_WATCH = ["USD/JPY", "EUR/USD", "GBP/USD", "XAU/USD", "NAS100"]


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
        closes = await instrument_closes(ins.td_symbol, interval, outputsize)
        if not closes:
            results.append({"symbol": ins.symbol, "ok": False,
                            "reason": "no data"})
            continue
        b = scan_closes(closes, window=window, z_threshold=z_threshold).to_dict()
        b["symbol"] = ins.symbol
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
