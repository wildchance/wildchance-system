"""Fetch the 01:00 & 13:00 UTC anchor candles..."""
from __future__ import annotations

from typing import Optional
import httpx
from decouple import config
from candlerange.engine import analyze

TWELVEDATA_KEY = config("TWELVEDATA_API_KEY", default=None) or config("TWELVEDATA_KEY", default=None)
ANCHOR_HOURS = (1, 13)


def _ohlc(v: dict) -> Optional[dict]:
    try:
        return {"open": float(v["open"]), "high": float(v["high"]),
                "low": float(v["low"]), "close": float(v["close"])}
    except (KeyError, ValueError, TypeError):
        return None


async def analyze_symbol(symbol: str, price: Optional[float] = None) -> Optional[dict]:
    if not TWELVEDATA_KEY:
        return None
    url = "https://api.twelvedata.com/time_series"
    params = {"symbol": symbol, "interval": "1h", "outputsize": 48,
              "timezone": "UTC", "apikey": TWELVEDATA_KEY}
    try:
        async with httpx.AsyncClient(timeout=12) as c:
            data = (await c.get(url, params=params)).json()
    except Exception:
        return None
    values = data.get("values")
    if not values:
        return None

    latest = _ohlc(values[0])
    if price is None and latest:
        price = latest["close"]
    if price is None:
        return None

    anchors = {1: None, 13: None}
    for v in values:
        ts = v.get("datetime", "")
        if len(ts) < 13:
            continue
        try:
            hour = int(ts[11:13])
        except ValueError:
            continue
        if hour in ANCHOR_HOURS and anchors[hour] is None:
            o = _ohlc(v)
            if o:
                o["datetime"] = ts
                anchors[hour] = o
        if all(anchors[h] is not None for h in ANCHOR_HOURS):
            break

    result = analyze(anchors[1], anchors[13], price)
    result["symbol"] = symbol
    result["anchor_times"] = {
        "0100": anchors[1]["datetime"] if anchors[1] else None,
        "1300": anchors[13]["datetime"] if anchors[13] else None,
    }
    return result


def format_alert(result: dict) -> Optional[str]:
    interesting = []
    for name, a in result.get("anchors", {}).items():
        if a and a["state"] in ("continuation", "reversal"):
            label = "01:00 UTC" if name == "0100" else "13:00 UTC"
            icon = "➡️" if a["state"] == "continuation" else "🔄"
            interesting.append(
                f"{icon} {label} candle ({a['candle_dir']}): *{a['state']}* "
                f"{a['break_dir']}  [body {a['body_low']}–{a['body_high']}]"
            )
    if not interesting:
        return None
    return "\n".join([f"🕯️ *Candle-range — {result['symbol']}*  @ {result['price']}",
                      ""] + interesting)
