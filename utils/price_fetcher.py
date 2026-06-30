"""Centralized price fetching with optional Redis cache (Polygon stream) + fallbacks.

Boot-safe: Redis is imported lazily inside get_redis(), and REDIS_URL defaults to
None, so the module imports and the app boots even when Redis/Polygon are not
configured (it simply skips the cache and uses the HTTP fallbacks). Uses
redis.asyncio (redis-py >= 5) — NOT the archived aioredis package.

Public entry point used across the app: get_forex_price(symbol).
"""
import re
from typing import Optional

import httpx
from decouple import config

from utils.logger import logger

# Config
TWELVEDATA_KEY = config("TWELVEDATA_API_KEY", default=None)
REDIS_URL = config("REDIS_URL", default=None)   # None → cache disabled, fallbacks only

BASE_URL_TWELVE = "https://api.twelvedata.com/price"
BASE_URL_FRANKFURTER = "https://api.frankfurter.app/latest"


async def get_redis():
    """Return a Redis client, or None if not configured/unavailable."""
    if not REDIS_URL:
        return None
    try:
        import redis.asyncio as redis_async      # lazy — only when a cache exists
        return await redis_async.from_url(REDIS_URL, decode_responses=True)
    except Exception as e:
        logger.error(f"Redis unavailable: {e}")
        return None


async def get_latest_price(symbol: str) -> Optional[float]:
    """Primary: Redis cache (Polygon stream). Fallback: TwelveData/Frankfurter/Metals."""
    redis = await get_redis()
    if redis:
        try:
            price_str = await redis.get(f"tick:{symbol}")
            if price_str:
                return float(price_str)
        except Exception:
            pass
        finally:
            try:
                await redis.close()
            except Exception:
                pass
    return await get_fallback_price(symbol)


async def get_fallback_price(symbol: str) -> Optional[float]:
    """HTTP price sources, tried in order."""
    pair_clean = symbol.strip().upper().replace("/", "")

    price = await _twelvedata_price(pair_clean)
    if price is not None:
        return price

    if re.fullmatch(r"[A-Z]{6}", pair_clean):
        base, quote = pair_clean[:3], pair_clean[3:]
        price = await _frankfurter_price(base, quote)
        if price is not None:
            return price

    if pair_clean.startswith("XAU") or pair_clean.startswith("XAG"):
        return await _metals_price(pair_clean)

    return None


# ==================== HTTP helpers ====================

async def _frankfurter_price(base: str, quote: str) -> Optional[float]:
    url = f"{BASE_URL_FRANKFURTER}?from={base}&to={quote}"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            data = resp.json()
            return float(data["rates"].get(quote))
    except Exception:
        return None


async def _twelvedata_price(symbol: str) -> Optional[float]:
    if not TWELVEDATA_KEY:
        return None
    params = {"symbol": symbol, "apikey": TWELVEDATA_KEY}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(BASE_URL_TWELVE, params=params)
            data = r.json()
            if "price" in data:
                return float(data["price"])
    except Exception:
        return None
    return None


async def _metals_price(pair_clean: str) -> Optional[float]:
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get("https://api.metals.live/v1/spot")
            arr = r.json()
            for item in arr:
                if isinstance(item, dict):
                    sym = item.get("symbol") or item.get("metal") or item.get("pair")
                    price = item.get("price") or item.get("last") or item.get("value")
                    if sym and sym.upper() == pair_clean:
                        return float(price)
    except Exception:
        return None
    return None


# ==================== Public names used by routes ====================

async def get_forex_price(symbol: str) -> Optional[float]:
    """Public entry point imported by routes/market.py and routes/cbdr.py."""
    return await get_latest_price(symbol)


async def get_latest_price_or_fallback(symbol: str, default: float = 0.0) -> float:
    price = await get_latest_price(symbol)
    return price if price is not None else default
