"""In-process scheduler for the Wildchance feed — the Railway-friendly
replacement for setup_schedule.sh (system cron doesn't run on ephemeral hosts).

Mirrors the scraper's three-tier cadence:
  6h     -> prices + recompute signals     (hour % 6 == 0)
  daily  -> retail + calendar              (hour == WILDCHANCE_DAILY_HOUR_UTC)
  weekly -> COT + gold COT                 (Fri, hour == WILDCHANCE_WEEKLY_HOUR_UTC)

Enable with WILDCHANCE_SCHEDULER_ENABLED=true. Implemented as a plain asyncio
task (no extra dependency), self-healing, runs each tier at most once per slot.
On first run with an empty table it seeds a full (weekly) scrape so the
dashboard has data immediately.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone, date

from decouple import config

from services.wildchance_service import get_latest_feed, scrape_and_store

ENABLED = config("WILDCHANCE_SCHEDULER_ENABLED", default="false").lower() == "true"
DAILY_HOUR_UTC = config("WILDCHANCE_DAILY_HOUR_UTC", default=6, cast=int)
WEEKLY_HOUR_UTC = config("WILDCHANCE_WEEKLY_HOUR_UTC", default=21, cast=int)  # Fri, after CFTC

_last: dict[str, object] = {"6h": None, "daily": None, "weekly": None}


async def _run(tier: str) -> None:
    try:
        feed = await scrape_and_store(tier)
        n = len(feed.get("signals", []))
        print(f"[wildchance] {datetime.now(timezone.utc).isoformat()} {tier} -> {n} signals stored")
    except Exception as e:  # never let the loop die
        print(f"[wildchance] {tier} scrape error: {e}")


async def _loop() -> None:
    print(f"[wildchance] scheduler enabled — daily {DAILY_HOUR_UTC:02d}:00, "
          f"weekly Fri {WEEKLY_HOUR_UTC:02d}:00 UTC, prices every 6h")
    # Seed once if the table is empty so the dashboard isn't stuck on SNAPSHOT.
    try:
        if await get_latest_feed() is None:
            await _run("weekly")
    except Exception as e:
        print(f"[wildchance] seed check error: {e}")

    while True:
        try:
            now = datetime.now(timezone.utc)
            today: date = now.date()
            if now.weekday() == 4 and now.hour == WEEKLY_HOUR_UTC and _last["weekly"] != today:
                _last["weekly"] = today
                await _run("weekly")
            elif now.hour == DAILY_HOUR_UTC and _last["daily"] != today:
                _last["daily"] = today
                await _run("daily")
            elif now.hour % 6 == 0 and _last["6h"] != (today, now.hour):
                _last["6h"] = (today, now.hour)
                await _run("6h")
        except Exception as e:
            print(f"[wildchance] loop error: {e}")
        await asyncio.sleep(60)


def start_wildchance_scheduler() -> None:
    """Launch the feed scheduler as a background task if enabled."""
    if not ENABLED:
        print("[wildchance] scheduler disabled (set WILDCHANCE_SCHEDULER_ENABLED=true to enable)")
        return
    asyncio.create_task(_loop())
