"""Polygon.io Real-Time WebSocket + Redis Cache Service"""
import asyncio
import json
from typing import List, Optional
import aioredis
from polygon import WebSocketClient
from decouple import config
from utils.logger import logger

POLYGON_API_KEY = config("POLYGON_API_KEY")
REDIS_URL = config("REDIS_URL", default="redis://localhost:6379")

# Symbols in Polygon format (C: for forex/crypto, I: for indices)
DEFAULT_SYMBOLS = [
    "C:EURUSD", "C:GBPUSD", "C:USDJPY", "C:USDCAD",
    "C:AUDUSD", "C:USDCHF", "C:XAUUSD", "I:NDX"
]


class PolygonStream:
    def __init__(self):
        self.redis = None
        self.ws_client = None
        self.running = False

    async def connect(self):
        """Connect to Redis"""
        self.redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
        logger.info("✅ Connected to Redis")

    async def handle_message(self, message: str):
        """Process incoming trades/quotes"""
        try:
            data = json.loads(message)
            if "trades" in data:
                for trade in data["trades"]:
                    symbol = trade.get("sym")
                    price = trade.get("p")
                    timestamp = trade.get("t")

                    if symbol and price:
                        # Store latest price
                        await self.redis.setex(
                            f"tick:{symbol}", 60, price  # cache 60 seconds
                        )
                        # Publish to pub/sub for real-time consumers
                        await self.redis.publish(
                            f"ticks:{symbol}", json.dumps({"price": price, "ts": timestamp})
                        )
                        logger.info(f"📈 {symbol} @ {price}")
        except Exception as e:
            logger.error(f"Polygon message error: {e}")

    async def start(self, symbols: Optional[List[str]] = None):
        if self.running:
            return
        self.running = True

        await self.connect()

        symbols = symbols or DEFAULT_SYMBOLS
        self.ws_client = WebSocketClient(api_key=POLYGON_API_KEY)

        async def on_msg(msg):
            await self.handle_message(msg)

        self.ws_client.subscribe(f"T.{','.join(s.replace('C:', '') for s in symbols)}")  # Trades
        self.ws_client.run(on_msg)

        logger.info(f"🚀 Polygon WebSocket started for {len(symbols)} symbols")

    async def stop(self):
        self.running = False
        if self.redis:
            await self.redis.close()
        logger.info("⛔ Polygon stream stopped")


# Global instance
polygon_stream = PolygonStream()
