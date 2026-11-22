"""Data-freshness guard — know when you're blind.

A scan that "succeeds" on stale prices is worse than one that fails. This tracks the
newest gold price the app has actually seen (EA broker bars in the DB, or the last
successful TwelveData/broker fetch in memory) and grades it:

  OK       newest price ≤ DATA_STALE_MIN minutes old (default 120)
  STALE    older than that while the market is open  → /health "degraded",
           Telegram warning (once per 3h), GitHub backup run fails red
  CLOSED   weekend (Fri 17:00 → Sun 17:00 New York) — no prices expected
  UNKNOWN  nothing fetched yet since boot (grace period: 75 min of uptime)
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from decouple import config

STALE_MIN = float(config("DATA_STALE_MIN", default="120"))
BOOT = time.time()
LAST_OK: Dict[str, Dict] = {}          # canon symbol → {"t": epoch, "source": str}
NY = ZoneInfo("America/New_York")


def record(symbol: str, source: str) -> None:
    try:
        from services.broker_feed import canon
        LAST_OK[canon(symbol)] = {"t": time.time(), "source": source}
    except Exception:
        pass


def market_open(now: Optional[dt.datetime] = None) -> bool:
    """FX/metals week: Sunday 17:00 → Friday 17:00 New York time."""
    ny = (now or dt.datetime.now(dt.timezone.utc)).astimezone(NY)
    wd, h = ny.weekday(), ny.hour
    if wd == 5:                                   # Saturday
        return False
    if wd == 4 and h >= 17:                       # Friday after close
        return False
    if wd == 6 and h < 17:                        # Sunday before open
        return False
    return True


def grade(age_min: Optional[float], now: Optional[dt.datetime] = None,
          uptime_min: Optional[float] = None) -> str:
    if not market_open(now):
        return "CLOSED"
    if age_min is None:
        up = uptime_min if uptime_min is not None else (time.time() - BOOT) / 60
        return "UNKNOWN" if up < 75 else "STALE"
    return "OK" if age_min <= STALE_MIN else "STALE"


async def gold_status(symbol: str = "XAUUSD") -> Dict:
    ages = {}
    mem = LAST_OK.get(symbol)
    if mem:
        ages["last_fetch"] = (time.time() - mem["t"]) / 60
    try:                                          # EA broker bars persist across restarts
        from sqlalchemy import func, select
        from database.db import AsyncSessionLocal
        from models.broker_bar_model import BrokerBar
        from services.broker_feed import TF_SECONDS
        async with AsyncSessionLocal() as db:
            rows = (await db.execute(select(BrokerBar.timeframe, func.max(BrokerBar.ts))
                                     .where(BrokerBar.symbol == symbol)
                                     .group_by(BrokerBar.timeframe))).all()
        now = dt.datetime.now(dt.timezone.utc)
        for tf, ts in rows:
            if ts is None:
                continue
            ts = ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)
            close = ts + dt.timedelta(seconds=TF_SECONDS.get(tf, 3600))
            ages[f"broker_{tf}"] = max(0.0, (now - close).total_seconds() / 60)
    except Exception:
        pass
    age = min(ages.values()) if ages else None
    status = grade(age)
    return {"symbol": symbol, "status": status,
            "age_min": round(age, 1) if age is not None else None,
            "stale_after_min": STALE_MIN, "sources": {k: round(v, 1) for k, v in ages.items()},
            "last_source": mem["source"] if mem else None}
