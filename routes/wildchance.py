"""Wildchance Confluence Engine API.

  GET  /wildchance/feed            latest stored feed (the dashboard reads this)
  POST /wildchance/scrape?tier=6h  trigger a scrape now (6h | daily | weekly)

The feed is the same JSON shape the CLI scraper writes to feed.json, but served
from Postgres so it survives an ephemeral host and is shared with the dashboard.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from services.wildchance_service import get_latest_feed, scrape_and_store

router = APIRouter(prefix="/wildchance", tags=["wildchance"])


@router.get("/feed")
async def latest_feed():
    feed = await get_latest_feed()
    if feed is None:
        # 404 lets the dashboard fall back to its SNAPSHOT view gracefully.
        raise HTTPException(status_code=404, detail="no feed stored yet")
    return feed


@router.post("/scrape")
async def scrape(tier: str = Query("6h", pattern="^(6h|daily|weekly)$")):
    feed = await scrape_and_store(tier)
    return {"status": "ok", "tier": tier, "signals": len(feed.get("signals", [])),
            "updated": feed.get("price_updated")}
