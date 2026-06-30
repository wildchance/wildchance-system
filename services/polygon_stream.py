"""Polygon.io real-time WebSocket → Redis cache — boot-safe.

Streams live trades into Redis (latest tick + pub/sub) for the real-time engine.

IMPORTANT (why this file is structured this way): main.py imports `polygon_stream`
at module load and starts it on startup. So nothing here may fail at import time:
  • the heavy deps (redis, polygon) are imported lazily inside start(), so a box
    without them still boots;
  • POLYGON_API_KEY has a default of None — the stream simply DISABLES itself and
    logs a notice if the key (or Redis) isn't configured, instead of crashing.

Enable it by setting on Render:  POLYGON_API_KEY  and  REDIS_URL (e.g. an Upstash
free Redis URL). Note: Polygon's real-time forex/crypto WebSocket needs a paid
plan; on the free tier the stream stays disabled and the rest of the app is
unaffected.
"""

from __future__ import annotations

import asyncio
import json
from typing import List, Optional

from decouple import config

from utils.logger import logger

POLYGON_API_KEY = config("POLYGON_API_KEY", default=None)
REDIS_URL = config("REDIS_URL", default=None)

# Symbols in Polygon format (C: forex/crypto, I: indices).
DEFAULT_SYMBOLS = [
    "C:EURUSD", "C:GBPUSD", "C:USDJPY", "C:USDCAD",
    "C:AUDUSD", "C:USDCHF", "C:XAUUSD", "I:NDX",
]


class PolygonStream:
    def __init__(self):
        self.redis = None
        self.ws_client = None
        self.running = False

    @property
    def enabled(self) -> bool:
        return bool(POLYGON_API_KEY and REDIS_URL)

    async def _connect_redis(self):
        # Lazy import — redis only needed when actually streaming.
        import redis.asyncio as redis_async
        self.redis = await redis_async.from_url(REDIS_URL, decode_responses=True)
        logger.info("✅ Connected to Redis")

    async def handle_message(self, message) -> None:
        """Process incoming trades and cache the latest price per symbol."""
        try:
            data = message if isinstance(message, (list, dict)) else json.loads(message)
            events = data if isinstance(data, list) else [data]
            for ev in events:
                symbol = ev.get("sym") or ev.get("pair")
                price = ev.get("p") or ev.get("c")
                ts = ev.get("t")
                if symbol and price is not None and self.redis is not None:
                    await self.redis.setex(f"tick:{symbol}", 60, price)  # 60s TTL
                    await self.redis.publish(
                        f"ticks:{symbol}", json.dumps({"price": price, "ts": ts})
                    )
        except Exception as e:
            logger.error(f"Polygon message error: {e}")

    async def start(self, symbols: Optional[List[str]] = None) -> None:
        if self.running:
            return
        if not self.enabled:
            logger.info("⏸️ Polygon stream disabled — set POLYGON_API_KEY and "
                        "REDIS_URL to enable (free tier has no realtime FX WS).")
            return

        try:
            await self._connect_redis()
            # Lazy import — polygon SDK only needed when streaming.
            from polygon import WebSocketClient
            symbols = symbols or DEFAULT_SYMBOLS
            self.running = True
            self.ws_client = WebSocketClient(api_key=POLYGON_API_KEY)
            subs = ",".join(f"T.{s}" for s in symbols)
            self.ws_client.subscribe(subs)
            logger.info(f"🚀 Polygon WebSocket starting for {len(symbols)} symbols")
            # run() blocks, so hand it to a worker thread to keep the loop free.
            await asyncio.to_thread(self.ws_client.run, self._sync_handler)
        except Exception as e:
            self.running = False
            logger.error(f"Polygon stream failed to start: {e}")

    def _sync_handler(self, msg):
        """Bridge the SDK's sync callback into the async handler."""
        try:
            asyncio.run(self.handle_message(msg))
        except Exception as e:
            logger.error(f"Polygon handler error: {e}")

    async def stop(self) -> None:
        self.running = False
        if self.redis is not None:
            await self.redis.close()
        logger.info("⛔ Polygon stream stopped")


# Global instance imported by main.py
polygon_stream = PolygonStream()
