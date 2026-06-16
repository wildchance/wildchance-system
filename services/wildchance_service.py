"""In-process Wildchance feed builder + Postgres persistence.

Reuses the *tested* logic in wildchance/wildchance_scraper.py (the same
fetch_* / evaluate / score_nfp functions the CLI scraper uses) but, instead of
writing feed.json, it builds the feed dict and stores it in the database so the
web app and dashboard share one source of truth on an ephemeral host.

The scraper's fetchers use blocking urllib, so build_feed() is run in a worker
thread (asyncio.to_thread) to keep the event loop responsive.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

from sqlalchemy import select

from database.db import AsyncSessionLocal
from models.wildchance_model import WildchanceFeed

# The engine lives in the sibling wildchance/ folder, not on the default path
# (same approach as tests/test_wildchance_engine.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "wildchance"))
import wildchance_scraper as wc  # noqa: E402


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_feed(tier: str, prev: dict | None) -> dict:
    """Build the feed dict for a tier, merging onto the previous feed.

    Mirrors wildchance_scraper.run() exactly, minus the file I/O:
      weekly -> COT (+ gold COT)
      weekly/daily -> retail + calendar
      every tier -> prices + recomputed signals + NFP
    Blocking (network) — call via asyncio.to_thread.
    """
    feed = dict(prev or {})
    now = _now()

    if tier == "weekly":
        cot = wc.fetch_cot()
        cot.update(wc.fetch_gold_cot())
        feed["cot"] = cot
        feed["cot_updated"] = now
    if tier in ("weekly", "daily"):
        feed["retail"] = wc.fetch_retail()
        feed["calendar"] = wc.fetch_calendar()
        feed["daily_updated"] = now

    feed["prices"] = wc.fetch_prices()
    feed["price_updated"] = now

    cot_by_code = feed.get("cot", {})
    retail = feed.get("retail") or wc.fetch_retail()
    prices = feed["prices"]
    feed["signals"] = [wc.evaluate(p, prices[p], retail[p], cot_by_code) for p in wc.WATCH]
    feed["nfp"] = wc.score_nfp(feed.get("calendar") or wc.fetch_calendar(), feed["signals"])
    feed["tier_last_run"] = {**feed.get("tier_last_run", {}), tier: now}
    return feed


async def get_latest_feed() -> dict | None:
    """Return the most recently stored feed dict, or None if none stored yet."""
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(WildchanceFeed).order_by(WildchanceFeed.id.desc()).limit(1))
        ).scalars().first()
        return json.loads(row.feed) if row else None


async def _store_feed(feed: dict) -> None:
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(select(WildchanceFeed).order_by(WildchanceFeed.id.asc()).limit(1))
        ).scalars().first()
        payload = json.dumps(feed)
        if row:
            row.feed = payload
            row.updated_at = dt.datetime.now(dt.timezone.utc)
        else:
            db.add(WildchanceFeed(feed=payload))
        await db.commit()


async def scrape_and_store(tier: str) -> dict:
    """Run a tier scrape in a worker thread and persist the result."""
    prev = await get_latest_feed()
    feed = await asyncio.to_thread(build_feed, tier, prev)
    await _store_feed(feed)
    return feed
