"""Once-a-day USD/JPY scanner — the automation layer.

Replaces the human "check the close and paste it in" step. Once a day (default
22:00 UTC, after the FX day rolls), it fetches the latest USD/JPY daily close,
ingests it, opens a trade if a signal fires, fills any due day+3 exits, and
sends a Telegram alert on a BUY/SELL.

Enable with USDJPY_SCANNER_ENABLED=true. Pick the run hour with
USDJPY_SCAN_HOUR_UTC (0-23). Implemented as a plain asyncio task so no extra
dependency is required; it self-heals on errors and runs at most once per day.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, date

from decouple import config

from database.db import AsyncSessionLocal
from services import usdjpy_service as svc
from services.usdjpy_close_service import fetch_daily_close
from services.usdjpy_alert import alert_signal
from usdjpy.risk_engine import trade_money_risk

ENABLED = config("USDJPY_SCANNER_ENABLED", default="false").lower() == "true"
SCAN_HOUR_UTC = config("USDJPY_SCAN_HOUR_UTC", default=22, cast=int)
ACCOUNT_SIZE = config("USDJPY_ACCOUNT_SIZE", default=0, cast=float)

_last_run_date: date | None = None


async def run_scan_once() -> dict:
    """Fetch + ingest a single daily close. Safe to call manually."""
    fetched = await fetch_daily_close()
    if not fetched:
        return {"status": "no_data"}
    d, close, source = fetched

    async with AsyncSessionLocal() as db:
        result = await svc.ingest_close(db, d, close, source=source)

    sig = result.get("signal") or {}
    if sig.get("is_trade"):
        trade_risk = None
        if ACCOUNT_SIZE > 0 and sig.get("stop_pips"):
            trade_risk = trade_money_risk(ACCOUNT_SIZE, sig["stop_pips"], sig["close"])
        await alert_signal(sig, trade_risk)

    return {"status": "ok", "date": str(d), "close": close, "signal": sig}


async def _loop():
    global _last_run_date
    print(f"[usdjpy-scanner] enabled — runs daily at {SCAN_HOUR_UTC:02d}:00 UTC")
    while True:
        try:
            now = datetime.now(timezone.utc)
            if now.hour == SCAN_HOUR_UTC and _last_run_date != now.date():
                _last_run_date = now.date()
                res = await run_scan_once()
                print(f"[usdjpy-scanner] {now.isoformat()} -> {res.get('status')}")
        except Exception as e:  # never let the loop die
            print(f"[usdjpy-scanner] error: {e}")
        await asyncio.sleep(60)


def start_scanner() -> None:
    """Launch the daily scanner as a background task if enabled."""
    if not ENABLED:
        print("[usdjpy-scanner] disabled (set USDJPY_SCANNER_ENABLED=true to enable)")
        return
    asyncio.create_task(_loop())
