"""Fetch the 01:00 & 13:00 UTC anchor candles and classify the move.

Pulls 1h bars in UTC (one TwelveData request), picks the most recent 01:00 and
13:00 candles, then runs candlerange.engine.analyze against the latest price.
Returns None on any failure so callers degrade gracefully.
"""

from __future__ import annotations

import datetime as _dt
from typing import Optional

import httpx
from decouple import config

from candlerange.engine import analyze, friday_gap_read, weekday_confidence

TWELVEDATA_KEY = config("TWELVEDATA_API_KEY", default=None) or config("TWELVEDATA_KEY", default=None)
ANCHOR_HOURS = (1, 13)
FRIDAY_CLOSE_HOUR = 20      # last full 1h candle before the 21:00 UTC Friday close


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

    # values are newest-first. Latest close is the working price if not given.
    latest = _ohlc(values[0])
    if price is None and latest:
        price = latest["close"]
    if price is None:
        return None

    anchors = {1: None, 13: None}
    for v in values:                       # newest-first → first hit is most recent
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
    # Confidence by weekday — Tue–Thu reads are choppy / low-confidence.
    wd = _dt.datetime.now(_dt.timezone.utc).weekday()
    result["confidence"] = weekday_confidence(wd)
    if result["confidence"] == "low":
        result["confidence_note"] = "mid-week — choppy, treat as low-confidence"
    return result


async def friday_close_read(symbol: str) -> Optional[dict]:
    """Friday pre-close 1h candle → Monday-gap anticipation (the reliable read)."""
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
    fri = None
    for v in values:                       # newest-first → most recent Friday 20:00
        ts = v.get("datetime", "")
        if len(ts) < 13:
            continue
        try:
            d = _dt.date.fromisoformat(ts[:10])
            hour = int(ts[11:13])
        except ValueError:
            continue
        if d.weekday() == 4 and hour == FRIDAY_CLOSE_HOUR:
            fri = _ohlc(v)
            if fri:
                fri["datetime"] = ts
                break
    if not fri:
        return None
    read = friday_gap_read(fri)
    read["symbol"] = symbol
    read["candle_time"] = fri["datetime"]
    return read


def format_friday_alert(read: Optional[dict]) -> Optional[str]:
    """Telegram text for the Friday→Monday gap read, or None when flat."""
    if not read or read.get("gap_bias") == "flat":
        return None
    icon = "⬆️" if read["gap_bias"] == "up" else "⬇️"
    return (f"📅 *Monday-gap read — {read['symbol']}*\n"
            f"{icon} gap *{read['gap_bias'].upper()}* ({read['strength']}) — "
            f"{read['note']}")


def format_alert(result: dict) -> Optional[str]:
    """Telegram text if any anchor shows a continuation/reversal, else None."""
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
