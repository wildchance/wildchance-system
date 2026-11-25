"""Broker feed — OHLC pushed by the MT5 EA, served in TwelveData's own shape.

Why: the prices your prop account fills on are the broker's, they cost nothing, and
they arrive the moment a bar closes (real-time instead of hourly cron + paid API).

  ingest(db, symbol, timeframe, bars)   upsert closed bars (UTC open time)
  td_values(symbol, interval, n, tz)    → TwelveData-style {"values": [...]} newest-first,
                                          or None when absent/stale → caller uses TwelveData

Freshness: the newest bar must be younger than 4 bar-lengths (60h over a weekend),
so a stopped EA can never feed the engines stale prices.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from decouple import config
from sqlalchemy import and_, delete, select

BROKER_FEED_ENABLED = str(config("BROKER_FEED_ENABLED", default="true")).strip().lower() in (
    "1", "true", "yes", "on")

# app interval → EA timeframe
INTERVAL_TF = {"15min": "M15", "1h": "H1", "4h": "H4", "1day": "D1", "1week": "W1"}
TF_SECONDS = {"M15": 900, "H1": 3600, "H4": 14400, "D1": 86400, "W1": 604800}

_ALIASES = {"GOLD": "XAUUSD", "SILVER": "XAGUSD", "XTIUSD": "USOIL", "WTI": "USOIL",
            "CRUDE": "USOIL", "USOUSD": "USOIL", "SPX500": "US500", "SP500": "US500",
            "SPX": "US500", "USTEC": "NAS100", "NAS": "NAS100", "NDX": "NAS100",
            "US100": "NAS100", "NQ": "NAS100", "DJ30": "US30", "DJI": "US30", "DOW": "US30", "WS30": "US30",
            "BTCUSDT": "BTCUSD", "BTC": "BTCUSD", "XBTUSD": "BTCUSD", "DE40": "GER40",
            "DAX": "GER40", "USDX": "DXY", "DX": "DXY"}
_ROOTS = ("XAUUSD", "XAGUSD", "BTCUSD", "ETHUSD", "USOIL", "UKOIL", "US500", "NAS100",
          "US30", "GER40", "UK100", "JP225", "DXY")
_CCY = {"USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF", "XAU", "XAG", "ZAR", "CNH", "MXN"}


def canon(symbol: str) -> str:
    """Broker/app symbol → canonical key. 'XAU/USD', 'XAUUSD.r', 'GOLDm' → 'XAUUSD'."""
    s = re.sub(r"[^A-Z0-9]", "", (symbol or "").upper())
    for alias in sorted(_ALIASES, key=len, reverse=True):
        if s.startswith(alias):
            return _ALIASES[alias]
    for root in _ROOTS:
        if s.startswith(root):
            return root
    if len(s) >= 6 and s[:3] in _CCY and s[3:6] in _CCY:
        return s[:6]
    return s


def _utc(x) -> dt.datetime:
    if isinstance(x, (int, float)):
        return dt.datetime.fromtimestamp(int(x), tz=dt.timezone.utc)
    d = dt.datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def is_fresh(last_open: dt.datetime, tf: str, now: Optional[dt.datetime] = None) -> bool:
    now = now or dt.datetime.now(dt.timezone.utc)
    if last_open.tzinfo is None:
        last_open = last_open.replace(tzinfo=dt.timezone.utc)
    age = (now - last_open).total_seconds() - TF_SECONDS.get(tf, 3600)   # since bar CLOSE
    limit = 4 * TF_SECONDS.get(tf, 3600)
    if now.weekday() >= 5 or (now.weekday() == 0 and now.hour < 2):     # weekend gap
        limit = max(limit, 60 * 3600)
    return age <= limit


async def ingest(db, symbol: str, timeframe: str, bars: Iterable[Dict]) -> Dict:
    """Upsert closed bars. bars: [{t: unix|iso (UTC open), o, h, l, c, v}]."""
    from models.broker_bar_model import BrokerBar
    sym, tf = canon(symbol), timeframe.upper()
    if tf not in TF_SECONDS:
        return {"ok": False, "reason": f"unsupported timeframe {timeframe}"}
    rows = []
    for b in bars:
        try:
            rows.append({"ts": _utc(b["t"]), "open": float(b["o"]), "high": float(b["h"]),
                         "low": float(b["l"]), "close": float(b["c"]),
                         "volume": float(b.get("v") or 0)})
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return {"ok": False, "reason": "no valid bars"}
    lo, hi = min(r["ts"] for r in rows), max(r["ts"] for r in rows)
    await db.execute(delete(BrokerBar).where(and_(
        BrokerBar.symbol == sym, BrokerBar.timeframe == tf,
        BrokerBar.ts >= lo, BrokerBar.ts <= hi)))
    db.add_all([BrokerBar(symbol=sym, timeframe=tf, **r) for r in rows])
    await db.commit()
    return {"ok": True, "symbol": sym, "timeframe": tf, "bars": len(rows),
            "last": hi.isoformat()}


async def recent(db, symbol: str, tf: str, n: int) -> List:
    from models.broker_bar_model import BrokerBar
    res = await db.execute(select(BrokerBar).where(and_(
        BrokerBar.symbol == canon(symbol), BrokerBar.timeframe == tf))
        .order_by(BrokerBar.ts.desc()).limit(n))
    return list(res.scalars())                               # newest first


async def td_values(symbol: str, interval: str, outputsize: int,
                    timezone: str = "UTC") -> Optional[Dict]:
    """TwelveData-shaped payload from broker bars, or None (absent / stale / error)."""
    tf = INTERVAL_TF.get(interval)
    if not BROKER_FEED_ENABLED or tf is None:
        return None
    try:
        from database.db import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            rows = await recent(db, symbol, tf, outputsize)
    except Exception:
        return None                                          # table missing / DB down
    if not rows or not is_fresh(rows[0].ts, tf):
        return None
    tz = ZoneInfo(timezone or "UTC")
    daily = tf in ("D1", "W1")
    values = []
    for r in rows:
        ts = r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=dt.timezone.utc)
        local = ts.astimezone(tz)
        values.append({"datetime": local.strftime("%Y-%m-%d" if daily else "%Y-%m-%d %H:%M:%S"),
                       "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                       "volume": r.volume})
    return {"values": values, "status": "ok", "source": "broker"}


async def status() -> Dict:
    """What the EA has pushed: per symbol/timeframe bar counts + freshness."""
    from sqlalchemy import func
    from models.broker_bar_model import BrokerBar
    from database.db import AsyncSessionLocal
    out = []
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(BrokerBar.symbol, BrokerBar.timeframe,
                                      func.count(BrokerBar.id), func.max(BrokerBar.ts))
                               .group_by(BrokerBar.symbol, BrokerBar.timeframe))
        for sym, tf, n, last in res.all():
            out.append({"symbol": sym, "timeframe": tf, "bars": n,
                        "last_open_utc": last.isoformat() if last else None,
                        "fresh": bool(last and is_fresh(last, tf))})
    return {"enabled": BROKER_FEED_ENABLED, "series": out}
