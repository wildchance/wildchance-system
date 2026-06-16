"""Persistence for the Wildchance Confluence Engine feed.

The standalone scraper writes a local feed.json. On an ephemeral host (Railway),
a file written by one container is invisible to the web container, so the
dashboard would stay on SNAPSHOT. This table is the shared-state replacement:
the in-process scheduler stores the latest feed here, and the dashboard reads it
back over HTTP — so it goes LIVE and survives restarts/redeploys.

One row, upserted on every scrape (the feed is a complete snapshot, not a log).
The payload is the same dict the scraper would have written to feed.json.
"""

from sqlalchemy import Column, Integer, Text, DateTime
from sqlalchemy.sql import func
from database.db import Base


class WildchanceFeed(Base):
    __tablename__ = "wildchance_feed"

    id = Column(Integer, primary_key=True, index=True)
    feed = Column(Text, nullable=False)            # json.dumps(feed_dict)
    updated_at = Column(DateTime(timezone=True), server_default=func.now())
