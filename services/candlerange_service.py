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
from candlerange.crt import crt_setup, crt_confirm, session_for_hour, SESSIONS

TWELVEDATA_KEY = config("TWELVEDATA_API_KEY", default=None) or config("TWELVEDATA_KEY", default=None)
ANCHOR_HOURS = (1, 13)
FRIDAY_CLOSE_HOUR = 20      # last full 1h candle before the 21:00 UTC Friday close


def _ohlc(v: dict) -> Optional[dict]:
    try:
        return {"open": float(v["open"]), "high": float(v["high"]),
                "low": float(v["low"]), "close": float(v["close"])}
    except (KeyError, ValueError, TypeError):
        return None


ANCHOR_MAX_AGE_H = 24          # yesterday's / weekend anchors are stale — skip them


def min_body(symbol: str) -> float:
    """Smallest body worth reading (smaller = doji / closed-market placeholder bar)."""
    s = symbol.upper().replace("/", "")
    if s.startswith(("XAU", "GOLD")):
        return 1.0
    if len(s) >= 6 and s[3:6] == "JPY":
        return 0.03
    return 0.0003 if len(s) >= 6 and s[:6].isalpha() else 0.0


def anchor_fresh(ts: str, now: Optional[_dt.datetime] = None) -> bool:
    try:
        t = _dt.datetime.fromisoformat(ts[:19]).replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return False
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return 0 <= (now - t).total_seconds() <= ANCHOR_MAX_AGE_H * 3600


async def analyze_symbol(symbol: str, price: Optional[float] = None) -> Optional[dict]:
    params = {"symbol": symbol, "interval": "1h", "outputsize": 48, "timezone": "UTC"}
    try:
        from services.ohlc_service import time_series   # EA broker bars first, then TwelveData
        data = await time_series(params)
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
            if o and (not anchor_fresh(ts) or abs(o["close"] - o["open"]) < min_body(symbol)):
                anchors[hour] = False          # stale or doji → no read for this anchor
                continue
            if o:
                o["datetime"] = ts
                anchors[hour] = o
        if all(anchors[h] is not None for h in ANCHOR_HOURS):
            break

    result = analyze(anchors[1] or None, anchors[13] or None, price)
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


async def crt_read(symbol: str, session: Optional[str] = None,
                   price: Optional[float] = None) -> Optional[dict]:
    """1-5-9 CRT read: build the session's limit ladder off its 1-o'clock range
    candle and check the 5-o'clock confirmation against the live price.

    ``session`` defaults from the current UTC hour (05:00→asia, 17:00→ny).
    """
    if not TWELVEDATA_KEY:
        return None
    if session is None:
        session = session_for_hour(_dt.datetime.now(_dt.timezone.utc).hour) or "asia"
    meta = SESSIONS.get(session)
    if meta is None:
        return None
    range_hour = meta["range_hour"]

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
    if price is None:
        latest = _ohlc(values[0])
        price = latest["close"] if latest else None

    candle = None
    for v in values:                       # newest-first → most recent range candle
        ts = v.get("datetime", "")
        if len(ts) < 13:
            continue
        try:
            hour = int(ts[11:13])
        except ValueError:
            continue
        if hour == range_hour:
            candle = _ohlc(v)
            if candle:
                candle["datetime"] = ts
                break
    if candle is None:
        return None

    setup = crt_setup(candle, session)
    out = {"symbol": symbol, "session": session, "range_candle": candle, **setup}
    if price is not None and setup.get("ok"):
        out["confirmation"] = crt_confirm(setup, price)
        out["price"] = price
    return out


def format_crt_alert(read: Optional[dict]) -> Optional[str]:
    """Telegram text when a CRT confirmation triggers, else None."""
    if not read or not read.get("ok"):
        return None
    conf = read.get("confirmation") or {}
    if not conf.get("confirmed"):
        return None
    o = conf["order"]
    arrow = "🟢 BUY" if o["side"] == "long" else "🔴 SELL"
    return (f"🎯 *CRT 1-5-9 — {read['symbol']}*  ({read['label']})\n"
            f"{arrow} limit `{o['entry']}` at {o['dev']:+g} dev — confirmed @ {read['price']}\n"
            f"_{conf['note']}_")


def format_friday_alert(read: Optional[dict]) -> Optional[str]:
    """Telegram text for the Friday→Monday gap read, or None when flat."""
    if not read or read.get("gap_bias") == "flat":
        return None
    icon = "⬆️" if read["gap_bias"] == "up" else "⬇️"
    return (f"📅 *Monday-gap read — {read['symbol']}*\n"
            f"{icon} gap *{read['gap_bias'].upper()}* ({read['strength']}) — "
            f"{read['note']}")


def plan_from_anchor(symbol: str, a: dict) -> Optional[dict]:
    """Candle-range read → CRT-style limit plan. Price broke ABOVE the body (continuation
    or reversal) → BUY the retest of the body high; below → SELL the body low retest.
    Stop: the far side of the body (floored to the owner's 10 pips). Targets: +1 and +2
    body-lengths beyond the body (CRT deviations)."""
    if not a or a.get("state") not in ("continuation", "reversal"):
        return None
    from services import trade_gate as tg
    lo, hi = float(a["body_low"]), float(a["body_high"])
    body = hi - lo
    if body <= 0:
        return None
    if a["break_dir"] == "up":
        side, entry, stop = "BUY", hi, lo
        tps = [hi + body, hi + 2 * body]
    else:
        side, entry, stop = "SELL", lo, hi
        tps = [lo - body, lo - 2 * body]
    stop = tg.widen_stop(symbol, side, entry, stop)
    return {"side": side, "entry": entry, "stop": stop, "tp1": tps[0], "tp2": tps[1],
            "rr_tp2": tg.best_rr(entry, stop, tps)}


async def add_plans(result: dict) -> dict:
    """Attach a gated plan to each actionable anchor (session, 1.3R, weekly, macro lock)."""
    from services import trade_gate as tg
    sym = result.get("symbol", "")
    weekly = await tg.weekly_profile(sym)
    try:
        from services.swing_service import macro_bias
        lock = macro_bias().get(sym.replace("/", ""))
    except Exception:
        lock = None
    for name, a in (result.get("anchors") or {}).items():
        p = plan_from_anchor(sym, a) if a else None
        if not p:
            continue
        g = tg.evaluate(sym, p["side"], p["entry"], p["stop"], [p["tp1"], p["tp2"]], weekly=weekly)
        if lock and ((lock == "BUY_ONLY" and p["side"] == "SELL") or
                     (lock == "SELL_ONLY" and p["side"] == "BUY")):
            g["allow"] = False
            g["reasons"].append(f"macro lock {lock}")
        p["gate"] = g
        a["plan"] = p
    return result


def format_alert(result: dict) -> Optional[str]:
    """Telegram text if any anchor shows a continuation/reversal, else None."""
    interesting = []
    for name, a in result.get("anchors", {}).items():
        if a and a["state"] in ("continuation", "reversal"):
            label = "01:00 UTC" if name == "0100" else "13:00 UTC"
            icon = "➡️" if a["state"] == "continuation" else "🔄"
            sym = result["symbol"]
            interesting.append(
                f"{icon} {label} candle ({a['candle_dir']}): *{a['state']}* "
                f"{a['break_dir']}  [body {_px(sym, a['body_low'])}–{_px(sym, a['body_high'])}]"
            )
            p = a.get("plan")
            if p and p["gate"]["allow"]:
                arrow = "🟢 BUY" if p["side"] == "BUY" else "🔴 SELL"
                interesting.append(
                    f"   {arrow} limit `{_px(sym, p['entry'])}` · SL `{_px(sym, p['stop'])}` · "
                    f"TP1 `{_px(sym, p['tp1'])}` · TP2 `{_px(sym, p['tp2'])}` (R {p['rr_tp2']})")
                try:
                    from propfirm.target_lots import card_lines
                    lots = card_lines(sym, p["entry"], p["stop"],
                                      [{"name": "TP1", "price": p["tp1"]},
                                       {"name": "TP2", "price": p["tp2"]}])
                    interesting += ["   " + l for l in lots]
                except Exception:
                    pass
                crt = "05:00" if name == "0100" else "17:00"
                interesting.append(f"   _CRT confirmation at {crt} UTC — enter only on the retest_")
            elif p:
                interesting.append("   ⏸ no trade: " + "; ".join(p["gate"]["reasons"]))
    if not interesting:
        return None
    return "\n".join([f"🕯️ *Candle-range — {result['symbol']}*  @ {_px(result['symbol'], result['price'])}",
                      ""] + interesting)


def _px(symbol: str, v) -> str:
    from routes.cbdr import px
    return px(symbol, v)
